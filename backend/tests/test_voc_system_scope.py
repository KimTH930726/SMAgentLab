"""VOC 지식 검색 = 라우팅 영역 + 메일과 가장 가까운 시스템 컬렉션(애매하면 2개, 11/05).

막힌 일: 11/02 개편으로 지식이 시스템 컬렉션으로 옮겨져, 라우팅 영역만 찾던 VOC는 11/03부터 근거 지식 0건이었다.
메일함 문의도 대부분 라우팅과 다른 시스템이었다(딜리버스 메일함 300건 중 284건이 홈페이지 시스템 쪽).
실 DB 시험은 트랜잭션 안에서 만들고 되돌린다(tests/realdb.py). 벡터는 한 차원만 켠 단위 벡터 — 실제 지식(촘촘한 벡터)과는 거의 0.
"""
import math
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))   # tests/ 공용 장치
from realdb import load, ns, run  # noqa: E402

database = load("_vss_real_database", "core/database.py")


def _vec(*pairs) -> list[float]:
    """(차원, 값) 짝 → 정규화한 1024차원 벡터."""
    v = [0.0] * 1024
    for i, x in pairs:
        v[i] = x
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _txt(v) -> str:
    return "[" + ",".join(str(x) for x in v) + "]"


async def _k(conn, ns_id, vec, content="시험 지식"):
    return await conn.fetchval("INSERT INTO rag_knowledge (namespace_id, content, embedding, status) VALUES ($1, $2, $3::vector, 'active') RETURNING id",
                               ns_id, content, _txt(vec))


# ── 시스템 고르기(nearest_system_ids) — 실 DB ─────────────────────────────

def test_picks_one_system_when_clear():
    async def scenario(conn):
        a, b = await ns(conn, "시험_가까운 시스템"), await ns(conn, "시험_먼 시스템")
        await _k(conn, a, _vec((7, 1.0)))
        await _k(conn, b, _vec((8, 1.0)))
        return a, await database.nearest_system_ids(conn, _vec((7, 1.0)))
    a, picked = run(scenario)
    assert picked == [a]


def test_picks_two_systems_when_close():
    """1·2위 최고 유사도 차이 < 0.03 → 둘 다(가까운 순)."""
    async def scenario(conn):
        a, b = await ns(conn, "시험_1위 시스템"), await ns(conn, "시험_2위 시스템")
        await _k(conn, a, _vec((7, 1.0)))
        await _k(conn, b, _vec((7, 1.0), (8, 0.1)))   # 유사도 ≈ 0.995
        return a, b, await database.nearest_system_ids(conn, _vec((7, 1.0)))
    a, b, picked = run(scenario)
    assert picked == [a, b]


def test_ignores_policy_area_and_inactive():
    """시스템 컬렉션(area='system')의 사용 중 지식만 — 정책 영역·삭제된 지식은 순위에 안 든다."""
    async def scenario(conn):
        p, s = await ns(conn, "시험_정책 영역", "policy"), await ns(conn, "시험_꺼진 지식 시스템")
        await _k(conn, p, _vec((7, 1.0)))
        k = await _k(conn, s, _vec((7, 1.0)))
        await conn.execute("UPDATE rag_knowledge SET status = 'deleted' WHERE id = $1", k)
        return {p, s}, await database.nearest_system_ids(conn, _vec((7, 1.0)))
    mine, picked = run(scenario)
    assert not (mine & set(picked))


# ── 코어 API: nearest · contents의 with_systems — 실 DB ───────────────────

