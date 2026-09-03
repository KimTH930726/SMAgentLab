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
