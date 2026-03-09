"""대화 엔드포인트 — SSE 스트리밍 + 단일 응답 + 디버그 + 대화방 CRUD."""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from fastapi.responses import StreamingResponse

from core.database import get_conn
from core.dependencies import get_current_user
from core.security import decrypt_api_key
from domain.chat.schemas import (
    ChatRequest, ChatResponse, KnowledgeResult,
    DebugSearchResponse, GlossaryMatchInfo, DebugResult, FewshotResult,
    ConversationCreate, ConversationResponse, MessageResponse,
)
from core.config import settings
from domain.llm.factory import get_llm_provider
from domain.knowledge import retrieval
from domain.chat import memory
from domain.knowledge.retrieval import GlossaryMatch, RetrievalResult
from shared.embedding import embedding_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

_LLM_UNAVAILABLE_MSG = "[LLM 서버에 연결할 수 없습니다. 검색 결과를 참고하세요.]"

MAX_MESSAGES_PER_NS = 100
QUERY_LOG_RETENTION_DAYS = 90


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── 공통 파이프라인 ──────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    query_vec: list[float]
    glossary_match: Optional[GlossaryMatch]
    mapped_term: Optional[str]
    enriched_query: str
    results: list[RetrievalResult]
    fewshots: list[dict]
    context: str


async def _run_pipeline(
    namespace: str, question: str, query_vec: list[float],
    w_vector: float, w_keyword: float, top_k: int,
    *, debug: bool = False,
) -> PipelineResult:
    glossary_match = await retrieval.map_glossary_term(namespace, query_vec)
    mapped_term = glossary_match.term if glossary_match else None
    enriched_query = f"{question} {mapped_term}" if mapped_term else question

    fewshot_kwargs = {"min_similarity": 0.0} if debug else {}
    results, fewshots = await asyncio.gather(
        retrieval.search_knowledge(namespace, query_vec, enriched_query, w_vector, w_keyword, top_k),
        retrieval.fetch_fewshots(namespace, query_vec, **fewshot_kwargs),
    )

    fs_section = retrieval.build_fewshot_section(fewshots)
    doc_context = retrieval.build_context(results)
    context = f"{fs_section}\n\n{doc_context}" if fs_section else doc_context

    return PipelineResult(
        query_vec=query_vec, glossary_match=glossary_match,
        mapped_term=mapped_term, enriched_query=enriched_query,
        results=results, fewshots=fewshots, context=context,
    )


def _results_to_json(results: list[RetrievalResult]) -> str:
    return json.dumps(
        [{"id": r.id, "content": r.content, "final_score": r.final_score,
          "container_name": r.container_name, "target_tables": r.target_tables,
          "query_template": r.query_template}
         for r in results],
        ensure_ascii=False,
    )


def _results_to_payload(results: list[RetrievalResult]) -> list[dict]:
    return [
        {"id": r.id, "container_name": r.container_name,
         "target_tables": r.target_tables, "content": r.content,
         "query_template": r.query_template, "final_score": r.final_score}
        for r in results
    ]


def _get_user_api_key(user: dict) -> Optional[str]:
    """사용자의 암호화된 API Key를 복호화. 없으면 None."""
    encrypted = user.get("encrypted_llm_api_key")
    if not encrypted:
        return None
    try:
        return decrypt_api_key(encrypted)
    except Exception:
        return None


def _require_api_key(user: dict) -> str:
    """사내 LLM 사용 시 사용자 API Key가 필수. 없으면 HTTPException."""
    key = _get_user_api_key(user)
    if not key and settings.llm_provider == "inhouse":
        raise HTTPException(
            status_code=403,
            detail="사내 LLM 사용을 위해 API Key가 필요합니다. 프로필에서 API Key를 등록해주세요.",
        )
    return key


# ── DB 헬퍼 ──────────────────────────────────────────────────────────────────

async def _get_or_create_conversation(
    namespace: str, question: str, conversation_id: Optional[int],
    user_id: int,
) -> int:
    async with get_conn() as conn:
        if conversation_id:
            exists = await conn.fetchval(
                "SELECT 1 FROM ops_conversation WHERE id = $1 AND user_id = $2",
                conversation_id, user_id,
            )
            if exists:
                return conversation_id
        row = await conn.fetchrow(
            "INSERT INTO ops_conversation (namespace, title, user_id) VALUES ($1, $2, $3) RETURNING id",
            namespace, question[:200], user_id,
        )
        return row["id"]


