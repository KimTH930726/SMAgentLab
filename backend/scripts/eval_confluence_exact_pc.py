"""정확한 parent-child(P1 섹션 / P2 부모) vs 현재(A) vs 근사(C) — 검색 커버리지 비교 (2026-10-01).

C(근사: 같은 상위 문자열 + id 근접 6개)로 잰 효과가 "정확한 구조"에서도 나오는지 본다. 확장 정의는
eval_confluence_structure.exact_expand(). LLM 미사용 — 정답 청크가 컨텍스트에 들어오는지와 길이만.
단일({"chunk_id"})·통합형({"chunk_ids"}) 질문 파일 모두 받는다. 출력은 지표뿐.

    python scripts/eval_confluence_exact_pc.py /tmp/conf_questions.jsonl /tmp/conf_integrated.jsonl
"""
import asyncio
import json
import statistics
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

from core.database import close_pool, get_conn, init_pool
from shared.embedding import embedding_service
from agents.knowledge_rag.knowledge import retrieval
import eval_confluence_structure as base


async def run(path: str) -> None:
    qs = []
    for l in open(path, encoding="utf-8"):
        if l.strip():
            o = json.loads(l)
            o["gold"] = o.get("chunk_ids") or [o["chunk_id"]]
            if o.get("question"):
                qs.append(o)
    th = retrieval.get_thresholds()
    d = retrieval.get_search_defaults()
    top_k = int(d["default_top_k"])
    async with get_conn() as conn:
        ns_of = {r["id"]: r["name"] for r in await conn.fetch(
            "SELECT k.id, n.name FROM rag_knowledge k JOIN ops_namespace n ON n.id=k.namespace_id WHERE k.id = ANY($1::int[])",
            sorted({g for q in qs for g in q["gold"]}))}
    cov = {s: [] for s in ("A", "C", "P1", "P2")}
    extra = {s: [] for s in ("A", "C", "P1", "P2")}
    for q in qs:
        gold = [g for g in q["gold"] if g in ns_of]
        ns = ns_of[gold[0]]
        qvec = await embedding_service.embed(q["question"])
        glossary = await retrieval.map_glossary_term(ns, qvec)
        enriched = f"{q['question']} {glossary.term}" if glossary else q["question"]
        res = await retrieval.search_knowledge(ns, qvec, enriched, d["default_w_vector"], d["default_w_keyword"], top_k)
        adopted = [r for r in res if retrieval.is_adopted(r, th)]
        base_ids = {r.id for r in adopted}
        async with get_conn() as conn:
            exp = {
                "A": [],
                "C": await base.siblings_for(conn, ns, adopted),
                "P1": await base.exact_expand(conn, adopted, "section"),
                "P2": await base.exact_expand(conn, adopted, "parent"),
            }
        for s, rows in exp.items():
            got = base_ids | {r["id"] for r in rows}
            cov[s].append(len(set(gold) & got) / len(gold))
            extra[s].append(sum(len(r["content"]) for r in rows))
    n = len(cov["A"])
    print(f"\n=== {path.rsplit('/', 1)[-1]} — {n}문항 (운영 top_k={top_k}) ===")
    for s, label in (("A", "A 현재"), ("C", "C 근사(id 근접 6개)"), ("P1", "P1 정확·섹션"), ("P2", "P2 정확·부모(6천자)")):
        print(f"  {label:<20} 정답 커버리지 {statistics.mean(cov[s]):.0%} | 전부 포함 {sum(c == 1 for c in cov[s])}/{n}"
              f" | 덧붙인 글자 평균 {statistics.mean(extra[s]):.0f}자")


async def main() -> None:
    embedding_service.load()
    await init_pool()
    try:
        for p in sys.argv[1:]:
            await run(p)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
