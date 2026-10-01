"""컨플루언스 청크 구조 3방식 비교 — 통합형 질문(여러 청크를 함께 봐야 답하는 질문) (2026-10-01).

`eval_confluence_structure.py`(청크당 1문항)는 C(부모 확장)의 장점이 드러날 질문이 없었다 — 단일
청크 질문은 A(현재)도 88%를 맞힌다. 여기선 같은 상위 섹션의 청크 2~4개가 정답인 질문으로
"필요한 청크가 컨텍스트에 몇 개 들어오나(커버리지)"를 잰다. 방식 정의·임시 네임스페이스 복사·형제
확장은 그 스크립트의 함수를 그대로 쓴다.

질문 세트(JSONL {"chunk_ids": [...], "question"})는 로컬 LLM이 생성(원문 미열람 규칙). 답변이 정답을
빠짐없이 담았는지는 정답 청크 원문과 대조해야 해서, --dump-judge 경로에 판정용 입력(질문·정답 청크
원문·답변 3개, 방식 라벨은 무작위로 섞음)을 써두고 판정도 로컬 LLM이 한다. 화면 출력은 지표뿐.

실행 (컨테이너 안):
    python scripts/eval_confluence_integrated.py /tmp/conf_integrated.jsonl --dump-judge /tmp/judge
"""
import argparse
import asyncio
import json
import os
import random
import statistics
import sys
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


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("questions")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--dump-judge", default="", help="판정용 입력을 쓸 디렉터리(원문 포함 — 로컬에만)")
    args = ap.parse_args()

    qs = [json.loads(l) for l in open(args.questions, encoding="utf-8") if l.strip()]
    rng = random.Random(42)
    embedding_service.load()
    await init_pool()
    tmp_names = []
    try:
        async with get_conn() as conn:
            all_ids = sorted({i for q in qs for i in q["chunk_ids"]})
            meta = {r["id"]: r for r in await conn.fetch(
                "SELECT k.id, n.name AS ns, k.content, k.heading_path FROM rag_knowledge k "
                "JOIN ops_namespace n ON n.id=k.namespace_id WHERE k.id = ANY($1::int[])", all_ids)}
            copies = {}
            for ns in sorted({meta[i]["ns"] for i in all_ids if i in meta}):
                tmp, id_map = await base.make_ctx_embedding_copy(conn, ns)
                tmp_names.append(tmp)
                copies[ns] = (tmp, id_map)

        llm = None if args.skip_llm else get_llm_provider()
        sp = None if args.skip_llm else await resolve_system_prompt()
        th = retrieval.get_thresholds()
        d = retrieval.get_search_defaults()
        top_k = int(d["default_top_k"])
        if args.dump_judge:
            os.makedirs(args.dump_judge, exist_ok=True)

        stats = {s: defaultdict(list) for s in "ABC"}
        for i, q in enumerate(qs, 1):
            gold = [g for g in q["chunk_ids"] if g in meta]
            if len(gold) < 2:
                continue
            ns = meta[gold[0]]["ns"]
            qvec = await embedding_service.embed(q["question"])
            glossary = await retrieval.map_glossary_term(ns, qvec)
            enriched = f"{q['question']} {glossary.term}" if glossary else q["question"]
            pr = policy_search.PolicySearchResult()
            if await policy_search.has_policy_data(ns):
                pr = await policy_search.search_policy(ns, enriched, top_k=POLICY_CONTEXT_TOP_K, query_vec=qvec)

            a_res = await retrieval.search_knowledge(ns, qvec, enriched, d["default_w_vector"], d["default_w_keyword"], top_k)
            tmp, id_map = copies[ns]
            b_res = await retrieval.search_knowledge(tmp, qvec, enriched, d["default_w_vector"], d["default_w_keyword"], top_k)
            a_adopted = [r for r in a_res if retrieval.is_adopted(r, th)]
            b_adopted = [r for r in b_res if retrieval.is_adopted(r, th)]
            async with get_conn() as conn:
                sibs = await base.siblings_for(conn, ns, a_adopted)
            ctx = {
                "A": _build_rrf_context(a_res, pr),
                "B": _build_rrf_context(b_res, pr),
            }
            ctx["C"] = ctx["A"] + "".join(
                f"\n\n--- 문서 #{s['id']} (같은 상위 섹션 확장) ---\n상위 맥락: {base.heading_text(s['heading_path'])}\n내용:\n{s['content']}"
                for s in sibs)
            ids = {
                "A": {r.id for r in a_adopted},
                "B": {id_map.get(r.id) for r in b_adopted},
                "C": {r.id for r in a_adopted} | {s["id"] for s in sibs},
            }
            answers = {}
            for s in "ABC":
                cov = len(set(gold) & ids[s]) / len(gold)
                stats[s]["cov"].append(cov)
                stats[s]["full"].append(cov == 1.0)
                stats[s]["chars"].append(len(ctx[s]))
                if llm is not None:
                    text, _ = await llm.generate(ctx[s], q["question"], None, system_prompt=sp)
                    answers[s] = text or ""
                    stats[s]["nok"].append(NO_KNOWLEDGE_MARKER in answers[s])
            if args.dump_judge and answers:
                labels = list("XYZ")
                rng.shuffle(labels)
                mapping = dict(zip("ABC", labels))  # 방식 → 판정자에게 보이는 라벨(무작위)
                with open(os.path.join(args.dump_judge, f"judge_{i}.txt"), "w", encoding="utf-8") as f:
                    f.write(f"[질문]\n{q['question']}\n\n[정답 근거 청크]\n")
                    for g in gold:
                        f.write(f"--- 청크 {g} (상위 맥락: {base.heading_text(meta[g]['heading_path'])})\n{meta[g]['content']}\n")
                    for s in sorted("ABC", key=lambda s: mapping[s]):
                        f.write(f"\n[답변 {mapping[s]}]\n{answers[s]}\n")
                with open(os.path.join(args.dump_judge, f"map_{i}.json"), "w", encoding="utf-8") as f:
                    json.dump({"i": i, "mapping": mapping, "gold_n": len(gold)}, f)
            if i % 5 == 0:
                print(f"  ... {i}/{len(qs)}", flush=True)

        n = len(stats["A"]["cov"])
        print(f"\n=== 통합형 질문 {n}문항 (정답 청크 2~4개, 운영 top_k={top_k}) ===")
        print(f"{'방식':<14}{'평균 커버리지':>12}{'정답 전부 포함':>14}{'지식없음 답변':>14}{'컨텍스트 평균':>14}")
        for s, label in (("A", "A 현재"), ("B", "B 맥락임베딩"), ("C", "C 부모확장")):
            st = stats[s]
            nok = f"{sum(st['nok'])}/{n}" if st["nok"] else "-"
            print(f"{label:<14}{statistics.mean(st['cov']):>11.0%}{sum(st['full']):>10}/{n}{nok:>14}{statistics.mean(st['chars']):>12.0f}자")
    finally:
        async with get_conn() as conn:
            for tmp in tmp_names:
                await conn.execute("DELETE FROM ops_namespace WHERE name=$1", tmp)
            left = await conn.fetchval("SELECT COUNT(*) FROM ops_namespace WHERE name LIKE $1", f"%{base.TMP_SUFFIX}")
        print(f"\n[정리] 임시 네임스페이스 잔존: {left}")
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
