"""Tests for agents/knowledge_rag/knowledge/retrieval.py — 지식 검색 점수·채택·키워드 구제.

업무구분(category) 키워드 전용 분기(DB/공통코드)와 업무구분 필터는 10/22 업무구분 폐지로 삭제
(category-removal-design §2-5 C3) — 예전 파일 이름 test_retrieval_category_routing.py에서 옮김.

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
    monkeypatch.setattr(retrieval, "namespace_scope_ids", AsyncMock(return_value=[1]))   # 검색 범위(자신 + 직계 하위) — 하위 없음
    return conn


def _make_result(final_score=0.0, base_weight=0.0, k_score=0.0, v_score=0.0):
    return retrieval.RetrievalResult(
        id=1, namespace="ns", content="c", base_weight=base_weight,
        final_score=final_score, v_score=v_score, k_score=k_score,
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
    """실제 chat 프롬프트에 포함할지 최종 판단 — 관련성 점수 임계치 하나로만 갈린다.
    업무구분 키워드 전용(DB/공통코드) "매칭만 되면 채택" 분기는 10/22 업무구분 폐지로 삭제."""

    def test_gated_by_relevance_threshold(self):
        th = {"knowledge_min_score": 0.35}
        above = _make_result(final_score=0.72, base_weight=1.0)  # raw=0.36
        below = _make_result(final_score=0.60, base_weight=1.0)  # raw=0.30
        assert retrieval.is_adopted(above, th) is True
        assert retrieval.is_adopted(below, th) is False

    def test_tiny_keyword_match_no_longer_adopted(self):
        # 예전엔 DB/공통코드면 final_score > 0만으로 채택됐다 — 이제 모든 지식이 같은 임계치
        th = {"knowledge_min_score": 0.35}
        assert retrieval.is_adopted(_make_result(final_score=0.0304, base_weight=1.0), th) is False


class TestSearchKnowledgeNoCategory:
    """업무구분 폐지(10/22) 뒤 검색 SQL — 업무구분 칸을 읽지 않고, 점수식은 예전 ELSE 쪽 가중합 그대로(해당 활성 행 0건이라
    순위 동일), 자리표시자($n)와 넘기는 값 개수가 정확히 맞아야 한다(번호를 당기다 어긋나면 실행 시점에야 깨진다)."""

    @pytest.mark.asyncio
    async def test_sql_does_not_read_category(self, patch_db):
        await retrieval.search_knowledge("ns", [0.1, 0.2], "질문")
        sql, *_ = patch_db.fetch.call_args.args
        assert "category" not in sql
        assert "CASE" not in sql

    @pytest.mark.asyncio
    async def test_final_score_is_plain_weighted_sum(self, patch_db):
        await retrieval.search_knowledge("ns", [0.1, 0.2], "질문")
        sql, *_ = patch_db.fetch.call_args.args
        compact = " ".join(sql.split())
        assert ("($4 * COALESCE(vs.v_score, 0.0) + $5 * COALESCE(ks.k_score, 0.0)) * (1.0 + k.base_weight) AS final_score"
                in compact)

    @pytest.mark.parametrize("rescue", [False, True])
    @pytest.mark.asyncio
    async def test_placeholders_match_params(self, patch_db, monkeypatch, rescue):
        import re
        monkeypatch.setattr(retrieval, "_keyword_ids", AsyncMock(return_value=[]))
        await retrieval.search_knowledge("ns", [0.1, 0.2], "질문", keyword_rescue=rescue)
        sql, *params = patch_db.fetch.call_args.args
        used = {int(n) for n in re.findall(r"\$(\d+)", sql)}
        assert used == set(range(1, len(params) + 1))   # 빈 번호·남는 값 없음
        assert len(params) == 7

    def test_no_categories_parameter(self):
        import inspect
        assert "categories" not in inspect.signature(retrieval.search_knowledge).parameters
        assert not hasattr(retrieval, "is_keyword_only_category")


def _row(i, score):
    return {"id": i, "namespace": "ns", "content": f"c{i}", "base_weight": 0.0, "heading_path": [],
            "source_type": None, "source_file": None, "created_at": None, "v_score": score, "k_score": 0.0,
            "final_score": score, "updated_at": None}


class TestKnowledgeKeywordRescue:
    """지식 BM25 구제(v2.139, 기본 꺼짐) — 컨플루언스 게이트 실패로 꺼 둠. 켜면 trustkit 규칙(문맥 30%, 1위 고정)대로."""

    def test_default_off(self):
        import os
        # 환경값이 없으면 꺼짐(재적재 후 1로 켜고 게이트를 다시 돌리는 상태에서도 이 테스트가 깨지지 않게 — 리뷰 10/19)
        assert retrieval.KEYWORD_RESCUE is (os.environ.get("KNOWLEDGE_KEYWORD_RESCUE", "0") == "1")
        assert "KNOWLEDGE_KEYWORD_RESCUE\", \"0\")" in open(retrieval.__file__, encoding="utf-8").read()

    @pytest.mark.asyncio
    async def test_off_keeps_sql_limit_and_order(self, patch_db, monkeypatch):
        monkeypatch.setattr(retrieval.settings, "freshness_decay_halflife_days", 0)
        patch_db.fetch = AsyncMock(return_value=[_row(1, .9), _row(2, .8), _row(3, .7)])
        res = await retrieval.search_knowledge("ns", [0.1], "질문", top_k=3)
        sql, *params = patch_db.fetch.call_args.args
        assert params[5] == 3 and "ORDER BY final_score DESC, k.id" in sql   # 동점 순서 고정
        assert [r.id for r in res] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_on_rescues_one_slot_and_keeps_top1(self, patch_db, monkeypatch):
        monkeypatch.setattr(retrieval.settings, "freshness_decay_halflife_days", 0)
        patch_db.fetch = AsyncMock(return_value=[_row(i, 1 - i / 100) for i in range(1, 8)])
        monkeypatch.setattr(retrieval, "_keyword_ids", AsyncMock(return_value=[6, 99, 2]))   # 99는 후보 풀에 없음 → 구제 안 함
        res = await retrieval.search_knowledge("ns", [0.1], "질문", top_k=3, keyword_rescue=True)
        _, *params = patch_db.fetch.call_args.args
        assert params[5] == retrieval._RESCUE_POOL                        # 구제 후보를 고르려고 더 깊게
        assert [r.id for r in res] == [1, 2, 6]                           # 3자리 중 1자리(30%)만, 1위 그대로
