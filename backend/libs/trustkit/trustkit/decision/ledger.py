"""AI 판단 원장 — 확신 규칙과 집계(표준 라이브러리만).

확신은 LLM 자기 평가로 정하지 않는다: **LLM이 고른 값 = 비슷한 기존 항목 k개의 다수결(need개 이상)** 일 때만 확신.
근거(OpsLens collection-design §14·§15, 10/22 실측 73건): AI만 → 자동 71 중 틀림 15, 이웃 5중 4만 → 자동 60 중 틀림 2,
AI 일치 + 이웃 5중 4 → 자동 51 중 틀림 0. 숫자보다 "두 신호가 맞는지"가 정확도를 만든다.

원장 한 줄(dict)의 키 — 쓰는 쪽 DB 칸과 같은 이름:
  kind · confident(bool) · ai_value · final_value(None = 아직 사람 확정 전) · human_changed(bool)
  · ai_verdict('wrong' | 'context_changed' | 'unknown' | None) · verdict_reason
"""
from collections import Counter
from typing import Hashable, Iterable, Optional

VERDICTS = ("wrong", "context_changed", "unknown")


def vote(neighbor_values: Iterable[Hashable]) -> tuple[Optional[Hashable], int, int]:
    """이웃 다수결 — (가장 많은 값, 그 개수, 이웃 수). 동률이면 먼저 나온 값(이웃은 가까운 순으로 준다). 이웃 0이면 (None, 0, 0)."""
    vals = [v for v in neighbor_values if v is not None]
    if not vals:
        return None, 0, 0
    counts = Counter(vals)
    best = max(counts.values())
    top = next(v for v in vals if counts[v] == best)
    return top, best, len(vals)


def is_confident(ai_value: Optional[Hashable], neighbor_values: Iterable[Hashable], *, k: int = 5, need: int = 4) -> bool:
    """확신 = AI 값이 있고, 가까운 이웃 k개 중 need개 이상이 같은 값이며, 그 값이 AI 값과 같다.
    이웃이 k개보다 적으면 확신하지 않는다(빈 컬렉션·새 업무 — 비교할 근거 부족)."""
    if ai_value is None:
        return False
    near = [v for v in neighbor_values if v is not None][:k]
    if len(near) < k:
        return False
    top, n, _ = vote(near)
    return top == ai_value and n >= need


def summarize(rows: Iterable[dict]) -> dict[str, dict]:
    """종류별 원장 집계 — 운영 리포트의 숫자.

    decided = 사람이 확정한 줄(final_value 있음). wrong = 사람이 바꾸고 'AI가 틀렸다'고 답한 줄.
    auto_wrong = 확신(자동 반영 후보)이었는데 틀림 — 1건이라도 있으면 자동 반영 기준을 다시 봐야 한다.
    unknown(바꿨는데 답 없음)·context_changed(조직 개편 등)는 틀림에 넣지 않고 따로 센다 — 섞으면 오답률이 부풀거나 줄어든다.
    """
    out: dict[str, dict] = {}
    for r in rows:
        s = out.setdefault(r["kind"], {"total": 0, "confident": 0, "decided": 0, "changed": 0, "wrong": 0,
                                       "auto_wrong": 0, "context_changed": 0, "unknown": 0, "reasons": Counter(),
                                       "directions": Counter()})
        s["total"] += 1
        s["confident"] += bool(r.get("confident"))
        if r.get("final_value") is None:
            continue
        s["decided"] += 1
        if not r.get("human_changed"):
            continue
        s["changed"] += 1
        v = r.get("ai_verdict") or "unknown"
        if v == "wrong":
            s["wrong"] += 1
            s["auto_wrong"] += bool(r.get("confident"))
            if r.get("verdict_reason"):
                s["reasons"][r["verdict_reason"]] += 1
        else:
            s[v if v in VERDICTS else "unknown"] += 1
        s["directions"][(_key(r.get("ai_value")), _key(r.get("final_value")))] += 1
    for s in out.values():
        s["wrong_rate"] = s["wrong"] / s["decided"] if s["decided"] else None
        s["reasons"] = dict(s["reasons"].most_common())
        s["directions"] = [{"from": a, "to": b, "n": n} for (a, b), n in s["directions"].most_common()]
    return out


def _key(v):
    # 방향 묶기용 — dict 값이면 정렬한 항목 문자열(해시 가능하게)
    if isinstance(v, dict):
        return ",".join(f"{k}={v[k]}" for k in sorted(v))
    return v
