"""질의 기록 상태 판정 — 답변(pending) / 지식 공백(no_knowledge) 둘뿐 (2026-10-02, v2.121).

공백 = 임계값을 넘는 근거가 없었거나(근거 없이 답했으면 환각 위험이라 답변으로 세지 않음) "관련 지식을 찾지 못했습니다"가
뜬 것. LLM 연결 실패는 통계 밖 system_error로. 한때 문구 기준으로만 좁혔다가 "근거 없으면 공백이 맞다"는 지적으로 되돌렸고,
그 과정에서 캐시 응답이 정책 근거를 못 보고 공백으로 잘못 세지던 버그도 고쳤다(agent.py) — 이 판정을 고정한다.

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


def _stub(name: str, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


with patch.dict(sys.modules, {
    "core.database": _stub("core.database", get_conn=MagicMock(), resolve_namespace_id=MagicMock()),
    "service.chat": _stub("service.chat", memory=MagicMock()),
    "agents.knowledge_rag.knowledge.retrieval": _stub("agents.knowledge_rag.knowledge.retrieval", RetrievalResult=object),
}):
    _spec = _ilu.spec_from_file_location("_chat_helpers_under_test", str(_backend_dir / "service/chat/helpers.py"))
    helpers = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(helpers)


@pytest.fixture
def inserted(monkeypatch):
    rows: list[tuple] = []

    class _Conn:
        async def fetchrow(self, sql, *args):
            rows.append(args)
            return {"id": len(rows)}

    @asynccontextmanager
    async def _get_conn():
        yield _Conn()

    monkeypatch.setattr(helpers, "get_conn", _get_conn)
    monkeypatch.setattr(helpers, "resolve_namespace_id", AsyncMock(return_value=7))
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, had_context, expected", [
    ("장바구니는 최대 20개입니다.", True, "pending"),
    ("서울은 흐리고 비가 옵니다.", False, "no_knowledge"),                     # 근거 없이 답함 → 공백
    ("관련 지식을 찾지 못했습니다.", True, "no_knowledge"),
    ("정리하면 ... 관련 지식을 찾지 못했습니다.", True, "no_knowledge"),        # 문구가 뒤쪽에 섞여도
])
async def test_status(inserted, answer, had_context, expected):
    await helpers.create_query_log("ns", "q", answer, None, 1, user_id=2, had_context=had_context)
    assert inserted[-1][3] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    "", None, helpers.LLM_UNAVAILABLE_MSG, helpers.LLM_EMPTY_MSG,
    "⚠️ 요청하신 내용에 다음과 같은 민감 정보가 포함되어 있어 응답을 제공할 수 없습니다. (IP: 1.2.3.4)",
])
async def test_llm_failure_is_system_error(inserted, answer):
    """LLM 연결 실패·게이트웨이 민감정보 거부는 답변·공백이 아닌 system_error — 통계에선 빠지고 장애 건수로만 보인다(#64·#68)."""
    await helpers.create_query_log("ns", "q", answer, None, 1, had_context=True)
    assert inserted[-1][3] == "system_error"


def test_refusal_marker_only_at_start():
    """답변 본문 뒤쪽에서 같은 문구를 인용한 정상 답은 거부로 보지 않는다(앞 200자만 본다)."""
    assert helpers.is_llm_failure("⚠️ 요청하신 내용에 다음과 같은 민감 정보가 포함되어 있어")
    assert not helpers.is_llm_failure("가" * 300 + "요청하신 내용에 다음과 같은 민감 정보가 포함되어")
    assert not helpers.is_llm_failure("정상 답변입니다.")
