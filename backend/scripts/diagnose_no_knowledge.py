""""관련 지식을 찾지 못했습니다" 응답의 원인을 단계별로 분류한다(2026-09-28).

배경: 실사용 로그 138건 중 38건(28%)이 no_knowledge, 가드 평가(eval_prompt_guard.py)에선 골든셋
89문항 중 35건이 "지식 없음"이었다. 그런데 정책 검색 단독 적중률(track2 hit@K)은 88.8% — 검색은
찾는데 답변 단계에서 버려지는지, 애초에 검색이 못 찾는지, 콘텐츠가 없는지 구분이 안 됐다.
(주의: eval_prompt_guard.py 첫 버전은 89문항을 전부 "딜리버스 DB"에서 검색해 온라인스토어
46문항이 구조적으로 실패했다 — 이 스크립트는 track2 로더로 문항별 네임스페이스·정답 id를 쓴다.)

골든셋 분류(실제 챗 경로: 용어매핑 → 지식/정책/참조데이터 검색 → _build_rrf_context → LLM):
  A 컨텍스트 비어있음        — 채택 게이트/검색이 아무것도 못 넘김
  B 컨텍스트 있음·정답 빠짐   — 검색이 엉뚱한 걸 가져옴(정답이 top_k 밖이면 B-rank로 따로 표시)
  C 정답이 컨텍스트에 있는데 "지식 없음" — 답변(LLM/프롬프트) 단계 실패
  D 정답 포함 + 답변함        — 정상
  E 정답 빠졌는데 답변함      — 다른 문서로 답함(정답 여부는 이 스크립트로 판단 불가)
실사용 no_knowledge 로그: 지금 데이터로 검색만 재실행해 컨텍스트 유무를 센다(LLM 미호출).

출력은 분류 집계와 qid만 — 질문/문서/답변 원문은 출력하지 않는다.

실행 (컨테이너 안):
    docker compose exec backend python scripts/diagnose_no_knowledge.py [--skip-llm] [--limit N]
"""
import argparse
import asyncio
import collections
import sys

sys.path.insert(0, "/app")

from core.database import init_pool, close_pool, get_conn
from shared.embedding import embedding_service
from service.llm.base import resolve_system_prompt
from service.llm.factory import get_llm_provider
from service.chat.helpers import NO_KNOWLEDGE_MARKER
from agents.knowledge_rag.knowledge import retrieval
from agents.knowledge_rag.agent import POLICY_CONTEXT_TOP_K, _build_rrf_context
from service.policy import search as policy_search
from service.policy import track2
from service.refdata import service as refdata_search


async def build(namespace: str, question: str, policy_top_k: int = POLICY_CONTEXT_TOP_K):
    """agent.py stream_chat의 컨텍스트 조립과 같은 순서(리랭커·멀티턴 보강·카테고리 라우팅 제외)."""
    query_vec = await embedding_service.embed(question)
    glossary = await retrieval.map_glossary_term(namespace, query_vec)
    enriched = f"{question} {glossary.term}" if glossary else question
    d = retrieval.get_search_defaults()
    results = await retrieval.search_knowledge(
        namespace, query_vec, enriched, d["default_w_vector"], d["default_w_keyword"], int(d["default_top_k"]),
    )
    policy_result = policy_search.PolicySearchResult()
    if await policy_search.has_policy_data(namespace):
        policy_result = await policy_search.search_policy(namespace, enriched, top_k=policy_top_k, query_vec=query_vec)
    codes, cols = [], []
    if await refdata_search.has_refdata(namespace):
        codes, cols = await asyncio.gather(
            refdata_search.search_common_codes(namespace, enriched, top_k=5),
            refdata_search.search_db_columns(namespace, enriched, top_k=5),
        )
    ctx = _build_rrf_context(results, policy_result, codes, cols)
    th = retrieval.get_thresholds()
    adopted = sum(1 for r in results if retrieval.is_adopted(r, th))
    return ctx, policy_result, enriched, query_vec, len(results), adopted


def policy_ids(pr) -> set[int]:
    return {h.item_id for h in pr.params} | {h.item_id for h in pr.narratives}


