"""대화 메모리 관리 — ConversationSummaryBuffer + Semantic Recall 패턴."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.database import get_conn
from service.prompt.loader import get_prompt as load_prompt
from shared.embedding import embedding_service

logger = logging.getLogger(__name__)

SUMMARY_TRIGGER = 4
RECENT_EXCHANGES = 2
MAX_RECALL = 2
RECALL_THRESHOLD = 0.45
MULTITURN_RELEVANCE_THRESHOLD = 0.35


async def augment_query_for_search(
    conversation_id: int, query: str, *, exclude_message_id: Optional[int] = None,
) -> tuple[str, list[float]]:
    """직전 턴이 현재 질문과 실제로 관련 있을 때만 검색 질의에 결합한다.

    무조건 직전 Q+A를 붙이면 대화 도중 주제가 바뀐 질문에서도 이전 맥락이
    임베딩을 오염시켜 엉뚱한 문서가 검색되는 문제가 있었음 — 결합 전에
    직전 맥락과 현재 질문의 임베딩 유사도를 확인해 관련 있을 때만 결합한다.

    Returns:
        (search_question, query_vec) — query_vec은 query 단독 임베딩(재사용 가능)
    """
    async with get_conn() as conn:
        if exclude_message_id is not None:
            rows = await conn.fetch(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at, id FROM ops_message
                    WHERE conversation_id = $1 AND id < $2
                    ORDER BY created_at DESC, id DESC LIMIT 2
                ) sub ORDER BY sub.created_at ASC, sub.id ASC
                """,
                conversation_id, exclude_message_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at, id FROM ops_message
                    WHERE conversation_id = $1
                    ORDER BY created_at DESC, id DESC LIMIT 2
                ) sub ORDER BY sub.created_at ASC, sub.id ASC
                """,
                conversation_id,
            )

    prev_context = " ".join(r["content"][:80] for r in rows if r["content"]) if rows else ""
    if not prev_context:
        return query, await embedding_service.embed(query)

    query_vec, prev_vec = await asyncio.gather(
        embedding_service.embed(query),
        embedding_service.embed(prev_context),
    )
    similarity = sum(a * b for a, b in zip(query_vec, prev_vec))
    if similarity < MULTITURN_RELEVANCE_THRESHOLD:
        logger.info(
            "멀티턴 검색 보강 스킵 (유사도 %.3f < %.2f): '%s'",
            similarity, MULTITURN_RELEVANCE_THRESHOLD, query,
        )
        return query, query_vec

    return f"{prev_context} {query}", query_vec


async def load_recent_history(
    conversation_id: int, exchanges: int = RECENT_EXCHANGES,
) -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT role, content FROM (
                SELECT role, content, created_at, id
                FROM ops_message
                WHERE conversation_id = $1
                ORDER BY created_at DESC, CASE WHEN role = 'user' THEN 1 ELSE 0 END, id DESC
                LIMIT $2
            ) sub
            ORDER BY sub.created_at ASC, CASE WHEN sub.role = 'user' THEN 0 ELSE 1 END, sub.id ASC
            """,
            conversation_id, exchanges * 2,
        )
    return [{"role": r["role"], "content": r["content"]} for r in rows]


async def _store_summary(
    conversation_id: int, summary: str, turn_start: int, turn_end: int,
) -> None:
    vec = await embedding_service.embed(summary)
    async with get_conn() as conn:
        await conn.execute(
            """
            INSERT INTO rag_conv_summary (conversation_id, summary, embedding, turn_start, turn_end)
            VALUES ($1, $2, $3::vector, $4, $5)
            """,
            conversation_id, summary, str(vec), turn_start, turn_end,
        )


