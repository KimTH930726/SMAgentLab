"""임베딩 모델 교체 마이그레이션 — mpnet(768차원) → KURE-v1(1024차원), v2.72.

docs/tech/embedding-reranker-upgrade-plan.md 실측(89문항 골든셋, 2026-09-11) 결과
KURE-v1이 전체 hit@10 82.0%(현재 모델 55.1% 대비 +26.9%p, 전 유형 개선)로 1위 —
공개 벤치마크(MTEB-ko-retrieval) 순위와도 일치해 팀 확정. 이 스크립트는 그 확정에
따른 전체 재색인이다.

대상 테이블 8개(vector(768) 보유 테이블 중 sql_* 3개는 Text2SQL이 main.py에
미등록된 죽은 코드라 제외 — routers 미등록 확인함):
rag_knowledge(24건 active) / policy_chunk(597건) / rag_glossary(162건) /
rag_fewshot(13건) / rag_conv_summary(8건) / ops_email_analysis(1070건) /
ops_voc_cluster(78건) / rag_knowledge_history(0건, 스키마만 변경)

원문 텍스트 재구성 — 각 테이블의 실제 저장 코드와 동일한 소스를 그대로 재사용:
- rag_knowledge: content 컬럼 그대로 (knowledge/service.py:create_knowledge), status='active'만
- rag_glossary: description 컬럼 그대로 (knowledge/service.py:create_glossary)
- rag_fewshot: question 컬럼 그대로 (fewshot/router.py:create_fewshot)
- rag_conv_summary: summary 컬럼 그대로 (chat/memory.py:_store_summary)
- policy_chunk: chunk_text 컬럼 그대로 (policy/service.py)
- ops_email_analysis: 원래 임베딩 소스(LLM이 매 건 생성하는 issue_signature)는 DB에
  영속화된 적이 없어 그대로 재현 불가 — 코드가 이미 정의해둔 폴백 경로를 그대로
  씀(pipeline.py 주석: "issue_signature가 없으면 원문 임베딩으로 안전하게 폴백") —
  subject+body를 email_voc/service.py:check_relevance()와 동일하게 재구성
- ops_voc_cluster.representative_embedding: 신규 멤버 유입마다 갱신되는 이동평균
  centroid라 과거 값 자체를 정확히 복원할 방법이 없음 — representative_subject를
  다시 embed()해서 "새 클러스터 갓 생성된 시점"의 값으로 리셋(이후 detect_and_
  update_cluster()의 이동평균 로직이 신규 멤버 유입마다 계속 갱신하므로 자연 수렴)

**실측으로 확인한 진짜 원인(3차례 재현 끝에 특정, 2026-09-11)**: 매번 "rag_knowledge
인코딩 중..."에서 죽길래 처음엔 asyncio 이벤트루프 문제로 의심했으나(무관한 것으로
결론, 아래는 참고용으로만 남김), 실제 원인은 rag_knowledge active 24건 중 id=17
("딜리버스 전체 API 정책서" 22KB 마크다운 표 블롭 — 2026-09-10 DB/공통코드 정리 때
"판단 대기"로 남겨뒀던 잔여물)의 토큰 수가 **12,061개로 KURE-v1의 max_seq_length
(8192)를 넘어** 인코딩 시 인덱싱 에러/비정상 메모리 사용을 유발했다. 그래서 모든
테이블 공통으로 임베딩 입력 텍스트를 안전하게 자르는 `_safe_text()`를 추가한다
(길이 초과 문서를 통째로 버리지 않고 앞부분만 사용 — production `embed_long()`의
"긴 텍스트를 어떻게든 반영" 정신과 동일, 이 일회성 마이그레이션에서는 청킹 대신
단순 절단으로 충분).

실행: docker exec -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 -it ops-backend \
      python scripts/migrate_embedding_model.py
(HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE 오버라이드도 필수 — 안 하면 모델 로딩은 되지만
encode() 단계에서 비슷한 증상으로 죽는 게 실측 확인됨)
"""
from __future__ import annotations

import asyncio
import os
import time

import asyncpg
from sentence_transformers import SentenceTransformer

_MODEL_NAME = "nlpai-lab/KURE-v1"
_DIM = 1024
_BATCH = 32
_DB_URL = os.environ.get("DATABASE_URL", "postgresql://ops:ops1234@localhost:5432/opsdb")
_MAX_CHARS = 6000  # 한글 기준 토큰수가 대략 글자수 이하이므로 8192토큰 한도에 안전하게 못 미침


def _safe_text(text: str | None) -> str:
    text = text or ""
    return text[:_MAX_CHARS]


def _fetch_texts(sql: str) -> list[asyncpg.Record]:
    async def _run():
        conn = await asyncpg.connect(_DB_URL)
        try:
            return await conn.fetch(sql)
        finally:
            await conn.close()
    return asyncio.run(_run())


def _apply_update(table, id_col, embed_col, index_name, index_def, rows, vecs, extra_set=""):
    async def _run():
        conn = await asyncpg.connect(_DB_URL)
        try:
            async with conn.transaction():
                if index_name:
                    await conn.execute(f"DROP INDEX IF EXISTS {index_name}")
                await conn.execute(f"ALTER TABLE {table} ALTER COLUMN {embed_col} TYPE vector({_DIM}) USING NULL::vector({_DIM})")
                for r, v in zip(rows, vecs):
                    await conn.execute(
                        f"UPDATE {table} SET {embed_col} = $1::vector{extra_set} WHERE {id_col} = $2",
                        str(list(v)), r["id"],
                    )
                if index_name:
                    await conn.execute(index_def)
        finally:
            await conn.close()
    asyncio.run(_run())


