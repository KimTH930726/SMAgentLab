"""POST /api/feedback — "답변 틀림" 신고만 받는다(2026-10-02).

예전엔 👍마다 지식 base_weight +0.1, 질의 상태 해결/미해결 전이, ops_feedback 기록까지 했다. 가중치는 사람 확인 없이
누를수록 오르는 자동 반영이라 뺐고(👎 감점은 10/1 제거), 질의 상태는 답변/지식 공백 둘로 줄였다(#61). 이제 👍는
아무것도 바꾸지 않고, 👎는 본인 대화의 답변일 때만 개선 원장에 신고 1건 — 이 "부작용 없음"을 고정한다. API는 둘 다
201이라 스모크 테스트로는 안 걸린다.

conftest가 service/core를 MagicMock으로 막아 두므로 파일 경로로 실제 모듈을 로드한다(test_admin_category_suggest.py 참고).
"""
import importlib.util as _ilu
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend_dir = Path(__file__).resolve().parent.parent


def _load_real(name: str, rel_path: str):
    spec = _ilu.spec_from_file_location(name, str(_backend_dir / rel_path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stub(name: str, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


# 라우터가 모듈 수준에서 가져오는 것들은 로드하는 동안만 가짜로 — 다른 테스트의 sys.modules를 오염시키지 않게
with patch.dict(sys.modules, {
    "core.database": _stub("core.database", get_conn=MagicMock(), resolve_namespace_id=MagicMock()),
    "core.dependencies": _stub("core.dependencies", get_current_user=MagicMock()),
    "service.improvement": _stub("service.improvement", service=MagicMock()),
}):
    schemas = _load_real("service.feedback.schemas", "service/feedback/schemas.py")
    router = _load_real("_feedback_router_under_test", "service/feedback/router.py")
FeedbackCreate = schemas.FeedbackCreate


class _Conn:
    def __init__(self, owned=True):
        self.sql: list[str] = []
        self.owned = owned

    async def execute(self, sql, *args):
        self.sql.append(" ".join(sql.split()))

    async def fetchval(self, sql, *args):
        return self.owned


@pytest.fixture
def conn(monkeypatch):
    c = _Conn()
    c.opened = 0

    @asynccontextmanager
    async def _get_conn():
        c.opened += 1
        yield c

    monkeypatch.setattr(router, "get_conn", _get_conn)
    monkeypatch.setattr(router, "resolve_namespace_id", AsyncMock(return_value=7))
    signal = AsyncMock()
    monkeypatch.setattr(router.improvement, "record_answer_signal", signal)
    c.signal = signal
    return c


USER = {"id": 3, "part": "p"}


@pytest.mark.asyncio
async def test_thumbs_up_changes_nothing(conn):
    await router.submit_feedback(FeedbackCreate(namespace="ns", question="q", knowledge_id=11, is_positive=True,
                                                message_id=99), USER)
    assert conn.opened == 0 and conn.sql == []
    conn.signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_wrong_goes_to_ledger_only(conn):
    await router.submit_feedback(FeedbackCreate(namespace="ns", question="q", knowledge_id=11, is_positive=False,
                                                message_id=99), USER)
    assert conn.sql == []  # 가중치·질의 상태·피드백 기록 쓰기 없음
    conn.signal.assert_awaited_once_with(7, 99, 3)


@pytest.mark.asyncio
async def test_answer_wrong_on_others_message_is_ignored(conn):
    conn.owned = False
    await router.submit_feedback(FeedbackCreate(namespace="ns", question="q", is_positive=False, message_id=99), USER)
    conn.signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_wrong_save_failure_is_not_hidden(conn):
    """원장 1건이 신고의 유일한 기록 — 저장이 실패하면(None) 201로 숨기지 않고 503(화면에 실패 표시)."""
    conn.signal.return_value = None
    with pytest.raises(router.HTTPException) as e:
        await router.submit_feedback(FeedbackCreate(namespace="ns", question="q", is_positive=False, message_id=99), USER)
    assert e.value.status_code == 503
