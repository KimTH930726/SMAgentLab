"""Tests for admin/service.py — add_tables, delete_table 증분 관리."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
import asyncio


# ── Mock DB connection context manager ────────────────────────────────────────

class FakeConn:
    """asyncpg connection mock."""
    def __init__(self):
        self.fetchval = AsyncMock(return_value=1)
        self.fetchrow = AsyncMock(return_value=None)
        self.fetch = AsyncMock(return_value=[])
        self.execute = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


_fake_conn = FakeConn()


def _mock_get_conn():
    return _fake_conn


# ── Patch dependencies and import ─────────────────────────────────────────────

# Need to patch get_conn before importing service
with patch("core.database.get_conn", _mock_get_conn):
    from agents.text2sql.admin import service


class TestAddTables:
    """service.add_tables() — 증분 테이블 추가."""

    @pytest.mark.asyncio
    async def test_add_tables_no_target_db_raises(self):
        """대상 DB 설정이 없으면 에러."""
        with patch.object(service, "get_target_db_config", AsyncMock(return_value=None)):
            with pytest.raises(ValueError, match="대상 DB 연결 정보"):
                await service.add_tables(1, ["users"])

    @pytest.mark.asyncio
    async def test_add_tables_all_existing_skipped(self):
        """모든 테이블이 이미 등록됨 → added=0."""
        fake_cfg = {"db_type": "postgresql", "host": "h", "port": 5432, "db_name": "d", "username": "u", "password": "p"}

        fake_conn = FakeConn()
        fake_conn.fetch = AsyncMock(return_value=[{"table_name": "users"}, {"table_name": "orders"}])

        with patch.object(service, "get_target_db_config", AsyncMock(return_value=fake_cfg)), \
             patch("agents.text2sql.admin.service.get_conn", lambda: fake_conn):
            result = await service.add_tables(1, ["users", "orders"])
            assert result["added"] == 0
            assert result["skipped"] == 2

    @pytest.mark.asyncio
    async def test_add_tables_new_table_added(self):
        """신규 테이블은 추가됨."""
        fake_cfg = {"db_type": "postgresql", "host": "h", "port": 5432, "db_name": "d", "username": "u", "password": "p"}

        call_count = {"n": 0}
        async def fake_fetch(*args, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # 기존 테이블 조회 → 비어있음
                return []
            # embed_queue 조회
            return [{"id": 1, "name": "id", "data_type": "int", "description": None, "table_name": "users", "namespace_id": 1}]

        fake_conn = FakeConn()
        fake_conn.fetch = AsyncMock(side_effect=fake_fetch)
        fake_conn.fetchval = AsyncMock(side_effect=[0, 100, 1])  # max_x, table_id, col_id

        fake_db = MagicMock()
        fake_db.get_tables = AsyncMock(return_value=[
            {"table_name": "users", "columns": [{"name": "id", "type": "int", "is_pk": True}]}
        ])

        fake_emb = MagicMock()
        fake_emb.embed_batch = AsyncMock(return_value=[[0.1] * 768])

        with patch.object(service, "get_target_db_config", AsyncMock(return_value=fake_cfg)), \
             patch.object(service, "build_target_db", return_value=fake_db), \
             patch("agents.text2sql.admin.service.get_conn", lambda: fake_conn), \
             patch("agents.text2sql.admin.service.embedding_service", fake_emb):
            result = await service.add_tables(1, ["users"])
            assert result["added"] == 1


class TestDeleteTable:
    """service.delete_table() — cascade 삭제."""

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self):
        """존재하지 않는 테이블 → False."""
        fake_conn = FakeConn()
        fake_conn.fetchval = AsyncMock(return_value=None)

        with patch("agents.text2sql.admin.service.get_conn", lambda: fake_conn):
            result = await service.delete_table(1, "nonexistent")
            assert result is False

    @pytest.mark.asyncio
    async def test_delete_existing_returns_true(self):
        """존재하는 테이블 → cascade 삭제 후 True."""
        fake_conn = FakeConn()
        fake_conn.fetchval = AsyncMock(return_value=42)  # table_id = 42

        with patch("agents.text2sql.admin.service.get_conn", lambda: fake_conn):
            result = await service.delete_table(1, "users")
            assert result is True
            # 벡터, 컬럼, 관계, 테이블 순서로 삭제 확인
            assert fake_conn.execute.call_count >= 4


class TestGetTableSummary:
    """service.get_table_summary()."""

    @pytest.mark.asyncio
    async def test_no_config_raises(self):
        with patch.object(service, "get_target_db_config", AsyncMock(return_value=None)):
            with pytest.raises(ValueError, match="대상 DB 연결 정보"):
                await service.get_table_summary(1)

    @pytest.mark.asyncio
    async def test_returns_summary(self):
        fake_cfg = {"db_type": "postgresql", "host": "h", "port": 5432, "db_name": "d", "username": "u", "password": "p"}
        fake_db = MagicMock()
        fake_db.get_table_summary = AsyncMock(return_value=[{"table": "t", "column_count": 3}])

        with patch.object(service, "get_target_db_config", AsyncMock(return_value=fake_cfg)), \
             patch.object(service, "build_target_db", return_value=fake_db):
            result = await service.get_table_summary(1)
            assert result == [{"table": "t", "column_count": 3}]
