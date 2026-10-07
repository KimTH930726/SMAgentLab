"""질문 기록 → 동의어 수집 (2026-10-07 코드 리뷰 보완).

- 같은 질문(재질문·캐시 응답 기록)은 한 번만 LLM에 보내고 근거도 한 번 — 근거 2건이 "서로 다른 질문 2개"가 되게
- LLM이 실패한 배치가 있으면 처리 위치를 그 배치 앞까지만 옮긴다(예전엔 실패해도 넘어가 그 질문들이 영구 누락)
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.knowledge_rag.knowledge import glossary_mining as gm
from agents.knowledge_rag.knowledge import glossary_terms as gt


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _conn(rows, last="0"):
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.transaction = MagicMock(side_effect=lambda: _Tx())
    c.fetchval = AsyncMock(return_value=last)

    async def fetch(sql, *a):
        if "ops_query_log" in sql:
            return rows
        return [{"id": 7, "term": "배송비"}]
    c.fetch = AsyncMock(side_effect=fetch)
    c.execute = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_duplicate_questions_sent_once_and_position_advances():
    rows = [{"id": 10, "question": "택배비 얼마?"}, {"id": 11, "question": "택배비 얼마"}, {"id": 12, "question": "반품비는?"}]
    conn = _conn(rows)
    mine = AsyncMock(return_value=[("택배비", "배송비", 0)])
    save = AsyncMock(return_value=1)
    with patch.object(gm, "get_conn", return_value=conn), \
         patch.object(gt, "load_entries", AsyncMock(return_value=[gt.GlossaryEntry("배송비", "요금")])), \
         patch.object(gt, "mine_query_expressions", mine), patch.object(gt, "save_synonyms", save):
        out = await gm.mine_namespace(1, llm=None)
    assert mine.await_args.args[0] == ["택배비 얼마?", "반품비는?"]           # 띄어쓰기·문장부호만 다른 질문은 하나로
    assert save.await_args.kwargs["question"] == "택배비 얼마?"               # 근거 키 = 그 질문
    assert out["unique"] == 2 and out["saved"] == 1
    assert conn.execute.await_args.args[2] == "12"                           # 끝까지 처리


@pytest.mark.asyncio
async def test_failed_batch_holds_position_for_retry():
    rows = [{"id": i, "question": f"질문 {i}"} for i in range(100, 130)]
    conn = _conn(rows, last="99")

    async def mine(questions, entries, llm, embed=None, failed=None):
        failed.append(20)   # 두 번째 배치(21번째 질문부터) 실패
        return []
    with patch.object(gm, "get_conn", return_value=conn), \
         patch.object(gt, "load_entries", AsyncMock(return_value=[gt.GlossaryEntry("배송비", "요금")])), \
         patch.object(gt, "mine_query_expressions", side_effect=mine):
        out = await gm.mine_namespace(1, llm=None)
    assert out["failed_batches"] == 1
    assert conn.execute.await_args.args[2] == "119"    # 실패 배치 첫 질문(id 120) 앞까지만 — 다음 주기에 120부터 다시