async def retrieve_relevant_summaries(
    conversation_id: int, query_vec: list[float],
    limit: int = MAX_RECALL, threshold: float = RECALL_THRESHOLD,
) -> list[str]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT summary, 1 - (embedding <=> $2::vector) AS similarity
            FROM rag_conv_summary
            WHERE conversation_id = $1 AND embedding IS NOT NULL
              AND 1 - (embedding <=> $2::vector) >= $3
            ORDER BY embedding <=> $2::vector LIMIT $4
            """,
            conversation_id, str(query_vec), threshold, limit,
        )
    return [r["summary"] for r in rows]


async def maybe_summarize(conversation_id: int, llm_provider) -> None:
    async with get_conn() as conn:
        last_summarized_end = await conn.fetchval(
            "SELECT COALESCE(MAX(turn_end), 0) FROM rag_conv_summary WHERE conversation_id=$1",
            conversation_id,
        )
        recent_cutoff_row = await conn.fetchrow(
            "SELECT id FROM ops_message WHERE conversation_id = $1 ORDER BY created_at DESC OFFSET $2 LIMIT 1",
            conversation_id, RECENT_EXCHANGES * 2,
        )
        if not recent_cutoff_row:
            return

        recent_cutoff_id = recent_cutoff_row["id"]
        unsummarized = await conn.fetch(
            """
            SELECT m.id, m.role, m.content FROM ops_message m
            WHERE m.conversation_id = $1 AND m.id > $2 AND m.id <= $3
            ORDER BY m.created_at ASC, CASE WHEN m.role = 'user' THEN 0 ELSE 1 END, m.id ASC
            """,
            conversation_id, last_summarized_end, recent_cutoff_id,
        )

    if not unsummarized:
        return

    user_count = sum(1 for r in unsummarized if r["role"] == "user")
    if user_count < SUMMARY_TRIGGER:
        return

    pairs: list[tuple] = []
    current_pair: list = []
    for row in unsummarized:
        current_pair.append(row)
        if row["role"] == "assistant":
            pairs.append(tuple(current_pair))
            current_pair = []

    chunks = [pairs[i:i + SUMMARY_TRIGGER] for i in range(0, len(pairs), SUMMARY_TRIGGER)]
    for chunk in chunks:
        if len(chunk) < SUMMARY_TRIGGER:
            break
        messages = [m for pair in chunk for m in pair]
        turn_start = messages[0]["id"]
        turn_end = messages[-1]["id"]
        summary = await _summarize_with_llm(messages, llm_provider)
        if summary:
            await _store_summary(conversation_id, summary, turn_start, turn_end)
            logger.info("대화 요약 저장: conv=%d, msg %d~%d", conversation_id, turn_start, turn_end)


_CONV_SUMMARIZE_FALLBACK = (
    "다음은 IT 운영 지원 챗봇과의 대화 기록입니다. "
    "핵심 질문, 파악된 원인, 제시된 해결책, 주요 기술 사실을 "
    "3~5문장으로 간결하게 요약해 주세요.\n\n"
    "[대화 기록]\n{dialogue}\n\n요약:"
)


async def _summarize_with_llm(messages: list, llm_provider) -> Optional[str]:
    dialogue = "\n".join(
        f"{'사용자' if r['role'] == 'user' else '어시스턴트'}: {r['content']}" for r in messages
    )
    template = await load_prompt("conv_summarize", _CONV_SUMMARIZE_FALLBACK)
    # .format() 대신 replace 사용: 대화 내용에 {변수명} 패턴이 있으면 ValueError/KeyError 발생함
    # (IT 운영 질문에는 {table_name}, {env} 같은 패턴이 흔히 포함됨)
    prompt = template.replace("{dialogue}", dialogue)
    if "{dialogue}" in prompt:
        # replace가 실패한 경우(템플릿에 플레이스홀더 없음) → fallback 사용
        prompt = _CONV_SUMMARIZE_FALLBACK.replace("{dialogue}", dialogue)
    try:
        summary, _ = await llm_provider.generate(context="", question=prompt)
        return summary.strip() or None
    except Exception as e:
        logger.warning("요약 생성 실패: %s", e)
        return None


async def build_context_history(
    conversation_id: int, query_vec: list[float],
) -> list[dict]:
    # 서로 독립적인 조회 — 각자 별도 커넥션을 사용하므로 병렬 실행 가능
    summaries, recent = await asyncio.gather(
        retrieve_relevant_summaries(conversation_id, query_vec),
        load_recent_history(conversation_id),
    )

    history: list[dict] = []
    if summaries:
        summary_block = "\n\n".join(
            f"[과거 맥락 {i + 1}]\n{s}" for i, s in enumerate(summaries)
        )
        history.append({
            "role": "system",
            "content": f"이 대화의 관련 과거 맥락입니다:\n\n{summary_block}",
        })
    history.extend(recent)
    return history
