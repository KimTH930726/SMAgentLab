"""Tests for _require_category()의 키워드 전용 카테고리 재발 방지 로그(2026-09-18).

DB/공통코드 카테고리로 지식이 등록되면 rag_knowledge 안에서 벡터축과 final_score로
경쟁하다 top_k 후보 선별 단계에서 밀려날 수 있다("DS14가 뭐야?" 실사고) — 등록 자체를
막지는 않되(기존 저장 경로 보존), 재발을 알아챌 수 있게 경고 로그를 남긴다."""
import logging

import pytest

from agents.knowledge_rag.knowledge.service import _require_category


class TestRequireCategoryGuard:
    def test_normal_category_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _require_category("공통지식")
        assert result == "공통지식"
        assert not caplog.records

    def test_keyword_only_category_logs_warning_but_still_succeeds(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = _require_category("DB")
        assert result == "DB"  # 등록 자체는 막지 않음
        assert any("키워드 전용 카테고리" in r.message for r in caplog.records)

    def test_common_code_category_also_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            _require_category("공통코드")
        assert any("공통코드" in r.message for r in caplog.records)

    def test_empty_category_still_rejected(self):
        with pytest.raises(ValueError):
            _require_category("")
