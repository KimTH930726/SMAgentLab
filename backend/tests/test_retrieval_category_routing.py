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

    def test_public_helper_matches_private_constant(self):
        assert retrieval.is_keyword_only_category("DB") is True
        assert retrieval.is_keyword_only_category("공통코드") is True
        assert retrieval.is_keyword_only_category("공통지식") is False
        assert retrieval.is_keyword_only_category(None) is False


def _make_result(category=None, final_score=0.0, base_weight=0.0, k_score=0.0, v_score=0.0):
    return retrieval.RetrievalResult(
        id=1, namespace="ns", content="c", base_weight=base_weight,
        final_score=final_score, v_score=v_score, k_score=k_score, category=category,
    )


class TestRelevanceScore:
    """final_score에서 base_weight 배율을 걷어낸 "진짜 관련성" 점수(2026-09-18) —
    실측: base_weight 기본값이 1.0이라 final_score=raw*2가 되고, 이 상태로
    knowledge_min_score(0.35)를 대면 완전 무관한 질문("오늘 날씨 어때?")조차 후보
    20/20건이 전부 통과했다. final_score를 그대로 게이트에 쓰면 안 되는 이유."""

    def test_divides_out_base_weight(self):
        r = _make_result(final_score=0.6, base_weight=1.0)
        assert retrieval.relevance_score(r) == pytest.approx(0.3)

    def test_zero_base_weight_is_unchanged(self):
        r = _make_result(final_score=0.42, base_weight=0.0)
        assert retrieval.relevance_score(r) == pytest.approx(0.42)


class TestIsAdopted:
    """실제 chat 프롬프트에 포함할지 최종 판단 — 일반 카테고리는 관련성 점수 임계치,
    키워드 전용 카테고리(DB/공통코드)는 매칭 여부(이진)로 갈린다(ts_rank는 코사인과
    스케일이 달라 같은 임계치를 못 씀 — "DS14가 뭐야?" 실측: 조사 제거 버그를 고친
    뒤에도 k_score=0.0304로 코사인 스케일 임계치 0.35를 못 넘음)."""

    def test_general_category_gated_by_relevance_threshold(self):
        th = {"knowledge_min_score": 0.35}
        above = _make_result(category="공통지식", final_score=0.72, base_weight=1.0)  # raw=0.36
        below = _make_result(category="공통지식", final_score=0.60, base_weight=1.0)  # raw=0.30
        assert retrieval.is_adopted(above, th) is True
        assert retrieval.is_adopted(below, th) is False

    def test_keyword_only_category_adopted_on_any_positive_match_regardless_of_magnitude(self):
        th = {"knowledge_min_score": 0.35}
        tiny_match = _make_result(category="DB", final_score=0.0304, base_weight=1.0)
        assert retrieval.is_adopted(tiny_match, th) is True

    def test_keyword_only_category_rejected_when_no_keyword_match(self):
        th = {"knowledge_min_score": 0.35}
        no_match = _make_result(category="공통코드", final_score=0.0, base_weight=1.0)
        assert retrieval.is_adopted(no_match, th) is False


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
