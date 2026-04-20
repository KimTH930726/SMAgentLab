"""Tests for pipeline/safety.py — SQL 안전 검증 + 주석 차단."""
import pytest
from agents.text2sql.pipeline.safety import validate_sql_safety, BlockedQueryError


class TestValidateSqlSafety:
    """validate_sql_safety 핵심 검증."""

    def test_valid_select(self):
        """정상 SELECT 쿼리는 통과."""
        validate_sql_safety("SELECT id, name FROM users WHERE active = true")

    def test_valid_with_cte(self):
        """WITH CTE 쿼리도 통과."""
        validate_sql_safety("WITH cte AS (SELECT 1 AS x) SELECT * FROM cte")

    def test_empty_sql_raises(self):
        with pytest.raises(BlockedQueryError, match="비어"):
            validate_sql_safety("")

    def test_whitespace_only_raises(self):
        with pytest.raises(BlockedQueryError, match="비어"):
            validate_sql_safety("   \n\t  ")

    def test_none_sql_raises(self):
        with pytest.raises(BlockedQueryError):
            validate_sql_safety(None)  # type: ignore

    # ── 주석만 있는 SQL 차단 (신규) ──────────────────────────────────────────

    def test_comment_only_sql_blocked(self):
        """주석만 있는 SQL → BlockedQueryError '빈 SQL'."""
        with pytest.raises(BlockedQueryError, match="빈 SQL"):
            validate_sql_safety("-- 이건 주석입니다\n-- 또 다른 주석")

    def test_comment_with_select_passes(self):
        """주석 + 실제 SQL → 통과."""
        validate_sql_safety("-- 사용자 조회\nSELECT id FROM users")

    def test_block_comment_only_blocked(self):
        """블록 주석만 → 차단."""
        with pytest.raises(BlockedQueryError, match="빈 SQL"):
            validate_sql_safety("/* 전체 주석 */")

    # ── 기존 차단 키워드 테스트 ──────────────────────────────────────────────

    def test_drop_blocked(self):
        """DROP은 sqlparse가 statement type으로 잡음 → '허용되지 않는 쿼리 타입'."""
        with pytest.raises(BlockedQueryError):
            validate_sql_safety("DROP TABLE users")

    def test_delete_blocked(self):
        with pytest.raises(BlockedQueryError):
            validate_sql_safety("DELETE FROM users WHERE id = 1")

    def test_update_blocked(self):
        with pytest.raises(BlockedQueryError):
            validate_sql_safety("UPDATE users SET name = 'test'")

    def test_insert_blocked(self):
        with pytest.raises(BlockedQueryError):
            validate_sql_safety("INSERT INTO users (name) VALUES ('test')")

    def test_multi_statement_blocked(self):
        with pytest.raises(BlockedQueryError):
            validate_sql_safety("SELECT 1; DROP TABLE users")

    def test_truncate_blocked(self):
        with pytest.raises(BlockedQueryError):
            validate_sql_safety("TRUNCATE TABLE users")
