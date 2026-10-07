"""질문 기록 → 용어 동의어 수집을 지금 한 번 실행 (2026-10-06, v2.128). 평소엔 앱이 하루 1회 자동(glossary_mining).

  --dry-run  DB에 쓰지 않고, 처리했다면 붙었을 (표현 → 용어) 목록과 건수만 출력(처리 위치도 안 옮김) — 품질 점검용

실행(컨테이너 안): python scripts/glossary_mine_queries.py [--dry-run] [--limit 200]
"""
import argparse
import asyncio
import collections
import sys

sys.path.insert(0, "/app")

from core.database import init_pool, get_conn  # noqa: E402
from agents.knowledge_rag.knowledge import glossary_mining, glossary_terms  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    await init_pool()
    from shared.embedding import embedding_service
    embedding_service.load()   # 품질 게이트용(앱에선 기동 시 로드됨)
    from service.llm.factory import get_llm_provider
    llm = get_llm_provider()
    if not args.dry_run:
        print(await glossary_mining.run_once(llm))
        return
    async with get_conn() as conn:
        nss = await conn.fetch("SELECT DISTINCT n.id, n.name FROM ops_namespace n JOIN rag_glossary g ON g.namespace_id = n.id")
    for ns in nss:
        async with get_conn() as conn:
            qs = [r["question"] for r in await conn.fetch(
                "SELECT question FROM ops_query_log WHERE namespace_id = $1 AND status <> 'system_error' AND question IS NOT NULL "
                "ORDER BY id DESC LIMIT $2", ns["id"], args.limit)]
            entries = await glossary_terms.load_entries(conn, ns["id"])
        found = await glossary_terms.mine_query_expressions(qs, entries, llm)
        per = collections.Counter((n, t) for n, t, _ in {(glossary_terms.norm(e), t, qi) for e, t, qi in found})
        surface = {(glossary_terms.norm(e), t): e for e, t, _ in found}
        print(f"[{ns['name']}] 질문 {len(qs)}개 → 표현 {len(per)}종 (서로 다른 질문 2건 이상: {sum(c >= glossary_terms.QUERY_MIN_EVIDENCE for c in per.values())})")
        for (n, t), c in per.most_common(40):
            print(f"   {surface[(n, t)]} → {t}  (질문 {c}건)")


if __name__ == "__main__":
    asyncio.run(main())
