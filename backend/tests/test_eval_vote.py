"""채점 다수결(2026-10-12) — 같은 답·같은 기준인데 판정이 바뀌어(실측 2/64) ±2건 차이를 개선인지 잡음인지 못 가렸다."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("_eval_answers", str(Path(__file__).resolve().parent.parent / "scripts" / "eval_answers.py"))
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)


def test_majority_and_conservative_tie():
    assert E.vote(["정답", "정답", "부분"]) == "정답"
    assert E.vote(["부분", "정답", "부분"]) == "부분"
    assert E.vote(["정답", "부분"]) == "부분"            # 동률이면 낮은 쪽 — 흔들리는 문항을 정답으로 세지 않는다
    assert E.vote(["정답", "오답", "부분"]) == "오답"
    assert E.vote(["거절"]) == "거절"


def test_rule_judgements_without_llm():
    """빈 답·거절·정답 정책이 전부 반려/폐기(기준 없음)는 LLM 없이 규칙으로 — 기준 없이 채점한 임의 라벨이 분모에 들어가지 않게."""
    assert E._judge_rule({"answer": "", "error": "ReadTimeout"})["label"] == "오류"
    assert E._judge_rule({"answer": E.NO_KNOWLEDGE})["label"] == "거절"
    assert E._judge_rule({"answer": "답", "n_refs_total": 0, "gold_inactive": 2})["label"] == "판정불가"
    assert E._judge_rule({"answer": "답", "n_refs_total": 3, "gold_inactive": 0}) is None


def test_reuse_hash_covers_every_llm_input():
    """--reuse는 LLM 입력이 전부 같을 때만 — 질문 문구·모델·문맥·프롬프트·variant 중 하나라도 다르면 해시가 다르다."""
    base = dict(variant="after", system_prompt="S", context="C", question="Q", model="InHouse:m1")
    h = E.ctx_hash(**base)
    assert h == E.ctx_hash(**base)
    for k, v in (("variant", "before"), ("system_prompt", "S2"), ("context", "C2"), ("question", "Q2"), ("model", "InHouse:m2")):
        assert E.ctx_hash(**{**base, k: v}) != h, k


def test_unscorable_excluded_from_denominator():
    rows = [{"qid": "a", "type": "t", "judge": {"label": "정답"}}, {"qid": "b", "type": "t", "judge": {"label": "판정불가"}}]
    assert [r["qid"] for r in E._scored(rows)] == ["a"]
    assert E._aggregate(rows)["total_n"] == 1
    assert "전체 1문항" in E._summary(rows) and "판정불가 1" in E._summary(rows)


def test_nav_prompt_lists_every_gold_name():
    """목록형은 정답 정책 이름 전부를 기준으로(본문은 15개까지) — 예전엔 15개만 보여 "빠짐없는지"를 못 봤다."""
    r = {"type": "navigation", "question": "q", "answer": "a", "n_refs_total": 20, "expected": "x",
         "ref_names": [f"분류 > 정책{i}" for i in range(20)],
         "refs": [{"category": "분류", "policy": f"정책{i}", "body": "b"} for i in range(15)]}
    p = E._judge_prompt(r)
    assert "정답 정책 목록 — 20개" in p and "정책19" in p and "[기대 답]" not in p
    assert not E._is_nav({**r, "ref_names": []}) and "[기준 — 정답 정책 원문]" in E._judge_prompt({**r, "ref_names": []})
