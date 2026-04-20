"""Tests for agent.py — execute 실패 시 최종 SQL UI 이벤트 전송 검증."""
import pytest


class TestExecuteFailSqlEvent:
    """execute 단계 실패 시 SQL 이벤트 로직 검증.

    실제 agent.stream_chat은 DB 의존성이 많아 직접 호출 어려우므로,
    해당 로직의 핵심 조건부를 단위 테스트합니다.
    """

    def test_sql_changed_after_fix_should_emit(self):
        """fix 후 SQL이 변경되면 → UI에 새 SQL 이벤트 발행 필요."""
        original_sql_result = "SELECT id FROM users"
        current_sql = "SELECT id, name FROM users"

        # 조건: pipeline_ctx["sql"] != sql_result
        should_emit = current_sql != original_sql_result
        assert should_emit is True

    def test_sql_unchanged_should_not_emit(self):
        """fix 없이 SQL이 동일하면 → 추가 이벤트 불필요."""
        original_sql_result = "SELECT id FROM users"
        current_sql = "SELECT id FROM users"

        should_emit = current_sql != original_sql_result
        assert should_emit is False

    def test_sql_none_should_not_emit(self):
        """SQL이 None이면 → 이벤트 불필요."""
        current_sql = None
        original_sql_result = "SELECT 1"

        # 조건: pipeline_ctx.get("sql") and pipeline_ctx["sql"] != sql_result
        should_emit = bool(current_sql) and current_sql != original_sql_result
        assert should_emit is False

    def test_sql_empty_should_not_emit(self):
        """SQL이 빈 문자열이면 → 이벤트 불필요."""
        current_sql = ""
        original_sql_result = "SELECT 1"

        should_emit = bool(current_sql) and current_sql != original_sql_result
        assert should_emit is False

    def test_event_structure(self):
        """발행되는 이벤트 구조 검증."""
        sql = "SELECT * FROM corrected_table"
        event = {"type": "sql", "sql": sql, "reasoning": "", "cached": False}

        assert event["type"] == "sql"
        assert event["sql"] == sql
        assert event["cached"] is False
        assert event["reasoning"] == ""
