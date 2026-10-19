"""지식(컨플루언스 등) 검색 측정 — 정답 조각이 채팅 문맥에 실제로 들어가나(상위 top_k 중 관련도 기준 통과), 몇 위인가.

문항: scripts/confluence_synth_questions.py가 로컬 LLM으로 만든 합성 문항(eval_out/, git 제외). 출력은 숫자만(문항·원문 없음).
상세(--details)는 qid별 hit·순위 — scripts/eval_gate.py --retrieval-before/after로 전/후 판정.

    python scripts/eval_knowledge_retrieval.py --set eval_out/confluence_synth.jsonl --details eval_out/kretr_A.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import logging  # noqa: E402
logging.disable(logging.INFO)

DEEP = 10   # 순위를 보는 깊이(문맥에 들어가는 건 top_k)


async def main() -> None:
    from core.database import init_pool, get_conn
    from shared.embedding import embedding_service
    from agents.knowledge_rag.knowledge import retrieval, glossary_terms
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--details")
    a = ap.parse_args()
    await init_pool()
    embedding_service.load()
    async with get_conn() as conn:
        await retrieval.load_runtime_overrides_from_db(conn)
    d, th = retrieval.get_search_defaults(), retrieval.get_thresholds()
    top_k = int(d["default_top_k"])
    items = [json.loads(l) for l in Path(a.set).read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = []
    for it in items:
        vec = await embedding_service.embed(it["question"])
        _, _, enriched = await glossary_terms.resolve_query_terms(it["ns"], it["question"], vec)
        res = await retrieval.search_knowledge(it["ns"], vec, enriched, d["default_w_vector"], d["default_w_keyword"], DEEP)
        ids = [r.id for r in res]
        gold = set(it["gold_ids"])
        rank = next((i + 1 for i, x in enumerate(ids) if x in gold), None)   # 순위 진단은 하이브리드 원순위(구제 전)
        # 문맥은 채팅과 같은 호출로(top_k + 키워드 구제 설정) — agent.build_chat_context와 같은 인자
        chat = await retrieval.search_knowledge(it["ns"], vec, enriched, d["default_w_vector"], d["default_w_keyword"], top_k,
                                                keyword_rescue=retrieval.KEYWORD_RESCUE)
        ctx = [r.id for r in chat if retrieval.is_adopted(r, th)]
        rows.append({"set": "knowledge", "qid": it["qid"], "type": "knowledge", "cluster": it.get("cluster"),
                     "hit": bool(gold & set(ctx)), "at10": rank is not None, "rank": rank,
                     "in_topk_not_adopted": bool(rank and rank <= top_k and not (gold & set(ctx)))})
    n = len(rows) or 1
    ranks = [r["rank"] for r in rows if r["rank"]]
    print(f"지식 문항 {len(rows)}개 · top_k={top_k} · 관련도 기준 {th['knowledge_min_score']} · 키워드 구제 {'켬' if retrieval.KEYWORD_RESCUE else '끔'}")
    print(f"  문맥에 들어감(Recall@{top_k}·채택): {sum(r['hit'] for r in rows)}/{len(rows)} = {sum(r['hit'] for r in rows) / n:.1%}")
    print(f"  상위 {DEEP} 안: {sum(r['at10'] for r in rows) / n:.1%} · MRR@{DEEP} {sum(1 / r['rank'] for r in rows if r['rank']) / n:.3f}"
          + (f" · 순위 중앙값 {statistics.median(ranks)}" if ranks else ""))
    print(f"  top_k 안인데 관련도 기준에 걸려 빠짐: {sum(r['in_topk_not_adopted'] for r in rows)}")
    if a.details:
        Path(a.details).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
