"""질문 기록 → 용어 동의어 자동 수집 (2026-10-06, v2.128).

사용자는 용어집 이름과 다른 말로 묻는다("상품쿠폰" ↔ "상품 쿠폰", "택배비" ↔ "배송비"). 사람은 동의어를 등록하지 않으므로(사용자 결정)
쌓인 질문 기록을 주기적으로 LLM에 보내 "용어집 용어를 가리키지만 다르게 쓴 표현"을 뽑고(glossary_terms.mine_query_expressions —
질문에 실제로 있는 표현·용어집에 있는 용어만 통과 + 품질 게이트), source='llm_query'로 붙인다. 한 번 나온 오추출이 검색을 오염시키지
않게 서로 다른 질문 QUERY_MIN_EVIDENCE건 이상에서 나와야 검색에 쓴다(그 전엔 화면에 "근거 부족"으로만 보임). 사람이 지운 표현은 다시 안 붙는다.

"서로 다른 질문": 같은 질문의 재질문·캐시 응답도 질의 기록에 따로 남아서, 한 실행 안에선 정규화 질문으로 중복을 빼고 실행을 넘어선
중복은 동의어 행의 evidence_questions(질문 해시)로 막는다(코드 리뷰 2026-10-07).

질문마다 LLM을 부르지 않는다(게이트웨이 30~150초) — 하루 한 번 배치. 파트별로 마지막 처리 질의 id를 ops_system_config에 남긴다.
LLM이 실패한 배치는 처리 위치를 그 앞까지만 옮겨 다음 주기에 다시 처리한다(예전엔 실패해도 위치가 넘어가 영구 누락됐다).
시스템 오류(답 못 함) 질의는 제외.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.config import settings
from core.database import get_conn
from agents.knowledge_rag.knowledge import glossary_terms

logger = logging.getLogger(__name__)

_STATE_KEY = "glossary_mining_last_id:{ns_id}"
_MAX_QUESTIONS_PER_RUN = 400
_MAX_ROUNDS_PER_CYCLE = 5   # 하루 질문이 400건을 넘어도 밀리지 않게 한 주기에 여러 번(상한 있음)
_FIRST_DELAY_SECONDS = 600
_task: Optional[asyncio.Task] = None


async def mine_namespace(ns_id: int, llm) -> dict:
    """한 파트의 새 질문을 처리. 반환: {questions, unique, found, saved, failed_batches}."""
    key = _STATE_KEY.format(ns_id=ns_id)
    async with get_conn() as conn:
        last = int(await conn.fetchval("SELECT value FROM ops_system_config WHERE key = $1", key) or 0)
        rows = await conn.fetch(
            "SELECT id, question FROM ops_query_log WHERE namespace_id = $1 AND id > $2 AND status <> 'system_error' "
            "AND question IS NOT NULL ORDER BY id LIMIT $3", ns_id, last, _MAX_QUESTIONS_PER_RUN)
        entries = await glossary_terms.load_entries(conn, ns_id)
        ids = {r["term"]: r["id"] for r in await conn.fetch("SELECT id, term FROM rag_glossary WHERE namespace_id = $1", ns_id)}
    if not rows:
        return {"questions": 0, "unique": 0, "found": 0, "saved": 0, "failed_batches": 0}
    # 같은 질문(정규화 기준)은 한 번만 — LLM 비용도 줄고 근거가 같은 질문으로 부풀지 않는다
    unique: list[tuple[int, str]] = []
    seen: set[str] = set()
    for r in rows:
        k = glossary_terms.question_key(r["question"])
        if k not in seen:
            seen.add(k)
            unique.append((r["id"], r["question"]))
    failed: list[int] = []
    found = (await glossary_terms.mine_query_expressions([q for _, q in unique], entries, llm, failed=failed)
             if entries else [])
    pairs = {(glossary_terms.norm(expr), term, qi): expr for expr, term, qi in found}
    # 실패한 배치가 있으면 그 배치 첫 질문 앞까지만 처리한 것으로 — 다음 주기에 그 뒤를 다시
    new_last = unique[min(failed)][0] - 1 if failed else rows[-1]["id"]
    saved = 0
    async with get_conn() as conn:
        async with conn.transaction():
            for (_, term, qi), expr in pairs.items():
                if term in ids:
                    saved += await glossary_terms.save_synonyms(conn, ids[term], [expr], "llm_query", question=unique[qi][1])
            await conn.execute(
                "INSERT INTO ops_system_config (key, value) VALUES ($1, $2) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", key, str(max(new_last, last)))
    return {"questions": len(rows), "unique": len(unique), "found": len(found), "saved": saved, "failed_batches": len(failed)}


async def run_once(llm=None) -> dict:
    """용어집이 있는 모든 파트. 파트 하나가 실패해도 나머지는 계속(경고 로그)."""
    if llm is None:
        from service.llm.factory import get_llm_provider
        llm = get_llm_provider()
    async with get_conn() as conn:
        ns_rows = await conn.fetch(
            "SELECT DISTINCT n.id, n.name FROM ops_namespace n JOIN rag_glossary g ON g.namespace_id = n.id ORDER BY n.id")
    result = {}
    for r in ns_rows:
        try:
            for _ in range(_MAX_ROUNDS_PER_CYCLE):
                stats = await mine_namespace(r["id"], llm)
                prev = result.get(r["name"])
                result[r["name"]] = stats if prev is None else {k: prev[k] + stats[k] for k in stats}
                if stats["questions"] < _MAX_QUESTIONS_PER_RUN or stats["failed_batches"]:
                    break
        except Exception as e:
            logger.warning("질문 기록 용어 수집 실패(파트 %s, 다음 주기에 다시): %s", r["name"], e)
    logger.info("질문 기록 용어 수집: %s", result)
    return result


async def _loop() -> None:
    await asyncio.sleep(_FIRST_DELAY_SECONDS)   # 기동 직후 부하·게이트웨이 경합을 피해 잠시 뒤
    while True:
        hours = settings.glossary_mining_interval_hours
        if hours > 0:
            try:
                await run_once()
            except Exception as e:
                logger.warning("질문 기록 용어 수집 주기 실패: %s", e)
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
