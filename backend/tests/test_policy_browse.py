"""Tests for service/policy/browse.py — item 단위 브라우저(param/narrative 자식 포함)."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent

sys.modules["service.policy"] = MagicMock()
_spec = _ilu.spec_from_file_location("service.policy.browse", str(_backend_dir / "service" / "policy" / "browse.py"))
browse = _ilu.module_from_spec(_spec)
sys.modules["service.policy.browse"] = browse
_spec.loader.exec_module(browse)


def _make_fake_conn(item_rows=None, param_rows=None, chunk_rows=None):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(side_effect=[item_rows or [], param_rows or [], chunk_rows or []])
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    def _apply(item_rows=None, param_rows=None, chunk_rows=None):
        conn = _make_fake_conn(item_rows, param_rows, chunk_rows)
        monkeypatch.setattr(browse, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(browse, "resolve_namespace_id", AsyncMock(return_value=1))
        return conn
    return _apply


class TestListPolicyItems:
    @pytest.mark.asyncio
    async def test_namespace_not_found_raises(self, patch_db, monkeypatch):
        patch_db()
        monkeypatch.setattr(browse, "resolve_namespace_id", AsyncMock(return_value=None))
        with pytest.raises(ValueError, match="네임스페이스"):
            await browse.list_policy_items("없는곳")

    @pytest.mark.asyncio
    async def test_no_items_returns_empty_list_without_extra_queries(self, patch_db):
        conn = patch_db(item_rows=[])
        result = await browse.list_policy_items("ns")
        assert result == []
        # item이 없으면 param/chunk 조회 자체를 스킵해야 함(불필요한 쿼리 방지)
        assert conn.fetch.await_count == 1

    @pytest.mark.asyncio
    async def test_item_with_params_and_narratives_grouped_correctly(self, patch_db):
        item_rows = [
            {"id": 1, "logical_id": 1, "version": 1, "policy_name": "장바구니 담기",
             "category_path": ["1.주문", "1-1.장바구니"], "status": "pending_review",
             "parse_status": "parsed", "system_key": "온라인스토어"},
        ]
        param_rows = [
            {"id": 10, "policy_item_id": 1, "name": "최대개수", "condition": "일반 배달", "value": "20", "unit": "개"},
        ]
        chunk_rows = [
            {"id": 20, "policy_item_id": 1, "chunk_text": "재고 없으면 SOLD OUT 표기", "chunk_idx": 0},
        ]
        patch_db(item_rows=item_rows, param_rows=param_rows, chunk_rows=chunk_rows)

        result = await browse.list_policy_items("ns")

        assert len(result) == 1
        item = result[0]
        assert item.item_id == 1
        assert item.policy_name == "장바구니 담기"
        assert len(item.params) == 1
        assert item.params[0].name == "최대개수"
        assert len(item.narratives) == 1
        assert item.narratives[0].chunk_text == "재고 없으면 SOLD OUT 표기"

    @pytest.mark.asyncio
    async def test_item_without_children_has_empty_lists_not_missing(self, patch_db):
        item_rows = [
            {"id": 2, "logical_id": 2, "version": 1, "policy_name": "미분해 항목",
             "category_path": [], "status": "pending_review", "parse_status": "unresolved", "system_key": None},
        ]
        patch_db(item_rows=item_rows, param_rows=[], chunk_rows=[])

        result = await browse.list_policy_items("ns")

        assert result[0].params == []
        assert result[0].narratives == []

    @pytest.mark.asyncio
    async def test_children_correctly_mapped_to_matching_item_only(self, patch_db):
        """여러 item이 섞여있을 때 param/chunk가 잘못된 item에 붙지 않는지 확인."""
        item_rows = [
            {"id": 1, "logical_id": 1, "version": 1, "policy_name": "정책A", "category_path": [],
             "status": "pending_review", "parse_status": "parsed", "system_key": "s"},
            {"id": 2, "logical_id": 2, "version": 1, "policy_name": "정책B", "category_path": [],
             "status": "pending_review", "parse_status": "parsed", "system_key": "s"},
        ]
        param_rows = [
            {"id": 10, "policy_item_id": 2, "name": "B의 파라미터", "condition": None, "value": "1", "unit": None},
        ]
        patch_db(item_rows=item_rows, param_rows=param_rows, chunk_rows=[])

        result = await browse.list_policy_items("ns")

        by_id = {r.item_id: r for r in result}
        assert by_id[1].params == []
        assert len(by_id[2].params) == 1
        assert by_id[2].params[0].name == "B의 파라미터"

    @pytest.mark.asyncio
    async def test_category_filter_passed_to_query(self, patch_db):
        conn = patch_db(item_rows=[])
        await browse.list_policy_items("ns", category="1.주문")

        call = conn.fetch.call_args_list[0]
        assert "= ANY(category_path)" in call.args[0]
        assert call.args[-1] == "1.주문"

    @pytest.mark.asyncio
    async def test_q_filter_uses_ilike_on_policy_name(self, patch_db):
        conn = patch_db(item_rows=[])
        await browse.list_policy_items("ns", q="장바구니")

        call = conn.fetch.call_args_list[0]
        assert "policy_name ILIKE" in call.args[0]
        assert call.args[-1] == "%장바구니%"

    @pytest.mark.asyncio
    async def test_no_filters_omits_both_clauses(self, patch_db):
        conn = patch_db(item_rows=[])
        await browse.list_policy_items("ns")

        call = conn.fetch.call_args_list[0]
        assert "ANY(category_path)" not in call.args[0]
        assert "ILIKE" not in call.args[0]
        assert len(call.args) == 2  # sql + ns_id만
