"""다른 컬렉션에서 찾아보기(C2) 부하 측정 (2026-10-27, 사용자 "인원 300명 최대치로 부하 테스트").

가정: 300명 · 1인당 5분에 1번 질문 → 초당 1건, 답 하나 ~20초라 동시에 끝나는 답 20~30건. 제안 계산(collection_suggest.find, 강제 실행)을
동시 N건 돌리면서, 같은 시각에 다른 사용자의 평범한 지식 검색이 얼마나 늦어지는지(커넥션 풀이 차는지) 함께 잰다. LLM 0, DB 읽기만.
출력은 숫자만. 실행(컨테이너 안): python scripts/load_collection_suggest.py [--levels 10,30,60]
"""
import argparse
import asyncio
import statistics
import sys
import time

sys.path.insert(0, "/app")
import logging  # noqa: E402
logging.disable(logging.INFO)


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else 0.0


async def main(levels: list[int]) -> None:
    from core.config import settings
    from core.database import close_pool, get_conn, init_pool
    from shared.embedding import embedding_service
    from agents.knowledge_rag.knowledge import glossary_terms, retrieval
    from service.chat import collection_suggest
    from service.policy import scope as policy_scope

    await init_pool()
    embedding_service.load()
    try:
        async with get_conn() as conn:
            await retrieval.load_runtime_overrides_from_db(conn)
            rows = await conn.fetch(
                """SELECT DISTINCT ON (q.question) q.question, n.name AS ns FROM ops_query_log q JOIN ops_namespace n ON n.id = q.namespace_id
                   WHERE n.kind = 'domain' AND n.name NOT LIKE '%test%' AND length(trim(q.question)) > 3 ORDER BY q.question, q.id DESC LIMIT 120""")
        d = retrieval.get_search_defaults()
        top_k = int(d["default_top_k"])
        qs = [(r["question"], r["ns"], await embedding_service.embed(r["question"])) for r in rows]
        print(f"질문 {len(qs)}개 · DB 풀 최대 {settings.db_pool_max_size} · 제안 동시 검색 {collection_suggest.CONCURRENCY}")

        async def plain(q, ns, vec):
            async with get_conn() as conn:
                ids = await policy_scope.policy_namespace_ids(conn, ns)
            _, _, enr = await glossary_terms.resolve_query_terms(ns, q, vec, extra_ns_ids=ids)
            return await retrieval.search_knowledge(ns, vec, enr, d["default_w_vector"], d["default_w_keyword"], top_k,
                                                    keyword_rescue=retrieval.KEYWORD_RESCUE)

        async def timed(coro):
            t0 = time.perf_counter()
            try:
                await coro
                return time.perf_counter() - t0, None
            except Exception as e:   # noqa: BLE001 — 풀 대기 초과 등도 결과로 센다
                return time.perf_counter() - t0, type(e).__name__

        # 기준: 혼자 도는 평범한 검색
        base = [t for t, _ in [await timed(plain(*qs[i % len(qs)])) for i in range(20)]]
        print(f"평범한 검색 혼자: 중앙 {statistics.median(base) * 1000:.0f}ms · p95 {pct(base, .95) * 1000:.0f}ms")
        for n in levels:
            sug = [timed(collection_suggest.find(ns, q, vec, None, w_vector=d["default_w_vector"], w_keyword=d["default_w_keyword"],
                                                 top_k=top_k, force=True)) for q, ns, vec in (qs[i % len(qs)] for i in range(n))]
            others = [timed(plain(*qs[(i * 7) % len(qs)])) for i in range(10)]   # 같은 시각 다른 사용자 검색 10건
            t0 = time.perf_counter()
            res = await asyncio.gather(*sug, *others)
            wall = time.perf_counter() - t0
            s, o = res[:n], res[n:]
            st = [t for t, _ in s] or [0.0]
            ot = [t for t, _ in o]
            errs = [e for _, e in res if e]
            print(f"제안 동시 {n:3d}: 제안 중앙 {statistics.median(st):.2f}s · p95 {pct(st, .95):.2f}s · 최대 {max(st):.2f}s | "
                  f"다른 검색 중앙 {statistics.median(ot) * 1000:.0f}ms · p95 {pct(ot, .95) * 1000:.0f}ms | 오류 {len(errs)}{(' ' + str(sorted(set(errs)))) if errs else ''} | 전체 {wall:.1f}s")
    finally:
        await close_pool()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="0,10,30,60")
    asyncio.run(main([int(x) for x in ap.parse_args().levels.split(",")]))
