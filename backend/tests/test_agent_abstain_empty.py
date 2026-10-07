"""채팅 실패가 화면·기록에 드러나는지 (2026-10-06, v2.128).

- 근거 없음 즉시 판정(설정으로 켬): LLM을 부르지 않고 "관련 지식을 찾지 못했습니다" — 지식 공백으로 기록, 캐시엔 안 넣음
- LLM이 예외 없이 토큰 0개로 끝남: 예전엔 화면에 아무것도 안 보내 빈 말풍선이었다(10/6 실측 4건) → 오류 문구 + system_error 기록
- should_abstain 경계: 하한 미설정이면 꺼짐, 지식·공통코드·강한 파라미터가 있으면 거절하지 않음
"""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent


def _load_real(name: str, rel_path: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = _ilu.spec_from_file_location(name, str(_backend_dir / rel_path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load_real("shared.rrf", "shared/rrf.py")
sys.modules.setdefault("service.policy", MagicMock())
_load_real("service.policy.query_type", "service/policy/query_type.py")
_load_real("service.policy.search", "service/policy/search.py")
sys.modules.setdefault("service.refdata", MagicMock())
_load_real("service.refdata.parser", "service/refdata/parser.py")
_load_real("service.refdata.service", "service/refdata/service.py")

from unittest.mock import patch  # noqa: E402

from service.policy.search import PolicySearchResult  # noqa: E402

# conftest가 agents.base를 MagicMock으로 바꿔 두어 그대로 import하면 KnowledgeRagAgent 자체가 가짜가 된다 — 진짜 AgentBase로
# agent.py를 따로(다른 모듈 이름으로) 로드해 stream_chat을 실제로 돌린다. 나머지 의존성(memory·cache 등)은 아래에서 monkeypatch.
_real_base = _ilu.module_from_spec(_ilu.spec_from_file_location("_real_agents_base", str(_backend_dir / "agents/base.py")))
_real_base.__spec__.loader.exec_module(_real_base)
with patch.dict(sys.modules, {"agents.base": _real_base}):
    _spec = _ilu.spec_from_file_location("_agent_under_test", str(_backend_dir / "agents/knowledge_rag/agent.py"))
    agent = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(agent)

NO_KNOWLEDGE = "관련 지식을 찾지 못했습니다"
EMPTY = "[AI가 빈 답변을 보냈습니다. 잠시 후 다시 질문해 주세요.]"


class TestShouldAbstain:
    weak = {"adopted": 0, "codes": 0, "columns": 0, "max_param_rank": 0.01, "top_narrative": 0.47}

    def test_off_when_threshold_unset(self):
        assert agent.should_abstain(self.weak, None, 0.05) is False

    def test_weak_signals_abstain(self):
        assert agent.should_abstain(self.weak, 0.50, 0.05) is True

    @pytest.mark.parametrize("override", [{"adopted": 1}, {"codes": 1}, {"columns": 2}, {"max_param_rank": 0.2},
                                          {"top_narrative": 0.52}, {"category_items": 3}])
    def test_any_real_evidence_keeps_answering(self, override):
        assert agent.should_abstain({**self.weak, **override}, 0.50, 0.05) is False


class _Conn:
    async def execute(self, *a, **k):
        return "OK"


class _ConnCtx:
    async def __aenter__(self):
        return _Conn()

    async def __aexit__(self, *a):
        return False


UNAVAILABLE = "[LLM 서버에 연결할 수 없습니다. 검색 결과를 참고하세요.]"
REPLACE = "\x00REPLACE\x00"


def _wire(monkeypatch, *, abstain: bool, tokens: list[str]):
    # service.chat.helpers도 conftest가 MagicMock — 이 테스트가 보는 상수·판정만 실제 값으로
    monkeypatch.setattr(agent, "NO_KNOWLEDGE_MARKER", NO_KNOWLEDGE)
    monkeypatch.setattr(agent, "LLM_EMPTY_MSG", EMPTY)
    monkeypatch.setattr(agent, "LLM_UNAVAILABLE_MSG", UNAVAILABLE)
    monkeypatch.setattr(agent, "is_llm_failure", lambda a: not a or a in (UNAVAILABLE, EMPTY))
    monkeypatch.setattr(agent, "REPLACE_PREFIX", REPLACE)
    monkeypatch.setattr(agent.memory, "augment_query_for_search", AsyncMock(return_value=("q", [0.1])))
    monkeypatch.setattr(agent.memory, "build_context_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent.embedding_service, "embed", AsyncMock(return_value=[0.1]))
    monkeypatch.setattr(agent.sem_cache, "normalize_query", lambda q: q)
    monkeypatch.setattr(agent.sem_cache, "get_cached", AsyncMock(return_value=None))
    set_cached = AsyncMock()
    monkeypatch.setattr(agent.sem_cache, "set_cached", set_cached)
    cc = agent.ChatContext(results=[], policy_result=PolicySearchResult(), common_codes=[], db_columns=[],
                           llm_context="" if abstain else "ctx", enriched_query="q", mapped_term=None, abstain=abstain)
    monkeypatch.setattr(agent, "build_chat_context", AsyncMock(return_value=cc))
    monkeypatch.setattr(agent, "get_conn", lambda: _ConnCtx())
    upd = AsyncMock()
    monkeypatch.setattr(agent, "update_assistant_message", upd)
    log = AsyncMock()
    monkeypatch.setattr(agent, "create_query_log", log)
    monkeypatch.setattr(agent, "post_save_tasks", AsyncMock())
    monkeypatch.setattr(agent, "resolve_system_prompt", AsyncMock(return_value="S"))
    llm = MagicMock()
    calls = []

    async def gen(*a, **k):
        calls.append(1)
        for t in tokens:
            yield t
    llm.generate_stream = gen
    monkeypatch.setattr(agent, "get_llm_provider", lambda: llm)
    return upd, log, set_cached, calls


async def _run():
    events = []
    async for ev in agent.KnowledgeRagAgent().stream_chat("오늘 날씨 어때?", {"id": 7}, 1, {"namespace": "ns", "msg_id": 5}):
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_abstain_answers_without_llm_and_logs_gap(monkeypatch):
    upd, log, set_cached, llm_calls = _wire(monkeypatch, abstain=True, tokens=["지어낸 답"])
    events = await _run()
    assert [e["data"] for e in events if e["type"] == "token"] == [NO_KNOWLEDGE]
    assert llm_calls == []                                           # LLM 안 부름
    assert log.await_args.args[2] == NO_KNOWLEDGE and log.await_args.kwargs["had_context"] is False
    set_cached.assert_not_awaited()                                  # 고정 거절은 캐시 안 함
    assert events[-1] == {"type": "done", "message_id": 5, "status": "completed"}


@pytest.mark.asyncio
async def test_zero_tokens_shows_error_and_is_not_cached(monkeypatch):
    upd, log, set_cached, llm_calls = _wire(monkeypatch, abstain=False, tokens=[])
    events = await _run()
    assert llm_calls == [1]
    assert [e["data"] for e in events if e["type"] == "token"] == [EMPTY]   # 빈 말풍선 대신 문구
    assert log.await_args.args[2] == EMPTY
    set_cached.assert_not_awaited()
    assert events[-1]["status"] == "failed"


@pytest.mark.asyncio
async def test_normal_answer_unchanged(monkeypatch):
    upd, log, set_cached, _ = _wire(monkeypatch, abstain=False, tokens=["정상 ", "답변"])
    events = await _run()
    assert "".join(e["data"] for e in events if e["type"] == "token") == "정상 답변"
    set_cached.assert_awaited_once()
    assert events[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_gateway_replace_discards_streamed_text(monkeypatch):
    """게이트웨이 검열(message_replace)은 답을 통째로 바꾼다 — 저장·캐시·화면 모두 교체문만(리뷰 2026-10-07: 원문 뒤에 붙던 문제)."""
    upd, log, set_cached, _ = _wire(monkeypatch, abstain=False, tokens=["검열될 ", "원래 답", REPLACE + "교체된 안내문"])
    events = await _run()
    assert {"type": "replace", "data": "교체된 안내문"} in events
    assert log.await_args.args[2] == "교체된 안내문"
    assert set_cached.await_args.args[3]["answer"] == "교체된 안내문"
