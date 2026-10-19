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


def test_answer_items_from_table_or_bullets():
    table = "설명\n| 정책명 | 한 줄 |\n|---|---|\n| A | a |\n| B | b |\n\n궁금하면 다시 물어보세요"
    assert E.answer_items(table) == ["| A | a |", "| B | b |"]
    assert E.answer_items("- A: a\n- B: b\n1. C: c\n본문") == ["- A: a", "- B: b", "1. C: c"]
    assert E.answer_items("그냥 문장") == []


def test_nav_reconcile_follows_countable_facts():
    """목록형 라벨은 셀 수 있는 대조표를 따른다 — 빠지면 정답→부분, 목록 밖 항목이 있으면 정답→오답, 부분을 정답으로 올리지는 않음."""
    ans = "| 정책 | 요약 |\n|---|---|\n| A | a |\n| B | b |"
    full = {"matches": {"1": "A", "2": "B"}, "label": "정답", "reason": "r"}
    assert E._nav_reconcile(full, 2, ans)[0] == "정답"
    assert E._nav_reconcile({**full, "matches": {"1": "A", "2": None}}, 2, ans)[0] == "부분"
    extra_ans = ans + "\n| 다른분류 | x |"
    assert E._nav_reconcile(full, 2, extra_ans)[:1] == ("오답",) and E._nav_reconcile(full, 2, extra_ans)[3] == 1
    assert E._nav_reconcile({**full, "label": "부분"}, 2, ans)[0] == "부분"


def test_clarify_rows_are_scored_not_excluded():
    """재질문 문항은 "전부 보기" 답으로 채점 — 분모에서 빼지 않는다(빼면 재질문 규칙이 넓어질수록 수치가 부푼다, 리뷰 2026-10-12)."""
    assert E._judge_rule({"clarified": True, "answer": "답", "n_refs_total": 3}) is None
    rows = [{"qid": "a", "type": "t", "judge": {"label": "정답"}}, {"qid": "b", "type": "t", "judge": {"label": "채점실패"}}]
    assert E._aggregate(rows)["total_n"] == 2 and "채점실패 1" in E._summary(rows)   # 채점기 출력 깨짐은 분모에 남는다


def test_answer_items_multi_table_and_bullet_headings():
    """업무별로 묶은 답(표 여러 개) — 표마다 머리 행을 빼고, 글머리는 묶음 제목을 뺀다(리뷰 2026-10-12: 맞힌 답이 오답으로 내려갔다)."""
    two = ("**배송 > 공통**\n| 정책명 | 요약 |\n|---|---|\n| A | a |\n\n**클레임 > 공통**\n| 정책명 | 요약 |\n|---|---|\n| B | b |")
    assert E.answer_items(two) == ["| A | a |", "| B | b |"]
    bullets = "1. 배송 > 공통\n- A — a\n2. 클레임 > 공통\n- B: b\n- 세부 조건은 다시 물어보세요"
    assert E.answer_items(bullets) == ["- A — a", "- B: b"]


def test_extra_counts_items_beyond_gold_only_when_nothing_missing():
    ans = "| 정책 | 요약 |\n|---|---|\n| A | a |\n| B | b |"
    dup = {"matches": {"1": "A", "2": "A"}, "label": "정답", "reason": "r"}   # 같은 행을 두 번 대조해도 목록 밖 항목으로 안 셈
    assert E._nav_reconcile(dup, 2, ans)[0] == "정답" and E._nav_reconcile(dup, 2, ans)[3] == 0


def test_indented_detail_lines_are_not_items():
    ans = "- A — 요약\n  - 기간: 7일\n  - 대상: 전체\n- B — 요약"
    assert E.answer_items(ans) == ["- A — 요약", "- B — 요약"]



def test_reuse_per_repetition():
    """--reuse는 회차마다 같은 회차의 이전 답을 — 예전엔 0회차만 재사용해 1회차는 문맥이 같아도 매번 새로 생성(10/13)."""
    ok = {"ctx_hash": "h", "answer": "a", "error": None}
    assert E.reusable(ok, "h", False)
    assert not E.reusable(ok, "other", False)                       # 입력이 바뀜
    assert not E.reusable({**ok, "error": "HTTPStatusError"}, "h", False)
    assert not E.reusable({**ok, "answer": ""}, "h", False)
    assert not E.reusable(ok, "h", True)                            # 근거 없음 판정은 LLM 안 부름
    src = (Path(__file__).resolve().parent.parent / "scripts" / "eval_answers.py").read_text(encoding="utf-8")
    assert '(r["qid"], r.get("rep", 0))' in src and "prev.get((qid, k))" in src and "and k == 0" not in src
