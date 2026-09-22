"""Tests for service/policy/search.py — 파라미터/서술 검색 SQL 구성 및 결과 매핑."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent

sys.modules["service.policy"] = MagicMock()

_qt_spec = _ilu.spec_from_file_location("service.policy.query_type", str(_backend_dir / "service" / "policy" / "query_type.py"))
_query_type = _ilu.module_from_spec(_qt_spec)
sys.modules["service.policy.query_type"] = _query_type
_qt_spec.loader.exec_module(_query_type)

_spec = _ilu.spec_from_file_location("service.policy.search", str(_backend_dir / "service" / "policy" / "search.py"))
search = _ilu.module_from_spec(_spec)
sys.modules["service.policy.search"] = search
_spec.loader.exec_module(search)


def _make_fake_conn(param_rows=None, chunk_rows=None):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(side_effect=[param_rows or [], chunk_rows or []])
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    def _apply(param_rows=None, chunk_rows=None):
        conn = _make_fake_conn(param_rows, chunk_rows)
        monkeypatch.setattr(search, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(search, "resolve_namespace_id", AsyncMock(return_value=1))
        monkeypatch.setattr(search, "embedding_service", MagicMock(embed=AsyncMock(return_value=[0.1] * 768)))
        return conn
    return _apply


class TestSearchPolicy:
    @pytest.mark.asyncio
    async def test_namespace_not_found_raises(self, patch_db, monkeypatch):
        patch_db()
        monkeypatch.setattr(search, "resolve_namespace_id", AsyncMock(return_value=None))
        with pytest.raises(ValueError, match="네임스페이스"):
            await search.search_policy("없는곳", "질문")

    @pytest.mark.asyncio
    async def test_param_and_narrative_hits_mapped_correctly(self, patch_db):
        param_rows = [{
            "item_id": 1, "logical_id": 1, "policy_name": "장바구니 담기",
            "category_path": ["1.주문", "1-1.장바구니"], "status": "pending_review", "raw_body": "일반 배달: 20개",
            "param_name": "최대개수", "condition": "일반 배달", "value": "20", "unit": "개", "rank": 0.06,
        }]
        chunk_rows = [{
            "item_id": 2, "logical_id": 2, "policy_name": "장바구니 조회",
            "category_path": ["1.주문"], "status": "pending_review", "raw_body": "재고 없으면 SOLD OUT 표기",
            "chunk_text": "재고 없으면 SOLD OUT 표기", "score": 0.83,
        }]
        patch_db(param_rows=param_rows, chunk_rows=chunk_rows)

        result = await search.search_policy("ns", "장바구니 개수")

        assert len(result.params) == 1
        assert result.params[0].param_name == "최대개수"
        assert result.params[0].condition == "일반 배달"
        assert len(result.narratives) == 1
        assert result.narratives[0].chunk_text == "재고 없으면 SOLD OUT 표기"
        assert result.narratives[0].score == 0.83

    @pytest.mark.asyncio
    async def test_no_hits_returns_empty_lists(self, patch_db):
        patch_db(param_rows=[], chunk_rows=[])
        result = await search.search_policy("ns", "존재안하는질문")
        assert result.params == []
        assert result.narratives == []

    @pytest.mark.asyncio
    async def test_category_filter_adds_fourth_param_to_both_queries(self, patch_db):
        conn = patch_db()
        await search.search_policy("ns", "질문", category="1.주문")

        param_call = conn.fetch.call_args_list[0]
        chunk_call = conn.fetch.call_args_list[1]
        assert "= ANY(i.category_path)" in param_call.args[0]
        assert param_call.args[-1] == "1.주문"
        assert "= ANY(i.category_path)" in chunk_call.args[0]
        assert chunk_call.args[-1] == "1.주문"

    @pytest.mark.asyncio
    async def test_no_category_omits_filter_clause(self, patch_db):
        conn = patch_db()
        await search.search_policy("ns", "질문")

        param_call = conn.fetch.call_args_list[0]
        assert "category_path" not in param_call.args[0] or "= ANY" not in param_call.args[0]
        assert len(param_call.args) == 4  # sql + ns_id + like + top_k (category 없음)

    @pytest.mark.asyncio
    async def test_param_query_applies_korean_suffix_stripping(self, patch_db):
        """2026-09-10(v2.71) — 콘텐츠/쿼리 양쪽에 policy_strip_ko()를 적용해야 조사/어미가
        달라도 매칭된다(init/06-policy-strip-ko.sql). 원문 쿼리 텍스트 자체는 그대로 $2로
        넘기고(파이썬에서 미리 자르지 않음), SQL 함수가 콘텐츠·쿼리 양쪽을 동일하게 변환한다."""
        conn = patch_db()
        await search.search_policy("ns", "질문")

        param_sql = conn.fetch.call_args_list[0].args[0]
        assert "policy_strip_ko(p.name || ' ' || COALESCE(p.condition, '') || ' ' || i.policy_name)" in param_sql
        assert "policy_strip_ko($2)" in param_sql


class TestSearchPolicyPrecomputedVector:
    """2026-09-04 편입 1단계 — agent.py가 이미 계산해둔 query_vec을 넘기면 재계산하지 않는다."""

    @pytest.mark.asyncio
    async def test_query_vec_provided_skips_embedding(self, patch_db):
        patch_db()
        embed_mock = search.embedding_service.embed

        await search.search_policy("ns", "질문", query_vec=[0.5] * 768)

        embed_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_query_vec_falls_back_to_embedding(self, patch_db):
        patch_db()
        embed_mock = search.embedding_service.embed

        await search.search_policy("ns", "질문")

        embed_mock.assert_awaited_once_with("질문")

    @pytest.mark.asyncio
    async def test_provided_vector_used_in_chunk_query(self, patch_db):
        conn = patch_db()
        await search.search_policy("ns", "질문", query_vec=[0.5] * 768)

        chunk_call = conn.fetch.call_args_list[1]
        assert chunk_call.args[2] == str([0.5] * 768)


class TestHasPolicyData:
    @pytest.mark.asyncio
    async def test_namespace_not_found_returns_false(self, monkeypatch):
        monkeypatch.setattr(search, "resolve_namespace_id", AsyncMock(return_value=None))
        assert await search.has_policy_data("없는곳") is False

    @pytest.mark.asyncio
    async def test_returns_true_when_items_exist(self, monkeypatch):
        conn = MagicMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=False)
        conn.fetchval = AsyncMock(return_value=True)
        monkeypatch.setattr(search, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(search, "resolve_namespace_id", AsyncMock(return_value=1))

        assert await search.has_policy_data("ns") is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_items(self, monkeypatch):
        conn = MagicMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=False)
        conn.fetchval = AsyncMock(return_value=False)
        monkeypatch.setattr(search, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(search, "resolve_namespace_id", AsyncMock(return_value=1))

        assert await search.has_policy_data("ns") is False


class TestBuildPolicyContext:
    def test_empty_result_returns_empty_string(self):
        assert search.build_policy_context(search.PolicySearchResult()) == ""

    def test_param_hit_formatted_without_score(self):
        result = search.PolicySearchResult(params=[
            search.ParamHit(item_id=1, logical_id=1, policy_name="배달비", category_path=[], status="active",
                             param_name="최대개수", condition="일반 배달", value="20", unit="개"),
        ])
        text = search.build_policy_context(result)
        assert "배달비" in text
        assert "20개" in text
        assert "정확 일치" in text

    def test_narrative_hit_includes_confidence_label(self):
        result = search.PolicySearchResult(narratives=[
            search.NarrativeHit(item_id=1, logical_id=1, policy_name="재고정책", category_path=[], status="active",
                                 chunk_text="재고 없으면 SOLD OUT 표기", score=0.75),
        ])
        text = search.build_policy_context(result)
        assert "재고 없으면 SOLD OUT 표기" in text
        assert "신뢰도: 높음" in text

    def test_low_score_narrative_labeled_low_confidence(self):
        result = search.PolicySearchResult(narratives=[
            search.NarrativeHit(item_id=1, logical_id=1, policy_name="p", category_path=[], status="active",
                                 chunk_text="text", score=0.2),
        ])
        assert "신뢰도: 낮음" in search.build_policy_context(result)

    def test_prefers_raw_body_over_chunk_text_when_both_present(self):
        """회귀 방지(2026-09-22, B2C 실사고) — chunk_text는 LLM이 분해한 조각 하나뿐이라
        한 항목이 여러 조각으로 쪼개졌을 때 적용범위 조건이 별도 조각으로 분리되면 그
        조건 없이 LLM에 전달된다. raw_body(원문 전체)는 이미 검색 시점에 같이 fetch되고
        있었는데도 이 함수가 chunk_text만 써서 실제로 정보 유실이 있었다 — chunk_text와
        raw_body를 의도적으로 다르게 줘서, raw_body 쪽 내용이 실제로 쓰이는지 확인한다.
        `raw_body` 필드가 dataclass 기본값(빈 문자열)으로 되돌아가는 것만으로는 이
        테스트가 통과하지 않는다(폴백이 아니라 실제 선호를 검증)."""
        result = search.PolicySearchResult(narratives=[
            search.NarrativeHit(
                item_id=1, logical_id=1, policy_name="B2C", category_path=[], status="active",
                chunk_text="각인 옵션이 제공된다.",
                raw_body="1. 자사몰만 적용\n2. SKU 기준 등록\n3. 주문선택옵션\n4. 각인옵션\n5. 선물포장옵션 등록 가능",
                score=0.75,
            ),
        ])
        text = search.build_policy_context(result)
        assert "자사몰만 적용" in text, "raw_body(원문 전체)가 아니라 chunk_text만 쓰고 있음 — 2026-09-22 수정 회귀"


class TestSelectCitedHit:
    """2026-09-07 — "근거가 너무 많이 보인다, 원문 정책 1건만 보여달라" + "10개를 참조해서
    답변 만든 거냐"는 피드백. 검색 직후 벡터 점수로 미리 고르면(구버전 select_top_policy_hit)
    실제로 무관한 후보가 뽑히고 LLM 컨텍스트까지 줄면 답변 자체가 실패하는 게 실측으로
    확인돼 폐기 — 이제는 답변 텍스트와 겹치는 후보를 답변 생성 "후"에 역추적한다."""

    def test_empty_result_returns_empty(self):
        picked = search.select_cited_hit(search.PolicySearchResult(), "아무 답변")
        assert picked.params == [] and picked.narratives == []

    def test_no_overlap_with_answer_returns_empty(self):
        result = search.PolicySearchResult(narratives=[
            search.NarrativeHit(item_id=1, logical_id=1, policy_name="배송지", category_path=[], status="active",
                                 chunk_text="배송지 관련", score=0.9, raw_body="기본 배송지 자동 노출, 최대 30개"),
        ])
        picked = search.select_cited_hit(result, "관련 지식을 찾지 못했습니다")
        assert picked.params == [] and picked.narratives == []

    def test_picks_narrative_with_highest_text_overlap_over_higher_vector_score(self):
        # 실측 재현: 벡터 점수는 "배송지"가 더 높아도, 실제 답변엔 "장바구니" 항목 내용이 쓰였다.
        result = search.PolicySearchResult(narratives=[
            search.NarrativeHit(item_id=1, logical_id=1, policy_name="배송지", category_path=[], status="active",
                                 chunk_text="배송지 관련", score=0.9, raw_body="기본 배송지 자동 노출, 최대 30개"),
            search.NarrativeHit(item_id=2, logical_id=2, policy_name="장바구니 최대 보관 수량", category_path=[], status="active",
                                 chunk_text="장바구니 관련", score=0.5,
                                 raw_body="기본 : 20개\n일반 탭 : 최대 20개\n예약 탭 : 최대 20개\n제외대상 : 추가구매상품"),
        ])
        answer = "장바구니 최대 보관 수량은 기본 20개이며, 일반 탭과 예약 탭 모두 최대 20개까지 보관 가능합니다. 제외대상은 추가구매상품입니다."
        picked = search.select_cited_hit(result, answer)
        assert len(picked.narratives) == 1
        assert picked.narratives[0].policy_name == "장바구니 최대 보관 수량"

    def test_picks_param_when_param_overlap_is_higher(self):
        result = search.PolicySearchResult(
            params=[search.ParamHit(item_id=1, logical_id=1, policy_name="장바구니", category_path=[], status="active",
                                     param_name="최대개수", condition=None, value="20", unit="개",
                                     raw_body="장바구니 최대개수 20개")],
            narratives=[search.NarrativeHit(item_id=2, logical_id=2, policy_name="무관", category_path=[], status="active",
                                             chunk_text="무관 내용", score=0.9, raw_body="전혀 다른 내용")],
        )
        picked = search.select_cited_hit(result, "장바구니 최대개수는 20개입니다.")
        assert picked.params[0].policy_name == "장바구니"
        assert picked.narratives == []


class TestSearchPolicyReranker:
    """v2.73 — reranker_enabled=True + navigation형이 아닌 질문일 때만 조건부로
    fetch_k를 넓히고(reranker_candidates) 채널별로 재정렬한다. 기본값(reranker_enabled=
    False)일 때 기존 동작이 안 바뀌는 것부터 확인한다."""

    @pytest.mark.asyncio
    async def test_reranker_disabled_by_default_uses_top_k_as_fetch_k(self, patch_db):
        conn = patch_db()
        await search.search_policy("ns", "장바구니 개수", top_k=5)
        param_call = conn.fetch.call_args_list[0]
        assert param_call.args[-1] == 5  # top_k 그대로, reranker_candidates 아님

    @pytest.mark.asyncio
    async def test_reranker_enabled_but_navigation_query_skips_reranking(self, patch_db, monkeypatch):
        conn = patch_db()
        monkeypatch.setattr(search.settings, "reranker_enabled", True)
        monkeypatch.setattr(search.settings, "reranker_candidates", 20)
        monkeypatch.setattr(search.reranker, "is_available", MagicMock(return_value=True))
        rerank_mock = AsyncMock()
        monkeypatch.setattr(search.reranker, "rerank", rerank_mock)

        await search.search_policy("ns", "장바구니 관련 정책 다 보여줘", top_k=5)

        param_call = conn.fetch.call_args_list[0]
        assert param_call.args[-1] == 5  # navigation형이라 fetch_k 확장 안 됨
        rerank_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reranker_enabled_and_non_navigation_query_widens_and_reranks(self, patch_db, monkeypatch):
        param_rows = [{
            "item_id": 1, "logical_id": 1, "policy_name": "장바구니 담기",
            "category_path": [], "status": "active", "raw_body": "본문",
            "param_name": "최대개수", "condition": None, "value": "20", "unit": "개", "rank": 0.5,
        }]
        chunk_rows = [{
            "item_id": 2, "logical_id": 2, "policy_name": "재고정책",
            "category_path": [], "status": "active", "raw_body": "본문2",
            "chunk_text": "재고 없으면 SOLD OUT", "score": 0.8,
        }]
        conn = patch_db(param_rows=param_rows, chunk_rows=chunk_rows)
        monkeypatch.setattr(search.settings, "reranker_enabled", True)
        monkeypatch.setattr(search.settings, "reranker_candidates", 20)
        monkeypatch.setattr(search.reranker, "is_available", MagicMock(return_value=True))

        async def _fake_rerank(query, wrapped, top_k):
            return wrapped[:top_k]  # 원래 순서 그대로 top_k만 자르는 스텁
        monkeypatch.setattr(search.reranker, "rerank", AsyncMock(side_effect=_fake_rerank))

        result = await search.search_policy("ns", "장바구니 최대 개수 알려줘", top_k=5)

        param_call = conn.fetch.call_args_list[0]
        assert param_call.args[-1] == 20  # fetch_k가 reranker_candidates로 확장됨
        assert len(result.params) == 1
        assert result.params[0].policy_name == "장바구니 담기"
        assert len(result.narratives) == 1
        assert result.narratives[0].chunk_text == "재고 없으면 SOLD OUT"

    @pytest.mark.asyncio
    async def test_rerank_skipped_when_candidates_already_within_top_k(self, patch_db, monkeypatch):
        """_rerank_hits는 후보 수가 이미 top_k 이하면 reranker.rerank()를 아예 안 부른다
        (불필요한 모델 호출 방지)."""
        param_rows = [{
            "item_id": 1, "logical_id": 1, "policy_name": "P", "category_path": [], "status": "active",
            "raw_body": "", "param_name": "N", "condition": None, "value": "1", "unit": None, "rank": 0.1,
        }]
        conn = patch_db(param_rows=param_rows, chunk_rows=[])
        monkeypatch.setattr(search.settings, "reranker_enabled", True)
        monkeypatch.setattr(search.settings, "reranker_candidates", 20)
        monkeypatch.setattr(search.reranker, "is_available", MagicMock(return_value=True))
        rerank_mock = AsyncMock()
        monkeypatch.setattr(search.reranker, "rerank", rerank_mock)

        await search.search_policy("ns", "질문", top_k=5)
        rerank_mock.assert_not_awaited()


class TestBuildPolicyCitations:
    """2026-09-06 편입 2단계 — 채팅 화면의 "정책 근거" 카드용 데이터. 원문(raw_body)까지
    포함해야 사용자가 "이 답이 어디서 왔는지" 직접 확인할 수 있다(사용자 피드백)."""

    def test_empty_result_returns_empty_list(self):
        assert search.build_policy_citations(search.PolicySearchResult()) == []

    def test_param_citation_includes_raw_body_and_detail(self):
        result = search.PolicySearchResult(params=[
            search.ParamHit(item_id=1, logical_id=1, policy_name="배달비", category_path=["1.주문"], status="active",
                             param_name="최대개수", condition="일반 배달", value="20", unit="개",
                             raw_body="일반 배달: 20개\n도보 배달: 4개"),
        ])
        citations = search.build_policy_citations(result)

        assert len(citations) == 1
        c = citations[0]
        assert c["kind"] == "param"
        assert c["policy_name"] == "배달비"
        assert c["category_path"] == ["1.주문"]
        assert "최대개수" in c["detail"] and "20개" in c["detail"]
        assert c["raw_body"] == "일반 배달: 20개\n도보 배달: 4개"

    def test_narrative_citation_uses_chunk_text_as_detail(self):
        result = search.PolicySearchResult(narratives=[
            search.NarrativeHit(item_id=1, logical_id=1, policy_name="재고정책", category_path=[], status="active",
                                 chunk_text="재고 없으면 SOLD OUT 표기", score=0.8,
                                 raw_body="재고 없으면 SOLD OUT 표기. 재입고 시 자동 노출."),
        ])
        citations = search.build_policy_citations(result)

        assert citations[0]["kind"] == "narrative"
        assert citations[0]["detail"] == "재고 없으면 SOLD OUT 표기"
        assert citations[0]["raw_body"] == "재고 없으면 SOLD OUT 표기. 재입고 시 자동 노출."

    def test_params_and_narratives_both_included(self):
        result = search.PolicySearchResult(
            params=[search.ParamHit(item_id=1, logical_id=1, policy_name="p1", category_path=[], status="active",
                                     param_name="n", condition=None, value="1", unit=None)],
            narratives=[search.NarrativeHit(item_id=2, logical_id=2, policy_name="p2", category_path=[], status="active",
                                             chunk_text="t", score=0.9)],
        )
        citations = search.build_policy_citations(result)
        kinds = {c["kind"] for c in citations}
        assert kinds == {"param", "narrative"}
