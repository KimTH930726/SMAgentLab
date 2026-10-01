"""부모 확장(C)을 항상 쓸지, 구조 신호로 조건부로 쓸지(R) — 단일·통합형 질문 양쪽에서 비교 (2026-10-01).

`eval_confluence_integrated.py`에서 통합형 질문은 C가 핵심 항목 재현율 69%→85%로 좋았지만, 단일 청크
질문에서 C의 긴 컨텍스트(3.5~4.4배)가 답을 흐리는지와 응답 시간은 재지 않았다. 여기서:

  A 현재     : 검색 상위 top_k만
  C 항상 확장 : 운영 코드의 부모 섹션 확장(retrieval.expand_parent_sections) — 정확한 경계·문서 순서
  R 조건부   : 채택된 청크 2개 이상이 같은 직계 상위를 공유할 때만 C처럼 확장 — "답이 그 섹션에
              퍼져 있다"는 신호를 검색 결과에서 공짜로 얻는 라우팅(추가 LLM 호출 없음)

R의 컨텍스트는 A나 C와 문자 그대로 같으므로 답변은 해당 쪽 것을 재사용한다(호출 절약, 비교 공정).
질문 파일은 단일({"chunk_id"})·통합형({"chunk_ids"}) 둘 다 받는다. 판정용 입력은 --dump-judge에
(A·C 답변 2개, 라벨 무작위) 쓰고 판정은 로컬 LLM이 한다. 화면 출력은 지표뿐.

실행 (컨테이너 안):
    python scripts/eval_confluence_routing.py /tmp/q.jsonl --set single --dump-judge /tmp/judge_single
"""
import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from collections import defaultdict

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

from core.database import close_pool, get_conn, init_pool
from shared.embedding import embedding_service
from service.llm.base import resolve_system_prompt
from service.llm.factory import get_llm_provider
from service.chat.helpers import NO_KNOWLEDGE_MARKER
from agents.knowledge_rag.knowledge import retrieval
from agents.knowledge_rag.agent import POLICY_CONTEXT_TOP_K, _build_rrf_context
from service.policy import search as policy_search
import eval_confluence_structure as base


def parent_key(path) -> tuple:
    path = list(path or [])
    return tuple(path[:-1]) if len(path) > 1 else tuple(path)


