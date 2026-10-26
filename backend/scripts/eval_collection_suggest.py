"""채팅 "다른 컬렉션에서 찾아보기" 제안 — 켜기 전 측정 (2026-10-23, 사용자 "딜리버스에서 물었는데 제휴서비스 지식이면 AI가 판단해 제안").

기본 검색 범위는 그대로 두고(합치면 섞임 0→6.6%, collection-design §1), 다른 업무 컬렉션에서 더 맞는 지식이 보이면 버튼으로만 제안한다.
이 스크립트는 제안 규칙의 정확도를 LLM 없이 잰다 — 지식 문항(합성 106, 정답 조각의 컬렉션 = 정답 컬렉션)을
  (가) 정답 컬렉션에서 물을 때: 제안이 뜨면 거짓 제안
  (나) 다른 컬렉션에서 물을 때: 정답 컬렉션을 제안하면 맞음, 다른 곳을 제안하면 틀림, 안 뜨면 놓침
  (다) 답 없는 질문(골든 v0 abstain 60)을 각 업무에서 물을 때: 제안이 뜨면 잡음
규칙 = 다른 컬렉션 최고 관련도 ≥ 하한 이고 (물은 곳 채택 0 또는 다른 곳 최고 − 물은 곳 최고 ≥ 차이). 하한·차이 격자로.
출력은 숫자만(문항·원문 없음). 실행(컨테이너 안): python scripts/eval_collection_suggest.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")
import logging  # noqa: E402
logging.disable(logging.INFO)

KSET = Path("/app/eval_out/confluence_synth.jsonl")
GOLDEN = Path("/app/eval_out/golden_v0/reviewed")
FLOORS = (0.35, 0.40, 0.45, 0.48, 0.50, 0.55)
MARGINS = (0.0, 0.05, 0.10)


async def main() -> None:
    from core.database import close_pool, get_conn, init_pool
    from shared.embedding import embedding_service
    from agents.knowledge_rag.knowledge import glossary_terms, retrieval
    from service.policy import scope as policy_scope

    await init_pool()
    embedding_service.load()
    try:
        async with get_conn() as conn:
            await retrieval.load_runtime_overrides_from_db(conn)
            # 검색되는 지식이 있는 업무 컬렉션(조직·테스트 제외) — 제안 후보. 상태 조건은 검색과 같게(지운·은퇴한 지식만 있는 곳은 빈 곳, 리뷰 M2)
            colls = [r["name"] for r in await conn.fetch(
                """SELECT n.name FROM ops_namespace n WHERE n.kind = 'domain' AND n.name NOT LIKE '%test%'
                   AND EXISTS (SELECT 1 FROM rag_knowledge k WHERE k.namespace_id = n.id AND (k.status IS NULL OR k.status = 'active'))
                   ORDER BY n.name""")]
            gold_ns = {r["id"]: r["name"] for r in await conn.fetch(
                """SELECT k.id, n.name FROM rag_knowledge k JOIN ops_namespace n ON n.id = k.namespace_id
                   WHERE k.status IS NULL OR k.status = 'active'""")}   # 은퇴한 정답 조각은 정답 컬렉션을 못 정함 → 제외(리뷰 L1)
            # 채팅은 용어 매칭을 정책 범위 컬렉션 용어집까지 넓힌다(agent.build_chat_context extra_ns_ids) — 같게(리뷰 M1)
            term_scope = {c: await policy_scope.policy_namespace_ids(conn, c) for c in colls}
        d, th = retrieval.get_search_defaults(), retrieval.get_thresholds()
        top_k = int(d["default_top_k"])

        async def best(ns: str, q: str, vec) -> tuple[float, int]:
            """그 컬렉션에서 채팅과 같은 검색 — (최고 관련도, 채택 수)."""
            _, _, enriched = await glossary_terms.resolve_query_terms(ns, q, vec, extra_ns_ids=term_scope[ns])
            res = await retrieval.search_knowledge(ns, vec, enriched, d["default_w_vector"], d["default_w_keyword"], top_k,
                                                   keyword_rescue=retrieval.KEYWORD_RESCUE)
            scores = [retrieval.relevance_score(r) for r in res]
            return (max(scores) if scores else 0.0), sum(retrieval.is_adopted(r, th) for r in res)

        items = [json.loads(l) for l in KSET.read_text(encoding="utf-8").splitlines() if l.strip()]
        neg = []
        for f in sorted(GOLDEN.glob("*.jsonl")):
            neg += [r for r in (json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip())
                    if r.get("verdict") == "approved" and r.get("expect") == "abstain"]
        probes = []   # (종류, 물은 곳, 정답 컬렉션 또는 None, 컬렉션별 (최고, 채택))
        skipped = 0
        for it in items:
            gns = {gold_ns.get(g) for g in it["gold_ids"]} - {None}
            if len(gns) != 1 or next(iter(gns)) not in colls:
                skipped += 1   # 정답 조각이 지워졌거나 여러 컬렉션 — 셈에서 빼고 개수만 보인다
                continue
            gold = next(iter(gns))
            vec = await embedding_service.embed(it["question"])
            per = dict(zip(colls, await asyncio.gather(*(best(c, it["question"], vec) for c in colls))))
            for asked in colls:
                probes.append(("right" if asked == gold else "wrong", asked, gold, per))
        for r in neg:
            vec = await embedding_service.embed(r["query"])
            per = dict(zip(colls, await asyncio.gather(*(best(c, r["query"], vec) for c in colls))))
            for asked in colls:
                probes.append(("noise", asked, None, per))

        def suggest(asked: str, per: dict, floor: float, margin: float):
            own_s, own_n = per[asked]
            others = sorted(((s, c) for c, (s, n) in per.items() if c != asked and n > 0), reverse=True)
            if not others:
                return None
            s, c = others[0]
            if s >= floor and (own_n == 0 or s - own_s >= margin):
                return c
            return None

        n_right = sum(p[0] == "right" for p in probes)
        n_wrong = sum(p[0] == "wrong" for p in probes)
        n_noise = sum(p[0] == "noise" for p in probes)
        print(f"컬렉션 {len(colls)}개 {colls} · 지식 문항 {len(items) - skipped}(제외 {skipped}) · 답 없는 질문 {len(neg)}"
              f" · 용어 범위 컬렉션 수 {[len(term_scope[c]) for c in colls]}")
        print(f"  (가) 정답 컬렉션에서 물음 {n_right} · (나) 다른 컬렉션에서 물음 {n_wrong} · (다) 답 없는 질문 {n_noise}")
        # 상한 — 정답 컬렉션에서 물어도 채택이 0이면 다른 곳에서 물을 때 제안할 근거도 없다
        reach = sum(1 for k, a, g, per in probes if k == "wrong" and per[g][1] > 0)
        print(f"  (나)의 상한: 정답 컬렉션 검색이 채택 1건 이상인 경우 {reach}/{n_wrong}")
        print("  하한  차이 | (가) 거짓 제안 | (나) 맞게 제안 / 다른 곳 / 놓침 | (다) 잡음 제안")
        for floor in FLOORS:
            for margin in MARGINS:
                fp = sum(1 for k, a, g, per in probes if k == "right" and suggest(a, per, floor, margin))
                ok = sum(1 for k, a, g, per in probes if k == "wrong" and suggest(a, per, floor, margin) == g)
                bad = sum(1 for k, a, g, per in probes if k == "wrong" and suggest(a, per, floor, margin) not in (None, g))
                miss = n_wrong - ok - bad
                noise = sum(1 for k, a, g, per in probes if k == "noise" and suggest(a, per, floor, margin))
                print(f"  {floor:.2f}  {margin:.2f} | {fp:3d}/{n_right} | {ok:3d} / {bad:2d} / {miss:3d} (맞음 {ok / max(1, n_wrong):.0%}) | {noise:3d}/{n_noise}")
        # 물은 곳별 — 컬렉션이 2곳뿐이면 "다른 곳" 오제안은 구조상 0이라 의미 없음(리뷰 M2), 물은 곳마다 맞음률을 따로 본다
        print("  물은 곳별(하한 0.45 · 차이 0): 다른 컬렉션에서 물음 → 맞게 제안 / 문항 · 답 없는 질문 잡음")
        for asked in colls:
            w = [p for p in probes if p[0] == "wrong" and p[1] == asked]
            nz = [p for p in probes if p[0] == "noise" and p[1] == asked]
            ok = sum(1 for k, a, g, per in w if suggest(a, per, 0.45, 0.0) == g)
            nn = sum(1 for k, a, g, per in nz if suggest(a, per, 0.45, 0.0))
            print(f"    {asked}: {ok}/{len(w)} · 잡음 {nn}/{len(nz)}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
