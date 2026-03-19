"""대화 도메인 공유 헬퍼 — 에이전트와 라우터 양쪽에서 사용."""
import asyncio
import json
import logging
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from domain.chat import memory
from domain.knowledge.retrieval import RetrievalResult

logger = logging.getLogger(__name__)

LLM_UNAVAILABLE_MSG = "[LLM 서버에 연결할 수 없습니다. 검색 결과를 참고하세요.]"

MAX_MESSAGES_PER_NS = 100
QUERY_LOG_RETENTION_DAYS = 90


def results_to_json(results: list[RetrievalResult]) -> str:
    return json.dumps(
        [{"id": r.id, "content": r.content, "final_score": r.final_score,
          "container_name": r.container_name, "target_tables": r.target_tables,
          "query_template": r.query_template}
         for r in results],
        ensure_ascii=False,
    )


def results_to_payload(results: list[RetrievalResult]) -> list[dict]:
    return [
        {"id": r.id, "container_name": r.container_name,
         "target_tables": r.target_tables, "content": r.content,
         "query_template": r.query_template, "final_score": r.final_score}
        for r in results
    ]


async def update_assistant_message(msg_id: int, content: str, status: str = "generating", metadata: dict | None = None) -> None:
    async with get_conn() as conn:
        if metadata is not None:
            await conn.execute(
                "UPDATE ops_message SET content = $1, status = $2, metadata = $3::jsonb WHERE id = $4",
                content, status, json.dumps(metadata, ensure_ascii=False), msg_id,
            )
        else:
            await conn.execute(
                "UPDATE ops_message SET content = $1, status = $2 WHERE id = $3", content, status, msg_id,
            )


async def update_inhouse_conv_id(conv_id: int, inhouse_conv_id: str) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE ops_conversation SET inhouse_conv_id = $1 WHERE id = $2",
            inhouse_conv_id, conv_id,
        )


async def create_query_log(
    namespace: str, question: str, answer: str,
    has_results: bool, mapped_term: Optional[str] = None,
    message_id: Optional[int] = None,
) -> int:
    is_real_answer = answer and answer != LLM_UNAVAILABLE_MSG
    status = "unresolved" if (not has_results and not is_real_answer) else "pending"
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        row = await conn.fetchrow(
            """
            INSERT INTO ops_query_log (namespace_id, question, answer, status, mapped_term, message_id)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING id
            """,
            ns_id, question, answer, status, mapped_term, message_id,
        )
    return row["id"]


async def cleanup_old_messages(namespace: str) -> int:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return 0
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM ops_message m JOIN ops_conversation c ON m.conversation_id = c.id WHERE c.namespace_id = $1",
            ns_id,
        )
        if total <= MAX_MESSAGES_PER_NS:
            return 0

        excess = total - MAX_MESSAGES_PER_NS
        affected_ids = await conn.fetch(
            """
            WITH deleted AS (
                DELETE FROM ops_message WHERE id IN (
                    SELECT m.id FROM ops_message m
                    JOIN ops_conversation c ON m.conversation_id = c.id
                    WHERE c.namespace_id = $1 ORDER BY m.created_at ASC LIMIT $2
                ) RETURNING conversation_id
            )
            SELECT DISTINCT conversation_id FROM deleted
            """,
            ns_id, excess,
        )
        deleted = len(affected_ids)
        if deleted > 0:
            conv_ids = [r["conversation_id"] for r in affected_ids]
            await conn.execute(
                "UPDATE ops_conversation SET trimmed = TRUE WHERE id = ANY($1::int[])", conv_ids,
            )
            logger.info("cleanup: %s 네임스페이스에서 %d개 메시지 트리밍", namespace, deleted)
        return deleted


async def cleanup_resolved_query_logs() -> int:
    async with get_conn() as conn:
        result = await conn.execute(
            "DELETE FROM ops_query_log WHERE status = 'resolved' AND created_at < NOW() - INTERVAL '1 day' * $1",
            QUERY_LOG_RETENTION_DAYS,
        )
    deleted = int(result.split()[-1]) if result else 0
    if deleted > 0:
        logger.info("cleanup: resolved query_log %d건 삭제 (%d일 경과)", deleted, QUERY_LOG_RETENTION_DAYS)
    return deleted


async def post_save_tasks(conv_id: int, namespace: Optional[str] = None) -> None:
    from domain.llm.factory import get_llm_provider
    tasks = [memory.maybe_summarize(conv_id, get_llm_provider())]
    if namespace:
        tasks.append(cleanup_old_messages(namespace))
    tasks.append(cleanup_resolved_query_logs())
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            logger.warning("후처리 실패: %s", r)
