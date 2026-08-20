"""Tests for service/email_voc/retention.py — 30일 보관정책 정리 로직.

conftest.py가 sys.modules["service"] 전체를 MagicMock으로 치환해두므로(다른
email_voc 테스트와 동일한 문제), 파일 경로 기반으로 직접 로드한다.
"""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent
_spec = _ilu.spec_from_file_location(
    "email_voc_retention_under_test", str(_backend_dir / "service" / "email_voc" / "retention.py"),
)
retention = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = retention
_spec.loader.exec_module(retention)


def _make_fake_conn(analysis_result="DELETE 0", cycle_result="DELETE 0"):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.execute = AsyncMock(side_effect=[analysis_result, cycle_result])
    return conn


class TestParseDeleteCount:
    def test_parses_normal_command_tag(self):
        assert retention._parse_delete_count("DELETE 42") == 42

    def test_parses_zero(self):
        assert retention._parse_delete_count("DELETE 0") == 0

    def test_malformed_string_returns_zero(self):
        assert retention._parse_delete_count("") == 0
        assert retention._parse_delete_count("not a command tag") == 0


class TestCleanupOldRecords:
    @pytest.mark.asyncio
    async def test_returns_deleted_counts(self, monkeypatch):
        conn = _make_fake_conn(analysis_result="DELETE 12", cycle_result="DELETE 3")
        monkeypatch.setattr(retention, "get_conn", MagicMock(return_value=conn))
        result = await retention.cleanup_old_records()
        assert result == {"deleted_analysis": 12, "deleted_cycles": 3}

    @pytest.mark.asyncio
    async def test_zero_deletions_is_safe_to_call_repeatedly(self, monkeypatch):
        conn = _make_fake_conn(analysis_result="DELETE 0", cycle_result="DELETE 0")
        monkeypatch.setattr(retention, "get_conn", MagicMock(return_value=conn))
        result = await retention.cleanup_old_records()
        assert result == {"deleted_analysis": 0, "deleted_cycles": 0}

    @pytest.mark.asyncio
    async def test_uses_retention_days_cutoff(self, monkeypatch):
        from datetime import timedelta
        conn = _make_fake_conn()
        monkeypatch.setattr(retention, "get_conn", MagicMock(return_value=conn))
        await retention.cleanup_old_records()
        first_call_args = conn.execute.call_args_list[0].args
        assert first_call_args[1] == timedelta(days=retention.RETENTION_DAYS)
        assert "ops_email_analysis" in first_call_args[0]
        second_call_args = conn.execute.call_args_list[1].args
        assert "ops_email_poll_cycle" in second_call_args[0]
