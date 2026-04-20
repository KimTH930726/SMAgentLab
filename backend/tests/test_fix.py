"""Tests for pipeline/fix.py — SQL Fixer 안정성 개선 검증."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# get_prompt를 stub으로 교체
_get_prompt_stub = AsyncMock(side_effect=lambda key, default: default)
with patch.dict("sys.modules", {"service.prompt.loader": MagicMock(get_prompt=_get_prompt_stub)}):
    from agents.text2sql.pipeline.fix import _extract_sql, run
    from agents.text2sql.pipeline.safety import BlockedQueryError


# ─── _extract_sql tests ──────────────────────────────────────────────────────

class TestExtractSql:
    """_extract_sql 코드블록 SQL 추출 검증."""

    def test_normal_code_block(self):
        text = "Here is the fix:\n```sql\nSELECT id FROM users\n```"
        assert _extract_sql(text) == "SELECT id FROM users"

    def test_with_block(self):
        text = "```sql\nWITH cte AS (SELECT 1) SELECT * FROM cte\n```"
        assert _extract_sql(text).startswith("WITH")

    def test_comment_only_code_block_returns_empty(self):
        """주석만 있는 코드블록 → 빈 문자열 반환."""
        text = "```sql\n-- 이 쿼리는 올바르지 않습니다\n-- 다른 주석\n```"
        assert _extract_sql(text) == ""

    def test_non_sql_code_block_rejected(self):
        """SELECT/WITH로 시작하지 않는 코드블록 → 거부."""
        text = "```sql\nDROP TABLE users;\n```"
        assert _extract_sql(text) == ""

    def test_prose_in_code_block_rejected(self):
        """산문처럼 보이는 코드블록 → 거부 (INSERT 등)."""
        text = "```sql\nINSERT INTO logs VALUES (1)\n```"
        assert _extract_sql(text) == ""

    def test_fallback_select_extraction(self):
        """코드블록 없이 SELECT로 시작하는 부분 추출."""
        text = "The answer is SELECT name FROM products WHERE id = 1"
        result = _extract_sql(text)
        assert result.startswith("SELECT")

    def test_fallback_with_extraction(self):
        """코드블록 없이 WITH로 시작하는 부분 추출."""
        text = "Answer: WITH t AS (SELECT 1 AS x) SELECT * FROM t"
        result = _extract_sql(text)
        assert result.startswith("WITH")

    def test_empty_input(self):
        assert _extract_sql("") == ""

    def test_garbage_input_returns_empty(self):
        """SQL이 전혀 없는 텍스트 → 빈 문자열."""
        text = "I don't know how to fix this query. Please try again."
        assert _extract_sql(text) == ""

    def test_code_block_with_extra_text(self):
        """코드블록 앞뒤에 설명이 있는 경우."""
        text = "수정된 SQL입니다:\n```sql\nSELECT * FROM orders WHERE status = 'active'\n```\n위 SQL을 사용하세요."
        result = _extract_sql(text)
        assert "SELECT" in result
        assert "orders" in result


# ─── run() tests ─────────────────────────────────────────────────────────────

class TestFixRun:
    """fix.run() — 원본 SQL 보존 & 빈 SQL 방어 검증."""

    @pytest.mark.asyncio
    async def test_no_errors_returns_original(self):
        """에러 없으면 원본 SQL 그대로 반환."""
        ctx = {"sql": "SELECT 1", "validation_errors": []}
        result = await run(ctx, MagicMock(), {})
        assert result == {"sql": "SELECT 1", "fixed": False}

    @pytest.mark.asyncio
    async def test_successful_fix(self):
        """LLM이 올바른 SQL을 반환하면 fixed=True."""
        llm = AsyncMock()
        llm.generate_once = AsyncMock(return_value="```sql\nSELECT id, name FROM users\n```")

        ctx = {"sql": "SELECT id name FROM users", "validation_errors": ["syntax error"], "rag": {}}
        result = await run(ctx, llm, {})
        assert result["fixed"] is True
        assert "SELECT" in result["sql"]

    @pytest.mark.asyncio
    async def test_empty_llm_response_preserves_original(self):
        """LLM이 빈 응답을 반환하면 원본 SQL 유지 후 재시도."""
        llm = AsyncMock()
        # 첫 시도: 빈 응답, 두 번째: 또 빈 응답
        llm.generate_once = AsyncMock(return_value="I can't fix this query.")

        original_sql = "SELECT id FROM users"
        ctx = {"sql": original_sql, "validation_errors": ["error"], "rag": {}}
        result = await run(ctx, llm, {})

        # 최종 실패 → 원본 SQL 반환
        assert result["sql"] == original_sql
        assert result["fixed"] is False

    @pytest.mark.asyncio
    async def test_comment_only_response_preserves_original(self):
        """LLM이 주석만 반환하면 원본 유지."""
        llm = AsyncMock()
        llm.generate_once = AsyncMock(return_value="```sql\n-- 수정 불가\n```")

        original_sql = "SELECT * FROM orders"
        ctx = {"sql": original_sql, "validation_errors": ["error"], "rag": {}}
        result = await run(ctx, llm, {})

        assert result["sql"] == original_sql
        assert result["fixed"] is False

    @pytest.mark.asyncio
    async def test_safety_violation_preserves_original(self):
        """LLM이 위험한 SQL을 반환하면 원본 유지."""
        llm = AsyncMock()
        llm.generate_once = AsyncMock(return_value="```sql\nSELECT 1; DROP TABLE users\n```")

        original_sql = "SELECT * FROM users"
        ctx = {"sql": original_sql, "validation_errors": ["error"], "rag": {}}
        result = await run(ctx, llm, {})

        # DROP은 safety 위반 → 원본 반환
        assert result["sql"] == original_sql
        assert result["fixed"] is False

    @pytest.mark.asyncio
    async def test_max_retries_returns_original(self):
        """MAX_RETRIES 후 원본 SQL 반환 (마지막 시도 SQL이 아닌)."""
        llm = AsyncMock()
        llm.generate_once = AsyncMock(side_effect=Exception("LLM 서버 오류"))

        original_sql = "SELECT count(*) FROM logs"
        ctx = {"sql": original_sql, "validation_errors": ["error"], "rag": {}}
        result = await run(ctx, llm, {})

        assert result["sql"] == original_sql
        assert result["fixed"] is False
