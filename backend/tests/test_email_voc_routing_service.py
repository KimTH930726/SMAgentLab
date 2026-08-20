"""Tests for service/email_voc/routing_service.py — 폴링 설정 검증 + 라우팅 CRUD.

conftest.py가 sys.modules["service"] 전체를 MagicMock으로 치환해두므로(다른
email_voc 테스트와 동일한 문제), 파일 경로 기반으로 직접 로드한다. routing_service.py는
service.email_voc 서브모듈을 직접 import하지 않아 pipeline.py 테스트에서 필요했던
추가 stub 등록은 필요 없다.
"""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

_backend_dir = Path(__file__).resolve().parent.parent
_spec = _ilu.spec_from_file_location(
    "email_voc_routing_service_under_test", str(_backend_dir / "service" / "email_voc" / "routing_service.py"),
)
routing_service = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = routing_service
_spec.loader.exec_module(routing_service)


def _make_fake_conn():
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="")
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    conn = _make_fake_conn()
    monkeypatch.setattr(routing_service, "get_conn", MagicMock(return_value=conn))
    resolve_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(routing_service, "resolve_namespace_id", resolve_mock)
    return conn, resolve_mock


class TestUpdateSettingsValidation:
    """유효성 검사는 DB 커넥션을 열기 전에 일어난다 — DB 호출이 없어야 한다."""

    @pytest.mark.asyncio
    async def test_rejects_polling_interval_below_minimum(self, patch_db):
        conn, _ = patch_db
        with pytest.raises(ValueError, match="폴링 주기"):
            await routing_service.update_settings({"email_polling_interval_minutes": 0})
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_negative_lookback_days(self, patch_db):
        conn, _ = patch_db
        with pytest.raises(ValueError, match="재조회 기간"):
            await routing_service.update_settings({"email_lookback_days": 0})
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_relevance_score_out_of_range(self, patch_db):
        conn, _ = patch_db
        with pytest.raises(ValueError, match="관련지식 임계치"):
            await routing_service.update_settings({"email_relevance_min_score": 1.5})
        with pytest.raises(ValueError, match="관련지식 임계치"):
            await routing_service.update_settings({"email_relevance_min_score": -0.1})
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_boundary_values(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = []
        await routing_service.update_settings({"email_relevance_min_score": 0.0})
        await routing_service.update_settings({"email_relevance_min_score": 1.0})
        await routing_service.update_settings({"email_polling_interval_minutes": 1})
        # 예외 없이 통과하면 됨 — DB까지 도달했는지만 확인
        assert conn.execute.await_count == 3


class TestGetSettings:
    @pytest.mark.asyncio
    async def test_defaults_when_no_rows(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = []
        result = await routing_service.get_settings()
        assert result == {
            "email_collection_enabled": False,
            "email_polling_interval_minutes": 5,
            "email_lookback_days": 7,
            "email_relevance_min_score": 0.38,
        }

    @pytest.mark.asyncio
    async def test_parses_stored_values(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = [
            {"key": "email_collection_enabled", "value": "true"},
            {"key": "email_polling_interval_minutes", "value": "10"},
            {"key": "email_relevance_min_score", "value": "0.42"},
        ]
        result = await routing_service.get_settings()
        assert result["email_collection_enabled"] is True
        assert result["email_polling_interval_minutes"] == 10
        assert result["email_relevance_min_score"] == 0.42
        assert result["email_lookback_days"] == 7  # 값 없으면 기본값


class TestListRouting:
    @pytest.mark.asyncio
    async def test_unknown_namespace_returns_empty(self, patch_db):
        conn, resolve_mock = patch_db
        resolve_mock.return_value = None
        result = await routing_service.list_routing("no-such-ns")
        assert result == []
        conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_attaches_namespace_to_each_row(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = [{"id": 1, "part": "결제팀"}]
        result = await routing_service.list_routing("ns")
        assert result == [{"id": 1, "part": "결제팀", "namespace": "ns"}]


class TestCreateRouting:
    @pytest.mark.asyncio
    async def test_unknown_namespace_raises(self, patch_db):
        _, resolve_mock = patch_db
        resolve_mock.return_value = None
        with pytest.raises(ValueError, match="존재하지 않는 namespace"):
            await routing_service.create_routing("no-such-ns", {"mailbox_upn": "a@b.com"})

    @pytest.mark.asyncio
    async def test_duplicate_mailbox_precheck_raises(self, patch_db):
        conn, _ = patch_db
        conn.fetchval.return_value = 1  # 이미 존재
        with pytest.raises(ValueError, match="이미 등록된 메일함"):
            await routing_service.create_routing("ns", {"part": "p", "mailbox_upn": "a@b.com"})
        conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_toctou_unique_violation_converted_to_value_error(self, patch_db):
        conn, _ = patch_db
        conn.fetchval.return_value = None  # 사전체크는 통과
        conn.fetchrow.side_effect = asyncpg.exceptions.UniqueViolationError("dup")
        with pytest.raises(ValueError, match="이미 등록된 메일함"):
            await routing_service.create_routing("ns", {"part": "p", "mailbox_upn": "a@b.com"})

    @pytest.mark.asyncio
    async def test_success_attaches_namespace(self, patch_db):
        conn, _ = patch_db
        conn.fetchval.return_value = None
        conn.fetchrow.return_value = {"id": 5, "part": "p", "mailbox_upn": "a@b.com"}
        result = await routing_service.create_routing("ns", {"part": "p", "mailbox_upn": "a@b.com"})
        assert result == {"id": 5, "part": "p", "mailbox_upn": "a@b.com", "namespace": "ns"}


class TestUpdateRouting:
    @pytest.mark.asyncio
    async def test_unknown_namespace_returns_none(self, patch_db):
        _, resolve_mock = patch_db
        resolve_mock.return_value = None
        result = await routing_service.update_routing(1, "no-such-ns", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_routing_not_found_returns_none(self, patch_db):
        conn, _ = patch_db
        conn.fetchrow.return_value = None
        result = await routing_service.update_routing(999, "ns", {"part": "new"})
        assert result is None

    @pytest.mark.asyncio
    async def test_partial_update_falls_back_to_current_values(self, patch_db):
        conn, _ = patch_db
        current = {
            "part": "old_part", "mailbox_upn": "old@b.com", "teams_webhook_url": None,
            "oncall_contact_name": None, "oncall_contact_phone": None, "is_active": True,
            "mail_folder_id": None, "mail_folder_name": None,
        }
        updated = {**current, "id": 1, "part": "new_part"}
        conn.fetchrow.side_effect = [current, updated]
        result = await routing_service.update_routing(1, "ns", {"part": "new_part"})
        assert result["part"] == "new_part"
        # UPDATE 호출의 두 번째 위치 인자가 _pick("part") 결과("new_part")여야 함
        update_call_args = conn.fetchrow.call_args_list[1].args
        assert update_call_args[1] == "new_part"
        assert update_call_args[2] == "old@b.com"  # 안 넘긴 필드는 기존값 유지


class TestDeleteRouting:
    @pytest.mark.asyncio
    async def test_unknown_namespace_returns_false(self, patch_db):
        _, resolve_mock = patch_db
        resolve_mock.return_value = None
        result = await routing_service.delete_routing(1, "no-such-ns")
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_delete_returns_true(self, patch_db):
        conn, _ = patch_db
        conn.execute.return_value = "DELETE 1"
        result = await routing_service.delete_routing(1, "ns")
        assert result is True

    @pytest.mark.asyncio
    async def test_no_matching_row_returns_false(self, patch_db):
        conn, _ = patch_db
        conn.execute.return_value = "DELETE 0"
        result = await routing_service.delete_routing(999, "ns")
        assert result is False
