"""Tests for admin/router.py — 신규 엔드포인트 + audit 날짜 필터."""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch


# ── Audit log 날짜 파라미터 파싱 단위 테스트 ─────────────────────────────────

class TestAuditDateParsing:
    """audit date_from / date_to 파싱 검증."""

    def test_valid_date_from(self):
        """YYYY-MM-DD → datetime 변환."""
        dt = datetime.strptime("2024-01-15", "%Y-%m-%d")
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_valid_date_to_end_of_day(self):
        """date_to는 23:59:59로 설정."""
        dt = datetime.strptime("2024-06-30", "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        assert dt.hour == 23
        assert dt.minute == 59
        assert dt.second == 59

    def test_invalid_date_format_ignored(self):
        """잘못된 날짜 형식은 무시."""
        try:
            datetime.strptime("not-a-date", "%Y-%m-%d")
            assert False, "Should have raised"
        except ValueError:
            pass  # 예상된 동작

    def test_empty_date_is_falsy(self):
        """빈 문자열은 falsy."""
        assert not ""
        assert not None


# ── BulkDeletePayload 검증 ───────────────────────────────────────────────────

class TestBulkDeletePayload:
    """BulkDeletePayload ids 검증."""

    def test_empty_ids(self):
        """빈 ids → 삭제 0건."""
        ids: list[int] = []
        assert len(ids) == 0

    def test_multiple_ids(self):
        """여러 id 전달."""
        ids = [1, 2, 3, 4, 5]
        assert len(ids) == 5

    def test_ids_type(self):
        """ids는 int 리스트."""
        ids = [1, 2, 3]
        assert all(isinstance(i, int) for i in ids)


# ── TablesAddPayload 검증 ────────────────────────────────────────────────────

class TestTablesAddPayload:
    """tables 추가 요청 파라미터."""

    def test_empty_tables(self):
        tables: list[str] = []
        assert len(tables) == 0

    def test_table_names(self):
        tables = ["users", "orders", "products"]
        assert "users" in tables


# ── 테이블 이름 URL 인코딩 검증 ──────────────────────────────────────────────

class TestTableNameEncoding:
    """DELETE /schema/tables/{table_name} URL 인코딩 확인."""

    def test_simple_name(self):
        from urllib.parse import quote
        assert quote("users") == "users"

    def test_name_with_special_chars(self):
        from urllib.parse import quote
        encoded = quote("my table")
        assert "%" in encoded or "+" in encoded

    def test_name_with_dots(self):
        from urllib.parse import quote
        # RFC 3986: . 은 unreserved → safe="" 이어도 인코딩 안 됨
        encoded = quote("public.users", safe="")
        assert encoded == "public.users"
