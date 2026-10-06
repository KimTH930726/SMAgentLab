"""사내 게이트웨이 스트림 이벤트 처리 (2026-10-06, v2.128).

10/6 실측: 무관 질문에 0.4초 만에 빈 응답 4건 — SSE에 토큰 이벤트가 없었고 로그도 없어 원인을 몰랐다. 파서가 `message`만 읽어
dify 계열의 다른 이벤트(에이전트형 `agent_message`, 검열 `message_replace`, 실패 `error`)는 조용히 버려졌다. 고정하는 것:
agent_message도 토큰 / message_replace는 교체문을 내보냄 / error는 예외(→ 에이전트가 오류 문구) / 토큰 0개면 경고 로그(가려서).
"""
import importlib.util
import json
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_LLM_DIR = Path(__file__).resolve().parent.parent / "service" / "llm"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, str(_LLM_DIR / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_base = _load("_real_llm_base_ev", "base.py")
_gw = _load("_real_llm_gateway_text_ev", "gateway_text.py")
with patch.dict(sys.modules, {"service.llm.base": _base, "service.llm.gateway_text": _gw}):
    inhouse = _load("_real_llm_inhouse_ev", "inhouse.py")


class _Resp:
    def __init__(self, events, status=200):
        self.status_code = status
        self._lines = [f"data: {json.dumps(e, ensure_ascii=False)}" for e in events]

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _Stream:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *a):
        return False


class _Client:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, *a, **k):
        return _Stream(self.resp)


def _provider(monkeypatch, events):
    p = inhouse.InHouseLLMProvider.__new__(inhouse.InHouseLLMProvider)
    p._response_mode, p._timeout, p._chat_url = "streaming", 10, "http://gw/chat"
    p._agent_code, p._agent_id, p._model, p._fixed_conversation_id = "a", None, None, ""
    p._sys_client_id, p._sys_client_secret = "id", "secret"
    monkeypatch.setattr(p, "_build_headers", AsyncMock(return_value={}))
    monkeypatch.setattr(inhouse.httpx, "AsyncClient", lambda **k: _Client(_Resp(events)))
    return p


async def _collect(p):
    return [t async for t in p.generate_stream("ctx", "q", system_prompt="S")]


@pytest.mark.asyncio
async def test_message_and_agent_message_both_stream(monkeypatch):
    p = _provider(monkeypatch, [{"event": "agent_message", "answer": "안녕"}, {"event": "message", "answer": "하세요"},
                                {"event": "message_end"}])
    assert "".join(await _collect(p)) == "안녕하세요"


@pytest.mark.asyncio
async def test_message_replace_without_prior_tokens_yields_replacement(monkeypatch):
    p = _provider(monkeypatch, [{"event": "message_replace", "answer": "검열로 교체된 안내"}, {"event": "message_end"}])
    assert await _collect(p) == ["검열로 교체된 안내"]


@pytest.mark.asyncio
async def test_error_event_raises_so_agent_shows_error(monkeypatch):
    p = _provider(monkeypatch, [{"event": "error", "status": 400, "code": "bad", "message": "user 010-1234-5678 blocked"}])
    with pytest.raises(RuntimeError) as e:
        await _collect(p)
    assert "status=400" in str(e.value) and "010" not in str(e.value)   # 숫자는 가림


@pytest.mark.asyncio
async def test_zero_tokens_logs_warning_with_event_types(monkeypatch, caplog):
    p = _provider(monkeypatch, [{"event": "workflow_started"}, {"event": "node_finished", "outputs": "ip 10.1.2.3"},
                                {"event": "message_end"}])
    with caplog.at_level(logging.WARNING):
        assert await _collect(p) == []
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "토큰 0개" in msg and "node_finished" in msg and "10.1.2.3" not in msg
