"""대화 메모리 관리 — ConversationSummaryBuffer + Semantic Recall 패턴."""
from __future__ import annotations

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
        total_pairs = await conn.fetchval(
            "SELECT COUNT(*) FROM ops_message WHERE conversation_id=$1 AND role='user'",
            conversation_id,
        )
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
    try:
        prompt = template.format(dialogue=dialogue)
    except KeyError:
        prompt = _CONV_SUMMARIZE_FALLBACK.format(dialogue=dialogue)
    try:
        summary, _ = await llm_provider.generate(context="", question=prompt)
        return summary.strip() or None
    except Exception as e:
        logger.warning("요약 생성 실패: %s", e)
        return None


async def build_context_history(
    conversation_id: int, query_vec: list[float],
) -> list[dict]:
    summaries = await retrieve_relevant_summaries(conversation_id, query_vec)
    recent = await load_recent_history(conversation_id)

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
