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
async def test_message_replace_sends_replace_signal_and_drops_held_digits(monkeypatch):
    """교체는 이어 붙이지 않고 신호로 — 원복 대기 중이던 숫자 조각("90")도 버린다(리뷰 2026-10-07)."""
    p = _provider(monkeypatch, [{"event": "message", "answer": "주기는 90"}, {"event": "message_replace", "answer": "검열로 교체된 안내"},
                                {"event": "message_end"}])
    out = await _collect(p)
    assert out == ["주기는 ", inhouse.REPLACE_PREFIX + "검열로 교체된 안내"]


@pytest.mark.asyncio
async def test_workflow_text_chunk_streams(monkeypatch):
    """워크플로형 앱은 text_chunk(data.text)로 토큰을 보낸다 — 예전엔 버려져 빈 답이 됐을 경로."""
    p = _provider(monkeypatch, [{"event": "workflow_started"}, {"event": "text_chunk", "data": {"text": "안녕"}},
                                {"event": "text_chunk", "data": {"text": "하세요"}}, {"event": "message_end"}])
    assert "".join(await _collect(p)) == "안녕하세요"


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


# ── 사용량 기록(2026-10-12 리뷰) — 성공·실패·사용자 중단을 구분하고, 토큰 발급 실패도 남긴다 ──

@pytest.mark.asyncio
async def test_usage_logged_once_per_stream_with_ok_flag(monkeypatch):
    log = []
    monkeypatch.setattr(inhouse, "_log_usage", lambda **k: log.append(k))
    p = _provider(monkeypatch, [{"event": "message", "answer": "안녕"},
                                {"event": "message_end", "metadata": {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}}])
    await _collect(p)
    assert len(log) == 1 and log[0]["ok"] is True and log[0]["output_chars"] == 2
    assert log[0]["meta"]["usage"]["prompt_tokens"] == 10


@pytest.mark.asyncio
async def test_usage_user_abort_is_not_a_failure(monkeypatch):
    """답변 중 멈춤(소비자가 스트림을 닫음)은 실패로 세지 않는다 — ok=None."""
    log = []
    monkeypatch.setattr(inhouse, "_log_usage", lambda **k: log.append(k))
    p = _provider(monkeypatch, [{"event": "message", "answer": "첫 조각 "}, {"event": "message", "answer": "둘째"},
                                {"event": "message_end"}])
    gen = p.generate_stream("ctx", "q", system_prompt="S")
    await gen.__anext__()
    await gen.aclose()
    assert len(log) == 1 and log[0]["ok"] is None


@pytest.mark.asyncio
async def test_usage_gateway_error_is_a_failure(monkeypatch):
    log = []
    monkeypatch.setattr(inhouse, "_log_usage", lambda **k: log.append(k))
    p = _provider(monkeypatch, [{"event": "error", "status": 500, "code": "x", "message": "boom"}])
    with pytest.raises(Exception):
        await _collect(p)
    assert len(log) == 1 and log[0]["ok"] is False


@pytest.mark.asyncio
async def test_usage_logged_when_token_issue_fails(monkeypatch):
    """토큰 발급(_build_headers) 실패도 기록 — 예전엔 try 밖이라 인증 장애가 사용량 화면에 안 보였다."""
    log = []
    monkeypatch.setattr(inhouse, "_log_usage", lambda **k: log.append(k))
    p = _provider(monkeypatch, [])
    monkeypatch.setattr(p, "_build_headers", AsyncMock(side_effect=RuntimeError("토큰 발급 실패")))
    with pytest.raises(RuntimeError):
        await p.generate_once("질문")
    with pytest.raises(RuntimeError):
        await p.generate("ctx", "q")
    assert [x["method"] for x in log] == ["once", "generate"] and all(x["ok"] is False for x in log)


@pytest.mark.asyncio
async def test_usage_blocking_cancel_is_abort_not_failure(monkeypatch):
    """요청 도중 취소(사용자 중단)는 blocking 경로에서도 실패가 아니라 중단(재리뷰 2026-10-12)."""
    import asyncio
    log = []
    monkeypatch.setattr(inhouse, "_log_usage", lambda **k: log.append(k))
    p = _provider(monkeypatch, [])

    class _Cancel(_Client):
        async def post(self, *a, **k):
            raise asyncio.CancelledError()
    monkeypatch.setattr(inhouse.httpx, "AsyncClient", lambda **k: _Cancel(None))
    with pytest.raises(asyncio.CancelledError):
        await p.generate_once("질문")
    with pytest.raises(asyncio.CancelledError):
        await p.generate("ctx", "q")
    assert [x["ok"] for x in log] == [None, None]
