"""대화 도메인 공유 헬퍼 — 에이전트와 라우터 양쪽에서 사용."""
import asyncio
import json
import logging
import random
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from service.chat import memory
from agents.knowledge_rag.knowledge.retrieval import RetrievalResult

logger = logging.getLogger(__name__)

LLM_UNAVAILABLE_MSG = "[LLM 서버에 연결할 수 없습니다. 검색 결과를 참고하세요.]"
# 시스템 프롬프트가 LLM에게 "관련 문서가 없으면 이 문구로 답변" 하도록 지시하는 고정 문구
# (main.py / service/llm/base.py 기본 프롬프트 참고). had_context만으로는 임계값을 살짝
# 넘는 "약하게만 관련된" 문서가 섞여 들어간 경우를 지식 공백으로 못 잡아서, LLM 스스로
# "모르겠다"고 답한 경우도 지식 공백으로 판단하는 데 사용한다.
NO_KNOWLEDGE_MARKER = "관련 지식을 찾지 못했습니다"

MAX_MESSAGES_PER_NS = 100
QUERY_LOG_RETENTION_DAYS = 90
# 스케줄러 인프라(cron/APScheduler 등)가 없어 채팅 턴에 얹혀 실행되는 하우스키핑 작업.
# 매 턴마다 돌리면 DB 스캔 비용이 누적되므로 확률적으로만 샘플링 실행한다.
CLEANUP_SAMPLE_RATE = 0.05


def results_to_json(results: list[RetrievalResult]) -> str:
    return json.dumps(
        [{"id": r.id, "content": r.content, "final_score": r.final_score}
         for r in results],
        ensure_ascii=False,
    )


def results_to_payload(results: list[RetrievalResult]) -> list[dict]:
    return [
        {"id": r.id, "content": r.content, "final_score": r.final_score}
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
    had_context: bool = True,
    user_id: Optional[int] = None,
) -> int:
    is_real_answer = answer and answer != LLM_UNAVAILABLE_MSG
    # LLM이 마커 문구로 "시작"하는 답변만 준다는 가정(예전 startswith 체크)은 실제로는
    # 안 지켜짐 — 프롬프트의 [형식] 규칙(마크다운/근거 표시 등)이 이 케이스에도 그대로
    # 적용돼, "DS14에 대한 정의가 없습니다 ... 정리 ... 관련 지식을 찾지 못했습니다"처럼
    # 마커가 답변 뒤쪽 문단에 섞여 나와 지식공백으로 분류되지 않는 사례가 실사용에서
    # 확인됨. 이 마커 문구는 프롬프트상 모른다고 답할 때만 쓰도록 지정된 전용 문구라
    # 정상 답변에 우연히 섞일 가능성이 낮으므로 in으로 완화.
    llm_says_no_knowledge = bool(answer) and NO_KNOWLEDGE_MARKER in answer
    if not had_context or llm_says_no_knowledge:
        # had_context=False: 임계값을 넘는 문서가 아예 없었음
        # llm_says_no_knowledge: 문서는 임계값을 넘어 컨텍스트에 포함됐지만, 실제로는
        #   질문과 무관해서 LLM이 스스로 "모르겠다"고 답한 경우 — 둘 다 지식 갭으로 취급
        status = "no_knowledge"
    elif not has_results and not is_real_answer:
        status = "unresolved"
    else:
        status = "pending"
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        row = await conn.fetchrow(
            """
            INSERT INTO ops_query_log (namespace_id, question, answer, status, mapped_term, message_id, user_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
            """,
            ns_id, question, answer, status, mapped_term, message_id, user_id,
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


async def cleanup_old_conversations() -> int:
    """`chat_retention_days`(ops_system_config)보다 오래 활동이 없는 대화를 삭제.

    거버넌스 선제 설계 검토(docs/tech/governance-design-review.md §3-3) — 지금까지
    ops_message/ops_conversation은 시간 기준 보존정책이 전혀 없었고(§2-3), 오직
    cleanup_old_messages()의 네임스페이스당 100건 캡만 있었다. 실제 보존일수는
    거버넌스팀이 아직 확정하지 않아, 기본값 '0'(비활성)일 땐 아무것도 지우지 않는다 —
    임의로 숫자를 정해 실 사용자 대화를 지우지 않기 위함. "마지막 메시지 기준"으로
    판단(conversation.created_at만 보면 오래전에 시작했지만 계속 쓰는 대화가 삭제될
    수 있음). ops_message는 ops_conversation의 ON DELETE CASCADE로 함께 삭제된다.
    """
    async with get_conn() as conn:
        retention_days_raw = await conn.fetchval(
            "SELECT value FROM ops_system_config WHERE key = 'chat_retention_days'",
        )
        retention_days = int(retention_days_raw) if retention_days_raw else 0
        if retention_days <= 0:
            return 0
        result = await conn.execute(
            """
            DELETE FROM ops_conversation c WHERE NOT EXISTS (
                SELECT 1 FROM ops_message m
                WHERE m.conversation_id = c.id AND m.created_at > NOW() - INTERVAL '1 day' * $1
            ) AND c.created_at < NOW() - INTERVAL '1 day' * $1
            """,
            retention_days,
        )
    deleted = int(result.split()[-1]) if result else 0
    if deleted > 0:
        logger.info("cleanup: 대화 %d건 삭제 (%d일간 활동 없음)", deleted, retention_days)
    return deleted


async def post_save_tasks(conv_id: int, namespace: Optional[str] = None) -> None:
    from service.llm.factory import get_llm_provider
    tasks = [memory.maybe_summarize(conv_id, get_llm_provider())]
    if random.random() < CLEANUP_SAMPLE_RATE:
        if namespace:
            tasks.append(cleanup_old_messages(namespace))
        tasks.append(cleanup_resolved_query_logs())
        tasks.append(cleanup_old_conversations())
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            logger.warning("후처리 실패: %s", r)
