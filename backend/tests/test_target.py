"""Tests for admin/target.py — get_table_summary + get_tables(only=)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.text2sql.admin.target import TargetDBManager, PgDialect, MysqlDialect, SqliteDialect, OracleDialect


# ─── TargetDBManager.get_tables(only=) 테스트 ────────────────────────────────

class TestGetTablesOnly:
    """get_tables(only=) 파라미터 필터링 검증."""

    @pytest.mark.asyncio
    async def test_get_tables_without_only_returns_all(self):
        """only 없이 호출하면 전체 테이블 반환."""
        fake_tables = [
            {"table_name": "users", "columns": [{"name": "id", "type": "int", "is_pk": True}]},
            {"table_name": "orders", "columns": [{"name": "id", "type": "int", "is_pk": True}]},
            {"table_name": "products", "columns": [{"name": "id", "type": "int", "is_pk": True}]},
        ]
        mgr = TargetDBManager("postgresql", "localhost", 5432, "test", "user", "pass")
        mgr.connect = AsyncMock()
        mgr.close = AsyncMock()
        mgr._dialect.get_tables = AsyncMock(return_value=fake_tables)

        result = await mgr.get_tables()
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_get_tables_with_only_filters(self):
        """only=['users', 'orders'] → 해당 테이블만 반환."""
        fake_tables = [
            {"table_name": "users", "columns": []},
            {"table_name": "orders", "columns": []},
            {"table_name": "products", "columns": []},
        ]
        mgr = TargetDBManager("postgresql", "localhost", 5432, "test", "user", "pass")
        mgr.connect = AsyncMock()
        mgr.close = AsyncMock()
        mgr._dialect.get_tables = AsyncMock(return_value=fake_tables)

        result = await mgr.get_tables(only=["users", "orders"])
        assert len(result) == 2
        names = {t["table_name"] for t in result}
        assert names == {"users", "orders"}

    @pytest.mark.asyncio
    async def test_get_tables_only_case_insensitive(self):
        """only는 대소문자 무시."""
        fake_tables = [
            {"table_name": "Users", "columns": []},
            {"table_name": "ORDERS", "columns": []},
        ]
        mgr = TargetDBManager("postgresql", "localhost", 5432, "test", "user", "pass")
        mgr.connect = AsyncMock()
        mgr.close = AsyncMock()
        mgr._dialect.get_tables = AsyncMock(return_value=fake_tables)

        result = await mgr.get_tables(only=["users"])
        assert len(result) == 1
        assert result[0]["table_name"] == "Users"

    @pytest.mark.asyncio
    async def test_get_tables_only_nonexistent_returns_empty(self):
        """존재하지 않는 테이블 요청 → 빈 리스트."""
        fake_tables = [{"table_name": "users", "columns": []}]
        mgr = TargetDBManager("postgresql", "localhost", 5432, "test", "user", "pass")
        mgr.connect = AsyncMock()
        mgr.close = AsyncMock()
        mgr._dialect.get_tables = AsyncMock(return_value=fake_tables)

        result = await mgr.get_tables(only=["nonexistent"])
        assert result == []

    @pytest.mark.asyncio
    async def test_get_tables_only_empty_list_returns_all(self):
        """only=[] 전달 시 전체 반환 (falsy)."""
        fake_tables = [{"table_name": "a", "columns": []}, {"table_name": "b", "columns": []}]
        mgr = TargetDBManager("postgresql", "localhost", 5432, "test", "user", "pass")
        mgr.connect = AsyncMock()
        mgr.close = AsyncMock()
        mgr._dialect.get_tables = AsyncMock(return_value=fake_tables)

        result = await mgr.get_tables(only=[])
        assert len(result) == 2


# ─── get_table_summary 테스트 ────────────────────────────────────────────────

class TestGetTableSummary:
    """get_table_summary() — 빠른 테이블 요약 조회."""

    @pytest.mark.asyncio
    async def test_summary_returns_table_and_count(self):
        """summary 포맷: [{table, column_count}]."""
        fake_summary = [
            {"table": "users", "column_count": 5},
            {"table": "orders", "column_count": 8},
        ]
        mgr = TargetDBManager("postgresql", "localhost", 5432, "test", "user", "pass")
        mgr.connect = AsyncMock()
        mgr.close = AsyncMock()
        mgr._dialect.get_table_summary = AsyncMock(return_value=fake_summary)

        result = await mgr.get_table_summary()
        assert len(result) == 2
        assert result[0]["table"] == "users"
        assert result[0]["column_count"] == 5

    @pytest.mark.asyncio
    async def test_summary_closes_connection(self):
        """summary 호출 후 반드시 connection close."""
        mgr = TargetDBManager("postgresql", "localhost", 5432, "test", "user", "pass")
        mgr.connect = AsyncMock()
        mgr.close = AsyncMock()
        mgr._dialect.get_table_summary = AsyncMock(return_value=[])

        await mgr.get_table_summary()
        mgr.close.assert_called_once()


# ─── BaseDialect fallback 테스트 ─────────────────────────────────────────────

class TestDialectSummaryFallback:
    """BaseDialect.get_table_summary 기본 구현 (fallback)은 get_tables 결과를 변환."""

    @pytest.mark.asyncio
    async def test_fallback_converts_get_tables_result(self):
        """get_table_summary 기본 구현은 get_tables → [{table, column_count}] 변환."""
        from agents.text2sql.admin.target import BaseDialect

        class StubDialect(BaseDialect):
            async def connect(self, *a, **kw): pass
            async def close(self, *a): pass
            async def get_schemas(self, *a): return []
            async def get_tables(self, conn, schema):
                return [
                    {"table_name": "t1", "columns": [1, 2, 3]},
                    {"table_name": "t2", "columns": [1]},
                ]
            async def execute_query(self, *a): return {}

        d = StubDialect()
        result = await d.get_table_summary(None, None)
        assert result == [
            {"table": "t1", "column_count": 3},
            {"table": "t2", "column_count": 1},
        ]


# ─── 지원하지 않는 DB 타입 ───────────────────────────────────────────────────

class TestUnsupportedDialect:
    def test_unsupported_db_type_raises(self):
        with pytest.raises(ValueError, match="지원하지 않는 DB 타입"):
            TargetDBManager("mongodb", "localhost", 27017, "test", "user", "pass")
