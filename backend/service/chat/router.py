"""대화 엔드포인트 — SSE 스트리밍 + 단일 응답 + 디버그 + 대화방 CRUD."""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from core.database import get_conn, resolve_namespace_id
from core.dependencies import get_current_user
from core.security import get_user_api_key
from core.config import settings
from service.chat.schemas import (
    ChatRequest, ChatResponse, KnowledgeResult,
    DebugSearchResponse, GlossaryMatchInfo, DebugResult, FewshotResult,
    ConversationCreate, ConversationResponse, MessageResponse,
)
from service.chat.helpers import (
    LLM_UNAVAILABLE_MSG, results_to_json,
    update_inhouse_conv_id, create_query_log, post_save_tasks,
)
from service.llm.factory import get_llm_provider
from agents.knowledge_rag.knowledge import retrieval
from service.chat import memory
from agents.knowledge_rag.knowledge.retrieval import GlossaryMatch, RetrievalResult
from shared.embedding import embedding_service
from agents.base import AgentRegistry

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── 공통 파이프라인 (비스트리밍 chat / debug 전용) ────────────────────────────

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
    *, debug: bool = False, category: Optional[str] = None,
) -> PipelineResult:
    glossary_match = await retrieval.map_glossary_term(namespace, query_vec)
    mapped_term = glossary_match.term if glossary_match else None
    enriched_query = f"{question} {mapped_term}" if mapped_term else question

    fewshot_kwargs = {"min_similarity": 0.0} if debug else {}
    results, fewshots = await asyncio.gather(
        retrieval.search_knowledge(namespace, query_vec, enriched_query, w_vector, w_keyword, top_k, category),
        retrieval.fetch_fewshots(namespace, query_vec, **fewshot_kwargs),
    )

    # debug 모드에서는 모든 퓨샷을 반환하지만, context 빌드는 실제 임계값 기준으로 필터링
    if debug:
        th = retrieval.get_thresholds()
        fewshots_for_context = [fs for fs in fewshots if fs["similarity"] >= th["fewshot_min_similarity"]]
    else:
        fewshots_for_context = fewshots

    fs_section = retrieval.build_fewshot_section(fewshots_for_context)
    doc_context = retrieval.build_context(results)
    context = f"{fs_section}\n\n{doc_context}" if fs_section else doc_context

    return PipelineResult(
        query_vec=query_vec, glossary_match=glossary_match,
        mapped_term=mapped_term, enriched_query=enriched_query,
        results=results, fewshots=fewshots, context=context,
    )


def _require_api_key(user: dict) -> str:
    """사내 LLM 사용 시 사용자 API Key가 필수. 없으면 HTTPException."""
    key = get_user_api_key(user)
    if not key and settings.llm_provider == "inhouse":
        raise HTTPException(
            status_code=403,
            detail="사내 LLM 사용을 위해 API Key가 필요합니다. 프로필에서 API Key를 등록해주세요.",
        )
    return key


# ── DB 헬퍼 (라우터 전용) ─────────────────────────────────────────────────────

async def _get_or_create_conversation(
    namespace: str, question: str, conversation_id: Optional[int],
    user_id: int,
) -> tuple[int, Optional[str]]:
    """(conv_id, inhouse_conv_id) 반환. inhouse_conv_id는 사내 LLM 측 대화 ID."""
    async with get_conn() as conn:
        if conversation_id:
            row = await conn.fetchrow(
                "SELECT id, inhouse_conv_id FROM ops_conversation WHERE id = $1 AND user_id = $2",
                conversation_id, user_id,
            )
            if row:
                return row["id"], row["inhouse_conv_id"]
        ns_id = await resolve_namespace_id(conn, namespace)
        row = await conn.fetchrow(
            "INSERT INTO ops_conversation (namespace_id, title, user_id) VALUES ($1, $2, $3) RETURNING id, inhouse_conv_id",
            ns_id, question[:200], user_id,
        )
        return row["id"], row["inhouse_conv_id"]


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
            conversation_id, "assistant", answer, mapped_term, results_to_json(results),
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
            conversation_id, "assistant", "", mapped_term, results_to_json(results), "generating",
        )
    return row["id"]


async def _cleanup_ghost_messages(conversation_id: int) -> None:
    async with get_conn() as conn:
        await conn.execute(
            "DELETE FROM ops_message WHERE conversation_id = $1 AND role = 'assistant' AND content = '' AND status != 'generating'",
            conversation_id,
        )


