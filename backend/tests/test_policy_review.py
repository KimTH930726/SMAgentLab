"""Tests for service/policy/review.py — 정책 항목 승인/반려 상태 전이 + 결정 이력(2026-10-01).

conftest가 service 패키지를 목으로 치환하므로 review.py와 실제 risk.py를 파일 경로로 로드하고,
auto_review는 호출만 확인하는 목으로 둔다(자동 통과 로직 자체는 test_policy_auto_review.py)."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = _ilu.spec_from_file_location(name, str(_backend_dir / rel))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sys.modules["service.policy"] = MagicMock()
risk = _load("service.policy.risk", "service/policy/risk.py")
sys.modules["service.policy"].risk = risk
auto_review = MagicMock()
auto_review.log = AsyncMock()
auto_review.on_human_reject = AsyncMock(return_value=False)
sys.modules["service.policy"].auto_review = auto_review
review = _load("service.policy.review", "service/policy/review.py")


def _row(status="pending_review", **kw):
    base = {"id": 1, "logical_id": 1, "namespace_id": 1, "status": status, "parse_status": "parsed",
            "review_sample": False, "review_rule": None, "chunk_count": 1, "unresolved_count": 0, "prior_reject": False}
    return base | kw


def _make_fake_conn(status_row):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetchrow = AsyncMock(return_value=status_row)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    def _apply(status_row):
        conn = _make_fake_conn(status_row)
        monkeypatch.setattr(review, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(review, "resolve_namespace_id", AsyncMock(return_value=1))
        auto_review.log.reset_mock()
        auto_review.on_human_reject.reset_mock()
        auto_review.on_human_reject.return_value = False
        return conn
    return _apply


class TestApproveItem:
    @pytest.mark.asyncio
    async def test_namespace_not_found_raises(self, patch_db, monkeypatch):
        patch_db(_row())
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
        patch_db(_row("active"))
        with pytest.raises(ValueError, match="검토 대기"):
            await review.approve_item("ns", 1, 99)

    @pytest.mark.asyncio
    async def test_pending_review_transitions_to_active(self, patch_db):
        conn = patch_db(_row())
        await review.approve_item("ns", 1, 99)

        assert "FOR UPDATE" in conn.fetchrow.call_args.args[0]  # 상태 확인과 변경 사이 경합 방지
        update_call = conn.execute.call_args_list[0]
        assert "UPDATE policy_item" in update_call.args[0]
        assert "reviewed_by" in update_call.args[0] and "review_source = 'human'" in update_call.args[0]
        assert update_call.args[1:] == ("active", 99, 1)

    @pytest.mark.asyncio
    async def test_decision_logged_with_risk_snapshot(self, patch_db):
        """승인/반려마다 결정 시점의 위험도·근거를 이력으로 — 자동 승인 기준 조정 데이터."""
        patch_db(_row(parse_status="unresolved", unresolved_count=2))
        await review.approve_item("ns", 1, 99)
        rows, action = auto_review.log.await_args.args[1], auto_review.log.await_args.args[2]
        assert action == "approved" and auto_review.log.await_args.kwargs["actor_id"] == 99
        assert rows[0]["risk_level"] == "high" and "미해결 조각 2개" in rows[0]["risk_reasons"][0]


class TestRejectItem:
    @pytest.mark.asyncio
    async def test_non_pending_status_raises(self, patch_db):
        patch_db(_row("rejected"))
        with pytest.raises(ValueError, match="검토 대기"):
            await review.reject_item("ns", 1, 99)

    @pytest.mark.asyncio
    async def test_pending_review_transitions_to_rejected(self, patch_db):
        conn = patch_db(_row())
        out = await review.reject_item("ns", 1, 99)

        update_call = conn.execute.call_args_list[0]
        assert update_call.args[1:] == ("rejected", 99, 1)
        assert auto_review.log.await_args.args[2] == "rejected"
        assert out == {"rule_paused": False}

    @pytest.mark.asyncio
    async def test_rejecting_sample_reports_rule_paused(self, patch_db):
        """자동 통과 표본을 반려하면 그 규칙이 멈췄다는 걸 응답으로 알린다(화면이 바로 표시)."""
        patch_db(_row(review_sample=True, review_rule=risk.LOW_RISK_RULE))
        auto_review.on_human_reject.return_value = True
        out = await review.reject_item("ns", 1, 99)
        assert out == {"rule_paused": True}
        assert auto_review.log.await_args.args[1][0]["rule_key"] == risk.LOW_RISK_RULE

    @pytest.mark.asyncio
    async def test_approve_never_pauses_rule(self, patch_db):
        patch_db(_row(review_sample=True, review_rule=risk.LOW_RISK_RULE))
        await review.approve_item("ns", 1, 99)
        auto_review.on_human_reject.assert_not_awaited()
