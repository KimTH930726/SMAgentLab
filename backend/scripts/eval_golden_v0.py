"""정책서 v0 골든셋 검색 측정 (2026-10-21) — 실제 채팅 경로(build_chat_context) 그대로, LLM은 부르지 않는다.

옛 측정(eval_chat_retrieval + track2)은 정답을 엑셀 시트·행으로 찾아 v0 문항을 조용히 뺐다 — 옛 정책 평가 자산은 폐기하고
v0 기준으로 재출발(사용자 10/21). 이 스크립트는 정답을 정책 원천 키(source_key) → 현행 logical_id로 찾고, 못 찾으면 실패로 센다.

  입력: eval_out/golden_v0/reviewed/*.jsonl 중 verdict=approved (팀원 문항 40 포함, git 제외 폴더)
  지표: Recall@K(정답 정책이 문맥에 들어감) · Top-1 · 층별 · 목록형 포함률 · 답 없는 질문의 근거 점수 분포(근거 없음 판정 재보정용)
  참고표·개요 정답 문항은 채팅에 아직 연결 안 됨(계획 #12) — 분모에서 빼되 개수를 따로 보여 준다(조용히 빼지 않음).
출력은 집계 숫자만. 문항별 상세는 --details(JSONL, eval_out) — scripts/eval_gate.py 검색 파일 형식(qid·hit)과 같다.

실행(컨테이너 안):  python scripts/eval_golden_v0.py [--details eval_out/golden_v0/retr_baseline.jsonl]
"""
import argparse
import asyncio
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "/app")

import logging  # noqa: E402
logging.disable(logging.INFO)

from core.database import init_pool, get_conn  # noqa: E402
from shared.embedding import embedding_service  # noqa: E402
from agents.knowledge_rag.agent import build_chat_context, should_abstain  # noqa: E402
from agents.knowledge_rag.agent import POLICY_CONTEXT_TOP_K  # noqa: E402
from agents.knowledge_rag.knowledge import retrieval  # noqa: E402

GOLDEN_DIR = Path("/app/eval_out/golden_v0/reviewed")


def load_golden() -> list[dict]:
    rows = []
    for f in sorted(GOLDEN_DIR.glob("*.jsonl")):
        rows += [r for r in (json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip())
                 if r.get("verdict") == "approved"]
    return rows


