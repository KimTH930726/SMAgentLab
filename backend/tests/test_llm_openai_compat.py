"""OpenAI 호환 LLM(로컬 vLLM Qwen, dev E2E용, 2026-10-20) — 요청 본문·생각 과정 걸러내기(스트림 포함)·주소 없으면 거부."""
import importlib.util
import random
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# conftest가 service 패키지를 MagicMock으로 두므로 파일로 직접 불러온다(부모 클래스 base도 진짜로 — 가짜면 상속이 MagicMock이 된다).
# sys.modules는 이 파일을 불러오는 동안만 바꾼다 — 전역으로 바꾸면 뒤 테스트가 순서에 따라 달라진다(리뷰 10/20 L5)
_root = Path(__file__).resolve().parents[1]
_bspec = importlib.util.spec_from_file_location("service.llm.base", str(_root / "service/llm/base.py"))
_base = importlib.util.module_from_spec(_bspec)
with patch.dict(sys.modules, {"service.llm.base": _base}):
    _bspec.loader.exec_module(_base)
    _spec = importlib.util.spec_from_file_location("openai_compat_under_test", str(_root / "service/llm/openai_compat.py"))
    oc = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(oc)


def _settings(url):
    return SimpleNamespace(openai_compat_base_url=url, openai_compat_model="", openai_compat_timeout=30)


def test_requires_base_url(monkeypatch):
    monkeypatch.setattr(oc, "settings", _settings(""))
    with pytest.raises(ValueError):
        oc.OpenAICompatProvider()


def test_body_turns_off_thinking_and_is_deterministic(monkeypatch):
    monkeypatch.setattr(oc, "settings", _settings("http://x/v1/"))
    p = oc.OpenAICompatProvider()
    assert p._base_url == "http://x/v1"
    b = p._body("m", [{"role": "user", "content": "q"}], 100, False)
    assert b["temperature"] == 0 and b["chat_template_kwargs"] == {"enable_thinking": False} and b["max_tokens"] == 100


def test_think_block_removed_closed_and_truncated():
    assert oc._THINK_RE.sub("", "<think>고민\n중</think>\n답입니다").strip() == "답입니다"
    assert oc._THINK_RE.sub("", "앞<think>잘린 생각").strip() == "앞"          # max_tokens로 안 닫힌 경우(L2)


def _stream(tokens):
    f = oc.ThinkFilter()
    return "".join(f.feed(t) for t in tokens) + f.flush()


def test_stream_filter_tags_in_one_token_and_split_across_tokens():
    assert _stream(["<think>x</think>답"]) == "답"                       # 한 토큰에 열고 닫음(L1)
    assert _stream(["앞<thi", "nk>생각</thi", "nk>뒤"]) == "앞뒤"          # 경계에 걸친 태그
    assert _stream(["a<", "b"]) == "a<b"                                 # 태그 아닌 '<'는 그대로


def test_stream_filter_property_random_chunking():
    """같은 글을 어떻게 쪼개 보내도 결과는 한 번에 거른 것과 같다."""
    rng = random.Random(3)
    texts = ["처음<think>생각1</think>중간<think>생각2</think>끝", "생각 없음 <b>굵게</b>", "<think>안 닫힘"]
    for t in texts:
        want = oc._THINK_RE.sub("", t)
        for _ in range(200):
            cuts = sorted(rng.sample(range(1, len(t)), rng.randint(0, min(6, len(t) - 1))))
            parts = [t[a:b] for a, b in zip([0, *cuts], [*cuts, len(t)])]
            assert _stream(parts) == want.rstrip() or _stream(parts) == want, (t, parts)


class _FakeClient:
    """httpx.AsyncClient 대역 — /models 응답만."""
    def __init__(self, status=200, data=None, raises=None, **_):
        self.status, self.data, self.raises = status, data, raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        if self.raises:
            raise self.raises
        return SimpleNamespace(status_code=self.status, json=lambda: {"data": self.data or []})


@pytest.mark.asyncio
@pytest.mark.parametrize("status, data, raises, ok, model", [
    (200, [{"id": "qwen-x"}], None, True, "qwen-x"),     # 연결 확인 응답으로 모델을 알아 둔다(설정 화면 표시, 요청 추가 없음)
    (200, [], None, True, ""),                          # 모델이 아직 없으면 빈 값(첫 호출 때 다시)
    (503, None, None, False, ""),
    (None, None, OSError("down"), False, ""),            # 연결 실패는 False(로그만)
])
async def test_health_check_learns_model(monkeypatch, status, data, raises, ok, model):
    monkeypatch.setattr(oc, "settings", _settings("http://x/v1"))
    monkeypatch.setattr(oc.httpx, "AsyncClient", lambda **kw: _FakeClient(status, data, raises))
    p = oc.OpenAICompatProvider()
    assert await p.health_check() is ok
    assert p.model == model


