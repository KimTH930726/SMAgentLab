"""용어집 동의어 일괄 생성 (2026-10-06, v2.128) — 기존 용어 전부를 LLM으로 한 번에(최초 1회).

이후엔 용어 등록·수정 때 자동(glossary_terms.refresh_term_synonyms), 질문 기록에서도 자동(glossary_mining)이라 이 스크립트는
처음 채울 때와 측정용으로만 쓴다.

  --out FILE   DB에 쓰지 않고 JSON으로만 저장 {파트: {용어: [동의어]}} — 배포 전 측정(eval_chat_retrieval.py --synonyms-file)용
  --write      DB(rag_glossary_synonym, #69 이후)에 저장. --from FILE이면 LLM을 다시 부르지 않고 그 파일을 넣는다
  --from FILE  LLM 대신 이 JSON을 쓴다(품질 게이트는 다시 적용). --out과 함께 쓰면 게이트만 다시 거친 파일을 만든다

실행(컨테이너 안): python scripts/glossary_synonyms.py --out eval_out/synonyms.json [--namespace "온라인스토어 DB"]
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from core.database import init_pool, get_conn  # noqa: E402
from agents.knowledge_rag.knowledge import glossary_terms  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace", action="append", help="대상 파트(여러 번). 없으면 용어집이 있는 모든 파트")
    ap.add_argument("--out", help="JSON으로 저장할 경로")
    ap.add_argument("--write", action="store_true", help="DB에 저장(#69 이후)")
    ap.add_argument("--from", dest="from_file", help="LLM 대신 이 JSON(품질 게이트 다시 적용)")
    args = ap.parse_args()
    if not args.out and not args.write:
        ap.error("--out 또는 --write 중 하나는 있어야 합니다")
    await init_pool()
    from shared.embedding import embedding_service
    embedding_service.load()   # 품질 게이트(gate_synonyms)용

    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT g.id, g.term, g.description, n.name AS ns FROM rag_glossary g JOIN ops_namespace n ON n.id = g.namespace_id "
            "ORDER BY n.name, g.id")
    by_ns: dict[str, list] = {}
    for r in rows:
        if not args.namespace or r["ns"] in args.namespace:
            by_ns.setdefault(r["ns"], []).append(r)

    if args.from_file:
        # 파일도 품질 게이트를 다시 통과시킨다(게이트 도입 전 파일이거나 기준값이 바뀌었을 수 있음)
        loaded = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
        result = {}
        for ns, terms in loaded.items():
            pairs = [(s, t) for t, syns in terms.items() for s in syns]
            keep = await glossary_terms.gate_synonyms(pairs, "llm_term")
            for (s, t), ok in zip(pairs, keep):
                if ok:
                    result.setdefault(ns, {}).setdefault(t, []).append(s)
            print(f"[{ns}] 게이트: 동의어 {len(pairs)}개 중 {sum(keep)}개 통과", flush=True)
    else:
        from service.llm.factory import get_llm_provider
        llm = get_llm_provider()
        result = {}
        for ns, items in by_ns.items():
            print(f"[{ns}] 용어 {len(items)}개 생성 중...", flush=True)
            result[ns] = await glossary_terms.generate_term_synonyms([(r["term"], r["description"] or "") for r in items], llm)
            print(f"[{ns}] 동의어가 생긴 용어 {len(result[ns])}/{len(items)}", flush=True)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"저장: {args.out}")
    if args.write:
        saved = 0
        async with get_conn() as conn:
            async with conn.transaction():
                for ns, items in by_ns.items():
                    ids = {r["term"]: r["id"] for r in items}
                    for term, syns in (result.get(ns) or {}).items():
                        if term in ids:
                            saved += await glossary_terms.save_synonyms(conn, ids[term], syns, "llm_term")
        print(f"DB 저장: 동의어 {saved}건")


if __name__ == "__main__":
    asyncio.run(main())