async def _safe_generate(
    context: str, question: str, history: list[dict] | None = None,
    *, api_key: Optional[str] = None, ext_conversation_id: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    try:
        return await get_llm_provider().generate(
            context, question, history, api_key=api_key, ext_conversation_id=ext_conversation_id,
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
        logger.warning("LLM 호출 실패: %s", e)
        return LLM_UNAVAILABLE_MSG, None


# ── Chat 엔드포인트 ──────────────────────────────────────────────────────────

@router.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user: dict = Depends(get_current_user)):
    api_key = _require_api_key(user)
    conv_id, inhouse_conv_id = await _get_or_create_conversation(req.namespace, req.question, req.conversation_id, user["id"])

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
        _run_pipeline(req.namespace, search_question, query_vec, req.w_vector, req.w_keyword, req.top_k, category=req.category),
        memory.build_context_history(conv_id, query_vec),
    )

    answer, new_inhouse_conv_id = await _safe_generate(
        pipe.context, req.question, history, api_key=api_key, ext_conversation_id=inhouse_conv_id,
    )
    if new_inhouse_conv_id and new_inhouse_conv_id != inhouse_conv_id:
        asyncio.create_task(update_inhouse_conv_id(conv_id, new_inhouse_conv_id))

    _, msg_id = await asyncio.gather(
        _save_user_message(conv_id, req.question),
        _save_assistant_message(conv_id, answer, pipe.mapped_term, pipe.results),
    )
    await create_query_log(req.namespace, req.question, answer, len(pipe.results) > 0, pipe.mapped_term, msg_id)
    asyncio.create_task(post_save_tasks(conv_id, req.namespace))

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
    conv_id, inhouse_conv_id = await _get_or_create_conversation(req.namespace, req.question, req.conversation_id, user["id"])
    await _cleanup_ghost_messages(conv_id)

    await _save_user_message(conv_id, req.question)
    msg_id = await _pre_create_assistant_message(conv_id, None, [])

    # ── AgentRegistry를 통한 위임 ──
    agent = AgentRegistry.get(req.agent_type)
    agent_context = {
        "namespace": req.namespace,
        "msg_id": msg_id,
        "w_vector": req.w_vector,
        "w_keyword": req.w_keyword,
        "top_k": req.top_k,
        "api_key": api_key,
        "inhouse_conv_id": inhouse_conv_id,
        "category": req.category,
    }
    # HTTP 도구 승인 정보가 있으면 컨텍스트에 추가
    if req.approved_tool:
        agent_context["approved_tool"] = req.approved_tool.model_dump()
    if req.selected_tool_id:
        agent_context["selected_tool_id"] = req.selected_tool_id

    async def event_generator():
        yield _sse({
            "type": "meta", "conversation_id": conv_id, "message_id": msg_id,
            "mapped_term": None, "results": [],
        })
        try:
            async for event in agent.stream_chat(req.question, dict(user), conv_id, agent_context):
                yield _sse(event)
        except (asyncio.CancelledError, GeneratorExit):
            pass
        except Exception as e:
            logger.error("SSE event_generator 에러: %s", e, exc_info=True)
        finally:
            # 안전장치: generating 상태로 남은 메시지를 completed로 전환
            try:
                async with get_conn() as conn:
                    row = await conn.fetchrow(
                        "SELECT status, content FROM ops_message WHERE id = $1", msg_id,
                    )
                if row and row["status"] == "generating":
                    content = row["content"]
                    if not content or not content.strip():
                        await update_assistant_message(msg_id, "[연결이 끊어졌습니다.]", "completed")
                    else:
                        await update_assistant_message(msg_id, content, "completed")
            except Exception:
                logger.warning("메시지 상태 정리 실패: msg_id=%s", msg_id, exc_info=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.patch("/api/chat/messages/{msg_id}/content")
async def save_partial_content(msg_id: int, body: dict, user: dict = Depends(get_current_user)):
    content = (body.get("content") or "").strip()
    if not content:
        return {"status": "ok"}
    async with get_conn() as conn:
        current = await conn.fetchval(
            "SELECT LENGTH(content) FROM ops_message WHERE id = $1 AND role = 'assistant'", msg_id,
        )
        if current is not None and len(content) > current:
            await conn.execute("UPDATE ops_message SET content = $1 WHERE id = $2", content, msg_id)
    return {"status": "ok"}


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
            return {"status": "ok"}
        conv_id = row["conversation_id"]
        await conn.execute("DELETE FROM ops_message WHERE id = $1", msg_id)
        if row["prev_user_id"]:
            await conn.execute("DELETE FROM ops_message WHERE id = $1", row["prev_user_id"])
        remaining = await conn.fetchval("SELECT COUNT(*) FROM ops_message WHERE conversation_id = $1", conv_id)
        if remaining == 0:
            await conn.execute("DELETE FROM ops_conversation WHERE id = $1", conv_id)
    return {"status": "ok"}


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
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT c.id, n.name AS namespace, c.title, c.trimmed, c.created_at::text
            FROM ops_conversation c
            JOIN ops_namespace n ON c.namespace_id = n.id
            WHERE c.namespace_id = $1 AND c.user_id = $2
            ORDER BY c.created_at DESC LIMIT 50
            """,
            ns_id, user["id"],
        )
    return [ConversationResponse(**dict(r)) for r in rows]


@router.post("/api/conversations", response_model=ConversationResponse, status_code=201)
async def create_conversation(body: ConversationCreate, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, body.namespace)
        row = await conn.fetchrow(
            """
            INSERT INTO ops_conversation (namespace_id, title, user_id) VALUES ($1, $2, $3)
            RETURNING id, $4::text AS namespace, title, trimmed, created_at::text
            """,
            ns_id, body.title[:200] if body.title else "", user["id"], body.namespace,
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
                   m.metadata,
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
            metadata=json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"],
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
