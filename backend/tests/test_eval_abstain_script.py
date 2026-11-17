"""scripts/eval_abstain.py(범위 밖 문항 측정, 11/16) — 채점 규칙·이어 하기·요약이 장애를 품질로 섞지 않는가."""
import importlib.util
import json
from pathlib import Path

_P = Path(__file__).resolve().parent.parent / "scripts" / "eval_abstain.py"
_spec = importlib.util.spec_from_file_location("eval_abstain_script", _P)
ea = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ea)


def test_rule_label_refusal_is_correct_and_failure_is_error():
    assert ea.rule_label(None, "관련 지식을 찾지 못했습니다.")["label"] == "정답"
    assert ea.rule_label("llm_failure", "x")["label"] == "오류"
    assert ea.rule_label(None, "  ")["label"] == "오류"
    assert ea.rule_label(None, "MSK 패치는 03:30에 끝납니다") is None   # LLM 채점으로


def test_parse_label_rejects_unknown_labels():
    assert ea.parse_label('{"label": "오답", "reason": "단정"}')["label"] == "오답"
    assert ea.parse_label('{"label": "거절"}')["label"] == "채점실패"   # 이 채점기엔 거절 라벨 없음
    assert ea.parse_label("not json")["label"] == "채점실패"


def test_summary_excludes_failures_and_last_row_wins(tmp_path):
    out = tmp_path / "abs.jsonl"
    rows = [{"qid": "a", "judge": {"label": "채점실패"}}, {"qid": "a", "judge": {"label": "정답"}},   # 재시도 뒤 마지막 줄이 이김
            {"qid": "b", "judge": {"label": "오답"}}, {"qid": "c", "judge": {"label": "오류"}}]
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    last = ea._last_rows(out)
    assert {q: r["judge"]["label"] for q, r in last.items()} == {"a": "정답", "b": "오답", "c": "오류"}
    # 이어 하기 대상에서 빠지는 것 = 장애 아닌 문항만(오류·채점실패는 다시 시도)
    assert {q for q, r in last.items() if r["judge"]["label"] not in ea.RETRY_LABELS} == {"a", "b"}
    s = ea.summarize(list(last.values()))
    assert (s["scored"], s["correct_abstain"], s["confident_wrong"]) == (2, 0.5, 0.5)   # 오류는 분모에서 뺌
