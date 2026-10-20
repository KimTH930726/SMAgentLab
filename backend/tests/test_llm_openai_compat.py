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