def _api():
    """api.py를 실제 파일로 — 의존(glossary_terms·retrieval)은 import 순간만 가짜로 끼운다(nearest·contents는 안 씀)."""
    fake_pkg = SimpleNamespace(glossary_terms=MagicMock(), retrieval=MagicMock(RetrievalResult=object))
    with patch.dict(sys.modules, {
        "agents.knowledge_rag.knowledge": fake_pkg,
        "agents.knowledge_rag.knowledge.retrieval": fake_pkg.retrieval,
        "core.database": database,
    }):
        import importlib.util
        from realdb import ROOT
        spec = importlib.util.spec_from_file_location("_vss_real_knowledge_api", ROOT / "agents/knowledge_rag/knowledge/api.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod   # dataclass가 모듈을 sys.modules에서 찾는다
        spec.loader.exec_module(mod)
        return mod


api = _api()


def test_nearest_with_systems_reaches_system_collection():
    """라우팅 영역(지식 0)에서 묶음 대표의 가장 가까운 지식 — 예전 범위는 없음, with_systems는 시스템 컬렉션 지식."""
    async def scenario(conn):
        route = await ns(conn, "시험_메일함 영역", "policy")
        s = await ns(conn, "시험_답 있는 시스템")
        k = await _k(conn, s, _vec((9, 1.0)), "해결 절차")
        items = [(1, route, _txt(_vec((9, 1.0))))]
        return k, (await api.nearest(conn, items, 50)).get(1), (await api.nearest(conn, items, 50, with_systems=True)).get(1)
    k, before, after = run(scenario)
    assert before["knowledge_id"] is None
    assert after["knowledge_id"] == k and after["similarity"] == pytest.approx(1.0) and after["snippet"] == "해결 절차"


def test_contents_with_systems_keeps_route_filter_otherwise():
    async def scenario(conn):
        route = await ns(conn, "시험_내용 메일함 영역", "policy")
        other_policy = await ns(conn, "시험_다른 정책 영역", "policy")
        s = await ns(conn, "시험_내용 시스템")
        ks, kp = await _k(conn, s, _vec((9, 1.0)), "시스템 지식"), await _k(conn, other_policy, _vec((9, 1.0)), "남의 정책 영역 지식")
        return ks, (await api.contents("시험_내용 메일함 영역", [ks, kp]),
                    await api.contents("시험_내용 메일함 영역", [ks, kp], with_systems=True))
    ks, (before, after) = run(scenario, [api])
    assert before == []
    assert [r["id"] for r in after] == [ks]   # 시스템 컬렉션만 더해진다 — 다른 정책 영역 지식은 여전히 안 나옴


# ── 코어 API: chat_context — 채팅과 같은 경로(agent.build_chat_context)에 범위만 넘긴다 ─────

def _fake_agent(policy):
    calls = []

    async def build_chat_context(namespace, q, vec, **kw):
        calls.append(kw)
        return SimpleNamespace(results=["r"], llm_context="문맥", mapped_term=None, policy_result=policy)
    return SimpleNamespace(build_chat_context=build_chat_context), calls


def _policy(narr=(), params=()):
    return SimpleNamespace(
        narratives=[SimpleNamespace(policy_name=f"서술{i}", chunk_text="본문", score=s, part="리워드") for i, s in enumerate(narr)],
        params=[SimpleNamespace(policy_name="파라미터", param_name="적립률", value="1", unit="%", condition=None, score=s, part=None) for s in params])


def test_chat_context_resolves_policy_names_in_order():
    """정책 컬렉션 이름 → id(넘긴 순서, 없는 이름은 빠짐) — 실 DB. 지식 범위·가중치는 채팅 기본값 그대로 넘긴다."""
    fake, calls = _fake_agent(_policy())

    async def scenario(conn):
        a, b = await ns(conn, "시험_정책A", "policy"), await ns(conn, "시험_정책B", "policy")
        with (patch.dict(sys.modules, {"agents.knowledge_rag": SimpleNamespace(agent=fake), "agents.knowledge_rag.agent": fake}),
              patch.object(api, "retrieval", SimpleNamespace(get_search_defaults=lambda: {"default_w_vector": 0.7, "default_w_keyword": 0.3, "default_top_k": 5}))):
            out = await api.chat_context("메일함", "q", [0.1], knowledge_scope="nearest_system", policy_collections=["시험_정책B", "없는 이름", "시험_정책A"])
            await api.chat_context("메일함", "q", [0.1], knowledge_scope=None)
        return a, b, out
    a, b, out = run(scenario, [api])
    assert calls[0]["policy_ns_ids"] == [b, a] and calls[0]["knowledge_scope"] == "nearest_system" and calls[0]["top_k"] == 5
    assert calls[1]["policy_ns_ids"] is None   # 덮어쓰기 없음 = 채팅 규칙
    assert out.context == "문맥" and out.results == ["r"] and out.policy_refs == [] and out.policy_found is False


@pytest.mark.asyncio
@pytest.mark.parametrize("narr,params,found", [((0.59,), (), False), ((0.60,), (), True), ((), (0.05,), False), ((), (0.06,), True)])
async def test_chat_context_policy_found_threshold(narr, params, found):
    """정책 근거 있음 = 서술 최고 ≥ 0.60(VOC 메일 실측 11/05) 또는 파라미터 > 0.05(채팅 근거 없음 판정의 실측 값) · 근거는 하한 넘은 것만, 파라미터 → 서술 순 dict."""
    fake, _ = _fake_agent(_policy(narr, params))
    with (patch.dict(sys.modules, {"agents.knowledge_rag": SimpleNamespace(agent=fake), "agents.knowledge_rag.agent": fake}),
          patch.object(api, "retrieval", SimpleNamespace(get_search_defaults=lambda: {"default_w_vector": 0.7, "default_w_keyword": 0.3, "default_top_k": 5}))):
        out = await api.chat_context("메일함", "q", [0.1], knowledge_scope=None)
    assert out.policy_found is found
    if not found:
        assert out.policy_refs == []   # 하한 미달은 근거로 안 내보낸다(리뷰 11/05 M1)
    elif params:
        assert out.policy_refs == [{"name": "파라미터", "snippet": "적립률 = 1%", "score": params[0], "collection": None}]
    else:
        assert out.policy_refs == [{"name": "서술0", "snippet": "본문", "score": narr[0], "collection": "리워드"}]


@pytest.mark.asyncio
async def test_chat_context_refs_only_passing_params_first():
    """약한 서술 4개 + 강한 파라미터 1개 → 근거는 파라미터 1개뿐(약한 서술이 앞을 차지해 판정 근거가 잘리지 않게, 리뷰 11/05 M1)."""
    fake, _ = _fake_agent(_policy((0.52, 0.51, 0.50, 0.49, 0.65), (0.08, 0.01)))
    with (patch.dict(sys.modules, {"agents.knowledge_rag": SimpleNamespace(agent=fake), "agents.knowledge_rag.agent": fake}),
          patch.object(api, "retrieval", SimpleNamespace(get_search_defaults=lambda: {"default_w_vector": 0.7, "default_w_keyword": 0.3, "default_top_k": 5}))):
        out = await api.chat_context("메일함", "q", [0.1], knowledge_scope=None)
    assert out.policy_found is True
    assert [(r["name"], r["score"]) for r in out.policy_refs] == [("파라미터", 0.08), ("서술4", 0.65)]


def test_scope_nearest_system_adds_picked_systems_to_linked():
    """지식 범위 nearest_system = 받침 연결 + 가까운 시스템(중복 없이) — 실 DB."""
    async def scenario(conn):
        route = await ns(conn, "시험_범위 메일함", "policy")
        linked, near = await ns(conn, "시험_연결 시스템"), await ns(conn, "시험_가까운 범위 시스템")
        await conn.execute("INSERT INTO ops_link (from_type, from_key, relation, to_type, to_key, source) VALUES ('domain', $1, 'supports', 'domain', $2, 'test')",
                           str(linked), str(route))
        await _k(conn, near, _vec((7, 1.0)))
        return linked, near, await database.knowledge_scope_extra_ids(conn, "시험_범위 메일함", "nearest_system", _vec((7, 1.0)))
    linked, near, ids = run(scenario)
    assert ids == [linked, near]


# ── VOC check_relevance — 채팅 경로에 범위·설정을 그대로 넘기고 정책 근거를 옮긴다(리뷰 11/05 L9) ─────

@pytest.mark.asyncio
async def test_check_relevance_uses_chat_path_with_scope_and_policy_setting():
    import importlib.util
    from realdb import ROOT
    spec = importlib.util.spec_from_file_location("_vss_real_voc_service", ROOT / "service/email_voc/service.py")
    voc = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = voc
    spec.loader.exec_module(voc)
    got = SimpleNamespace(results=[], context="문맥", mapped_term=None, w_vector=0.7, w_keyword=0.3,
                          policy_refs=[{"name": "p", "snippet": "s", "score": 0.6, "collection": "리워드"}], policy_found=True)
    chat = AsyncMock(return_value=got)
    with (patch.object(voc, "knowledge_api", SimpleNamespace(chat_context=chat)),
          patch.object(voc, "embedding_service", SimpleNamespace(embed_long=AsyncMock(return_value=[0.1])))):
        out = await voc.check_relevance("메일함", "제목", "본문", policy_collections=["리워드"])
    assert chat.await_args.kwargs == {"knowledge_scope": "nearest_system", "policy_collections": ["리워드"]}
    assert out.policy_found is True and out.policy_refs == got.policy_refs and out.context == "문맥" and out.top_score == 0.0
