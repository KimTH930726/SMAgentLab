"""AI 판단 원장 순수 계산 — 확신 규칙·집계. 사례보다 성질로: 확신이면 반드시 AI 값 = 이웃 다수, 집계 합이 줄 수와 맞음."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trustkit.decision.ledger import is_confident, summarize, vote  # noqa: E402


def test_vote_basic_and_ties():
    assert vote([]) == (None, 0, 0)
    assert vote([None, None]) == (None, 0, 0)
    assert vote(["a", "b", "a"]) == ("a", 2, 3)
    assert vote(["b", "a"])[0] == "b"   # 동률 → 가까운 쪽(먼저 나온 값)


def test_confident_needs_both_signals():
    assert is_confident("a", ["a", "a", "a", "a", "b"])
    assert not is_confident("b", ["a", "a", "a", "a", "b"])      # 이웃은 확신, AI가 다름
    assert not is_confident("a", ["a", "a", "a", "b", "b"])      # AI 일치, 이웃 3/5
    assert not is_confident(None, ["a"] * 5)                     # AI 실패
    assert not is_confident("a", ["a"] * 4)                      # 이웃 부족(빈 컬렉션)
    assert is_confident("a", ["a"] * 4 + ["b"] + ["b"] * 10)     # k개만 본다(가까운 순)


def test_confident_property_random():
    rnd = random.Random(7)
    for _ in range(2000):
        nb = [rnd.choice("abc") for _ in range(rnd.randint(0, 8))]
        ai = rnd.choice(["a", "b", "c", None])
        if is_confident(ai, nb):
            near = nb[:5]
            assert len(near) == 5 and near.count(ai) >= 4


def test_summarize_counts_and_separation():
    rows = [
        {"kind": "loc", "confident": True, "ai_value": "A", "final_value": "A", "human_changed": False},
        {"kind": "loc", "confident": True, "ai_value": "A", "final_value": "B", "human_changed": True, "ai_verdict": "wrong",
         "verdict_reason": "업무 이름 없음"},
        {"kind": "loc", "confident": False, "ai_value": "A", "final_value": "C", "human_changed": True, "ai_verdict": "context_changed"},
        {"kind": "loc", "confident": False, "ai_value": "A", "final_value": "C", "human_changed": True},   # 답 없음 → unknown
        {"kind": "loc", "confident": False, "ai_value": "A", "final_value": None},                          # 아직 확정 전
        {"kind": "clarify", "confident": False, "ai_value": {"options": 3}, "final_value": {"picked": 2}, "human_changed": True,
         "ai_verdict": "wrong"},
    ]
    s = summarize(rows)
    loc = s["loc"]
    assert (loc["total"], loc["confident"], loc["decided"], loc["changed"]) == (5, 2, 4, 3)
    assert (loc["wrong"], loc["auto_wrong"], loc["context_changed"], loc["unknown"]) == (1, 1, 1, 1)
    assert loc["wrong"] + loc["context_changed"] + loc["unknown"] == loc["changed"]
    assert loc["wrong_rate"] == 1 / 4
    assert loc["reasons"] == {"업무 이름 없음": 1}
    assert {"from": "A", "to": "C", "n": 2} in loc["directions"]
    assert s["clarify"]["directions"][0]["from"] == "options=3"   # dict 값도 묶인다
