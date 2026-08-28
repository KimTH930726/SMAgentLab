"""Tests for agents/knowledge_rag/knowledge/retrieval.py — 카테고리별 RDB/벡터 검색 분기.

실측(2026-08-28): rag_knowledge의 "DB"/"공통코드" 카테고리는 구조화된 코드표 덤프라
"정확히 일치해야 의미 있는" 데이터인데, 순수 코사인 유사도로 비교하면 다른 카테고리
지식과 어휘만 겹쳐도 오탐이 난다(VOC 반복 유형 커버리지 판정에서 실제로 확인됨 —
"사이렌오더 결제 취소" 문서가 완전히 무관한 배송 불만 클러스터들과 매칭됨). search_knowledge()의
랭킹 공식에서 이 카테고리는 벡터 점수를 0으로 만들어 키워드(RDB 텍스트) 매칭만으로
순위를 매기도록 바꿨다 — 질문을 분류하는 게 아니라 이미 등록된 지식 행의 category
값으로 판단(등록 시점에 정해지는 값).

core/shared는 conftest.py가 이미 모듈 단위로 mock해뒀지만, agents 패키지 자체는
mock되지 않아 정상적으로 import 가능하다.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.knowledge_rag.knowledge import retrieval


def _make_fake_conn(fetch_return=None):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    conn = _make_fake_conn()
    monkeypatch.setattr(retrieval, "get_conn", MagicMock(return_value=conn))
    monkeypatch.setattr(retrieval, "resolve_namespace_id", AsyncMock(return_value=1))
    return conn


class TestKeywordOnlyCategories:
    def test_db_and_common_code_are_keyword_only(self):
        # 이 상수를 다른 모듈(pattern_detection.py)도 그대로 재사용하므로, 값 자체가
        # 실수로 바뀌면 두 군데 동작이 동시에 달라진다 — 회귀 방지용 명시적 확인.
        assert set(retrieval._KEYWORD_ONLY_CATEGORIES) == {"DB", "공통코드"}


class TestSearchKnowledgeCategoryRouting:
    @pytest.mark.asyncio
    async def test_final_score_sql_zeroes_vector_for_keyword_only_categories(self, patch_db):
        """SQL 자체에 "DB/공통코드는 키워드 점수만 쓴다"는 CASE 분기가 들어가는지 —
        이게 실제 랭킹에서 벡터 유사도를 배제하는 지점이다."""
        conn = patch_db
        await retrieval.search_knowledge("ns", [0.1, 0.2], "질문")

        sql, *params = conn.fetch.call_args.args
        assert "CASE WHEN k.category = ANY($8::text[])" in sql
        assert "THEN COALESCE(ks.k_score, 0.0)" in sql
        # $8 자리에 실제로 _KEYWORD_ONLY_CATEGORIES가 전달되는지(인덱스: sql 다음
        # 7개 고정 파라미터 뒤, 0-based로 params[7] == $8)
        assert params[7] == list(retrieval._KEYWORD_ONLY_CATEGORIES)

    @pytest.mark.asyncio
    async def test_optional_category_filter_still_appended_after_fixed_param(self, patch_db):
        """사용자가 명시적으로 category 필터를 지정하는 기존 기능(v2.30)이 새 고정
        파라미터($8) 뒤에 $9로 밀려도 정상 동작해야 한다."""
        conn = patch_db
        await retrieval.search_knowledge("ns", [0.1, 0.2], "질문", categories=["공통지식"])

        sql, *params = conn.fetch.call_args.args
        assert "AND k.category = ANY($9)" in sql
        assert params[7] == list(retrieval._KEYWORD_ONLY_CATEGORIES)
        assert params[8] == ["공통지식"]

    @pytest.mark.asyncio
    async def test_no_optional_filter_by_default(self, patch_db):
        conn = patch_db
        await retrieval.search_knowledge("ns", [0.1, 0.2], "질문")

        sql, *params = conn.fetch.call_args.args
        assert "AND k.category = ANY($9)" not in sql
        # 고정 파라미터(_KEYWORD_ONLY_CATEGORIES)까지만 있고 사용자 필터는 없어야 함
        assert len(params) == 8
