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
            if "ref_table" in sql:   # 참고표·개요 원천 키(v0 #12) — 이 시나리오엔 없음
                return [{"id": 7, "source_key": "table_a+table_b"}]
            if "policy_doc_overview" in sql:
                return [{"id": 3, "source_key": "doc_01"}]
            seen["sql"] = sql
            # DB가 ORDER BY대로 검색 대상(현행)을 먼저 준다 — 뒤의 반려 행이 덮어쓰면 안 된다
            return [{"id": 11, "source_key": "pol_1", "ns": "정책"}, {"id": 12, "source_key": "pol_1", "ns": "정책"}]

    golden, _raw, _skipped = asyncio.run(ea.load_v0_golden(Conn(), extra=[]))
    assert golden[0]["gold_ids"] == {11} and golden[0]["unresolved"] == 0
    assert "ORDER BY" in seen["sql"] and "status = 'active'" in seen["sql"]
    # 시험지에 넣은 실사용 문항도 같은 규칙으로 — 층 "real"(잠근 시험지)
    real = [{"qid": "REAL-1", "verdict": "approved", "layer": "real", "expect": "answer", "query": "실사용 질문?", "doc": "정책",
             "gold": [{"kind": "policy", "source_key": "pol_1"}], "answer_facts": []}]
    golden2, raw2, _ = asyncio.run(ea.load_v0_golden(Conn(), extra=real))
    assert [g["type"] for g in golden2] == ["param", "real"] and raw2["실사용 질문?"]["qid"] == "REAL-1"
    # 골든과 같은 실사용 질문은 건너뛰고 센다 — 예전 규칙대로 멈추면 [시험지에 넣기] 한 번에 답 측정 전체가 멈춘다(리뷰 M1)
    same = [{**real[0], "qid": "REAL-2", "query": "반품 기한?"}]
    golden3, _, skipped3 = asyncio.run(ea.load_v0_golden(Conn(), extra=same))
    assert len(golden3) == 1 and skipped3.get("real_dup") == 1


def test_ref_clarify_rule_only_rewards_expected_clarify():
    """v0 #12 — 참고표 결정 조건 되묻기: 되묻기가 기대(ref_clarify)면 정답, 다른 문항에서 되물으면 오답(답을 못 받음)."""
    base = {"answer": "어느 쪽인가요?", "n_refs_total": 1, "gold_inactive": 0, "ref_clarified": True}
    assert ea._judge_rule({**base, "type": "ref_clarify"})["label"] == "정답"
    for t in ("ref", "overview", "fact", "human"):
        assert ea._judge_rule({**base, "type": t})["label"] == "오답"
    assert ea._judge_rule({**base, "ref_clarified": False, "type": "ref"}) is None   # 되묻지 않았으면 LLM 채점으로


def test_v0_golden_loads_ref_and_overview_questions(tmp_path, monkeypatch):
    """v0 #12 — 참고표·개요 정답 문항도 잰다(정답 = 그 표·개요). 이어 붙인 표 키("a+b")는 어느 쪽이든 같은 표, 영역 없으면 센다."""
    import json as _json
    lines = [{"qid": "참고표-1", "verdict": "approved", "layer": "ref", "query": "적립 몇 번?", "expect": "answer", "doc": "정책",
              "gold": [{"kind": "ref_table", "source_key": "table_b"}], "answer_facts": ["2회"]},
             {"qid": "개요-1", "verdict": "approved", "layer": "overview", "query": "이 정책서는 뭘 다뤄?", "expect": "answer", "doc": "정책",
              "gold": [{"kind": "overview", "source_key": "doc_01"}]},
             {"qid": "개요-2", "verdict": "approved", "layer": "overview", "query": "영역 없는 개요 질문", "expect": "answer", "doc": None,
              "gold": [{"kind": "overview", "source_key": "doc_01"}]}]
    (tmp_path / "a.jsonl").write_text(chr(10).join(_json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    monkeypatch.setattr(ea, "V0_GOLDEN_DIR", tmp_path)

    class Conn:
        async def fetch(self, sql, *a):
            if "ref_table" in sql:
                return [{"id": 7, "source_key": "table_a+table_b"}]
            if "policy_doc_overview" in sql:
                return [{"id": 3, "source_key": "doc_01"}]
            return []

    golden, raw, skipped = asyncio.run(ea.load_v0_golden(Conn(), extra=[]))
    by = {raw[g["query"]]["qid"]: g for g in golden}
    assert by["참고표-1"]["ref_keys"] == {("ref_table", 7)} and by["참고표-1"]["type"] == "ref" and not by["참고표-1"]["gold_ids"]
    assert by["개요-1"]["ref_keys"] == {("overview", 3)}
    assert "개요-2" not in by and skipped.get("overview") == 1   # 조용히 빼지 않고 센다
    # 골든이 이어 붙인 키 통째("a+b")로 적어도 같은 표(10/26 측정에서 19문항이 정답 없음으로 빠진 원인)
    lines2 = [{**lines[0], "qid": "참고표-2", "query": "통째 키 질문", "gold": [{"kind": "ref_table", "source_key": "table_a+table_b"}]}]
    (tmp_path / "a.jsonl").write_text(chr(10).join(_json.dumps(x, ensure_ascii=False) for x in lines2), encoding="utf-8")
    g2, _, _ = asyncio.run(ea.load_v0_golden(Conn(), extra=[]))
    assert g2[0]["ref_keys"] == {("ref_table", 7)} and g2[0]["unresolved"] == 0