@pytest.mark.asyncio
async def test_health_check_keeps_configured_model(monkeypatch):
    monkeypatch.setattr(oc, "settings", SimpleNamespace(openai_compat_base_url="http://x/v1", openai_compat_model="fixed", openai_compat_timeout=30))
    monkeypatch.setattr(oc.httpx, "AsyncClient", lambda **kw: _FakeClient(200, [{"id": "other"}]))
    p = oc.OpenAICompatProvider()
    await p.health_check()
    assert p.model == "fixed"                            # .env에 정한 모델은 바꾸지 않는다


# ── 사용량 기록·출력 상한(11/09 토큰 낭비 점검) — Bedrock 기준 기능별 사용량을 재려면 로컬 호출도 기록 · 채팅 답은 상한(폭주 방지) ──
import httpx  # noqa: E402

_REAL_CLIENT = httpx.AsyncClient


class _Usage:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def record(self, **kw):
        if self.fail:
            raise RuntimeError("기록 DB 다운")
        self.rows.append(kw)

    def caller_module(self):
        return "agents.knowledge_rag.agent"


def _transport(handler, seen):
    def h(request):
        import json as _j
        seen.append(_j.loads(request.content or b"{}"))
        return handler(request)
    return lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(h), **kw)


@pytest.mark.asyncio
async def test_generate_records_usage_and_caps_output(monkeypatch):
    monkeypatch.setattr(oc, "settings", SimpleNamespace(openai_compat_base_url="http://x/v1", openai_compat_model="m", openai_compat_timeout=30))
    u, seen = _Usage(), []
    monkeypatch.setattr(oc, "usage", u)
    monkeypatch.setattr(oc.httpx, "AsyncClient", _transport(lambda r: httpx.Response(200, json={
        "choices": [{"message": {"content": "답"}}], "usage": {"prompt_tokens": 120, "completion_tokens": 7}}), seen))
    ans, _ = await oc.OpenAICompatProvider().generate("근거", "질문")
    assert ans == "답" and seen[0]["max_tokens"] == oc.CHAT_MAX_OUTPUT_TOKENS
    r = u.rows[0]
    assert (r["provider"], r["method"], r["input_tokens"], r["output_tokens"], r["output_chars"], r["ok"]) == ("openai_compat", "generate", 120, 7, 1, True)
    assert r["input_chars"] > 0


@pytest.mark.asyncio
async def test_stream_records_end_usage_and_caps_output(monkeypatch):
    monkeypatch.setattr(oc, "settings", SimpleNamespace(openai_compat_base_url="http://x/v1", openai_compat_model="m", openai_compat_timeout=30))
    u, seen = _Usage(), []
    monkeypatch.setattr(oc, "usage", u)
    sse = ('data: {"choices":[{"delta":{"content":"안녕"}}]}\n\n'
           'data: {"choices":[{"delta":{"content":"하세요"}}]}\n\n'
           'data: {"choices":[],"usage":{"prompt_tokens":50,"completion_tokens":3}}\n\n'
           'data: [DONE]\n\n')
    monkeypatch.setattr(oc.httpx, "AsyncClient", _transport(lambda r: httpx.Response(200, text=sse), seen))
    out = "".join([t async for t in oc.OpenAICompatProvider().generate_stream("근거", "질문")])
    assert out == "안녕하세요"
    assert seen[0]["max_tokens"] == oc.CHAT_MAX_OUTPUT_TOKENS and seen[0]["stream_options"] == {"include_usage": True}
    r = u.rows[0]
    assert (r["method"], r["input_tokens"], r["output_tokens"], r["output_chars"], r["ok"]) == ("stream", 50, 3, 5, True)


@pytest.mark.asyncio
async def test_usage_failure_does_not_break_call_and_once_keeps_its_cap(monkeypatch):
    monkeypatch.setattr(oc, "settings", SimpleNamespace(openai_compat_base_url="http://x/v1", openai_compat_model="m", openai_compat_timeout=30))
    seen = []
    monkeypatch.setattr(oc, "usage", _Usage(fail=True))
    monkeypatch.setattr(oc.httpx, "AsyncClient", _transport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "네"}}]}), seen))
    assert await oc.OpenAICompatProvider().generate_once("p", max_tokens=30) == "네"
    assert seen[0]["max_tokens"] == 30   # 짧은 판정은 자기 상한 그대로