async def _save_user_message(conversation_id: int, question: str) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "INSERT INTO ops_message (conversation_id, role, content) VALUES ($1, $2, $3)",
            conversation_id, "user", question,
        )


async def _save_assistant_message(
    conversation_id: int, answer: str,
    mapped_term: Optional[str], results: list[RetrievalResult],
) -> int:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ops_message (conversation_id, role, content, mapped_term, results)
            VALUES ($1, $2, $3, $4, $5::jsonb) RETURNING id
            """,
            conversation_id, "assistant", answer, mapped_term, _results_to_json(results),
        )
    return row["id"]


async def _pre_create_assistant_message(
    conversation_id: int, mapped_term: Optional[str], results: list[RetrievalResult],
) -> int:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ops_message (conversation_id, role, content, mapped_term, results, status)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6) RETURNING id
            """,
            conversation_id, "assistant", "", mapped_term, _results_to_json(results), "generating",
        )
    return row["id"]


async def _update_assistant_message(msg_id: int, content: str, status: str = "generating") -> None:
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE ops_message SET content = $1, status = $2 WHERE id = $3", content, status, msg_id,
        )


async def _cleanup_ghost_messages(conversation_id: int) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "DELETE FROM ops_message WHERE conversation_id = $1 AND role = 'assistant' AND content = '' AND status != 'generating'",
            conversation_id,
        )


async def _safe_generate(context: str, question: str, history: list[dict] | None = None, *, api_key: Optional[str] = None) -> str:
    try:
        return await get_llm_provider().generate(context, question, history, api_key=api_key)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
        logger.warning("LLM 호출 실패: %s", e)
        return _LLM_UNAVAILABLE_MSG


