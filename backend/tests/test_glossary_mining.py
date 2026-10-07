"""반복된 지식 공백 → 용어 동의어 (2026-10-07 개편, 사용자 제안).

모든 질문을 매일 LLM에 보내던 방식은 오추출이 쌓이기 쉬웠다 — 이제 같은 질문이 기간 안에 N번 이상 답을 못 찾은 것만, 관련 용어만
보내고, 동의어를 넣었을 때 실제로 새 근거가 잡히는 것만 붙인다. 고정하는 것: 반복 공백이 없으면 LLM을 안 부름 / 효과 없으면 안 붙임 /
LLM 실패면 처리 표시를 안 남겨 다음에 다시 / 처리한 질문은 기간 안에 다시 안 봄.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.knowledge_rag.knowledge import glossary_mining as gm
from agents.knowledge_rag.knowledge import glossary_terms as gt


def _conn(gaps, done="{}"):
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.fetchval = AsyncMock(return_value=done)

    async def fetch(sql, *a):
        if "no_knowledge" in sql:
            return gaps
        return [{"id": 7, "term": "배송비"}]
    c.fetch = AsyncMock(side_effect=fetch)
    c.execute = AsyncMock()
    return c


def _patch(conn, found=None, improves=True, fail=False):
    async def mine(questions, entries, llm, embed=None, failed=None):
        if fail:
            failed.append(0)
            return []
        return found or []
    from types import SimpleNamespace
    return [patch.object(gm, "settings", SimpleNamespace(glossary_gap_window_days=14, glossary_gap_min_repeats=3,
                                                         policy_abstain_min_score=0.5, glossary_mining_interval_hours=24)),
            patch.object(gm, "get_conn", return_value=conn),
            patch.object(gt, "load_entries", AsyncMock(return_value=[gt.GlossaryEntry("배송비", "요금")])),
            patch.object(gm, "_related_entries", AsyncMock(return_value=[gt.GlossaryEntry("배송비", "요금")])),
            patch.object(gt, "mine_query_expressions", side_effect=mine),
            patch.object(gm, "_improves", AsyncMock(return_value=improves)),
            patch.object(gt, "save_synonyms", AsyncMock(return_value=1))]


async def _run(patches, **kw):
    for p in patches:
        p.start()
    try:
        return await gm.mine_namespace(1, "ns", llm=None, **kw)
    finally:
        for p in patches:
            p.stop()


GAP = [{"qn": "택배비얼마", "n": 3, "question": "택배비 얼마?"}]


@pytest.mark.asyncio
async def test_no_repeated_gap_means_no_llm_call():
    conn = _conn([])
    ps = _patch(conn)
    out = await _run(ps)
    assert out["clusters"] == 0 and conn.execute.await_count == 0


@pytest.mark.asyncio
async def test_applies_only_when_it_finds_new_evidence():
    conn = _conn(GAP)
    ps = _patch(conn, found=[("택배비", "배송비", 0)], improves=True)
    save = ps[-1]
    out = await _run(ps)
    assert out["applied"] == 1
    conn2 = _conn(GAP)
    out2 = await _run(_patch(conn2, found=[("택배비", "배송비", 0)], improves=False))
    assert out2["applied"] == 0 and out2["no_effect"] == 1


@pytest.mark.asyncio
async def test_llm_failure_leaves_question_for_next_time():
    conn = _conn(GAP)
    out = await _run(_patch(conn, fail=True))
    assert out["failed"] == 1
    saved_done = json.loads(conn.execute.await_args.args[2])
    assert gt.question_key("택배비 얼마?") not in saved_done


@pytest.mark.asyncio
async def test_processed_question_skipped_within_window():
    from datetime import date
    conn = _conn(GAP, done=json.dumps({gt.question_key("택배비 얼마?"): date.today().isoformat()}))
    out = await _run(_patch(conn, found=[("택배비", "배송비", 0)]))
    assert out["clusters"] == 0


@pytest.mark.asyncio
async def test_dry_run_writes_nothing():
    conn = _conn(GAP)
    ps = _patch(conn, found=[("택배비", "배송비", 0)], improves=True)
    out = await _run(ps, dry_run=True)
    assert out["details"] == [{"repeats": 3, "expression": "택배비", "term": "배송비", "improves": True}]
    assert conn.execute.await_count == 0
