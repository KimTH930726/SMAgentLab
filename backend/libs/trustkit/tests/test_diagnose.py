"""진단·리플레이(eval/diagnose) — 성질로: 한 문항 = 한 칸(겹침 0·합 = 틀린 수), 흐름 순서 우선, 리플레이는 거절한 문항만 바뀜."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trustkit.eval.diagnose import CAUSE_KEYS, diagnose, replay  # noqa: E402
from trustkit.eval.gate import per_question  # noqa: E402

LABELS = ["정답", "부분", "오답", "거절"]


def _rand_rows(seed):
    rng = random.Random(seed)
    rows = []
    for i in range(300):
        q = f"Q{i}"
        t = rng.choice(["fact", "navigation", "abstain", "cross"])
        for rep in range(2):
            rows.append({"qid": q, "label": rng.choice(LABELS), "type": t, "error": rng.random() < .02, "rep": rep})
    hits = {f"Q{i}": rng.random() < .9 for i in range(300)}
    neg = {r["qid"] for r in rows if r["type"] == "abstain"}
    return rows, hits, neg


def test_each_failed_question_in_exactly_one_cause():
    for seed in range(5):
        rows, hits, neg = _rand_rows(seed)
        d = diagnose(rows, hits=hits, negative=neg, list_types={"navigation"})
        acc, _ = per_question(rows)
        failed = {q for q, a in acc.items() if a < 0.5}
        all_q = [q for v in d["causes"].values() for q in v]
        assert len(all_q) == len(set(all_q)) == d["n_failed"] == len(failed)   # 겹침 0 · 유실 0
        assert set(all_q) == failed and set(d["causes"]) == set(CAUSE_KEYS)


def test_first_failure_wins():
    rows = [
        {"qid": "E", "label": "오답", "error": True},                 # 오류가 먼저 — 답 없음 질문이어도
        {"qid": "N", "label": "오답"},                                 # 답 없는 질문에 답함
        {"qid": "M", "label": "거절", "type": "fact"},                 # 검색 실패가 거절보다 먼저
        {"qid": "R", "label": "거절", "type": "fact"},
        {"qid": "L", "label": "부분", "type": "navigation"},
        {"qid": "W", "label": "오답", "type": "fact"},
        {"qid": "OK", "label": "정답", "type": "fact"},
    ]
    d = diagnose(rows, hits={"R": True, "L": True, "W": True, "E": True, "OK": True}, negative={"N", "E"}, list_types={"navigation"})
    c = d["causes"]
    assert c["llm_error"] == ["E"] and c["answered_no_answer"] == ["N"] and c["retrieval_miss"] == ["M"]
    assert c["refused_with_context"] == ["R"] and c["list_incomplete"] == ["L"] and c["wrong_with_context"] == ["W"]
    assert d["n"] == 7 and d["n_failed"] == 6


def test_error_only_when_majority_of_reps():
    rows = [{"qid": "H", "label": "오답", "error": True, "rep": 0}, {"qid": "H", "label": "오답", "rep": 1},   # 반반 — 답 품질
            {"qid": "M", "label": "오답", "error": True, "rep": 0}, {"qid": "M", "label": "오답", "error": True, "rep": 1},
            {"qid": "M", "label": "오답", "rep": 2}]
    d = diagnose(rows, hits={"H": True, "M": True}, negative=set())
    assert d["causes"]["llm_error"] == ["M"] and d["causes"]["wrong_with_context"] == ["H"]


def test_replay_changes_only_refused_questions():
    rng = random.Random(3)
    cur = {f"Q{i}": rng.choice([0.0, 0.5, 1.0]) for i in range(200)}
    neg = {f"Q{i}" for i in range(0, 200, 7)}
    refuse = {q for q in cur if rng.random() < .2}
    r = replay(cur, lambda q: q in refuse, neg)
    assert set(r["fixed"]) | set(r["broken"]) <= refuse              # 거절 안 한 문항은 그대로
    assert all(q in neg for q in r["fixed"]) and not any(q in neg for q in r["broken"])
    assert abs((r["after"] - r["before"]) * r["n"] - (sum(1 - cur[q] for q in refuse & neg) - sum(cur[q] for q in refuse - neg))) < 1e-9
    none = replay(cur, lambda q: False, neg)
    assert none["fixed"] == none["broken"] == [] and none["before"] == none["after"]
