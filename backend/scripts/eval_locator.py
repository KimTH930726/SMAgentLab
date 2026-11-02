"""등록 위치 판정 측정(E, collection-design §14·§8 관찰 숫자) — 사용 중 지식마다 "지금 위치"를 정답으로 보고 판정기를 돌린다.

자기 자신은 이웃에서 뺀다(leave-one-out). 원장에 기록하지 않는다(측정). 출력은 숫자만 — 원문·제목·이유를 찍지 않는다
(컨플루언스 원문은 시스템 LLM에만 간다 — 사내 LLM 경로, 이 세션엔 숫자만).
결과 줄(id·지금 위치·판정·이웃 — 원문 없음)은 eval_out/locator_rows_<llm>.jsonl(git 제외).

보는 것: AI만 일치 · 확신(자동 반영 후보) 수 · **확신인데 틀림(0이어야 자동 반영 가능)** · 종류 판정 분포.
실행: docker exec -w /app ops-backend python scripts/eval_locator.py [--llm system|local] [--concurrency 3]
"""
import argparse
import asyncio
import collections
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, "/app")
logging.disable(logging.INFO)

OUT_DIR = Path("/app/eval_out")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--llm", choices=["system", "local"], default="local",
                    help="system = 지금 시스템 LLM(게이트웨이 등), local = 로컬 OpenAI 호환(OPENAI_COMPAT_BASE_URL) — 판정 LLM 비교용")
    ap.add_argument("--unit", choices=["doc", "chunk"], default="doc", help="doc = 실제 등록 단위(문서), chunk = 조각 하나씩")
    ap.add_argument("--no-profile", action="store_true", help="컬렉션 대표 용어 없이(이름만) — 비교용")
    ap.add_argument("--examples", action="store_true", help="collection-design §4 가상 예시 20(기대값 대조, 가상이라 출력 가능)만")
    a = ap.parse_args()
    from core.database import get_conn, init_pool
    from shared.embedding import embedding_service
    from service.llm.factory import get_llm_provider
    from agents.knowledge_rag.ingestion import locator
    embedding_service.load()
    await init_pool()
    if a.llm == "local":
        from service.llm.openai_compat import OpenAICompatProvider
        llm = OpenAICompatProvider()
    else:
        llm = get_llm_provider()
    async with get_conn() as conn:
        cands = await locator.load_candidates(conn)
        profiles = None if a.no_profile else await locator.load_profiles(conn, [c.id for c in cands])
        ids = {c.id for c in cands}
        chunks = [r for r in await conn.fetch(
            "SELECT id, namespace_id, heading_path, content, embedding::text AS emb, ingestion_job_id, source_document_id "
            "FROM rag_knowledge WHERE status = 'active' AND embedding IS NOT NULL "
            "ORDER BY ingestion_job_id NULLS FIRST, source_chunk_idx NULLS LAST, id") if r["namespace_id"] in ids]
    # 단위 = 실제 등록과 같은 "문서"(등록 묶음·원문 하나, 직접 입력은 한 건). 그 문서의 조각은 전부 이웃에서 뺀다 — 같은 문서 형제가
    # 이웃이면 투표가 쉬워 보인다(10/23 조각 단위 측정의 확신 틀림 1건 = 121자 조각). --unit chunk는 조각 하나씩(옛 방식 비교용)
    groups: dict = {}
    for r in chunks:
        key = ("c", r["id"]) if a.unit == "chunk" else \
              ("j", r["ingestion_job_id"]) if r["ingestion_job_id"] else ("d", r["source_document_id"]) if r["source_document_id"] else ("c", r["id"])
        groups.setdefault(key, []).append(r)
    rows = []
    for key, g in groups.items():
        text = "\n\n".join((f"(제목 경로) {' > '.join(r['heading_path'])}\n" if r["heading_path"] else "") + (r["content"] or "") for r in g)
        rows.append({"id": g[0]["id"], "chunks": [r["id"] for r in g], "namespace_id": g[0]["namespace_id"], "text": text,
                     "emb": g[0]["emb"] if len(g) == 1 else None,
                     "mixed": len({r["namespace_id"] for r in g}) > 1})
    label = {c.id: c.label for c in cands}
    sem = asyncio.Semaphore(a.concurrency)
    tag = f"{a.llm}-{a.unit}{'' if profiles is not None else '-noprofile'}"
    if a.examples:
        await run_examples(cands, llm, profiles, label, tag)
        return

    async def one(r):
        text = r["text"]
        async with sem:
            llm_out = await locator.judge_llm(cands, text, llm, profiles=profiles)
        emb = json.loads(r["emb"]) if r["emb"] else await locator.embed_head(text)   # 여러 조각 문서 = 실제처럼 앞부분 임베딩
        async with get_conn() as conn:
            res = await locator.finish(conn, cands, llm_out, emb, exclude_ids=tuple(r["chunks"]), text=text)
        return {"id": r["id"], "n_chunks": len(r["chunks"]), "now": r["namespace_id"], "ai": res.ai_id, "doc_type": res.doc_type,
                "confident": res.confident, "vote": [res.vote_top, res.vote_n], "neighbors": [n for n, _ in res.neighbors],
                "failed": llm_out[3], "proposed": res.proposed_id, "mixed": r["mixed"]}

    out = await asyncio.gather(*(one(r) for r in rows))
    OUT = OUT_DIR / f"locator_rows_{tag}.jsonl"
    OUT.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in out), encoding="utf-8")
    n = len(out)
    ai_ok = sum(x["ai"] == x["now"] for x in out)
    conf = [x for x in out if x["confident"]]
    conf_wrong = sum(x["ai"] != x["now"] for x in conf)
    vote_ok = sum(x["vote"][0] == x["now"] for x in out)
    print(f"[판정 LLM: {tag}] 사용 중 지식 {len(chunks)}조각 = {n}{'문서' if a.unit == 'doc' else '조각'} · 후보 {len(cands)}곳 · 조각이 여러 컬렉션에 걸친 문서 {sum(x['mixed'] for x in out)} — LLM 실패 {sum(bool(x['failed']) for x in out)}")
    print(f"  AI만 지금 위치와 같음 {ai_ok}/{n} · 이웃 다수만 같음 {vote_ok}/{n}")
    print(f"  확신(자동 반영 후보) {len(conf)}/{n} · 확신인데 틀림 {conf_wrong} · 사람 확인 {n - len(conf)}"
          f"(그중 미리 고른 곳이 맞음 {sum(x['proposed'] == x['now'] for x in out if not x['confident'])})")
    print(f"  LLM 실패 종류 {dict(collections.Counter(x['failed'] for x in out if x['failed']))}")
    print(f"  종류 판정 {dict(collections.Counter(x['doc_type'] for x in out))}")
    for ns in sorted({x["now"] for x in out}):
        sub = [x for x in out if x["now"] == ns]
        other = collections.Counter(label.get(x["ai"], "애매/실패") for x in sub if x["ai"] != ns)
        print(f"  지금 {label.get(ns)} {len(sub)}건: AI 같음 {sum(x['ai'] == ns for x in sub)} · 확신 {sum(x['confident'] for x in sub)} · 다르게 {dict(other)}")
    print(f"  결과 줄(원문 없음): {OUT}")


