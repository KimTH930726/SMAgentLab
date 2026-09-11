"""리랭커 후보 비교 벤치마크 — 89문항 골든셋 + 실제 정책 데이터로, 현재 프로덕션
하이브리드 검색(search.py:search_policy(), RDB param + 벡터 narrative)이 반환하는
상위 후보를 CrossEncoder 후보들로 재정렬했을 때 hit@1이 얼마나 개선되는지 비교한다.

docs/tech/embedding-reranker-upgrade-plan.md §3의 조사된 후보:
- cross-encoder/ms-marco-MiniLM-L-6-v2 (현재 기본값, 영어 전용 — 한국어엔 원래 안 맞는 선택)
- dragonkue/bge-reranker-v2-m3-ko (임베딩 KURE-v1과 같은 BGE-M3 계열 한국어 파인튜닝)
- BAAI/bge-reranker-v2-m3 (다국어 원본)
- Dongjin-kr/ko-reranker (구형 bge-reranker-large 기반 한국어 파인튜닝)

방법: 각 질문에 대해 search_policy()로 실제 top-K(reranker_candidates=20) 후보를 얻고
(param+narrative 합쳐서, item_id 기준 중복 제거), 정답 item_id가 그 후보 안에 있는
문항만 채점 대상으로 삼는다(리랭커는 "후보 안에서 순서 바꾸기"만 하지 "없던 걸 찾아내기"는
못 하므로 — 후보에 아예 없으면 무엇으로 재정렬해도 정답이 안 나옴, hit@1 정의상 당연).
- baseline: 재정렬 없이 원래 순서(파라미터 ts_rank/벡터 코사인 혼합 순서) top-1이 정답인 비율
- 후보별: CrossEncoder(query, 후보텍스트)로 재정렬 후 top-1이 정답인 비율

실행: docker exec -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 -it ops-backend \
      python -m scripts.bench_reranker_models
(HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE 오버라이드 필수 — 임베딩 벤치마크와 동일한 이유.
`-m scripts.xxx` 형태로 실행해야 함 — shared.embedding을 임포트하므로 /app이
sys.path에 잡혀야 하는데, `python scripts/xxx.py`로 바로 실행하면 스크립트가 있는
scripts/ 디렉터리가 sys.path[0]이 돼서 실패함(bench_suffix_stripping.py와 동일 이슈).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import asyncpg

_GOLDEN_SET_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "golden_set" / "online_delivus_v1.jsonl"
_FILE_TO_NAMESPACE = [("온라인스토어", "온라인스토어 DB"), ("딜리버스", "딜리버스 DB")]
_TOP_K = 20  # core/config.py reranker_candidates 기본값과 동일

_CANDIDATES = [
    ("현재(baseline, 영어전용)", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
    ("dragonkue/bge-reranker-v2-m3-ko", "dragonkue/bge-reranker-v2-m3-ko"),
    ("BAAI/bge-reranker-v2-m3", "BAAI/bge-reranker-v2-m3"),
    ("Dongjin-kr/ko-reranker", "Dongjin-kr/ko-reranker"),
]


def _namespace_for_file(file_: str):
    for needle, ns in _FILE_TO_NAMESPACE:
        if needle in file_:
            return ns
    return None


async def _resolve_gold_ids(conn, entry, ns_id):
    qtype, src = entry["type"], entry["source"]
    if qtype in ("param", "narrative"):
        r = await conn.fetchrow(
            "SELECT id FROM policy_item WHERE source_file=$1 AND source_sheet=$2 AND source_row=$3 AND status != 'deprecated'",
            src["file"], src["sheet"], src["row"],
        )
        return {r["id"]} if r else set()
    elif qtype == "navigation":
        rows = await conn.fetch(
            "SELECT id FROM policy_item WHERE namespace_id=$1 AND status != 'deprecated' AND $2 = ANY(category_path)",
            ns_id, src["category"],
        )
        return {r["id"] for r in rows}
    else:
        rows = await conn.fetch(
            """SELECT DISTINCT p.policy_item_id AS id FROM policy_param p
               JOIN policy_item i ON i.id = p.policy_item_id
               WHERE i.namespace_id=$1 AND i.status != 'deprecated' AND p.condition = $2""",
            ns_id, src["condition"],
        )
        return {r["id"] for r in rows}


async def _fetch_candidates(conn, ns_id: int, query: str, query_vec: list[float], top_k: int):
    """search.py:search_policy()의 param+narrative SQL을 그대로 복제 — item_id 기준으로
    (텍스트, 원래 순위) 후보 목록을 만든다. 같은 item이 param/narrative 양쪽에 나오면
    narrative(원문 전체가 있어 리랭커 입력으로 더 적합)를 우선한다."""
    param_rows = await conn.fetch(
        """
        SELECT i.id AS item_id, i.policy_name, p.name AS param_name, p.condition, i.raw_body,
               ts_rank(to_tsvector('simple', policy_strip_ko(p.name || ' ' || COALESCE(p.condition, '') || ' ' || i.policy_name)), q.tsq) AS rank
        FROM policy_param p
        JOIN policy_item i ON i.id = p.policy_item_id
        CROSS JOIN LATERAL (
            SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS tsq
            FROM (SELECT DISTINCT lexeme FROM unnest(to_tsvector('simple', policy_strip_ko($2)))) t
            WHERE lexeme IS NOT NULL
        ) q
        WHERE i.namespace_id = $1 AND i.status != 'deprecated'
          AND to_tsvector('simple', policy_strip_ko(p.name || ' ' || COALESCE(p.condition, '') || ' ' || i.policy_name)) @@ q.tsq
        ORDER BY rank DESC LIMIT $3
        """,
        ns_id, query, top_k,
    )
    chunk_rows = await conn.fetch(
        """
        SELECT i.id AS item_id, i.policy_name, c.chunk_text, i.raw_body,
               1 - (c.embedding <=> $2::vector) AS score
        FROM policy_chunk c
        JOIN policy_item i ON i.id = c.policy_item_id
        WHERE i.namespace_id = $1 AND i.status != 'deprecated'
        ORDER BY c.embedding <=> $2::vector LIMIT $3
        """,
        ns_id, str(query_vec), top_k,
    )
    candidates: dict[int, str] = {}
    order: list[int] = []
    for r in chunk_rows:  # 벡터 결과 먼저(순위가 원래 하이브리드 union에서도 섞이므로 순서 자체는 근사)
        if r["item_id"] not in candidates:
            candidates[r["item_id"]] = r["chunk_text"]
            order.append(r["item_id"])
    for r in param_rows:
        if r["item_id"] not in candidates:
            text = f"{r['policy_name']} {r['param_name']} {r['condition'] or ''}".strip()
            candidates[r["item_id"]] = text
            order.append(r["item_id"])
    return order[:top_k], candidates


async def main():
    from shared.embedding import embedding_service
    embedding_service.load()

    db_url = os.environ.get("DATABASE_URL", "postgresql://ops:ops1234@localhost:5432/opsdb")
    conn = await asyncpg.connect(db_url)
    try:
        ns_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM ops_namespace")}
        with open(_GOLDEN_SET_PATH, encoding="utf-8") as f:
            golden = [json.loads(line) for line in f if line.strip()]

        scored = []  # (query, gold_ids, order[item_ids], candidates{id:text})
        for entry in golden:
            ns_name = _namespace_for_file(entry["source"].get("file", ""))
            if ns_name is None or ns_name not in ns_ids:
                continue
            ns_id = ns_ids[ns_name]
            gold_ids = await _resolve_gold_ids(conn, entry, ns_id)
            if not gold_ids:
                continue
            query = entry["query"]
            query_vec = await embedding_service.embed(query)
            order, candidates = await _fetch_candidates(conn, ns_id, query, query_vec, _TOP_K)
            if gold_ids & set(order):  # 후보 안에 정답이 있는 문항만 채점 대상
                scored.append((query, gold_ids, order, candidates))
    finally:
        await conn.close()

    n = len(scored)
    print(f"채점 대상(후보 top-{_TOP_K} 안에 정답 포함) = {n} / {len(golden)}문항\n")

    baseline_hit1 = sum(1 for _, gold, order, _ in scored if order[0] in gold) / n
    print(f"baseline(재정렬 없음) hit@1 = {baseline_hit1:.1%}\n")

    from sentence_transformers import CrossEncoder

    results = {"baseline(재정렬 없음)": baseline_hit1}
    for label, model_name in _CANDIDATES:
        print(f"=== {label} ({model_name}) 로딩 중... ===")
        try:
            model = CrossEncoder(model_name)
        except Exception as e:
            print(f"  로드 실패: {e}\n")
            continue
        hits = 0
        for query, gold, order, candidates in scored:
            pairs = [(query, candidates[i][:512]) for i in order]
            raw_scores = model.predict(pairs)
            reranked = [i for i, _ in sorted(zip(order, raw_scores), key=lambda x: x[1], reverse=True)]
            if reranked[0] in gold:
                hits += 1
        rate = hits / n
        results[label] = rate
        print(f"  hit@1 = {rate:.1%} (baseline 대비 {(rate - baseline_hit1) * 100:+.1f}%p)\n")
        del model

    print("=== 최종 비교 ===")
    for label, rate in results.items():
        print(f"{label:<32}{rate:.1%}")


if __name__ == "__main__":
    asyncio.run(main())
