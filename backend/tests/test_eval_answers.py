"""최종 답변 정확도 집계 (2026-10-07) — 이력 화면이 읽는 숫자(라벨별·유형별·"근거 있는데 틀림")와 채점 규칙의 고정 부분.

LLM 채점 자체는 테스트하지 않는다(로컬 LLM). 여기선 LLM 없이 정해지는 것만: 빈 답·오류는 "오류", 지식 없음 문구는 "거절"(LLM에
맡기지 않음), 집계가 문항 수와 맞는지.
"""
import asyncio
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_eval_answers", str(Path(__file__).resolve().parent.parent / "scripts" / "eval_answers.py"))
ea = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ea)


def _row(qid, t, label, gic=True):
    return {"qid": qid, "type": t, "gold_in_context": gic, "judge": {"label": label, "reason": ""}}


def test_aggregate_counts_and_retrieval_ok_but_wrong():
    rows = [_row("a", "param", "정답"), _row("b", "param", "오답"), _row("c", "narrative", "부분"),
            _row("d", "narrative", "거절", gic=False)]
    agg = ea._aggregate(rows)
    assert agg["total_n"] == 4 and agg["counts"] == {"정답": 1, "오답": 1, "부분": 1, "거절": 1}
    assert agg["by_type"] == {"param": {"정답": 1, "오답": 1}, "narrative": {"부분": 1, "거절": 1}}
    assert (agg["retrieval_ok"], agg["retrieval_ok_but_wrong"]) == (3, 2)   # 근거가 문맥에 있었는데 정답 아님


def test_rule_based_labels_do_not_call_llm():
    class NoCall:
        async def post(self, *a, **k):
            raise AssertionError("LLM을 부르면 안 됨")
    base = {"question": "q", "refs": [], "n_refs_total": 0}
    assert asyncio.run(ea._judge_one(NoCall(), "m", {**base, "answer": "", "error": "ReadTimeout"}))["label"] == "오류"
    assert asyncio.run(ea._judge_one(NoCall(), "m", {**base, "answer": "관련 지식을 찾지 못했습니다", "error": None}))["label"] == "거절"


def test_judge_prompt_marks_truncated_reference_list():
    r = {"question": "재고 정책 전부", "expected": None, "answer": "x", "n_refs_total": 30,
         "refs": [{"category": "재고", "policy": f"p{i}", "body": "b"} for i in range(15)]}
    p = ea._judge_prompt(r)
    assert "30개 중 15개만 표시" in p and "[기대 답]" not in p


def test_v0_golden_prefers_searchable_row_for_same_source_key(tmp_path, monkeypatch):
    """같은 원천 키에 반려 행과 현행 행이 같이 있으면 현행(검색 대상)을 정답으로 — 아무 행이나 잡으면 맞는 답이 gold_inactive로 빠졌다(10/21 리뷰 L2)."""
    import json as _json
    (tmp_path / "a.jsonl").write_text(_json.dumps({
        "qid": "q1", "verdict": "approved", "layer": "param", "query": "반품 기한?", "expect": "answer", "doc": "정책",
        "gold": [{"kind": "policy", "source_key": "pol_1"}]}, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(ea, "V0_GOLDEN_DIR", tmp_path)
    seen = {}

    class Conn:
        async def fetch(self, sql, *a):
            seen["sql"] = sql
            # DB가 ORDER BY대로 검색 대상(현행)을 먼저 준다 — 뒤의 반려 행이 덮어쓰면 안 된다
            return [{"id": 11, "source_key": "pol_1", "ns": "정책"}, {"id": 12, "source_key": "pol_1", "ns": "정책"}]

    golden, _raw, _skipped = asyncio.run(ea.load_v0_golden(Conn()))
    assert golden[0]["gold_ids"] == {11} and golden[0]["unresolved"] == 0
    assert "ORDER BY" in seen["sql"] and "status = 'active'" in seen["sql"]
