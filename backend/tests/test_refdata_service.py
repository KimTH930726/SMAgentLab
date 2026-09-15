"""Tests for service/refdata/service.py — DB 스키마 사전/공통코드 적재·검색."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent

sys.modules["service.refdata"] = MagicMock()
_parser_spec = _ilu.spec_from_file_location(
    "service.refdata.parser", str(_backend_dir / "service" / "refdata" / "parser.py")
)
parser = _ilu.module_from_spec(_parser_spec)
sys.modules["service.refdata.parser"] = parser
_parser_spec.loader.exec_module(parser)

_spec = _ilu.spec_from_file_location(
    "service.refdata.service", str(_backend_dir / "service" / "refdata" / "service.py")
)
service = _ilu.module_from_spec(_spec)
sys.modules["service.refdata.service"] = service
_spec.loader.exec_module(service)


def _make_fake_conn(fetch_return=None):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    def _apply(fetch_return=None):
        conn = _make_fake_conn(fetch_return)
        monkeypatch.setattr(service, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(service, "resolve_namespace_id", AsyncMock(return_value=1))
        return conn
    return _apply


class TestIngestDbColumns:
    @pytest.mark.asyncio
    async def test_namespace_not_found_raises(self, patch_db, monkeypatch):
        patch_db()
        monkeypatch.setattr(service, "resolve_namespace_id", AsyncMock(return_value=None))
        with pytest.raises(ValueError, match="네임스페이스"):
            await service.ingest_db_columns("없는곳", [], "f.md")

    @pytest.mark.asyncio
    async def test_inserts_valid_rows(self, patch_db):
        conn = patch_db()
        rows = [
            parser.ParsedDbColumn("T1", "설명", "COL1", "컬럼설명", "VARCHAR2(5)", "Y"),
            parser.ParsedDbColumn("T1", "설명", "COL2", "컬럼설명2", "NUMBER", "N"),
        ]
        summary = await service.ingest_db_columns("ns", rows, "f.md")
        assert summary.inserted == 2
        assert summary.skipped_empty == 0
        assert conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_skips_rows_missing_table_or_column_name(self, patch_db):
        conn = patch_db()
        rows = [
            parser.ParsedDbColumn("", "설명", "COL1", "", "VARCHAR2(5)", "Y"),
            parser.ParsedDbColumn("T1", "", "", "", "", ""),
        ]
        summary = await service.ingest_db_columns("ns", rows, "f.md")
        assert summary.inserted == 0
        assert summary.skipped_empty == 2
        conn.execute.assert_not_awaited()


class TestIngestCommonCodes:
    @pytest.mark.asyncio
    async def test_inserts_valid_rows(self, patch_db):
        conn = patch_db()
        rows = [parser.ParsedCommonCode("COM", "C0002", "서비스코드", "02", "모바일", {"MNGMN_ITM01_VALUE": "Y"})]
        summary = await service.ingest_common_codes("ns", rows, "f.md")
        assert summary.inserted == 1
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_rows_missing_group_or_code_id(self, patch_db):
        conn = patch_db()
        rows = [parser.ParsedCommonCode("COM", "", "서비스코드", "02", "모바일", {})]
        summary = await service.ingest_common_codes("ns", rows, "f.md")
        assert summary.inserted == 0
        assert summary.skipped_empty == 1
        conn.execute.assert_not_awaited()


class TestSearchDbColumns:
    @pytest.mark.asyncio
    async def test_namespace_not_found_returns_empty(self, patch_db, monkeypatch):
        patch_db()
        monkeypatch.setattr(service, "resolve_namespace_id", AsyncMock(return_value=None))
        assert await service.search_db_columns("없는곳", "질문") == []

    @pytest.mark.asyncio
    async def test_returns_mapped_rows(self, patch_db):
        patch_db(fetch_return=[{
            "table_name": "T1", "table_comment": "설명", "column_name": "COL1",
            "column_comment": "컬럼설명", "data_type": "VARCHAR2(5)", "nullable": "Y", "rank": 0.5,
        }])
        results = await service.search_db_columns("ns", "COL1")
        assert len(results) == 1
        assert results[0]["column_name"] == "COL1"


class TestSearchCommonCodes:
    @pytest.mark.asyncio
    async def test_returns_mapped_rows(self, patch_db):
        patch_db(fetch_return=[{
            "work_code": "COM", "group_code": "C0002", "group_code_name": "서비스코드",
            "code_id": "02", "code_name": "모바일", "mgmt_values": None, "rank": 0.5,
        }])
        results = await service.search_common_codes("ns", "서비스코드")
        assert len(results) == 1
        assert results[0]["code_name"] == "모바일"
