"""반복된 지식 공백 → 용어 동의어 (2026-10-07, v2.128 개편).

사용자는 용어집 이름과 다른 말로 묻는다("택배비" ↔ "배송비"). 그 차이 때문에 근거를 못 찾은 질문만 동의어가 고칠 수 있다.
처음엔 모든 질문 기록을 매일 LLM에 보냈는데(사용자 지적 "한 방 한 방 자동화하면 오류 가능성↑", 리뷰에서도 근거 부풀림·누락 결함),
지금은 증거가 쌓인 것만 다룬다:

1) 대상: 답을 못 찾은(status=no_knowledge) 질문 중 같은 질문(정규화 기준)이 GLOSSARY_GAP_WINDOW_DAYS 안에 GLOSSARY_GAP_MIN_REPEATS번
   이상 반복된 것 — 하루 한 번 SQL로만 확인하고(비용 없음), 걸린 게 있을 때만 LLM을 부른다(실측 30일: 3회 이상 반복 2개)
2) LLM에는 용어집 전체가 아니라 그 질문과 가까운 용어 _RELATED_TERMS개만(로컬 임베딩으로 고름 — 프롬프트 약 80% 감소)
3) LLM이 낸 (표현, 용어)는 질문에 실제로 있는지·품질 게이트(임베딩 0.85) 통과 후, **효과 확인**: 그 동의어를 넣고 실패했던 질문을
   채팅과 같은 경로(build_chat_context)로 다시 검색해 전엔 없던 근거가 새로 잡힐 때만 붙인다(source='llm_gap', 바로 사용).
   효과가 없으면 버린다 — 문서 자체가 없는 공백은 동의어로 못 고치므로 지식 공백 통계에 남아 담당자가 채운다.
4) 처리한 질문은 기간 동안 다시 보지 않는다(ops_system_config에 질문 해시·처리일). 사람은 틀린 표현만 지운다(다시 안 붙음).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta
from typing import Optional

from core.config import settings
from core.database import get_conn
from agents.knowledge_rag.knowledge import glossary_terms

logger = logging.getLogger(__name__)

_DONE_KEY = "glossary_gap_done:{ns_id}"
_RELATED_TERMS = 10
_MAX_CLUSTERS_PER_RUN = 20   # 한 파트에서 한 번에 다루는 반복 공백 상한(게이트웨이 호출 상한)
_FIRST_DELAY_SECONDS = 600
_task: Optional[asyncio.Task] = None

_REPEATED_GAPS_SQL = """
    SELECT regexp_replace(lower(question), '[[:space:][:punct:]]', '', 'g') AS qn,
           count(*) AS n, (array_agg(question ORDER BY id DESC))[1] AS question
    FROM ops_query_log
    WHERE namespace_id = $1 AND status = 'no_knowledge' AND question IS NOT NULL
      AND created_at > NOW() - make_interval(days => $2)
    GROUP BY 1 HAVING count(*) >= $3
    ORDER BY n DESC LIMIT $4