def should_expand(adopted) -> bool:
    """R의 라우팅 신호 — 채택 청크 2개 이상이 같은 직계 상위를 공유하면 확장."""
    keys = [parent_key(r.heading_path) for r in adopted if r.heading_path]
    return any(keys.count(k) >= 2 for k in set(keys))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("questions")
    ap.add_argument("--set", required=True, help="출력 표시용 이름(single/integrated)")
    ap.add_argument("--dump-judge", default="")
    args = ap.parse_args()

    qs = []
    for l in open(args.questions, encoding="utf-8"):
        if l.strip():
            o = json.loads(l)
            o["gold"] = o.get("chunk_ids") or [o["chunk_id"]]
            if o.get("question"):
                qs.append(o)
    rng = random.Random(7)
    embedding_service.load()
    await init_pool()
    try:
        async with get_conn() as conn:
            ids = sorted({g for q in qs for g in q["gold"]})
            meta = {r["id"]: r for r in await conn.fetch(
                "SELECT k.id, n.name AS ns, k.content, k.heading_path FROM rag_knowledge k "
                "JOIN ops_namespace n ON n.id=k.namespace_id WHERE k.id = ANY($1::int[])", ids)}
        llm = get_llm_provider()
        sp = await resolve_system_prompt()
        th = retrieval.get_thresholds()
        d = retrieval.get_search_defaults()
        top_k = int(d["default_top_k"])
        if args.dump_judge:
            os.makedirs(args.dump_judge, exist_ok=True)

        cov = {s: [] for s in "ACR"}
        chars = {s: [] for s in "ACR"}
        lat = {s: [] for s in "AC"}
        nok = {s: [] for s in "AC"}
        routed = []
        for i, q in enumerate(qs, 1):
            gold = [g for g in q["gold"] if g in meta]
            if not gold:
                continue
            ns = meta[gold[0]]["ns"]
            qvec = await embedding_service.embed(q["question"])
            glossary = await retrieval.map_glossary_term(ns, qvec)
            enriched = f"{q['question']} {glossary.term}" if glossary else q["question"]
            pr = policy_search.PolicySearchResult()
            if await policy_search.has_policy_data(ns):
                pr = await policy_search.search_policy(ns, enriched, top_k=POLICY_CONTEXT_TOP_K, query_vec=qvec)
            res = await retrieval.search_knowledge(ns, qvec, enriched, d["default_w_vector"], d["default_w_keyword"], top_k)
            adopted = [r for r in res if retrieval.is_adopted(r, th)]
            # C = 운영 코드 그대로(retrieval.expand_parent_sections + _build_rrf_context 보충 블록). 첫 측정은
            # 근사(base.siblings_for)였고, 운영 반영 후엔 운영 경로를 재야 측정과 실제가 어긋나지 않는다.
            sibs = await retrieval.expand_parent_sections(adopted)
            ctx_a = _build_rrf_context(res, pr)
            ctx_c = _build_rrf_context(res, pr, parent_expansions=sibs)
            expand = should_expand(adopted)
            routed.append(expand)
            ids_a = {r.id for r in adopted}
            ids_c = ids_a | {s["id"] for s in sibs}
            for s, got, ctx in (("A", ids_a, ctx_a), ("C", ids_c, ctx_c), ("R", ids_c if expand else ids_a, ctx_c if expand else ctx_a)):
                cov[s].append(len(set(gold) & got) / len(gold))
                chars[s].append(len(ctx))
            answers = {}
            for s, ctx in (("A", ctx_a), ("C", ctx_c)):
                t0 = time.monotonic()
                text, _ = await llm.generate(ctx, q["question"], None, system_prompt=sp)
                lat[s].append(time.monotonic() - t0)
                answers[s] = text or ""
                nok[s].append(NO_KNOWLEDGE_MARKER in answers[s])
            if args.dump_judge:
                labels = ["X", "Y"]
                rng.shuffle(labels)
                mapping = dict(zip("AC", labels))
                with open(os.path.join(args.dump_judge, f"judge_{i}.txt"), "w", encoding="utf-8") as f:
                    f.write(f"[질문]\n{q['question']}\n\n[정답 근거 청크]\n")
                    for g in gold:
                        f.write(f"--- 청크 {g} (상위 맥락: {base.heading_text(meta[g]['heading_path'])})\n{meta[g]['content']}\n")
                    for s in sorted("AC", key=lambda s: mapping[s]):
                        f.write(f"\n[답변 {mapping[s]}]\n{answers[s]}\n")
                with open(os.path.join(args.dump_judge, f"map_{i}.json"), "w", encoding="utf-8") as f:
                    json.dump({"i": i, "mapping": mapping, "routed": expand}, f)
            if i % 10 == 0:
                print(f"  ... {i}/{len(qs)}", flush=True)

        n = len(routed)
        print(f"\n=== [{args.set}] {n}문항 — A 현재 / C 항상 확장 / R 조건부(같은 상위 2개 이상) ===")
        for s, label in (("A", "A 현재"), ("C", "C 항상 확장"), ("R", "R 조건부")):
            line = f"{label:<12} 정답 커버리지 {statistics.mean(cov[s]):.0%} | 전부 포함 {sum(c == 1 for c in cov[s])}/{n} | 컨텍스트 평균 {statistics.mean(chars[s]):.0f}자"
            if s in lat:
                line += f" | 응답시간 평균 {statistics.mean(lat[s]):.1f}s (p90 {sorted(lat[s])[int(len(lat[s]) * 0.9) - 1]:.1f}s) | 지식없음 {sum(nok[s])}/{n}"
            print(line)
        print(f"  R이 확장을 켠 비율: {sum(routed)}/{n}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
