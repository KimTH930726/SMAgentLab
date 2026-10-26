"""다른 컬렉션에서 찾아보기(C2) — 실사용 질문으로 켜기 전 측정 (2026-10-26, collection-design §18 켜는 조건).

질의 기록(ops_query_log)의 실제 질문을, 물었던 업무 컬렉션에서 채팅과 같은 규칙(collection_suggest.find, 강제 실행)으로 돌려
"제안이 몇 번 뜨나"·"근거 없던 답(찾지 못했습니다)에서 몇 번 뜨나"를 센다. 정답이 없어 맞음률은 못 잰다 — 뜬 경우는
사람이 몇 건 보고(또는 로컬 LLM으로) 판정한다. 출력은 숫자만(질문 글 없음). 실행(컨테이너 안): python scripts/eval_collection_suggest_real.py
"""
import asyncio
import collections
import sys

sys.path.insert(0, "/app")
import logging  # noqa: E402
logging.disable(logging.INFO)


async def main(dump: str = "") -> None:
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
                """SELECT DISTINCT ON (q.question) q.question, n.name AS ns, (q.answer LIKE '%찾지 못했습니다%') AS no_answer
                   FROM ops_query_log q JOIN ops_namespace n ON n.id = q.namespace_id
                   WHERE n.kind = 'domain' AND n.name NOT LIKE '%test%' AND length(trim(q.question)) > 3
                   ORDER BY q.question, q.id DESC""")
        d = retrieval.get_search_defaults()
        top_k = int(d["default_top_k"])
        hits = collections.Counter()
        cases: list[dict] = []

        async def _ids(ns):
            async with get_conn() as conn:
                return await policy_scope.policy_namespace_ids(conn, ns)

        def _brief(x) -> str:
            return f"{' > '.join(x.heading_path or [])} — {(x.content or '')[:200]}"
        n_by = collections.Counter()
        for r in rows:
            vec = await embedding_service.embed(r["question"])
            async with get_conn() as conn:
                ids = await policy_scope.policy_namespace_ids(conn, r["ns"])
            _, _, enriched = await glossary_terms.resolve_query_terms(r["ns"], r["question"], vec, extra_ns_ids=ids)
            here = await retrieval.search_knowledge(r["ns"], vec, enriched, d["default_w_vector"], d["default_w_keyword"], top_k,
                                                    keyword_rescue=retrieval.KEYWORD_RESCUE)
            s = await collection_suggest.find(r["ns"], r["question"], vec, here, w_vector=d["default_w_vector"],
                                              w_keyword=d["default_w_keyword"], top_k=top_k, force=True)
            key = "답 못 함" if r["no_answer"] else "답함"
            n_by[key] += 1
            if s:
                hits[(key, r["ns"], s["namespace"])] += 1
                if dump:   # 사람·로컬 LLM 판정용 사례(질문 + 두 곳 상위 지식 제목·앞부분) — eval_out(커밋 안 됨)에만
                    _, _, enr2 = await glossary_terms.resolve_query_terms(s["namespace"], r["question"], vec,
                                                                          extra_ns_ids=await _ids(s["namespace"]))
                    there = await retrieval.search_knowledge(s["namespace"], vec, enr2, d["default_w_vector"], d["default_w_keyword"], 3,
                                                             keyword_rescue=retrieval.KEYWORD_RESCUE)
                    cases.append({"no": len(cases) + 1, "question": r["question"], "asked": r["ns"], "suggested": s["namespace"], "score": s["score"], "here_score": s["here"], "here_adopted": s["here_adopted"],
                                  "here": [_brief(x) for x in here[:3]], "there": [_brief(x) for x in there[:3]]})
        print(f"실사용 질문(같은 글 하나로) {len(rows)}개 — 답함 {n_by['답함']} · 답 못 함 {n_by['답 못 함']}")
        tot = collections.Counter()
        for (key, a, b), n in sorted(hits.items()):
            tot[key] += n
            print(f"  [{key}] {a} → {b} 제안 {n}")
        for key in ("답함", "답 못 함"):
            print(f"  {key}: 제안 {tot[key]}/{n_by[key]}")
        if dump:
            import json
            from pathlib import Path
            Path(dump).write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases), encoding="utf-8")
            print(f"판정용 사례 {len(cases)}건 → {dump}")
    finally:
        await close_pool()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="", help="제안이 뜬 사례를 이 파일(eval_out 아래)에 — 판정용, 원문이 들어가니 커밋하지 않는 곳에")
    asyncio.run(main(ap.parse_args().dump))
