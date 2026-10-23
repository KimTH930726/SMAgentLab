"""헤드라인 요약(eval/summary) — 성질로: 순서·중복 무관, 분모 일치, 층별 합 = 전체, 검색 맞고 답 틀림 ⊆ 양쪽 문항."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trustkit.eval.summary import answer_summary, golden_version, retrieval_ok_answer_wrong, retrieval_summary  # noqa: E402


def test_golden_version_is_a_set_fingerprint():
    qs = [f"Q-{i}" for i in range(50)]
    v = golden_version(qs)
    sh = qs[:] + qs[:5]
    random.Random(1).shuffle(sh)
    assert golden_version(sh) == v and v.startswith("50:")
    assert golden_version(qs[:-1]) != v                    # 문항 하나 빠지면 다른 세트
    assert golden_version(qs[:-1] + ["Q-x"]) != v          # 수가 같아도 다른 문항이면 다른 값
    assert golden_version(["", None, "A"]) == golden_version(["A"])


def test_retrieval_summary_counts_add_up():
    rng = random.Random(7)
    rows = [{"qid": f"Q{i}", "hit": rng.random() < .8, "top1": rng.random() < .6, "type": rng.choice("abc")} for i in range(200)]
    for r in rows:
        r["top1"] = r["top1"] and r["hit"]
    s = retrieval_summary(rows + rows[:10])                 # 같은 qid 반복 — 한 번만 센다
    assert s["n"] == 200 and s["hit"] == sum(r["hit"] for r in rows)
    assert sum(x["n"] for x in s["by_type"].values()) == s["n"]
    assert sum(x["hit"] for x in s["by_type"].values()) == s["hit"]
    assert s["recall"] == s["hit"] / 200
    assert retrieval_summary([])["recall"] is None


def test_answer_summary_per_question_average_and_labels():
    rows = [{"qid": "A", "label": "정답", "type": "fact", "rep": 0}, {"qid": "A", "label": "오답", "type": "fact", "rep": 1},
            {"qid": "B", "label": "정답", "type": "list", "rep": 0}, {"qid": "B", "label": "정답", "type": "list", "rep": 1},
            {"qid": "C", "label": "부분", "type": "list", "rep": 0}, {"qid": "C", "label": "오답", "type": "list", "rep": 1}]
    s = answer_summary(rows)
    assert s["n"] == 3 and s["reps"] == 2
    assert abs(s["accuracy"] - (0.5 + 1 + 0) / 3) < 1e-9     # 회차 평균의 평균
    assert s["labels"] == {"정답": 3, "오답": 2, "부분": 1}   # 회차까지 센 분포 — 합 = 행 수
    assert s["by_type"]["list"] == {"n": 2, "accuracy": 0.5}
    assert s["lo"] is not None and s["lo"] <= s["accuracy"] <= s["hi"]
    assert answer_summary([])["accuracy"] is None


def test_retrieval_ok_answer_wrong_only_hits_with_majority_wrong():
    hits = {"A": True, "B": True, "C": False, "D": True}
    rows = [{"qid": "A", "label": "오답"}, {"qid": "A", "label": "오답"},
            {"qid": "B", "label": "정답"}, {"qid": "B", "label": "오답"},      # 반반 = 과반 틀림 아님
            {"qid": "C", "label": "오답"},                                       # 검색부터 못 찾음 — 답 문제 아님
            {"qid": "E", "label": "오답"}]                                       # 검색 결과에 없음
    assert retrieval_ok_answer_wrong(hits, rows) == ["A"]
