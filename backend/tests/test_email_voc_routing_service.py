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
            "email_pattern_similarity_threshold": 0.8,   # 10/22 "대상 기능 + 증상" 요약 기준(#81)
            "email_pattern_window_days": 3,
            "email_pattern_min_count": 3,
            "email_pattern_cluster_min_id": 0,           # 요약 방식 경계 없음
            "email_pattern_signature_since": "",
        }

    @pytest.mark.asyncio
    async def test_boundary_keys_read_but_not_updatable(self, patch_db):
        """경계는 #81이 기록하는 값 — 읽어서 묶기에 넘기되, 설정 저장(화면)으로는 못 바꾼다."""
        conn, _ = patch_db
        conn.fetch.return_value = [{"key": "email_pattern_cluster_min_id", "value": "197"},
                                   {"key": "email_pattern_signature_since", "value": "2026-10-22 21:00:00+09"}]
        result = await routing_service.get_settings()
        assert result["email_pattern_cluster_min_id"] == 197 and result["email_pattern_signature_since"] == "2026-10-22 21:00:00+09"
        conn.execute.reset_mock()
        await routing_service.update_settings({"email_pattern_cluster_min_id": 0})
        assert conn.execute.await_count == 0

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
        # 사전체크 통과(None) → 파트 id(3) → INSERT에서 경합
        conn.fetchval.side_effect = [None, 3, asyncpg.exceptions.UniqueViolationError("dup")]
        with pytest.raises(ValueError, match="이미 등록된 메일함"):
            await routing_service.create_routing("ns", {"part": "p", "mailbox_upn": "a@b.com"})

    @pytest.mark.asyncio
    async def test_success_stores_part_link_and_returns_current_name(self, patch_db):
        """담당 파트는 이름 대신 연결(part_id)로 저장 — 파트 이름이 바뀌어도 끊기지 않게(2026-10-20)."""
        conn, _ = patch_db
        conn.fetchval.side_effect = [None, 3, 5]          # 사전체크 · 파트 id · 새 행 id
        conn.fetchrow.return_value = {"id": 5, "part": "p", "part_id": 3, "mailbox_upn": "a@b.com"}
        result = await routing_service.create_routing("ns", {"part": "p", "mailbox_upn": "a@b.com"})
        assert result == {"id": 5, "part": "p", "part_id": 3, "mailbox_upn": "a@b.com", "namespace": "ns"}
        insert = conn.fetchval.call_args_list[2].args
        assert "part_id" in insert[0] and insert[2] == 3      # 이름이 아니라 id

    @pytest.mark.asyncio
    async def test_unknown_part_rejected(self, patch_db):
        conn, _ = patch_db
        conn.fetchval.side_effect = [None, None]          # 사전체크 통과 · 파트 없음
        with pytest.raises(ValueError, match="없는 파트"):
            await routing_service.create_routing("ns", {"part": "없는파트", "mailbox_upn": "a@b.com"})


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
            "part": "old_part", "part_id": 1, "mailbox_upn": "old@b.com", "teams_webhook_url": None,
            "oncall_contact_name": None, "oncall_contact_phone": None, "is_active": True,
            "mail_folder_id": None, "mail_folder_name": None,
        }
        updated = {**current, "id": 1, "part": "new_part", "part_id": 2}
        conn.fetchrow.side_effect = [current, updated]
        conn.fetchval.return_value = 2                     # new_part의 id
        result = await routing_service.update_routing(1, "ns", {"part": "new_part"})
        assert result["part"] == "new_part"
        update_call_args = conn.execute.call_args_list[0].args
        assert update_call_args[1] == 2                    # 담당 파트는 연결(id)로
        assert update_call_args[2] == "old@b.com"          # 안 넘긴 필드는 기존값 유지


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
