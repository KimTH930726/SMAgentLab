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


def _wire(monkeypatch, *, abstain: bool, tokens: list[str], contextual: bool = False, clarify=None, has_prev: bool = False):
    # service.chat.helpers도 conftest가 MagicMock — 이 테스트가 보는 상수·판정만 실제 값으로
    monkeypatch.setattr(agent, "NO_KNOWLEDGE_MARKER", NO_KNOWLEDGE)
    monkeypatch.setattr(agent, "LLM_EMPTY_MSG", EMPTY)
    monkeypatch.setattr(agent, "LLM_UNAVAILABLE_MSG", UNAVAILABLE)
    monkeypatch.setattr(agent, "is_llm_failure", lambda a: not a or a in (UNAVAILABLE, EMPTY))
    monkeypatch.setattr(agent, "REPLACE_PREFIX", REPLACE)
    # 단독 질문이면 검색 질의 = 질문 그대로, 후속 질문이면 직전 턴이 붙는다(contextual)
    monkeypatch.setattr(agent.memory, "augment_query_for_search",
                        AsyncMock(side_effect=lambda conv, q, **k: ((f"직전 턴 {q}" if contextual else q), [0.1])))
    monkeypatch.setattr(agent.memory, "build_context_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent.memory, "has_previous_turns", AsyncMock(return_value=has_prev or contextual))
    monkeypatch.setattr(agent.embedding_service, "embed", AsyncMock(return_value=[0.1]))
    monkeypatch.setattr(agent.sem_cache, "normalize_query", lambda q: q)
    get_cached = AsyncMock(return_value=None)
    monkeypatch.setattr(agent.sem_cache, "get_cached", get_cached)
    set_cached = AsyncMock()
    monkeypatch.setattr(agent.sem_cache, "set_cached", set_cached)
    cc = agent.ChatContext(results=[], policy_result=PolicySearchResult(), common_codes=[], db_columns=[],
                           llm_context="" if abstain else "ctx", enriched_query="q", mapped_term=None, abstain=abstain,
                           clarify=clarify)
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
    _wire.get_cached = get_cached
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


@pytest.mark.asyncio
async def test_followup_question_skips_cache(monkeypatch):
    """앞 턴에 기대는 후속 질문(검색에 직전 턴이 붙음)은 캐시를 읽지도 쓰지도 않는다 — 같은 글("수신자는요?")이라도 대화마다 뜻이
    달라 다른 대화의 답이 나갔다(2026-10-13 멀티턴 실측)."""
    upd, log, set_cached, calls = _wire(monkeypatch, abstain=False, tokens=["대화 맥락 답"], contextual=True)
    events = await _run()
    assert calls == [1]
    _wire.get_cached.assert_not_awaited()
    set_cached.assert_not_awaited()
    assert "".join(e["data"] for e in events if e["type"] == "token") == "대화 맥락 답"


@pytest.mark.asyncio
async def test_standalone_question_still_cached(monkeypatch):
    upd, log, set_cached, calls = _wire(monkeypatch, abstain=False, tokens=["단독 답"], contextual=False)
    await _run()
    _wire.get_cached.assert_awaited_once()
    set_cached.assert_awaited_once()


@pytest.mark.asyncio
async def test_clarify_returns_options_without_llm_log_or_cache(monkeypatch):
    """재질문(v2.134): LLM 없이 선택지 이벤트 + 같은 글 토큰, 메시지 metadata에 선택지 저장, 질의 기록·캐시 없음."""
    cf = {"message": "어느 부분을 볼까요?", "options": [{"label": "A (3개)", "scope": "A", "query": "A 정책 전부 보여줘"},
                                                     {"label": "전부 보기 (30개)", "scope": "*", "query": "q"}]}
    upd, log, set_cached, calls = _wire(monkeypatch, abstain=False, tokens=["안 불려야 함"], clarify=cf)
    events = await _run()
    assert calls == []
    assert {"type": "clarify", **cf} in events
    assert [e["data"] for e in events if e["type"] == "token"] == ["어느 부분을 볼까요?"]
    assert upd.await_args.kwargs["metadata"] == {"clarify": cf}
    log.assert_not_awaited()
    set_cached.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_turn_without_augmentation_skips_cache(monkeypatch):
    """검색 보강이 안 붙은(유사도 미달) 짧은 후속 질문도 2턴째면 캐시를 안 쓴다 — LLM은 이력을 보고 답하기 때문(리뷰 2026-10-13)."""
    upd, log, set_cached, calls = _wire(monkeypatch, abstain=False, tokens=["이력 기반 답"], contextual=False, has_prev=True)
    await _run()
    _wire.get_cached.assert_not_awaited()
    set_cached.assert_not_awaited()



class TestAnswerShaping:
    """답 생성 문맥 다듬기(v2.138) — 목록 답은 목록 밖 정책 검색 결과를 빼고, 조건 한정 질문엔 답 구조를 붙인다."""

    def _wire_ctx(self, monkeypatch, *, explicit_all: bool, item_ids):
        from service.policy.search import NarrativeHit
        H = lambda i: NarrativeHit(item_id=i, logical_id=i, policy_name=f"p{i}", category_path=[], status="active",  # noqa: E731
                                   chunk_text="t", score=0.6, raw_body="b")
        pr = PolicySearchResult(narratives=[H(1), H(2), H(3)])
        monkeypatch.setattr(agent.glossary_terms, "resolve_query_terms", AsyncMock(return_value=([], None, "q")))
        monkeypatch.setattr(agent.glossary_terms, "definitions_block", lambda m: "")
        monkeypatch.setattr(agent.retrieval, "search_knowledge", AsyncMock(return_value=[]))
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: None)
        monkeypatch.setattr(agent.retrieval, "is_adopted", lambda r, th: False)
        monkeypatch.setattr(agent.retrieval, "expand_parent_sections", AsyncMock(return_value=[]))
        monkeypatch.setattr(agent.policy_scope, "policy_namespace_ids", AsyncMock(return_value=[1]))   # 정책서 범위(2026-10-20)
        monkeypatch.setattr(agent.policy_search, "PolicySearchResult", PolicySearchResult)   # 부모 패키지가 MagicMock이라 진짜 클래스로
        monkeypatch.setattr(agent.policy_search, "search_policy", AsyncMock(return_value=pr))
        monkeypatch.setattr(agent.refdata_search, "has_refdata", AsyncMock(return_value=False))
        monkeypatch.setattr(agent, "get_conn", lambda: _ConnCtx())
        cat = MagicMock(item_ids=list(item_ids), explicit_all=explicit_all, total=len(item_ids), scope_missed=False)
        cat.block.return_value = "[분류 목록]"
        monkeypatch.setattr(agent.category_list, "find_category_list", AsyncMock(return_value=cat if item_ids else None))
        monkeypatch.setattr(agent.category_list, "clarify_for", lambda c, q: None)
        monkeypatch.setattr(agent, "should_abstain", lambda *a: False)
        seen = {}

        def build(results, policy_result, *a):
            seen["ids"] = [n.item_id for n in policy_result.narratives]
            return "ctx"
        monkeypatch.setattr(agent, "_build_rrf_context", build)
        return pr, seen

    async def _run(self, q, scope=None):
        return await agent.build_chat_context("ns", q, [0.1], top_k=5, w_vector=0.7, w_keyword=0.3, clarify_scope=scope)

    @pytest.mark.asyncio
    async def test_listing_answer_drops_out_of_list_policies(self, monkeypatch):
        pr, seen = self._wire_ctx(monkeypatch, explicit_all=True, item_ids=[1, 3])
        cc = await self._run("교환 정책 전부 보여줘")
        assert seen["ids"] == [1, 3]                                  # 목록 밖(2)은 문맥에서 빠짐
        assert [n.item_id for n in cc.policy_result.narratives] == [1, 2, 3]   # 인용·판정용 원래 결과는 그대로

    @pytest.mark.asyncio
    async def test_out_of_list_hit_ranked_above_list_is_kept(self, monkeypatch):
        """ "전체 취소하면 쿠폰은?"처럼 "전체"만 든 구체 질문 — 1위 정답이 목록 밖이어도 문맥에 남는다(리뷰 H1)."""
        _, seen = self._wire_ctx(monkeypatch, explicit_all=True, item_ids=[2])
        await self._run("전체 취소하면 쿠폰은 복원되나요?")
        assert seen["ids"] == [1, 2]                                  # 1(목록 밖, 1위) 유지 · 3(목록 아래 섞임) 제외

    @pytest.mark.asyncio
    async def test_clarify_scope_choice_is_a_listing_answer(self, monkeypatch):
        _, seen = self._wire_ctx(monkeypatch, explicit_all=False, item_ids=[1])   # 범위를 고르면 explicit_all은 False로 온다
        await self._run("주문 > 장바구니 정책 전부 보여줘", scope="주문 > 장바구니")
        assert seen["ids"] == [1]

    @pytest.mark.asyncio
    async def test_topic_question_keeps_search_results(self, monkeypatch):
        _, seen = self._wire_ctx(monkeypatch, explicit_all=False, item_ids=[1])
        await self._run("교환 정책 좀")                               # 전부·목록 신호 없음 → 거르지 않음
        assert seen["ids"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_condition_hint_only_for_condition_questions(self, monkeypatch):
        self._wire_ctx(monkeypatch, explicit_all=False, item_ids=[])
        cc = await self._run("투고백에만 따로 적용되는 규정이 있나요?")
        assert cc.llm_context.startswith(agent.CONDITION_ANSWER_HINT)
        cc = await self._run("투고백 구매 제한은 몇 세트인가요?")
        assert agent.CONDITION_ANSWER_HINT not in cc.llm_context


@pytest.mark.parametrize("q,want", [
    ("투고백에만 따로 적용되는 규정이 있나요?", True),
    ("반품할 때만 추가되는 비용이 있어?", True),
    ("임직원 전용 쿠폰 조건 알려줘", True),
    ("일반 신용카드 결제인 경우에만 해당하는 제한", True),
    ("배송비는 얼마인가요?", False),
    ("장바구니 최대 몇 개까지 담을 수 있나요", False),
    ("만 원 이상 무료배송인가요", False),
    ("3만 적용 기준이 뭐야", False),
    ("전용계좌 입금 기한은?", False),
    ("쿠폰만 따로 쓸 수 있나요", True),
])
def test_condition_query_detection(q, want):
    from service.policy.query_type import looks_like_condition_query
    assert looks_like_condition_query(q) is want



def test_list_scoped_invariants():
    """목록 안 정책은 하나도 안 빠지고, 순서는 그대로, 목록 밖은 첫 목록 안 정책보다 위에 있던 것만."""
    import random
    rng = random.Random(3)
    H = lambda i: MagicMock(item_id=i)  # noqa: E731
    for _ in range(200):
        hits = [H(i) for i in rng.sample(range(30), rng.randint(0, 12))]
        keep = set(rng.sample(range(30), rng.randint(0, 10)))
        out = agent.list_scoped(hits, keep)
        ids = [h.item_id for h in hits]
        assert [h.item_id for h in out] == [i for i in ids if i in {h.item_id for h in out}]   # 순서 유지
        assert {i for i in ids if i in keep} <= {h.item_id for h in out}                     # 목록 안 유실 0
        first = next((k for k, i in enumerate(ids) if i in keep), len(ids))
        assert all(i in keep or ids.index(i) < first for i in (h.item_id for h in out))


@pytest.mark.parametrize("q,want", [
    ("임직원 전용으로 발급되는 쿠폰은?", True),
    ("전용은 뭐야", True),
    ("전용계좌 입금 기한", False),
])
def test_condition_query_particles(q, want):
    from service.policy.query_type import looks_like_condition_query
    assert looks_like_condition_query(q) is want