async def _create_query_log(
    namespace: str, question: str, answer: str,
    has_results: bool, mapped_term: Optional[str] = None,
    message_id: Optional[int] = None,
) -> int:
    is_real_answer = answer and answer != _LLM_UNAVAILABLE_MSG
    status = "unresolved" if (not has_results and not is_real_answer) else "pending"
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ops_query_log (namespace, question, answer, status, mapped_term, message_id)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING id
            """,
            namespace, question, answer, status, mapped_term, message_id,
        )
    return row["id"]


async def _post_save_tasks(conv_id: int, namespace: Optional[str] = None) -> None:
    try:
        await memory.maybe_summarize(conv_id, get_llm_provider())
    except Exception as e:
        logger.warning("요약 후처리 실패: %s", e)
    try:
        if namespace:
            await cleanup_old_messages(namespace)
        await cleanup_resolved_query_logs()
    except Exception as e:
        logger.warning("cleanup 실패: %s", e)


# ── Cleanup 함수 ─────────────────────────────────────────────────────────────

async def cleanup_old_messages(namespace: str) -> int:
    async with get_conn() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM ops_message m JOIN ops_conversation c ON m.conversation_id = c.id WHERE c.namespace = $1",
            namespace,
        )
        if total <= MAX_MESSAGES_PER_NS:
            return 0

        excess = total - MAX_MESSAGES_PER_NS
        affected_conv_ids = await conn.fetch(
            "SELECT DISTINCT m.conversation_id FROM ops_message m JOIN ops_conversation c ON m.conversation_id = c.id WHERE c.namespace = $1 ORDER BY m.conversation_id LIMIT $2",
            namespace, excess,
        )
        affected_ids = [r["conversation_id"] for r in affected_conv_ids]

        result = await conn.execute(
            """
            DELETE FROM ops_message WHERE id IN (
                SELECT m.id FROM ops_message m
                JOIN ops_conversation c ON m.conversation_id = c.id
                WHERE c.namespace = $1 ORDER BY m.created_at ASC LIMIT $2
            )
            """,
            namespace, excess,
        )
        deleted = int(result.split()[-1]) if result else 0

        if deleted > 0 and affected_ids:
            await conn.execute(
                "UPDATE ops_conversation SET trimmed = TRUE WHERE id = ANY($1::int[])", affected_ids,
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


# ── Chat 엔드포인트 ──────────────────────────────────────────────────────────

@router.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    api_key = _require_api_key(user)
    conv_id = await _get_or_create_conversation(req.namespace, req.question, req.conversation_id, user["id"])

    # ── 멀티턴 검색 보강: 직전 Q+A를 결합하여 검색 맥락 확보 ──
    search_question = req.question
    async with get_conn() as conn:
        prev_pair = await conn.fetch(
            """
            SELECT role, content FROM (
                SELECT role, content, created_at, id FROM ops_message
                WHERE conversation_id = $1
                ORDER BY created_at DESC, id DESC LIMIT 2
            ) sub ORDER BY sub.created_at ASC, sub.id ASC
            """,
            conv_id,
        )
    if prev_pair:
        prev_context = " ".join(r["content"][:80] for r in prev_pair if r["content"])
        search_question = f"{prev_context} {req.question}"

    query_vec = await embedding_service.embed(search_question)

    pipe, history = await asyncio.gather(
        _run_pipeline(req.namespace, search_question, query_vec, req.w_vector, req.w_keyword, req.top_k),
        memory.build_context_history(conv_id, query_vec),
    )

    answer = await _safe_generate(pipe.context, req.question, history, api_key=api_key)

    await _save_user_message(conv_id, req.question)
    msg_id = await _save_assistant_message(conv_id, answer, pipe.mapped_term, pipe.results)
    await _create_query_log(req.namespace, req.question, answer, len(pipe.results) > 0, pipe.mapped_term, msg_id)
    asyncio.create_task(_post_save_tasks(conv_id, req.namespace))

    return ChatResponse(
        conversation_id=conv_id, question=req.question, mapped_term=pipe.mapped_term,
        results=[
            KnowledgeResult(
                id=r.id, container_name=r.container_name,
                target_tables=r.target_tables, content=r.content,
                query_template=r.query_template, final_score=r.final_score,
            )
            for r in pipe.results
        ],
        answer=answer,
    )


@router.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, user: dict = Depends(get_current_user)):
    api_key = _require_api_key(user)
    conv_id = await _get_or_create_conversation(req.namespace, req.question, req.conversation_id, user["id"])
    await _cleanup_ghost_messages(conv_id)

    await _save_user_message(conv_id, req.question)
    msg_id = await _pre_create_assistant_message(conv_id, None, [])

    queue: asyncio.Queue = asyncio.Queue()
    asyncio.create_task(_generate_worker(
        queue, conv_id, msg_id, req.namespace, req.question,
        req.w_vector, req.w_keyword, req.top_k, api_key,
    ))

    async def event_generator():
        yield _sse({
            "type": "meta", "conversation_id": conv_id, "message_id": msg_id,
            "mapped_term": None, "results": [],
        })
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse(event)
        except (asyncio.CancelledError, GeneratorExit):
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _generate_worker(
    queue: asyncio.Queue,
    conv_id: int, msg_id: int,
    namespace: str, question: str,
    w_vector: float, w_keyword: float, top_k: int,
    api_key: Optional[str] = None,
) -> None:
    full_answer = ""
    token_count = 0
    _FLUSH_INTERVAL = 20
    mapped_term = None
    has_results = False

    try:
        await queue.put({"type": "status", "step": "embedding", "message": "질문 임베딩 생성 중..."})

        # ── 멀티턴 검색 보강: 직전 Q+A를 결합하여 검색 맥락 확보 ──
        search_question = question
        async with get_conn() as conn:
            prev_pair = await conn.fetch(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at, id FROM ops_message
                    WHERE conversation_id = $1 AND id < $2
                    ORDER BY created_at DESC, id DESC LIMIT 2
                ) sub ORDER BY sub.created_at ASC, sub.id ASC
                """,
                conv_id, msg_id,
            )
        if prev_pair:
            prev_context = " ".join(r["content"][:80] for r in prev_pair if r["content"])
            search_question = f"{prev_context} {question}"
            logger.info("멀티턴 검색 보강: '%s' → '%s'", question, search_question)

        query_vec = await embedding_service.embed(search_question)

        await queue.put({"type": "status", "step": "context", "message": "용어 매핑 및 대화 맥락 검색 중..."})
        glossary_match, history = await asyncio.gather(
            retrieval.map_glossary_term(namespace, query_vec),
            memory.build_context_history(conv_id, query_vec),
        )
        mapped_term = glossary_match.term if glossary_match else None
        enriched_query = f"{search_question} {mapped_term}" if mapped_term else search_question

        await queue.put({"type": "status", "step": "search", "message": "관련 문서 검색 중..."})
        results, fewshots = await asyncio.gather(
            retrieval.search_knowledge(namespace, query_vec, enriched_query, w_vector, w_keyword, top_k),
            retrieval.fetch_fewshots(namespace, query_vec),
        )

        fs_section = retrieval.build_fewshot_section(fewshots)
        doc_context = retrieval.build_context(results)
        context = f"{fs_section}\n\n{doc_context}" if fs_section else doc_context
        has_results = len(results) > 0

        async with get_conn() as conn:
            await conn.execute(
                "UPDATE ops_message SET mapped_term = $1, results = $2::jsonb WHERE id = $3",
                mapped_term, _results_to_json(results), msg_id,
            )

        await queue.put({
            "type": "meta", "conversation_id": conv_id, "message_id": msg_id,
            "mapped_term": mapped_term, "results": _results_to_payload(results),
        })

        await queue.put({"type": "status", "step": "llm", "message": "AI 답변 생성 중..."})

        try:
            async for token in get_llm_provider().generate_stream(context, question, history, api_key=api_key):
                full_answer += token
                token_count += 1
                if token_count == 1 or token_count % _FLUSH_INTERVAL == 0:
                    await _update_assistant_message(msg_id, full_answer)
                await queue.put({"type": "token", "data": token})
        except Exception as e:
            logger.warning("LLM 스트리밍 실패: %s", e)
            full_answer = _LLM_UNAVAILABLE_MSG
            await queue.put({"type": "token", "data": _LLM_UNAVAILABLE_MSG})

        final_answer = full_answer or _LLM_UNAVAILABLE_MSG
        await _update_assistant_message(msg_id, final_answer, "completed")
        await _create_query_log(namespace, question, final_answer, has_results, mapped_term, msg_id)
        await queue.put({"type": "done", "message_id": msg_id})

    except Exception as e:
        logger.error("generate_worker 에러: %s", e, exc_info=True)
        if not full_answer:
            full_answer = _LLM_UNAVAILABLE_MSG
        await _update_assistant_message(msg_id, full_answer, "completed")
    finally:
        await queue.put(None)
        try:
            await _post_save_tasks(conv_id, namespace)
        except Exception as e:
            logger.warning("post_save_tasks 실패: %s", e)


