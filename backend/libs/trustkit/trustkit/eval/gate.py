"""전/후 판정(게이트) — "바꿨더니 좋아졌나/나빠졌나"를 오차 범위로 판정한다. 입력은 채점 결과 행뿐(파일 형식·DB 무관).

답(answer_gate): 행 = {qid, label, type?, cluster?, rep?}. 같은 문항의 회차는 평균(반복 측정), 유형별·전체로 같은 문항끼리 짝 차이 +
95% 구간(군집 = 같은 원문 묶음). 실패 = 어느 묶음이든 "의미 있게 나빠짐"(구간 상한 < 0).
검색(retrieval_gate): 문항별 맞음/틀림(결정적 측정). McNemar 정확 검정(1947) — 서로 엇갈린 문항만 본다. 실패 = 나빠진 문항이 있음
(결정적이라 1건도 신호), 의미 = p < 0.05.
"""
from __future__ import annotations

import collections
import math
from typing import Iterable, Mapping, Optional

from trustkit.eval.stats import estimate, iid_se, paired_diff, t95


def per_question(rows: Iterable[Mapping], correct: str = "정답") -> tuple[dict, dict]:
    """(문항 → 회차 평균 정답률, 문항 → (유형, 묶음))."""
    acc: dict = collections.defaultdict(list)
    meta: dict = {}
    for r in rows:
        acc[r["qid"]].append(1.0 if r["label"] == correct else 0.0)
        meta[r["qid"]] = (r.get("type"), r.get("cluster") or "")
    return {q: sum(v) / len(v) for q, v in acc.items()}, meta


def answer_gate(before: Iterable[Mapping], after: Iterable[Mapping], correct: str = "정답") -> dict:
    b, meta = per_question(before, correct)
    a, meta_a = per_question(after, correct)
    meta = {**meta, **meta_a}
    groups: dict = {"전체": [q for q in a if q in b]}
    if not groups["전체"]:   # 짝지은 문항 0개(파일 오지정·qid 불일치) — 통과로 두면 아무것도 안 잰 게 통과가 된다(리뷰 10/19)
        return {"pass": False, "failed": ["짝지은 문항 없음"], "groups": {}}
    for q in groups["전체"]:
        groups.setdefault(meta[q][0] or "-", []).append(q)
    out, failed = {}, []
    for g, qs in groups.items():
        cl = lambda q: meta[q][1] or q   # noqa: E731 — 묶음 정보가 없으면 문항 자체가 묶음
        d = paired_diff({q: a[q] for q in qs}, {q: b[q] for q in qs}, cl)
        e_b = estimate([b[q] for q in qs], [cl(q) for q in qs])
        e_a = estimate([a[q] for q in qs], [cl(q) for q in qs])
        fallback = False
        if d["hi"] is None and len(qs) >= 2:   # 묶음이 1개면 군집 오차를 못 잰다 — 문항 독립 가정으로 대신(표시함, 덜 보수적)
            diffs = [a[q] - b[q] for q in qs]
            se = iid_se(diffs)
            if se is not None:
                crit = t95(len(qs) - 1)
                d = {**d, "lo": d["mean"] - crit * se, "hi": d["mean"] + crit * se}
                d["significant"] = se > 0 and (d["lo"] > 0 or d["hi"] < 0)   # 분산 0(차이가 모두 같음)은 표본이 작으면 과신이라 "의미 있음" 아님
                fallback = True
        undecidable = d["hi"] is None and any(a[q] < b[q] for q in qs)   # 오차를 못 재는데 나빠진 문항이 있으면 통과시키지 않는다
        worse = (d["hi"] is not None and d["hi"] < 0) or undecidable
        out[g] = {"n": len(qs), "before": e_b["mean"], "after": e_a["mean"], "diff": d["mean"], "lo": d["lo"], "hi": d["hi"],
                  "significant": d["significant"], "worse": worse, "iid_fallback": fallback, "undecidable": undecidable,
                  "up": sum(a[q] > b[q] for q in qs), "down": sum(a[q] < b[q] for q in qs)}
        if worse:
            failed.append(g)
    return {"pass": not failed, "failed": failed, "groups": out}


def mcnemar_p(up: int, down: int) -> float:
    """정확 검정 양측 p — 엇갈린 문항 n = up + down 중 작은 쪽이 k 이하일 확률 × 2."""
    n, k = up + down, min(up, down)
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def retrieval_gate(before: Mapping[str, bool], after: Mapping[str, bool], types: Optional[Mapping[str, str]] = None) -> dict:
    qs = [q for q in after if q in before]
    if not qs:
        return {"pass": False, "n": 0, "before": 0, "after": 0, "up": [], "down": [], "p": 1.0, "significant": False, "by_type": {},
                "error": "짝지은 문항 없음"}
    up = [q for q in qs if after[q] and not before[q]]
    down = [q for q in qs if before[q] and not after[q]]
    by_type: dict = {}
    for q in qs:
        t = (types or {}).get(q, "-")
        x = by_type.setdefault(t, {"n": 0, "before": 0, "after": 0})
        x["n"] += 1
        x["before"] += bool(before[q])
        x["after"] += bool(after[q])
    p = mcnemar_p(len(up), len(down))
    return {"pass": not down, "n": len(qs), "before": sum(bool(before[q]) for q in qs), "after": sum(bool(after[q]) for q in qs),
            "up": up, "down": down, "p": p, "significant": p < 0.05 and len(up) > len(down), "by_type": by_type}
