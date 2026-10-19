"""정확도 게이트 — 전/후 측정 파일을 받아 trustkit.eval.gate로 통과/실패를 판정한다(검색 나빠짐 0 · 답 의미 있는 하락 없음).

    python scripts/eval_gate.py --answers-before eval_out/judged_A.jsonl --answers-after eval_out/judged_B.jsonl \
        [--retrieval-before eval_out/retr_A.jsonl --retrieval-after eval_out/retr_B.jsonl]

- 답 파일: eval_answers judge 결과(같은 날 채점한 것끼리 — 채점기가 날마다 흔들림). 회차(rep)는 문항별 평균.
- 검색 파일: eval_chat_retrieval --details 결과(정답 세트 줄만, qid로 짝).
출력은 숫자만(질문·답 원문 없음). 종료 코드: 통과 0, 실패 1 — 나중에 자동 실행에 그대로 쓸 수 있게.
절차 전체: docs/tech/trustkit-design.md §5, 측정 순서는 scripts/scenarios/README.md와 같은 규칙(한 번에 하나).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs" / "trustkit"))   # 호스트에서도(표준 라이브러리만)
from trustkit.eval.gate import answer_gate, retrieval_gate  # noqa: E402


def _rows(path: str) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def _answer_rows(path: str) -> list[dict]:
    out = []
    for r in _rows(path):
        lab = (r.get("judge") or {}).get("label")
        if lab and lab != "판정불가":   # 판정불가(정답 정책이 모두 비활성)만 분모에서 뺀다 — eval_answers와 같은 규칙
            out.append({"qid": r["qid"], "label": lab, "type": r.get("type"), "cluster": r.get("cluster"), "rep": r.get("rep", 0)})
    return out


def _judge_meta(path: str) -> set:
    return {((r.get("judge_rubric") or ""), (r.get("judged_on") or "")) for r in _rows(path)}


def _pct(x):
    return "-" if x is None else f"{x * 100:+.1f}" if x < 0 or x > 0 else "+0.0"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers-before")
    ap.add_argument("--answers-after")
    ap.add_argument("--retrieval-before")
    ap.add_argument("--retrieval-after")
    ap.add_argument("--mode", default="lexical", help="검색 상세에 여러 용어 방식(mode)이 섞여 있으면 이것만 비교(기본: 운영값 lexical)")
    a = ap.parse_args()
    pairs = [(a.answers_before, a.answers_after), (a.retrieval_before, a.retrieval_after)]
    if any(bool(x) != bool(y) for x, y in pairs) or not any(x and y for x, y in pairs):
        print("입력 오류: --answers-before/after 또는 --retrieval-before/after를 짝으로 주세요")
        return 2
    ok = True
    if a.retrieval_before and a.retrieval_after:
        pick = lambda p: {r["qid"]: r["hit"] for r in _rows(p)   # noqa: E731 — mode 없는 파일(지식 측정)은 그대로
                          if r.get("set") != "negative" and r.get("qid") and r.get("mode", a.mode) == a.mode}
        types = {r["qid"]: r.get("type") for r in _rows(a.retrieval_after) if r.get("qid")}
        v = retrieval_gate(pick(a.retrieval_before), pick(a.retrieval_after), types)
        ok &= v["pass"]
        if v.get("error"):
            print(f"[검색] 실패 — {v['error']}(qid·mode 확인)")
        print(f"[검색] {'통과' if v['pass'] else '실패'} — Recall {v['before']}/{v['n']} → {v['after']}/{v['n']} · "
              f"좋아짐 {len(v['up'])} 나빠짐 {len(v['down'])} · McNemar p={v['p']:.3f}{' (의미 있음)' if v['significant'] else ''}")
        if v["down"]:
            print(f"       나빠진 문항: {v['down']}")
        for t, x in sorted(v["by_type"].items()):
            print(f"       {t:16s} {x['before']}/{x['n']} → {x['after']}/{x['n']}")
    if a.answers_before and a.answers_after:
        mb, ma = _judge_meta(a.answers_before), _judge_meta(a.answers_after)
        if mb != ma:
            print(f"⚠ 채점 기준·날짜가 다름 {sorted(mb)} vs {sorted(ma)} — 기준 답을 같은 날 다시 채점해서 비교할 것")
        v = answer_gate(_answer_rows(a.answers_before), _answer_rows(a.answers_after))
        ok &= v["pass"]
        print(f"[답]   {'통과' if v['pass'] else '실패 — ' + ', '.join(v['failed'])}")
        for g, x in v["groups"].items():
            ci = f"[{_pct(x['lo'])}, {_pct(x['hi'])}]" if x["lo"] is not None else "[계산불가]"
            tag = " 의미 있게 좋아짐" if x["significant"] and x["diff"] > 0 else (
                " 판정 불가(오차를 못 잼 + 나빠진 문항 있음)" if x["undecidable"] else " 의미 있게 나빠짐" if x["worse"] else "")
            tag += " ※묶음 1개 — 문항 독립 가정 구간" if x["iid_fallback"] else ""
            print(f"       {g:16s} {x['n']:3d}문항 {x['before'] * 100:5.1f}% → {x['after'] * 100:5.1f}%  "
                  f"차이 {_pct(x['diff'])}%p {ci}  좋아짐 {x['up']} 나빠짐 {x['down']}{tag}")
    print("게이트:", "통과" if ok else "실패")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