@router.patch("/api/chat/messages/{msg_id}/content")
async def save_partial_content(msg_id: int, body: dict, user: dict = Depends(get_current_user)):
    content = (body.get("content") or "").strip()
    if not content:
        return {"ok": True}
    async with get_conn() as conn:
        current = await conn.fetchval(
            "SELECT LENGTH(content) FROM ops_message WHERE id = $1 AND role = 'assistant'", msg_id,
        )
        if current is not None and len(content) > current:
            await conn.execute("UPDATE ops_message SET content = $1 WHERE id = $2", content, msg_id)
    return {"ok": True}


@router.delete("/api/chat/messages/{msg_id}")
async def delete_ghost_message(msg_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT m.conversation_id,
                   (SELECT id FROM ops_message WHERE conversation_id = m.conversation_id AND id < m.id AND role = 'user'
                    ORDER BY id DESC LIMIT 1) AS prev_user_id
            FROM ops_message m
            WHERE m.id = $1 AND m.role = 'assistant' AND (m.content IS NULL OR m.content = '')
            """,
            msg_id,
        )
        if not row:
            return {"ok": True}
        conv_id = row["conversation_id"]
        await conn.execute("DELETE FROM ops_message WHERE id = $1", msg_id)
        if row["prev_user_id"]:
            await conn.execute("DELETE FROM ops_message WHERE id = $1", row["prev_user_id"])
        remaining = await conn.fetchval("SELECT COUNT(*) FROM ops_message WHERE conversation_id = $1", conv_id)
        if remaining == 0:
            await conn.execute("DELETE FROM ops_conversation WHERE id = $1", conv_id)
    return {"ok": True}


@router.post("/api/chat/debug", response_model=DebugSearchResponse)
async def chat_debug(req: ChatRequest, user: dict = Depends(get_current_user)):
    query_vec = await embedding_service.embed(req.question)
    pipe = await _run_pipeline(
        req.namespace, req.question, query_vec, req.w_vector, req.w_keyword, req.top_k, debug=True,
    )
    return DebugSearchResponse(
        question=req.question, namespace=req.namespace,
        enriched_query=pipe.enriched_query,
        glossary_match=GlossaryMatchInfo(
            term=pipe.glossary_match.term, description=pipe.glossary_match.description,
            similarity=pipe.glossary_match.similarity,
        ) if pipe.glossary_match else None,
        w_vector=req.w_vector, w_keyword=req.w_keyword,
        fewshots=[
            FewshotResult(question=fs["question"], answer=fs["answer"], similarity=fs.get("similarity", 0.0))
            for fs in pipe.fewshots
        ],
        results=[
            DebugResult(
                id=r.id, container_name=r.container_name, target_tables=r.target_tables,
                content=r.content, query_template=r.query_template, base_weight=r.base_weight,
                v_score=r.v_score, k_score=r.k_score, final_score=r.final_score,
            )
            for r in pipe.results
        ],
        context_preview=pipe.context[:1200] + "\n..." if len(pipe.context) > 1200 else pipe.context,
    )


# ── Conversations CRUD ───────────────────────────────────────────────────────

@router.get("/api/conversations", response_model=list[ConversationResponse])
async def list_conversations(namespace: str = Query(...), user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, namespace, title, trimmed, created_at::text
            FROM ops_conversation
            WHERE namespace = $1 AND user_id = $2
            ORDER BY created_at DESC LIMIT 50
            """,
            namespace, user["id"],
        )
    return [ConversationResponse(**dict(r)) for r in rows]


