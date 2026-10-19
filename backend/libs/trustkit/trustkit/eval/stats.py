"""평가 통계 (2026-10-13) — 점수에 오차 범위를 붙이고, 두 판의 차이가 오차보다 큰지, 채점기가 사람과 얼마나 맞는지.

근거: Anthropic "A statistical approach to model evaluations" / Miller, "Adding Error Bars to Evals"(arXiv 2411.00640) —
문항은 더 큰 질문 집단에서 뽑힌 표본이라 점수는 추정치다. 표준오차·95% 구간을 같이 보고하고, 같은 원문(시트·분류)에서 나온
문항은 서로 독립이 아니라 묶어서(군집) 오차를 계산하고, 두 판은 같은 문항끼리 차이로 비교한다.
채점기 보정: Cohen's kappa(두 평가자·명목 라벨) — 실무 기준 0.6 이상 운영 가능, 0.8 이상 우수(업계 가이드, 확정 기준 아님).
외부 패키지 없이(표준 라이브러리) — 폐쇄망에서도 그대로 돈다.
"""
from __future__ import annotations

import collections
import math
from typing import Callable, Iterable, Optional

Z95 = 1.96
# 묶음이 적으면 정규분포(1.96)는 구간을 좁게(과신) 잡는다 — 군집 표준오차는 자유도 G−1의 t 값(Miller 2024, 리뷰 2026-10-13)
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201,
        12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 25: 2.060, 30: 2.042}


def t95(df: int) -> float:
    if df <= 0:
        return float("inf")
    if df in _T95:
        return _T95[df]
    if df > 30:
        return Z95 + 2.4 / df   # 큰 자유도 근사(30 넘으면 2.04→1.96)
    lower = max(k for k in _T95 if k < df)
    return _T95[lower]          # 표 사이는 보수적으로(작은 자유도 값)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def clustered_se(values: list[float], clusters: list[str]) -> Optional[float]:
    """평균의 군집 표준오차 — sqrt(Σ_c (Σ_{i∈c}(x_i − x̄))²) / n (Miller 2024 식). 군집이 모두 1개짜리면 보통 표준오차와 같다.
    묶음이 1개거나 문항이 1개면 오차를 잴 수 없다 → None(0으로 두면 구간 폭 0·"의미 있는 차이"로 잘못 나온다 — 리뷰)."""
    n = len(values)
    if n < 2 or len(set(clusters)) < 2:
        return None
    m = _mean(values)
    sums: dict = collections.defaultdict(float)
    for v, c in zip(values, clusters):
        sums[c] += v - m
    g = len(sums)
    adj = g / (g - 1) if g > 1 else 1.0   # 군집 수가 적을 때 작은 표본 보정
    return math.sqrt(adj * sum(s * s for s in sums.values())) / n


def iid_se(values: list[float]) -> Optional[float]:
    n = len(values)
    if n < 2:
        return None
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1) / n)


def estimate(values: list[float], clusters: Optional[list[str]] = None) -> dict:
    """평균 + 95% 구간(군집 있으면 군집 표준오차). 0~1 값(정답 여부)이면 비율."""
    m = _mean(values)
    se = clustered_se(values, clusters) if clusters else iid_se(values)
    if se is None:
        return {"n": len(values), "mean": m, "se": None, "lo": None, "hi": None, "crit": None}
    crit = t95(len(set(clusters)) - 1) if clusters else (t95(len(values) - 1) if len(values) <= 30 else Z95)
    return {"n": len(values), "mean": m, "se": se, "lo": m - crit * se, "hi": m + crit * se, "crit": crit}


def paired_diff(a: dict[str, float], b: dict[str, float], cluster_of: Callable[[str], str]) -> dict:
    """같은 문항끼리 차이(a − b) — 평균 차이·95% 구간·의미 있는 차이인지(구간이 0을 안 품으면). 두 판에 다 있는 문항만."""
    keys = sorted(set(a) & set(b))
    d = [a[k] - b[k] for k in keys]
    e = estimate(d, [cluster_of(k) for k in keys])
    e["significant"] = e["lo"] is not None and (e["lo"] > 0 or e["hi"] < 0)
    return e


def cohen_kappa(pairs: Iterable[tuple[str, str]]) -> dict:
    """두 평가자(채점기, 사람)의 일치 — 원 일치율·카파·혼동행렬. 한 라벨이 몰리면 원 일치율이 높아도 카파는 낮다."""
    pairs = list(pairs)
    n = len(pairs)
    if not n:
        return {"n": 0, "agree": 0.0, "kappa": None, "confusion": {}}
    labels = sorted({x for p in pairs for x in p})
    conf = {(x, y): 0 for x in labels for y in labels}
    for x, y in pairs:
        conf[(x, y)] += 1
    po = sum(conf[(x, x)] for x in labels) / n
    ca = collections.Counter(x for x, _ in pairs)
    cb = collections.Counter(y for _, y in pairs)
    pe = sum(ca[x] * cb[x] for x in labels) / (n * n)
    kappa = None if pe == 1 else (po - pe) / (1 - pe)
    return {"n": n, "agree": po, "kappa": kappa, "confusion": {f"{x}→{y}": c for (x, y), c in conf.items() if c}}


def kappa_band(k: Optional[float]) -> str:
    if k is None:
        return "계산 불가(한 라벨만)"
    if k >= 0.8:
        return "우수(≥0.8)"
    if k >= 0.6:
        return "운영 가능(≥0.6)"
    return "참고용만(<0.6) — 채점 기준·채점기 개선 필요"


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"
