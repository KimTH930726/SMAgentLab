"""지식 답 평가 판정 규칙(리뷰 10/19) — 채점 서버 장애는 라벨로 섞지 않고 중단, 형식이 깨진 응답만 채점실패, 규칙 판정은 eval_answers와 같게."""
import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_spec = importlib.util.spec_from_file_location(
    "_eval_kans", str(Path(__file__).resolve().parent.parent / "scripts" / "eval_knowledge_answers.py"))
K = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(K)


def test_rule_labels_match_eval_answers():
    assert K.rule_label("llm_failure", "게이트웨이 거부문", "기준")["label"] == "오류"
    assert K.rule_label(None, "", "기준")["label"] == "오류"                      # 빈 답 = 오류(eval_answers와 같음)
    assert K.rule_label(None, "설명 … 관련 지식을 찾지 못했습니다", "기준")["label"] == "거절"   # 답 어디에 있어도
    assert K.rule_label(None, "답", "")["label"] == "판정불가"                     # 정답 조각이 활성 상태로 없음
    assert K.rule_label(None, "답", "기준") is None


def _client(responses):
    c = MagicMock()
    c.post = AsyncMock(side_effect=responses)
    return c


def _resp(content):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value={"choices": [{"message": {"content": content}}]})
    return r


def test_judge_server_down_raises_instead_of_label(monkeypatch):
    monkeypatch.setattr(K.asyncio, "sleep", AsyncMock())
    with pytest.raises(K.JudgeUnavailable):
        asyncio.run(K.judge(_client([ConnectionError("x")] * 3), "m", "q", "ref", "ans"))


def test_judge_retries_then_succeeds_and_parse_failure_is_label(monkeypatch):
    monkeypatch.setattr(K.asyncio, "sleep", AsyncMock())
    ok = asyncio.run(K.judge(_client([ConnectionError("x"), _resp('{"label": "정답", "reason": "r"}')]), "m", "q", "ref", "ans"))
    assert ok["label"] == "정답"
    bad = asyncio.run(K.judge(_client([_resp("형식 없는 응답")]), "m", "q", "ref", "ans"))
    assert bad["label"] == "채점실패"


def test_unscorable_wins_over_refusal_and_error():
    """정답 조각이 없으면 답이 거절·오류여도 판정불가 — 정상 답만 빠지고 거절만 0점으로 남는 쏠림 방지(재리뷰 10/19)."""
    assert K.rule_label(None, "… 관련 지식을 찾지 못했습니다", "")["label"] == "판정불가"
    assert K.rule_label("llm_failure", "", "")["label"] == "판정불가"


def test_read_rows_skips_broken_last_line(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"qid": "a", "rep": 0}\n{"qid": "b", "re', encoding="utf-8")
    assert [r["qid"] for r in K._read_rows(p)] == ["a"]