@router.post("/api/conversations", response_model=ConversationResponse, status_code=201)
async def create_conversation(body: ConversationCreate, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO ops_conversation (namespace, title, user_id) VALUES ($1, $2, $3) RETURNING id, namespace, title, trimmed, created_at::text",
            body.namespace, body.title[:200] if body.title else "", user["id"],
        )
    return ConversationResponse(**dict(row))


@router.get("/api/conversations/{conv_id}/messages", response_model=list[MessageResponse])
async def get_messages(conv_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        # 대화 소유권 확인
        owner_id = await conn.fetchval("SELECT user_id FROM ops_conversation WHERE id = $1", conv_id)
        if owner_id is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if owner_id != user["id"] and user["role"] != "admin":
            raise HTTPException(status_code=403, detail="다른 사용자의 대화에 접근할 수 없습니다.")

        rows = await conn.fetch(
            """
            SELECT m.id, m.conversation_id, m.role, m.content,
                   m.mapped_term, m.results, m.status, m.created_at::text,
                   EXISTS(SELECT 1 FROM ops_feedback f WHERE f.message_id = m.id) AS has_feedback
            FROM ops_message m WHERE m.conversation_id = $1 ORDER BY m.id ASC
            """,
            conv_id,
        )
    return [
        MessageResponse(
            id=r["id"], conversation_id=r["conversation_id"], role=r["role"],
            content=r["content"], mapped_term=r["mapped_term"],
            results=json.loads(r["results"]) if isinstance(r["results"], str) else r["results"],
            status=r["status"], has_feedback=r["has_feedback"], created_at=r["created_at"],
        )
        for r in rows
    ]


@router.delete("/api/conversations/{conv_id}", status_code=204)
async def delete_conversation(conv_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        owner_id = await conn.fetchval("SELECT user_id FROM ops_conversation WHERE id = $1", conv_id)
        if owner_id is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if owner_id != user["id"] and user["role"] != "admin":
            raise HTTPException(status_code=403, detail="다른 사용자의 대화를 삭제할 수 없습니다.")
        result = await conn.execute("DELETE FROM ops_conversation WHERE id = $1", conv_id)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Conversation not found")
