"""지식 문서 구조 조회 (2026-10-07) — 분할 방식별(섹션 구조/문서 순서/단건)로 다른 것을 보여준다.

"검색 때 함께 붙는 이웃"은 검색 코드(retrieval.expand_parent_sections)와 같은 규칙이어야 한다 — 화면이 실제 동작과 다르면
경계 이상을 사람이 확인하라고 만든 화면이 오히려 오해를 만든다.
"""
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from agents.knowledge_rag.knowledge import service

S = "agents.knowledge_rag.knowledge.service"


class _Conn:
    def __init__(self, me, rows):
        self.me, self.rows = me, rows

    async def fetchrow(self, sql, *a):
        return self.me

    async def fetch(self, sql, *a):
        return self.rows


def _patch(me, rows):
    @asynccontextmanager
    async def cm():
        yield _Conn(me, rows)
    return patch(f"{S}.get_conn", cm)


def _row(i, idx, hp, content="## 제목\n본문"):
    # SQL이 미리보기(left 400)와 길이(clen)를 따로 주는 모양 그대로
    return {"id": i, "source_chunk_idx": idx, "heading_path": hp, "content": content[:400], "clen": len(content)}


@pytest.mark.asyncio
async def test_single_manual_knowledge():
    me = {"id": 8, "ingestion_job_id": None, "source_chunk_idx": None, "heading_path": None, "source_file": None,
          "source_type": "manual", "namespace": "ns"}
    with _patch(me, []):
        s = await service.get_knowledge_structure(8)
    assert s["mode"] == "single" and s["prev"] is None and s["expansion"] == []


@pytest.mark.asyncio
async def test_sequence_has_neighbors_but_no_expansion():
    me = {"id": 2, "ingestion_job_id": 9, "source_chunk_idx": 1, "heading_path": None, "source_file": "f", "source_type": "paste_split",
          "namespace": "ns"}
    rows = [{**_row(i, i - 1, None), "clen": 10} for i in (1, 2, 3)]
    with _patch(me, rows):
        s = await service.get_knowledge_structure(2)
    assert s["mode"] == "sequence" and (s["position"], s["total"]) == (2, 3)
    assert s["prev"]["id"] == 1 and s["next"]["id"] == 3 and s["expansion"] == []


@pytest.mark.asyncio
async def test_section_expansion_follows_retrieval_rule_and_budget():
    """후보 = 같은 상위 경로[:-1], 가까운 순, 글자 수 한도 — 한도를 넘는 첫 후보에서 멈춘다(검색 코드와 동일)."""
    me = {"id": 3, "ingestion_job_id": 9, "source_chunk_idx": 2, "heading_path": ["페이지", "1.2 매장"], "source_file": "f",
          "source_type": "confluence_bulk", "namespace": "ns"}
    big = "가" * 5000
    rows = [_row(1, 0, ["페이지", "1.1 개요"]),
            _row(2, 1, ["페이지", "1.2 매장"]),
            _row(3, 2, ["페이지", "1.2 매장"]),
            _row(4, 3, ["페이지", "1.3 메뉴"], big),     # 가까운 순 2번째 — 한도 안
            _row(5, 4, ["다른 페이지"]),                   # 상위 경로 다름 — 후보 아님
            _row(6, 5, ["페이지", "1.4 주문"], big)]      # 한도 초과 → 여기서 멈춤
    with _patch(me, rows), patch("agents.knowledge_rag.knowledge.retrieval.PARENT_EXPANSION_CHAR_BUDGET", 6000):
        s = await service.get_knowledge_structure(3)
    assert s["mode"] == "section" and s["heading_path"] == ["페이지", "1.2 매장"]
    assert [b["id"] for b in s["expansion"]] == [1, 2, 4]          # 문서 순서로 표시
    assert s["prev"]["id"] == 2 and s["next"]["id"] == 4
    assert s["prev"]["title"] == "제목"



@pytest.mark.asyncio
async def test_top_level_section_in_sectioned_job_is_section_mode():
    """리뷰: 섹션 분할 묶음의 최상위 섹션(상위 경로 없음)이 '문서 순서'로 잘못 표시되던 것."""
    me = {"id": 1, "ingestion_job_id": 9, "source_chunk_idx": 0, "heading_path": None, "source_file": "f",
          "source_type": "confluence_bulk", "namespace": "ns"}
    rows = [{**_row(1, 0, None), "clen": 10}, {**_row(2, 1, ["1. 상위"]), "clen": 10}]
    with _patch(me, rows):
        s = await service.get_knowledge_structure(1)
    assert s["mode"] == "section"


@pytest.mark.asyncio
async def test_outline_lists_whole_document_with_self_and_attached_flags():
    """화면은 '앞·뒤·이웃' 대신 원문 목차 — 순서·들여쓰기(상위 경로 깊이)·지금 보는 지식·함께 전달 표시가 검색 규칙과 맞아야 한다."""
    me = {"id": 2, "ingestion_job_id": 9, "source_chunk_idx": 1, "heading_path": ["페이지", "1.2"], "source_file": "f",
          "source_type": "confluence_bulk", "namespace": "ns"}
    rows = [_row(1, 0, ["페이지"]), _row(2, 1, ["페이지", "1.2"]), _row(3, 2, ["페이지", "1.2"]), _row(4, 3, ["다른"])]
    with _patch(me, rows):
        s = await service.get_knowledge_structure(2)
    o = s["outline"]
    assert [x["id"] for x in o] == [1, 2, 3, 4]
    assert [x["depth"] for x in o] == [1, 2, 2, 1]
    assert [x["is_self"] for x in o] == [False, True, False, False]
    assert [x["attached"] for x in o] == [True, False, True, False]   # 상위 경로[:-1]=["페이지"] 공유 → 1·3, "다른"은 아님
