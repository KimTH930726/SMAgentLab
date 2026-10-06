"""실제 채팅 경로 검색 측정 (2026-10-06, v2.128) — LLM은 부르지 않는다(빠르고 결정적).

채팅과 같은 함수(agents.knowledge_rag.agent.build_chat_context: 용어 → 지식/정책/참조데이터 검색 → RRF 문맥)를 그대로 써서
"측정한 경로 = 실제 채팅 경로"가 되게 한다. 예전 지표(track2 Recall@K 87.6%)는 용어 매핑을 안 거친 정책 검색 단독이었다.

  정답 세트(골든 89 / 검증 전용 holdout): Recall@10(정답 정책이 문맥에 들어감), Top-1(정답이 맨 앞 묶음 — 파라미터 1위·서술 1위 — 에 있음), 용어가 걸린 비율
  답 없는 세트(tests/fixtures/eval/negative_v1.jsonl, 정책 파트 2개 + 매뉴얼 파트 1개에서 각각): 용어 오염(엉뚱한 용어가 붙음),
    근거가 붙은 비율(지식 채택·파라미터)
  근거 없음 즉시 판정 후보값별: 정답 세트 거짓 거절 vs 답 없는 세트 차단

출력은 집계 숫자만(질문·문서 원문 없음). 문항별 상세는 --details 경로(JSONL, git 제외 폴더)로.

실행(컨테이너 안):
  python scripts/eval_chat_retrieval.py --modes off,embedding,lexical --synonyms-file eval_out/synonyms_v1.json
  python scripts/eval_chat_retrieval.py --set holdout --golden-file tests/fixtures/golden_set/holdout_v1.jsonl
"""
import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import logging  # noqa: E402
logging.disable(logging.WARNING)

from core.database import init_pool, get_conn, resolve_namespace_id  # noqa: E402
from shared.embedding import embedding_service  # noqa: E402
from agents.knowledge_rag.agent import build_chat_context, should_abstain  # noqa: E402
from agents.knowledge_rag.knowledge import retrieval, glossary_terms  # noqa: E402
from service.policy import track2  # noqa: E402

NEGATIVE_FILE = Path("/app/tests/fixtures/eval/negative_v1.jsonl")
NEGATIVE_NAMESPACES = ["온라인스토어 DB", "딜리버스 DB", "외부서비스DB"]
ABSTAIN_GRID = [(s, r) for s in (0.45, 0.50, 0.52, 0.55) for r in (0.0, 0.05, 0.1)]


async def load_entries(ns: str, synonyms: dict) -> list:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, ns)
        entries = await glossary_terms.load_entries(conn, ns_id) if ns_id is not None else []
    extra = synonyms.get(ns, {})
    for e in entries:
        if e.term in extra:
            e.synonyms = list(dict.fromkeys([*e.synonyms, *extra[e.term]]))
    return entries


