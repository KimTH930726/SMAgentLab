"""Tests for service/policy/browse.py — item 단위 브라우저(param/narrative 자식 + q 검색 포함)."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent


def _load(name: str, rel_path: str):
    spec = _ilu.spec_from_file_location(name, str(_backend_dir / rel_path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_policy_pkg = MagicMock()
sys.modules["service.policy"] = _policy_pkg
search = _load("service.policy.search", "service/policy/search.py")
_policy_pkg.search = search
browse = _load("service.policy.browse", "service/policy/browse.py")


def _make_fake_conn(item_rows=None, param_rows=None, chunk_rows=None):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(side_effect=[item_rows or [], param_rows or [], chunk_rows or []])
    return conn


def _item_row(id_, name="정책", category_path=None, raw_body="본문"):
    return {
        "id": id_, "logical_id": id_, "version": 1, "policy_name": name,
        "category_path": category_path or [], "raw_body": raw_body,
        "status": "pending_review", "parse_status": "parsed", "system_key": "s",
    }


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
        item_rows = [_item_row(1, "장바구니 담기", ["1.주문", "1-1.장바구니"], "일반 배달: 20개")]
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
        assert item.raw_body == "일반 배달: 20개"
        assert len(item.params) == 1
        assert item.params[0].name == "최대개수"
        assert len(item.narratives) == 1
        assert item.narratives[0].chunk_text == "재고 없으면 SOLD OUT 표기"
        assert item.matched_via == []  # q 없이 조회했으니 비어있어야 함

    @pytest.mark.asyncio
    async def test_item_without_children_has_empty_lists_not_missing(self, patch_db):
        item_rows = [_item_row(2, "미분해 항목")]
        patch_db(item_rows=item_rows, param_rows=[], chunk_rows=[])

        result = await browse.list_policy_items("ns")

        assert result[0].params == []
        assert result[0].narratives == []

    @pytest.mark.asyncio
    async def test_children_correctly_mapped_to_matching_item_only(self, patch_db):
        """여러 item이 섞여있을 때 param/chunk가 잘못된 item에 붙지 않는지 확인."""
        item_rows = [_item_row(1, "정책A"), _item_row(2, "정책B")]
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
    async def test_no_filters_omits_both_clauses(self, patch_db):
        conn = patch_db(item_rows=[])
        await browse.list_policy_items("ns")

        call = conn.fetch.call_args_list[0]
        assert "ANY(category_path)" not in call.args[0]
        assert "id = ANY" not in call.args[0]
        assert len(call.args) == 2  # sql + ns_id만


class TestListPolicyItemsWithQuery:
    """2026-09-04 개선 — q는 더 이상 policy_name ILIKE가 아니라 실제 search_policy()(RDB
    tsquery + 벡터)를 재사용한다. matched_via로 어느 경로로 걸렸는지도 알려준다."""

    def _mock_search_result(self, param_item_ids=(), narrative_item_ids=(), narrative_score=0.9):
        result = MagicMock()
        result.params = [MagicMock(item_id=i) for i in param_item_ids]
        result.narratives = [MagicMock(item_id=i, score=narrative_score) for i in narrative_item_ids]
        return result

    @pytest.mark.asyncio
    async def test_q_uses_search_policy_not_ilike(self, patch_db, monkeypatch):
        search_mock = AsyncMock(return_value=self._mock_search_result(param_item_ids=[1]))
        monkeypatch.setattr(search, "search_policy", search_mock)
        conn = patch_db(item_rows=[_item_row(1, "정책")])

        await browse.list_policy_items("ns", q="장바구니")

        search_mock.assert_awaited_once_with("ns", "장바구니", None, top_k=browse._SEARCH_MATCH_LIMIT)
        call = conn.fetch.call_args_list[0]
        assert "id = ANY" in call.args[0]
        assert "ILIKE" not in call.args[0]
        assert call.args[-1] == [1]

    @pytest.mark.asyncio
    async def test_no_search_matches_returns_empty_without_item_query(self, patch_db, monkeypatch):
        search_mock = AsyncMock(return_value=self._mock_search_result())
        monkeypatch.setattr(search, "search_policy", search_mock)
        conn = patch_db(item_rows=[_item_row(1)])  # 호출되면 안 됨

        result = await browse.list_policy_items("ns", q="존재안하는말")

        assert result == []
        conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_matched_via_reflects_param_only(self, patch_db, monkeypatch):
        monkeypatch.setattr(search, "search_policy", AsyncMock(return_value=self._mock_search_result(param_item_ids=[1])))
        patch_db(item_rows=[_item_row(1)])

        result = await browse.list_policy_items("ns", q="q")

        assert result[0].matched_via == ["param"]

    @pytest.mark.asyncio
    async def test_matched_via_reflects_narrative_only(self, patch_db, monkeypatch):
        monkeypatch.setattr(search, "search_policy", AsyncMock(return_value=self._mock_search_result(narrative_item_ids=[1])))
        patch_db(item_rows=[_item_row(1)])

        result = await browse.list_policy_items("ns", q="q")

        assert result[0].matched_via == ["narrative"]

    @pytest.mark.asyncio
    async def test_matched_via_reflects_both_when_item_hits_both(self, patch_db, monkeypatch):
        monkeypatch.setattr(
            search, "search_policy",
            AsyncMock(return_value=self._mock_search_result(param_item_ids=[1], narrative_item_ids=[1])),
        )
        patch_db(item_rows=[_item_row(1)])

        result = await browse.list_policy_items("ns", q="q")

        assert result[0].matched_via == ["narrative", "param"]  # sorted()

    @pytest.mark.asyncio
    async def test_low_score_narrative_excluded_from_matches(self, patch_db, monkeypatch):
        """narrative는 코사인 유사도라 임계치 없이 top-K를 그대로 쓰면 사실상 전부 매칭돼버림
        (실측: 딜리버스 71개 중 67개 — 필터 역할을 못 함, 2026-09-04). 최소 점수 미만은 걸러야 함."""
        low_score_result = self._mock_search_result(narrative_item_ids=[1], narrative_score=0.1)
        monkeypatch.setattr(search, "search_policy", AsyncMock(return_value=low_score_result))
        conn = patch_db(item_rows=[])

        result = await browse.list_policy_items("ns", q="q")

        assert result == []
        conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_score_narrative_included(self, patch_db, monkeypatch):
        high_score_result = self._mock_search_result(narrative_item_ids=[1], narrative_score=0.9)
        monkeypatch.setattr(search, "search_policy", AsyncMock(return_value=high_score_result))
        patch_db(item_rows=[_item_row(1)])

        result = await browse.list_policy_items("ns", q="q")

        assert len(result) == 1
        assert result[0].matched_via == ["narrative"]

    @pytest.mark.asyncio
    async def test_q_and_category_combined(self, patch_db, monkeypatch):
        monkeypatch.setattr(search, "search_policy", AsyncMock(return_value=self._mock_search_result(param_item_ids=[1, 2])))
        conn = patch_db(item_rows=[_item_row(1)])

        await browse.list_policy_items("ns", category="1.주문", q="q")

        call = conn.fetch.call_args_list[0]
        assert "ANY(category_path)" in call.args[0]
        assert "id = ANY" in call.args[0]