@pytest.mark.asyncio
async def test_failed_call_is_recorded_as_failure(monkeypatch):
    monkeypatch.setattr(oc, "settings", SimpleNamespace(openai_compat_base_url="http://x/v1", openai_compat_model="m", openai_compat_timeout=30))
    u, seen = _Usage(), []
    monkeypatch.setattr(oc, "usage", u)
    monkeypatch.setattr(oc.httpx, "AsyncClient", _transport(lambda r: httpx.Response(500, json={}), seen))
    with pytest.raises(httpx.HTTPStatusError):
        await oc.OpenAICompatProvider().generate_once("p")
    assert u.rows[0]["ok"] is False and u.rows[0]["output_chars"] == 0


def _sse(*events):
    return "".join(f"data: {e}\n\n" for e in events) + "data: [DONE]\n\n"


@pytest.mark.asyncio
async def test_stream_closed_early_records_once_as_aborted(monkeypatch):
    """소비자가 중간에 닫으면(사용자 중단) ok=None으로 한 번만 기록 — 실패로 세지 않는다(리뷰 지적: 이번 변경의 핵심 분기)."""
    monkeypatch.setattr(oc, "settings", SimpleNamespace(openai_compat_base_url="http://x/v1", openai_compat_model="m", openai_compat_timeout=30))
    u = _Usage()
    monkeypatch.setattr(oc, "usage", u)
    sse = _sse('{"choices":[{"delta":{"content":"하나"}}]}', '{"choices":[{"delta":{"content":"둘"}}]}')
    monkeypatch.setattr(oc.httpx, "AsyncClient", _transport(lambda r: httpx.Response(200, text=sse), []))
    gen = oc.OpenAICompatProvider().generate_stream("근거", "질문")
    assert await gen.__anext__() == "하나"
    await gen.aclose()
    assert len(u.rows) == 1 and u.rows[0]["ok"] is None and u.rows[0]["output_chars"] == 2


@pytest.mark.asyncio
async def test_stream_error_chunk_is_failure_and_missing_usage_is_none(monkeypatch):
    monkeypatch.setattr(oc, "settings", SimpleNamespace(openai_compat_base_url="http://x/v1", openai_compat_model="m", openai_compat_timeout=30))
    u = _Usage()
    monkeypatch.setattr(oc, "usage", u)
    sse = _sse('{"choices":[{"delta":{"content":"앞"}}]}', '{"error":{"message":"boom"}}')
    monkeypatch.setattr(oc.httpx, "AsyncClient", _transport(lambda r: httpx.Response(200, text=sse), []))
    with pytest.raises(RuntimeError):
        _ = [t async for t in oc.OpenAICompatProvider().generate_stream("근거", "질문")]
    r = u.rows[0]
    assert r["ok"] is False and r["input_tokens"] is None and r["output_tokens"] is None


@pytest.mark.asyncio
async def test_400_retries_once_without_optional_fields(monkeypatch):
    """리뷰 L3·L4: 서버가 출력 상한(문맥 한도 초과)·usage 요청을 400으로 거부하면 그 칸 없이 한 번 더 — 보조 기능 때문에 답이 깨지지 않게."""
    monkeypatch.setattr(oc, "settings", SimpleNamespace(openai_compat_base_url="http://x/v1", openai_compat_model="m", openai_compat_timeout=30))
    monkeypatch.setattr(oc, "usage", _Usage())
    seen = []

    def h(req):
        body = seen[-1]
        if "max_tokens" in body or "stream_options" in body:
            return httpx.Response(400, json={"error": "too long"})
        if body.get("stream"):
            return httpx.Response(200, text=_sse('{"choices":[{"delta":{"content":"답"}}]}'))
        return httpx.Response(200, json={"choices": [{"message": {"content": "답"}}]})
    monkeypatch.setattr(oc.httpx, "AsyncClient", _transport(h, seen))
    p = oc.OpenAICompatProvider()
    assert "".join([t async for t in p.generate_stream("근거", "질문")]) == "답"
    assert (await p.generate("근거", "질문"))[0] == "답"
    assert [("max_tokens" in b, "stream_options" in b) for b in seen] == [(True, True), (False, False), (True, False), (False, False)]


@pytest.mark.asyncio
async def test_once_never_drops_its_own_cap_on_400(monkeypatch):
    """재요청은 채팅 출력 상한(generate·스트림)만 — 짧은 판정(once)의 자기 상한은 400이어도 풀지 않는다(값이 4096이어도, 재리뷰 낮음)."""
    monkeypatch.setattr(oc, "settings", SimpleNamespace(openai_compat_base_url="http://x/v1", openai_compat_model="m", openai_compat_timeout=30))
    monkeypatch.setattr(oc, "usage", _Usage())
    seen = []
    monkeypatch.setattr(oc.httpx, "AsyncClient", _transport(lambda r: httpx.Response(400, json={}), seen))
    with pytest.raises(httpx.HTTPStatusError):
        await oc.OpenAICompatProvider().generate_once("p", max_tokens=oc.CHAT_MAX_OUTPUT_TOKENS)
    assert len(seen) == 1
