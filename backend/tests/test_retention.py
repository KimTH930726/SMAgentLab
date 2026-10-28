"""데이터 보관 주기(10/28) — 줄마다 주기·조건이 맞나, 지운 지식 중 다른 기록이 가리키는 것은 남기나, 한 줄 실패가 나머지를 막지 않나."""
import importlib.util as _ilu
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# conftest가 service 패키지를 가짜로 둔다 — retention이 가져오는 ledger는 경로로 미리 등록(시험에선 다시 가짜로 바꿈)
sys.modules.setdefault("service.decision", MagicMock())
sys.modules.setdefault("service.decision.ledger", MagicMock())
_spec = _ilu.spec_from_file_location("retention_under_test", str(Path(__file__).resolve().parent.parent / "service" / "retention.py"))
rt = _ilu.module_from_spec(_spec)
sys.modules["retention_under_test"] = rt
_spec.loader.exec_module(rt)


@pytest.fixture(autouse=True)
def _chat_stub(monkeypatch):
    """채팅 대화 정리는 가짜로 — 시험 동안만(다른 시험의 sys.modules를 바꾸지 않게, 리뷰 L4)"""
    helpers = MagicMock(cleanup_old_conversations=AsyncMock(return_value=0))
    monkeypatch.setitem(sys.modules, "service.chat", MagicMock(helpers=helpers))
    monkeypatch.setitem(sys.modules, "service.chat.helpers", helpers)


def _conn(execute):
    conn = MagicMock(execute=execute)
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    return conn


@pytest.mark.asyncio
async def test_each_step_days_and_guards(monkeypatch):
    calls = []

    async def ex(sql, *a):
        calls.append((sql, a))
        return "DELETE 0"
    conn = _conn(ex)
    monkeypatch.setattr(rt, "get_conn", MagicMock(return_value=conn))
    monkeypatch.setattr(rt, "ledger", SimpleNamespace(purge_stale_open=AsyncMock(return_value=0)))
    out = await rt.run_once()
    dec = next(sql for sql, a in calls if "ops_ai_decision" in sql)
    assert "final_value" not in dec   # 확정·미확정 모두 180일(답 없이 떠난 되묻기도 — 리뷰 L1)
    kn = next(sql for sql, a in calls if "rag_knowledge k" in sql)
    for guard in ("status = 'deleted'", "d.status = 'active'", "supersedes_id = k.id", "resolved_knowledge_id = k.id", "ops_improvement_item", "eval_question_candidate"):
        assert guard in kn
    assert sorted(a for _, a in calls) == sorted([(rt.DECISION_DAYS,), (rt.DELETED_KNOWLEDGE_DAYS,), (rt.QUERY_LOG_DAYS,)])
    assert (rt.DECISION_DAYS, rt.DELETED_KNOWLEDGE_DAYS, rt.QUERY_LOG_DAYS) == (180, 180, 365)
    assert set(out) == {"decisions", "deleted_knowledge", "query_log", "conversations", "open_decisions"}


@pytest.mark.asyncio
async def test_one_failure_does_not_stop_others(monkeypatch):
    async def ex(sql, *a):
        if "rag_knowledge k" in sql:
            raise RuntimeError("boom")
        return "DELETE 2"
    monkeypatch.setattr(rt, "get_conn", MagicMock(return_value=_conn(ex)))
    monkeypatch.setattr(rt, "ledger", SimpleNamespace(purge_stale_open=AsyncMock(return_value=1)))
    out = await rt.run_once()
    assert out == {"decisions": 2, "deleted_knowledge": None, "query_log": 2, "conversations": 0, "open_decisions": 1}
