"""Tests for service/policy/search.py — 파라미터/서술 검색 SQL 구성 및 결과 매핑."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent

sys.modules["service.policy"] = MagicMock()
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
            "category_path": ["1.주문", "1-1.장바구니"], "status": "pending_review",
            "param_name": "최대개수", "condition": "일반 배달", "value": "20", "unit": "개",
        }]
        chunk_rows = [{
            "item_id": 2, "logical_id": 2, "policy_name": "장바구니 조회",
            "category_path": ["1.주문"], "status": "pending_review",
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