async def diagnose_golden(llm, system_prompt, skip_llm: bool, limit: int, policy_top_k: int = POLICY_CONTEXT_TOP_K) -> None:
    async with get_conn() as conn:
        rows = await conn.fetch("SELECT id, name FROM ops_namespace")
    ns_ids = {r["name"]: r["id"] for r in rows}
    entries = await track2._load_golden_set(track2._GOLDEN_SET_PATH, ns_ids)
    if limit:
        entries = entries[:limit]

    by_class: dict[str, list[str]] = collections.defaultdict(list)
    by_type: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for i, e in enumerate(entries, 1):
        ns, q, gold = e["namespace_name"], e["query"], e["gold_ids"]
        ctx, pr, enriched, qvec, n_raw, n_adopted = await build(ns, q, policy_top_k=policy_top_k)
        gold_in_ctx = bool(policy_ids(pr) & gold)
        if not ctx.strip():
            cls = "A"
        elif not gold_in_ctx:
            # 정답이 5위 밖에 있었는지(top_k 컷) 아예 못 찾았는지 구분
            wide = await policy_search.search_policy(ns, enriched, top_k=20, query_vec=qvec)
            cls = "B-rank" if policy_ids(wide) & gold else "B"
        else:
            cls = "D?"
        if not skip_llm and cls != "A":
            text, _ = await llm.generate(ctx, q, None, system_prompt=system_prompt)
            nok = NO_KNOWLEDGE_MARKER in (text or "")
            if cls == "D?":
                cls = "C" if nok else "D"
            elif not nok:
                cls = cls + "→E"  # 정답은 빠졌지만 다른 문서로 답함
        tag = f"{e.get('qid', i)}"
        by_class[cls].append(tag)
        by_type[e["type"]][cls] += 1
        if i % 10 == 0:
            print(f"  ... {i}/{len(entries)}", flush=True)

    print(f"\n=== 골든셋 {len(entries)}문항 (문항별 네임스페이스, 정책 top_k={policy_top_k}) ===")
    order = ["A", "B", "B→E", "B-rank", "B-rank→E", "C", "D", "D?"]
    for cls in order + sorted(set(by_class) - set(order)):
        if by_class.get(cls):
            print(f"  {cls:10s} {len(by_class[cls]):3d}")
    print("\n  유형별:")
    for t, c in sorted(by_type.items()):
        print(f"    {t:16s} " + "  ".join(f"{k}={v}" for k, v in sorted(c.items())))
    for cls in ("A", "B", "B-rank", "C"):
        if by_class.get(cls):
            print(f"\n  {cls} 문항 인덱스: {', '.join(by_class[cls])}")


async def diagnose_logs() -> None:
    async with get_conn() as conn:
        rows = await conn.fetch("""
            SELECT n.name AS ns, q.question FROM ops_query_log q JOIN ops_namespace n ON n.id = q.namespace_id
            WHERE q.status = 'no_knowledge' ORDER BY q.created_at
        """)
    stats: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        ctx, pr, *_rest, n_raw, n_adopted = await build(r["ns"], r["question"])
        has_policy = bool(pr.params or pr.narratives)
        if not ctx.strip():
            key = "컨텍스트 없음"
        elif n_adopted == 0 and has_policy:
            key = "정책만 있음"
        else:
            key = "지식 채택 있음"
        stats[r["ns"]][key] += 1
    print(f"\n=== 실사용 no_knowledge 로그 {len(rows)}건 — 지금 데이터로 검색 재실행 ===")
    for ns, c in stats.items():
        print(f"  {ns:14s} " + "  ".join(f"{k}={v}" for k, v in c.items()))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-llm", action="store_true", help="검색 단계 분류만(A/B/D?) — LLM 미호출")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--policy-top-k", type=int, default=POLICY_CONTEXT_TOP_K, help="정책 검색 top_k(기본=운영 agent.POLICY_CONTEXT_TOP_K) — 순위 컷 실험용")
    ap.add_argument("--skip-logs", action="store_true")
    args = ap.parse_args()

    embedding_service.load()
    await init_pool()
    try:
        llm = get_llm_provider()
        system_prompt = await resolve_system_prompt()
        await diagnose_golden(llm, system_prompt, args.skip_llm, args.limit, args.policy_top_k)
        if not args.skip_logs:
            await diagnose_logs()
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
