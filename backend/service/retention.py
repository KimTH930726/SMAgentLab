"""데이터 보관 주기(10/28 사용자 결정) — 쌓이는 기록을 하루 한 번 정리한다. 원칙: 최종 판단·결정 근거는 남기고, 중간 이력·오래된 기록만 지운다.

| 무엇 | 주기 | 어디서 |
|---|---|---|
| AI 판단 원장 | 180일(자가 개선 진단은 90일만 봄, 감사용으로 두 배 — 답 없이 떠난 되묻기 같은 미확정 줄도) | 여기 |
| AI 판단 원장 — 확정 안 된 위치 판단(중간 이력) | 24시간 | service/decision/ledger.purge_stale_open(판단 낼 때) + 여기 |
| 지운 지식(status='deleted') | 지운 지 180일 — 사용 중 원문의 조각·다른 기록이 가리키는 것은 남긴다(아래) | 여기 |
| 질의 기록(ops_query_log) | 1년(실사용 시험 문항의 원천) | 여기 |
| 채팅 대화 | 1년 — 설정 chat_retention_days(#95가 365로) | service/chat/helpers.cleanup_old_conversations |
| VOC 분석·수집 기록·반복 유형 | 30일 | service/email_voc/retention.py |
정정 원장·정책 변경 기록·측정 기록은 지우지 않는다(결정 근거). 각 줄이 실패해도 다음 줄은 돈다(로그). 설계: docs/architecture.md v2.185.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.database import get_conn
from service.decision import ledger

logger = logging.getLogger(__name__)

DECISION_DAYS = 180
DELETED_KNOWLEDGE_DAYS = 180
QUERY_LOG_DAYS = 365
_FIRST_DELAY_SECONDS = 600     # 기동 직후는 피해서
_INTERVAL_SECONDS = 24 * 3600

# 지운 지식 중 남기는 것 — 지우면 결정 근거·가드가 끊긴다(FK는 SET NULL·CASCADE라 실패는 없지만 연결이 조용히 사라진다):
# · 사용 중인 원문의 조각 — 재등록·다시 분할 확인(source_ledger.touched_reasons의 removed)이 "사람이 지운 조각"을 세는 근거.
#   지우면 다음 재등록이 확인 없이 교체해 지운 내용이 되살아난다(10/28 리뷰 M1, 당시 409건)
# · 새 버전의 이전 버전(supersedes_id) · 정정 원장의 대상·반영 결과 · 시험지 정답 · 질의 기록의 해결 지식(소프트 삭제 때 이미 비워 대개 해당 없음)
# 같이 지워지는 것: 그 지식의 수정 이력(rag_knowledge_history, CASCADE)·중복 판정 기록 — 지운 지 180일 된 지식의 이력이라 함께 정리.
# "지운 시각" = updated_at — 지운 줄을 다른 이유로 고쳐도(트리거가 NOW()) 시계가 다시 돌 뿐 너무 일찍 지워지진 않는다(늦어질 수는 있음).
_DELETED_KNOWLEDGE_SQL = """
    DELETE FROM rag_knowledge k
    WHERE k.status = 'deleted' AND k.updated_at < NOW() - make_interval(days => $1)
      AND NOT EXISTS (SELECT 1 FROM rag_source_document d WHERE d.id = k.source_document_id AND d.status = 'active')
      AND NOT EXISTS (SELECT 1 FROM rag_knowledge n WHERE n.supersedes_id = k.id)
      AND NOT EXISTS (SELECT 1 FROM ops_query_log q WHERE q.resolved_knowledge_id = k.id)
      AND NOT EXISTS (SELECT 1 FROM ops_improvement_item i WHERE i.target_type = 'knowledge' AND (i.target_id = k.id OR i.applied_target_id = k.id))
      AND NOT EXISTS (SELECT 1 FROM eval_question_candidate e WHERE e.gold->>'kind' = 'knowledge' AND (e.gold->>'id')::bigint = k.id)"""


def _n(status: str) -> int:
    try:
        return int(status.split()[-1])
    except (ValueError, IndexError):
        return 0


async def run_once() -> dict:
    """한 번 정리 — 지운 수를 돌려준다(테스트·로그). 줄마다 따로 — 하나가 실패해도 나머지는 돈다."""
    steps = {
        "decisions": ("DELETE FROM ops_ai_decision WHERE created_at < NOW() - make_interval(days => $1)", DECISION_DAYS),   # 확정·미확정 모두(답 없이 떠난 되묻기 등도, 리뷰 L1)
        "deleted_knowledge": (_DELETED_KNOWLEDGE_SQL, DELETED_KNOWLEDGE_DAYS),
        "query_log": ("DELETE FROM ops_query_log WHERE created_at < NOW() - make_interval(days => $1)", QUERY_LOG_DAYS),
    }
    out: dict[str, Optional[int]] = {}
    for name, (sql, days) in steps.items():
        try:
            async with get_conn() as conn:
                out[name] = _n(await conn.execute(sql, days))
        except Exception:
            logger.warning("보관 주기 정리 실패(%s) — 내일 다시", name, exc_info=True)
            out[name] = None
    try:   # 채팅 대화(설정 chat_retention_days) — 채팅 저장 때 가끔만 돌던 것을 하루 한 번 확실히(대화가 없는 날에도)
        from service.chat.helpers import cleanup_old_conversations
        out["conversations"] = await cleanup_old_conversations()
    except Exception:
        logger.warning("보관 주기 정리 실패(conversations) — 내일 다시", exc_info=True)
        out["conversations"] = None
    try:
        async with get_conn() as conn:
            out["open_decisions"] = await ledger.purge_stale_open(conn, "knowledge_location")
    except Exception:
        logger.warning("보관 주기 정리 실패(open_decisions) — 내일 다시", exc_info=True)
        out["open_decisions"] = None
    if any(out.values()):
        logger.info("보관 주기 정리: %s", out)
    return out


_task: Optional[asyncio.Task] = None


async def _loop() -> None:
    await asyncio.sleep(_FIRST_DELAY_SECONDS)
    while True:
        await run_once()
        await asyncio.sleep(_INTERVAL_SECONDS)


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