def _reembed_simple(model, *, table, id_col, text_col, embed_col, index_name, index_def, extra_set="", where=""):
    rows = _fetch_texts(f"SELECT {id_col} AS id, {text_col} AS text FROM {table} WHERE {text_col} IS NOT NULL{where}")
    print(f"  {table}: {len(rows)}건 인코딩 중...")
    t0 = time.monotonic()
    texts = [_safe_text(r["text"]) for r in rows]
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False, batch_size=_BATCH) if texts else []
    print(f"    인코딩 {time.monotonic()-t0:.1f}s")
    _apply_update(table, id_col, embed_col, index_name, index_def, rows, vecs, extra_set)
    print(f"  {table}: 완료 ({len(rows)}건 반영)")


def main():
    print(f"모델 로딩: {_MODEL_NAME}")
    model = SentenceTransformer(_MODEL_NAME)
    print("로딩 완료\n")

    # 1. rag_knowledge — content 그대로, embedding_model 컬럼도 같이 기록.
    #    status='active'만(2026-09-10 soft-delete한 3040건 DB/공통코드 오염 데이터는 제외).
    _reembed_simple(
        model, table="rag_knowledge", id_col="id", text_col="content", embed_col="embedding",
        index_name="idx_knowledge_emb",
        index_def="CREATE INDEX idx_knowledge_emb ON rag_knowledge USING hnsw (embedding vector_cosine_ops)",
        extra_set=", embedding_model = 'nlpai-lab/KURE-v1'",
        where=" AND status = 'active'",
    )

    # 2. rag_glossary — description 그대로
    _reembed_simple(
        model, table="rag_glossary", id_col="id", text_col="description", embed_col="embedding",
        index_name="idx_glossary_emb",
        index_def="CREATE INDEX idx_glossary_emb ON rag_glossary USING hnsw (embedding vector_cosine_ops)",
    )

    # 3. rag_fewshot — question 그대로
    _reembed_simple(
        model, table="rag_fewshot", id_col="id", text_col="question", embed_col="embedding",
        index_name="idx_fewshot_emb",
        index_def="CREATE INDEX idx_fewshot_emb ON rag_fewshot USING hnsw (embedding vector_cosine_ops)",
    )

    # 4. rag_conv_summary — summary 그대로
    _reembed_simple(
        model, table="rag_conv_summary", id_col="id", text_col="summary", embed_col="embedding",
        index_name="idx_conv_summary_vec",
        index_def="CREATE INDEX idx_conv_summary_vec ON rag_conv_summary USING hnsw (embedding vector_cosine_ops)",
    )

    # 5. policy_chunk — chunk_text 그대로
    _reembed_simple(
        model, table="policy_chunk", id_col="id", text_col="chunk_text", embed_col="embedding",
        index_name="idx_policy_chunk_hnsw",
        index_def="CREATE INDEX idx_policy_chunk_hnsw ON policy_chunk USING hnsw (embedding vector_cosine_ops)",
    )

    # 6. ops_email_analysis — subject+body 재구성(email_voc/service.py:check_relevance()와 동일 패턴).
    rows = _fetch_texts("SELECT id, subject, body FROM ops_email_analysis WHERE embedding IS NOT NULL")
    print(f"  ops_email_analysis: {len(rows)}건 인코딩 중...")
    t0 = time.monotonic()
    texts = [_safe_text(f"{r['subject']}\n{r['body']}".strip()) for r in rows]
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False, batch_size=_BATCH) if texts else []
    print(f"    인코딩 {time.monotonic()-t0:.1f}s")

    async def _write_email_analysis():
        conn = await asyncpg.connect(_DB_URL)
        try:
            async with conn.transaction():
                await conn.execute("DROP INDEX IF EXISTS idx_email_analysis_embedding_hnsw")
                await conn.execute(
                    f"ALTER TABLE ops_email_analysis ALTER COLUMN embedding TYPE vector({_DIM}) USING NULL::vector({_DIM})"
                )
                for r, v in zip(rows, vecs):
                    await conn.execute("UPDATE ops_email_analysis SET embedding = $1::vector WHERE id = $2", str(list(v)), r["id"])
                await conn.execute(
                    "CREATE INDEX idx_email_analysis_embedding_hnsw ON ops_email_analysis "
                    "USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')"
                )
        finally:
            await conn.close()
    asyncio.run(_write_email_analysis())
    print(f"  ops_email_analysis: 완료 ({len(rows)}건 반영, subject+body 재구성 폴백)")

    # 7. ops_voc_cluster — representative_subject 재임베딩으로 centroid 리셋(인덱스 없음)
    _reembed_simple(
        model, table="ops_voc_cluster", id_col="id", text_col="representative_subject",
        embed_col="representative_embedding", index_name=None, index_def="",
    )

    # 8. rag_knowledge_history — 0건, 스키마만 맞춤
    async def _alter_history():
        conn = await asyncpg.connect(_DB_URL)
        try:
            await conn.execute(
                f"ALTER TABLE rag_knowledge_history ALTER COLUMN embedding TYPE vector({_DIM}) USING NULL::vector({_DIM})"
            )
        finally:
            await conn.close()
    asyncio.run(_alter_history())
    print("  rag_knowledge_history: 스키마만 변경(데이터 0건)")

    print("\n전체 재색인 완료.")


if __name__ == "__main__":
    main()