# 가상 예시(실제 문서 아님) — 기대 = 시스템 이름 앞부분, "애매"(None), "*"(어디든). 11/02 개편: 운영 지식은 시스템 컬렉션에만 —
# 옛 "팀 공통·파트" 기대(조직 컬렉션 폐지)는 어느 시스템에도 안 맞는 팀 노하우라 애매(사람이 고름), 정책 해설은 다루는 시스템 아무 곳.
EXAMPLES = [
    ("딜리버스 배차 화면 주문 상태 코드 뜻", "딜리버스"),
    ("젠킨스 공통 파이프라인 스테이징→운영 배포 순서(어느 시스템이든 같은 순서)", None),
    ("딜리버스 배포 때 캐시 서버 재기동 순서", "딜리버스"),
    ("장애 보고서 양식·보고 라인", None),
    ("온라인서비스 파트 당직 교대 규칙", None),
    ("배달의민족 정산 대사 파일 칸 설명", "외부서비스"),
    ("제휴사 공통 주문 연동 실패 재처리(배민·쿠팡·땡겨요 모두 같은 방식)", "외부서비스"),
    ("땡겨요 주문 취소 연동 규격", "외부서비스"),
    ("쿠폰 발급 오류 — 회원 등급 확인 뒤 쿠폰 재발급", "*"),
    ("DB 접속 권한 신청 절차(사내 공통)", None),
    ("딜리버스 DB 느린 쿼리 확인 SQL", "딜리버스"),
    ("신규 입사자 개발 환경 세팅", None),
    ("온라인스토어 파트 코드 리뷰 규칙(이 파트만 쓰는 규칙)", "*"),
    ("OpsLens 팀즈 알림 웹훅 설정", None),
    ("VOC 메일 응대 문구 틀(서비스 무관 공통 인사·사과 문구)", None),
    ("스타벅스카드 잔액 환불 정책 해설", "*"),
    ("월말 정산 마감 점검표 — 딜리버스와 온라인스토어 절차를 같이 다룸", "*"),
    ("클라우드 비용 알림 대응", None),
    ("10/7 딜리버스 배차 지연 장애 회고", "딜리버스"),
    ("홈페이지 휴면 회원 배치 실패 대응", "홈페이지"),
]


async def run_examples(cands, llm, profiles, label, tag) -> None:
    from agents.knowledge_rag.ingestion import locator
    ok = 0
    for text, want in EXAMPLES:
        ai, _, _, failed = await locator.judge_llm(cands, text, llm, profiles=profiles)
        got = label.get(ai)
        hit = want == "*" or (want is None and ai is None) or (want is not None and got is not None and got.startswith(want))
        ok += hit
        print(f"{'O' if hit else 'X'} | {text[:36]} | 기대 {want} | 판정 {got or ('실패' if failed else '애매')}")
    print(f"[{tag}] 가상 예시 일치 {ok}/{len(EXAMPLES)}")


if __name__ == "__main__":
    asyncio.run(main())
