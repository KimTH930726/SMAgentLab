"""Tests for Tier 3 — Analyzer Agent + 자동 Q&A 생성."""
import pytest
from unittest.mock import AsyncMock, MagicMock


# ─── Analyzer Agent 테스트 ───────────────────────────────────────────────────

from agents.knowledge_rag.ingestion.analyzer import (
    analyze_document, _validate_and_normalize, _default_result, _parse_json_object,
)


class TestParseJsonObject:
    def test_plain_json(self):
        result = _parse_json_object('{"key": "value"}')
        assert result == {"key": "value"}

    def test_code_block(self):
        result = _parse_json_object('```json\n{"a": 1}\n```')
        assert result == {"a": 1}

    def test_array_raises(self):
        with pytest.raises(ValueError, match="object"):
            _parse_json_object('[1, 2, 3]')

    def test_invalid_raises(self):
        with pytest.raises(Exception):
            _parse_json_object("not json")


class TestValidateAndNormalize:
    def test_valid_strategy_kept(self):
        result = _validate_and_normalize({"chunk_strategy": "section"})
        assert result["chunk_strategy"] == "section"

    def test_invalid_strategy_fallback_by_doc_type(self):
        result = _validate_and_normalize({
            "chunk_strategy": "semantic",
            "doc_type": "operation_manual",
        })
        assert result["chunk_strategy"] == "section"

    def test_unknown_doc_type_fallback_auto(self):
        result = _validate_and_normalize({
            "chunk_strategy": "invalid",
            "doc_type": "unknown_type",
        })
        assert result["chunk_strategy"] == "auto"

    def test_priority_score_dropped(self):
        """중요도 점수는 묻지도 남기지도 않는다(10/21 — 읽는 곳 없음). LLM이 내놓아도 결과에서 뺀다."""
        assert "priority_score" not in _validate_and_normalize({"priority_score": 0.9})
        assert "priority_score" not in _validate_and_normalize({})

    def test_prompts_do_not_ask_priority(self):
        from agents.knowledge_rag.ingestion import analyzer
        assert "priority_score" not in analyzer._ANALYZER_PROMPT
        assert "suggested_categories" not in analyzer._ANALYZER_PROMPT   # 업무구분 추천도 묻지 않는다(10/22)

    def test_estimated_chunks_non_numeric(self):
        result = _validate_and_normalize({"estimated_chunks": "many"})
        assert result["estimated_chunks"] == 0

    def test_defaults_filled(self):
        result = _validate_and_normalize({})
        assert result["doc_type"] == "mixed"
        assert result["domain"] == ""
        assert "suggested_categories" not in result   # 업무구분 추천은 10/22 폐지
        assert result["key_terms"] == []


class TestAnalyzeDocument:
    @pytest.mark.asyncio
    async def test_success(self):
        llm = MagicMock()
        llm.generate_once = AsyncMock(return_value='{"doc_type": "operation_manual", "domain": "IT운영/쿠폰", "structure": "hierarchical_sections", "has_tables": false, "has_code_blocks": true, "suggested_categories": ["쿠폰"], "key_terms": [{"term": "쿠폰회수", "description": "만료 쿠폰 자동 회수"}], "priority_score": 0.8, "chunk_strategy": "section", "estimated_chunks": 12}')

        result = await analyze_document("## 1. 개요\n쿠폰 시스템..." * 100, llm)
        assert result["doc_type"] == "operation_manual"
        assert result["chunk_strategy"] == "section"
        assert "priority_score" not in result   # LLM이 내놓아도 버린다(10/21)
        assert "suggested_categories" not in result   # 업무구분 추천도 버린다(10/22 폐지)
        assert len(result["key_terms"]) == 1

    @pytest.mark.asyncio
    async def test_llm_failure_returns_default(self):
        llm = MagicMock()
        llm.generate_once = AsyncMock(side_effect=Exception("LLM down"))

        result = await analyze_document("some text", llm)
        assert result["doc_type"] == "mixed"
        assert result["chunk_strategy"] == "auto"
        assert "priority_score" not in result
        assert "suggested_categories" not in result

    @pytest.mark.asyncio
    async def test_empty_text_returns_default(self):
        result = await analyze_document("", MagicMock())
        assert result == _default_result()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_default(self):
        llm = MagicMock()
        llm.generate_once = AsyncMock(return_value="This is not JSON at all")

        result = await analyze_document("text", llm)
        assert result["chunk_strategy"] == "auto"