"""


def _evidence_ids(cc) -> set:
    """검색이 잡은 근거 식별자 — 동의어를 넣기 전후로 "새 근거가 생겼나" 비교용."""
    from agents.knowledge_rag.knowledge import retrieval
    th = retrieval.get_thresholds()
    ids = {("k", r.id) for r in cc.results if retrieval.is_adopted(r, th)}
    ids |= {("p", h.item_id) for h in cc.policy_result.params}
    ids |= {("n", h.item_id) for h in cc.policy_result.narratives if h.score >= (settings.policy_abstain_min_score or 0.5)}
    ids |= {("c", c.get("code_id")) for c in cc.common_codes} | {("d", d.get("column_name")) for d in cc.db_columns}
    return ids


async def _improves(namespace: str, question: str, entries: list, expr: str, term: str) -> bool:
    """그 동의어를 넣으면 실패했던 질문에서 전엔 없던 근거가 잡히는가(채팅과 같은 경로, LLM 호출 없음)."""
    from agents.knowledge_rag.agent import build_chat_context
    from agents.knowledge_rag.knowledge import retrieval
    from shared.embedding import embedding_service
    d = retrieval.get_search_defaults()
    vec = await embedding_service.embed(question)
    kw = dict(top_k=int(d["default_top_k"]), w_vector=d["default_w_vector"], w_keyword=d["default_w_keyword"],
              glossary_mode="lexical")
    with_syn = [glossary_terms.GlossaryEntry(e.term, e.description, [*e.synonyms, expr] if e.term == term else list(e.synonyms))
                for e in entries]
    before = await build_chat_context(namespace, question, vec, glossary_entries=entries, **kw)
    after = await build_chat_context(namespace, question, vec, glossary_entries=with_syn, **kw)
    return bool(_evidence_ids(after) - _evidence_ids(before))


async def _related_entries(conn, ns_id: int, question: str, entries: list) -> list:
    """질문과 가까운 용어만(용어 설명 임베딩 기준) — LLM 프롬프트를 줄이고 엉뚱한 용어로 연결될 기회도 줄인다."""
    from shared.embedding import embedding_service
    vec = await embedding_service.embed(question)
    rows = await conn.fetch(
        "SELECT term FROM rag_glossary WHERE namespace_id = $1 AND embedding IS NOT NULL "
        "ORDER BY embedding <=> $2::vector LIMIT $3", ns_id, str(vec), _RELATED_TERMS)
    keep = {r["term"] for r in rows}
    return [e for e in entries if e.term in keep]


async def mine_namespace(ns_id: int, namespace: str, llm, *, dry_run: bool = False) -> dict:
    """한 파트의 반복 공백 처리. 반환: {clusters, candidates, applied, no_effect, failed, details(dry_run)}."""
    window, min_n = settings.glossary_gap_window_days, settings.glossary_gap_min_repeats
    key = _DONE_KEY.format(ns_id=ns_id)
    async with get_conn() as conn:
        done = json.loads(await conn.fetchval("SELECT value FROM ops_system_config WHERE key = $1", key) or "{}")
        cutoff = (date.today() - timedelta(days=window)).isoformat()
        done = {k: v for k, v in done.items() if v >= cutoff}   # 기간이 지나면 다시 볼 수 있게
        gaps = [g for g in await conn.fetch(_REPEATED_GAPS_SQL, ns_id, window, min_n, _MAX_CLUSTERS_PER_RUN)
                if glossary_terms.question_key(g["question"]) not in done]
        entries = await glossary_terms.load_entries(conn, ns_id) if gaps else []
        ids = {r["term"]: r["id"] for r in await conn.fetch("SELECT id, term FROM rag_glossary WHERE namespace_id = $1", ns_id)}
        related = {g["qn"]: await _related_entries(conn, ns_id, g["question"], entries) for g in gaps} if entries else {}
    stats = {"clusters": len(gaps), "candidates": 0, "applied": 0, "no_effect": 0, "failed": 0, "details": []}
    for g in gaps:
        q = g["question"]
        failed: list[int] = []
        found = await glossary_terms.mine_query_expressions([q], related.get(g["qn"], []), llm, failed=failed)
        if failed:
            stats["failed"] += 1   # LLM 실패 — 처리 표시를 안 남겨 다음 주기에 다시
            continue
        stats["candidates"] += len(found)
        for expr, term, _ in found:
            ok = await _improves(namespace, q, entries, expr, term)
            stats["applied" if ok else "no_effect"] += 1
            if dry_run:
                stats["details"].append({"repeats": g["n"], "expression": expr, "term": term, "improves": ok})
            elif ok and term in ids:
                async with get_conn() as conn:
                    await glossary_terms.save_synonyms(conn, ids[term], [expr], "llm_gap", question=q)
        done[glossary_terms.question_key(q)] = date.today().isoformat()
    if not dry_run and gaps:
        async with get_conn() as conn:
            await conn.execute(
                "INSERT INTO ops_system_config (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", key, json.dumps(done))
    return stats


async def run_once(llm=None, *, dry_run: bool = False) -> dict:
    """용어집이 있는 모든 파트. 반복 공백이 없으면 LLM을 부르지 않는다. 파트 하나가 실패해도 나머지는 계속."""
    if llm is None:
        from service.llm.factory import get_llm_provider
        llm = get_llm_provider()
    async with get_conn() as conn:
        ns_rows = await conn.fetch(
            "SELECT DISTINCT n.id, n.name FROM ops_namespace n JOIN rag_glossary g ON g.namespace_id = n.id ORDER BY n.id")
    result = {}
    for r in ns_rows:
        try:
            result[r["name"]] = await mine_namespace(r["id"], r["name"], llm, dry_run=dry_run)
        except Exception as e:
            logger.warning("반복 공백 용어 수집 실패(파트 %s, 다음 주기에 다시): %s", r["name"], e)
    logger.info("반복 공백 용어 수집: %s", {k: {x: v[x] for x in v if x != "details"} for k, v in result.items()})
    return result


async def _loop() -> None:
    await asyncio.sleep(_FIRST_DELAY_SECONDS)   # 기동 직후 부하·게이트웨이 경합을 피해 잠시 뒤
    while True:
        hours = settings.glossary_mining_interval_hours
        if hours > 0:
            try:
                await run_once()
            except Exception as e:
                logger.warning("반복 공백 용어 수집 주기 실패: %s", e)
        await asyncio.sleep(max(hours, 1) * 3600)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_loop())


async def stop() -> None:
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
