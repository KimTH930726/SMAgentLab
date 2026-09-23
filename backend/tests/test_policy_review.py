"""Tests for service/policy/review.py — 정책 항목 승인/반려 상태 전이."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent

sys.modules["service.policy"] = MagicMock()
_spec = _ilu.spec_from_file_location(
    "service.policy.review", str(_backend_dir / "service" / "policy" / "review.py")
)
review = _ilu.module_from_spec(_spec)
sys.modules["service.policy.review"] = review
_spec.loader.exec_module(review)


def _make_fake_conn(status_row):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetchrow = AsyncMock(return_value=status_row)
    conn.execute = AsyncMock()
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    def _apply(status_row):
        conn = _make_fake_conn(status_row)
        monkeypatch.setattr(review, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(review, "resolve_namespace_id", AsyncMock(return_value=1))
        return conn
    return _apply


class TestApproveItem:
    @pytest.mark.asyncio
    async def test_namespace_not_found_raises(self, patch_db, monkeypatch):
        patch_db({"status": "pending_review"})
        monkeypatch.setattr(review, "resolve_namespace_id", AsyncMock(return_value=None))
        with pytest.raises(ValueError, match="네임스페이스"):
            await review.approve_item("없는곳", 1, 99)

    @pytest.mark.asyncio
    async def test_item_not_found_raises(self, patch_db):
        patch_db(None)
        with pytest.raises(ValueError, match="정책 항목"):
            await review.approve_item("ns", 999, 99)

    @pytest.mark.asyncio
    async def test_non_pending_status_raises(self, patch_db):
        patch_db({"status": "active"})
        with pytest.raises(ValueError, match="검토 대기"):
            await review.approve_item("ns", 1, 99)

    @pytest.mark.asyncio
    async def test_pending_review_transitions_to_active(self, patch_db):
        conn = patch_db({"status": "pending_review"})
        await review.approve_item("ns", 1, 99)

        update_call = conn.execute.call_args_list[0]
        assert "UPDATE policy_item" in update_call.args[0]
        assert "reviewed_by" in update_call.args[0]
        assert update_call.args[1] == "active"
        assert update_call.args[2] == 99  # reviewer_id
        assert update_call.args[3] == 1  # item_id


class TestRejectItem:
    @pytest.mark.asyncio
    async def test_non_pending_status_raises(self, patch_db):
        patch_db({"status": "rejected"})
        with pytest.raises(ValueError, match="검토 대기"):
            await review.reject_item("ns", 1, 99)

    @pytest.mark.asyncio
    async def test_pending_review_transitions_to_rejected(self, patch_db):
        conn = patch_db({"status": "pending_review"})
        await review.reject_item("ns", 1, 99)

        update_call = conn.execute.call_args_list[0]
        assert update_call.args[1] == "rejected"
        assert update_call.args[2] == 99
        assert update_call.args[3] == 1