def pct(a: int, b: int) -> str:
    return f"{a / b * 100:.1f}%" if b else "-"


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--details", help="문항별 상세 JSONL 경로(eval_gate 입력)")
    ap.add_argument("--label", default="", help="이 측정의 이름(출력에만)")
    args = ap.parse_args()

    await init_pool()
    embedding_service.load()
    async with get_conn() as conn:
        await retrieval.load_runtime_overrides_from_db(conn)   # 관리자 화면에서 바꾼 값 = 운영값
        cur = await conn.fetch("""SELECT i.source_key, i.logical_id, n.name AS ns FROM policy_item i JOIN ops_namespace n ON n.id = i.namespace_id
                                  WHERE i.status <> 'deprecated' AND i.source_key IS NOT NULL""")
    key_to_lid = {r["source_key"]: r["logical_id"] for r in cur}
    key_to_ns = {r["source_key"]: r["ns"] for r in cur}
    defaults = retrieval.get_search_defaults()
    gold = load_golden()

    rows, unresolved = [], 0
    not_connected: dict[str, int] = defaultdict(int)
    details = open(args.details, "w", encoding="utf-8") if args.details else None
    for g in gold:
        kinds = {x["kind"] for x in g.get("gold", [])}
        if kinds - {"policy"}:
            not_connected[g["layer"]] += 1     # 참고표·개요 — 채팅 미연결(계획 #12)
            continue
        lids = {key_to_lid.get(x["source_key"]) for x in g.get("gold", [])}
        if None in lids:
            unresolved += 1                     # 정답 정책이 현행에 없음 — 실패로 센다
            lids.discard(None)
        ns = g.get("asked_from") if g.get("layer") == "cross" and g.get("asked_from") else g.get("doc")
        if not ns:   # 팀원 자연어 문항 중 정답이 여러 문서에 걸친 것 — 첫 정답 정책이 속한 영역에서 묻는다
            ns = next((key_to_ns[x["source_key"]] for x in g["gold"] if x["source_key"] in key_to_ns), None)
        if not ns:   # 물을 영역을 못 정함 — 분모·상세 파일에 실패로 남긴다(빼면 게이트 비교에서 조용히 사라짐, 리뷰 L1)
            unresolved += 1
            r = {"qid": g["qid"], "type": g["layer"], "expect": g["expect"], "ns": None, "hit": False, "top1": False,
                 "coverage": 0.0, "signals": {}, "n_policies": 0, "mapped": None}
            rows.append(r)
            if details:
                details.write(json.dumps({"set": "negative" if g["expect"] == "abstain" else "golden_v0",
                                          **{k: r[k] for k in ("qid", "type", "hit", "top1", "coverage", "ns", "mapped")}},
                                         ensure_ascii=False) + "\n")
            continue
        vec = await embedding_service.embed(g["query"])
        cc = await build_chat_context(
            ns, g["query"], vec, top_k=int(defaults["default_top_k"]), w_vector=defaults["default_w_vector"],
            w_keyword=defaults["default_w_keyword"])   # 용어 방식·용어 목록은 채팅과 같이 설정값·DB 그대로
        pr = cc.policy_result
        got = [h.logical_id for h in pr.params] + [h.logical_id for h in pr.narratives]
        first = {h.logical_id for h in (pr.params[:1] + pr.narratives[:1])}
        found = set(got) & lids
        r = {"qid": g["qid"], "type": g["layer"], "expect": g["expect"], "ns": ns,
             "hit": bool(found), "top1": bool(first & lids), "coverage": (len(found) / len(lids)) if lids else 0.0,
             "signals": cc.signals, "n_policies": len(got), "mapped": cc.mapped_term}
        rows.append(r)
        if details:
            # 답 없는 질문은 "negative" — 게이트 검색 비교(정답 들어감)의 분모에서 빠지게(리뷰 L2)
            details.write(json.dumps({"set": "negative" if g["expect"] == "abstain" else "golden_v0",
                                      **{k: r[k] for k in ("qid", "type", "hit", "top1", "coverage", "ns", "mapped")},
                                      "signals": r["signals"]}, ensure_ascii=False) + "\n")
    if details:
        details.close()

    ans = [r for r in rows if r["expect"] != "abstain"]   # answer·list·clarify(정책 정답) 모두 — clarify가 집계에서 빠지던 것(리뷰 L4)
    abst = [r for r in rows if r["expect"] == "abstain"]
    print(f"골든셋 v0 {args.label} — 확정 문항 {len(gold)} · 측정 {len(rows)} · 문맥 = 정책 파라미터·서술 각 상위 {POLICY_CONTEXT_TOP_K}")
    print(f"  채팅 미연결이라 제외(참고표·개요): {dict(not_connected)} · 정답 정책이 현행에 없거나 영역을 못 정함: {unresolved}(실패로 셈)")
    print(f"정답 있는 질문 {len(ans)}: Recall@{POLICY_CONTEXT_TOP_K} {pct(sum(r['hit'] for r in ans), len(ans))} · "
          f"Top-1 {pct(sum(r['top1'] for r in ans), len(ans))}")
    by = defaultdict(list)
    for r in ans:
        by[r["type"]].append(r)
    print("  층별(Recall / Top-1 / 문항): " + " · ".join(
        f"{t} {pct(sum(x['hit'] for x in v), len(v))}/{pct(sum(x['top1'] for x in v), len(v))}/{len(v)}" for t, v in sorted(by.items())))
    lst = by.get("list", [])
    if lst:
        print(f"  목록형: 정답 정책 평균 포함률 {statistics.mean(r['coverage'] for r in lst) * 100:.0f}% · 전부 포함 {sum(r['coverage'] == 1 for r in lst)}/{len(lst)}"
              " (검색 결과만 셈 — 채팅의 분류 목록 블록은 못 봄, 실제 목록 답은 답 정확도로 확인)")
    if abst:
        key = "top_narrative"
        a_s = [r["signals"].get(key, 0) for r in ans]
        n_s = [r["signals"].get(key, 0) for r in abst]
        print(f"답 없는 질문 {len(abst)}: 정책이 문맥에 붙음 {sum(r['n_policies'] > 0 for r in abst)}")
        print(f"  정책 최고 점수 — 정답 있는 질문 p10 {q(a_s, .1):.3f} 중앙 {statistics.median(a_s):.3f} / "
              f"답 없는 질문 중앙 {statistics.median(n_s):.3f} p90 {q(n_s, .9):.3f}")
        # 실제 판정 함수 그대로(지식 채택·코드·분류 목록·파라미터 조건까지) — 서술 점수만 보면 차단·거짓 거절을 부풀렸다(리뷰 L5)
        for cut in (0.45, 0.50, 0.55, 0.60, 0.65):
            fa = sum(should_abstain(r["signals"], cut, 0.0) for r in ans if r["signals"])
            ca = sum(should_abstain(r["signals"], cut, 0.0) for r in abst if r["signals"])
            print(f"    하한 {cut:.2f}(파라미터 하한 0) → 정답 있는 질문 거짓 거절 {fa}/{len(ans)} · 답 없는 질문 차단 {ca}/{len(abst)}")


if __name__ == "__main__":
    asyncio.run(main())