async def probe(ns: str, q: str, mode: str, entries_by_ns: dict, defaults: dict) -> dict:
    vec = await embedding_service.embed(q)
    cc = await build_chat_context(
        ns, q, vec, top_k=int(defaults["default_top_k"]), w_vector=defaults["default_w_vector"],
        w_keyword=defaults["default_w_keyword"], glossary_mode=mode,
        glossary_entries=entries_by_ns.get(ns) if mode == "lexical" else None,
    )
    pr = cc.policy_result
    # "맨 앞 묶음" — RRF 문맥에서 파라미터 1위와 서술 1위는 같은 순위(rrf_score(0))로 나란히 맨 앞에 놓인다
    first = {h.item_id for h in (pr.params[:1] + pr.narratives[:1])}
    return {
        "mapped": cc.mapped_term, "policy_ids": [h.item_id for h in pr.params] + [h.item_id for h in pr.narratives],
        "first": first, "signals": cc.signals, "ctx_len": len(cc.llm_context),
        "terms_in_q": [m.term for m in cc.term_matches],
    }


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="off,embedding,lexical")
    ap.add_argument("--set", default="golden", choices=["golden", "holdout"])
    ap.add_argument("--golden-file", help="정답 세트 파일(기본: 골든셋)")
    ap.add_argument("--synonyms-file", help="lexical에 덧붙일 동의어 JSON {파트: {용어: [동의어]}} (DB 반영 전 측정용)")
    ap.add_argument("--no-negative", action="store_true")
    ap.add_argument("--details", help="문항별 상세 JSONL 경로")
    args = ap.parse_args()

    await init_pool()
    embedding_service.load()
    async with get_conn() as conn:
        await retrieval.load_runtime_overrides_from_db(conn)   # 관리자 화면에서 바꾼 임계값·가중치 = 운영값
    defaults = retrieval.get_search_defaults()
    synonyms = json.loads(Path(args.synonyms_file).read_text(encoding="utf-8")) if args.synonyms_file else {}

    async with get_conn() as conn:
        ns_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM ops_namespace")}
    path = Path(args.golden_file) if args.golden_file else track2._GOLDEN_SET_PATH
    gold = await track2._load_golden_set(path, ns_ids)
    neg = [] if args.no_negative else [json.loads(l) for l in NEGATIVE_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    entries_by_ns = {ns: await load_entries(ns, synonyms) for ns in {*(g["namespace_name"] for g in gold), *NEGATIVE_NAMESPACES}
                     if ns in ns_ids}

    details = open(args.details, "w", encoding="utf-8") if args.details else None
    print(f"정답 세트({args.set}) {len(gold)}문항 · 답 없는 질문 {len(neg)}개 × 파트 {len(NEGATIVE_NAMESPACES)} · "
          f"top_k={defaults['default_top_k']} · 동의어 파일 {'있음' if synonyms else '없음'}\n")
    summary = {}
    for mode in [m.strip() for m in args.modes.split(",") if m.strip()]:
        g_rows = []
        for e in gold:
            r = await probe(e["namespace_name"], e["query"], mode, entries_by_ns, defaults)
            r.update(hit=bool(set(r["policy_ids"]) & e["gold_ids"]), top1=bool(r["first"] & e["gold_ids"]), type=e["type"])
            g_rows.append(r)
            if details:
                details.write(json.dumps({"mode": mode, "set": args.set, "type": e["type"], "hit": r["hit"], "top1": r["top1"],
                                          "mapped": r["mapped"], "signals": r["signals"]}, ensure_ascii=False) + "\n")
        n_rows = []
        for ns in NEGATIVE_NAMESPACES:
            if ns not in ns_ids:
                continue
            for e in neg:
                r = await probe(ns, e["query"], mode, entries_by_ns, defaults)
                r.update(kind=e["kind"], ns=ns)
                n_rows.append(r)
                if details:
                    details.write(json.dumps({"mode": mode, "set": "negative", "qid": e["qid"], "ns": ns, "mapped": r["mapped"],
                                              "signals": r["signals"]}, ensure_ascii=False) + "\n")
        n = len(g_rows)
        recall = sum(r["hit"] for r in g_rows) / n * 100
        top1 = sum(r["top1"] for r in g_rows) / n * 100
        by_type = {}
        for r in g_rows:
            t = by_type.setdefault(r["type"], [0, 0, 0])
            t[0] += 1
            t[1] += r["hit"]
            t[2] += r["top1"]
        print(f"=== 용어 방식: {mode} ===")
        print(f"정답 세트: Recall@10 {recall:.1f}% · Top-1 {top1:.1f}% · 용어 붙음 {sum(bool(r['mapped']) for r in g_rows)}/{n}")
        print("  유형별(Recall/Top-1): " + " · ".join(
            f"{t} {h / c * 100:.0f}%/{t1 / c * 100:.0f}%" for t, (c, h, t1) in sorted(by_type.items())))
        if n_rows:
            m = len(n_rows)
            print(f"답 없는 질문 {m}건: 용어 붙음 {sum(bool(r['mapped']) for r in n_rows)} · 지식 채택 있음 "
                  f"{sum(r['signals']['adopted'] > 0 for r in n_rows)} · 정책 파라미터 걸림 {sum(r['signals']['params'] > 0 for r in n_rows)}")
            gs = [r["signals"]["top_narrative"] for r in g_rows]
            ns_ = [r["signals"]["top_narrative"] for r in n_rows]
            gp = [r["signals"]["max_param_rank"] for r in g_rows]
            np_ = [r["signals"]["max_param_rank"] for r in n_rows]
            print(f"  정책 서술 최고점 — 정답 p10 {q(gs, .1):.3f} 중앙 {statistics.median(gs):.3f} / 답 없음 중앙 {statistics.median(ns_):.3f} p90 {q(ns_, .9):.3f}")
            print(f"  정책 파라미터 최고 rank — 정답 p10 {q(gp, .1):.3f} 중앙 {statistics.median(gp):.3f} / 답 없음 중앙 {statistics.median(np_):.3f} p90 {q(np_, .9):.3f}")
            print("  근거 없음 즉시 판정(서술 하한, 파라미터 rank 하한) → 정답 거짓 거절 / 답 없음 차단:")
            for s, pr in ABSTAIN_GRID:
                fa = sum(should_abstain(r["signals"], s, pr) for r in g_rows)
                ca = sum(should_abstain(r["signals"], s, pr) for r in n_rows)
                print(f"    ({s:.2f}, {pr:.2f}) → {fa}/{n} · {ca}/{m}")
        print()
        summary[mode] = {"recall": round(recall, 1), "top1": round(top1, 1)}
    if details:
        details.close()
    print("요약:", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
