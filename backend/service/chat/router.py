"""대화 엔드포인트 — SSE 스트리밍 + 대화방 CRUD. 옛 단일 응답 /api/chat은 11/09 삭제(화면은 스트림만, 캐시·문맥 예산·되묻기가 없는 LLM 경로)."""
import asyncio
import contextlib
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from core.database import get_conn, resolve_namespace_id
from core.dependencies import get_current_user, log_cross_part_namespace_read
from core.security import get_user_llm_credentials
from service.chat.schemas import (
    ChatRequest,
    ConversationCreate, ConversationResponse, MessageResponse,
)
from service.chat.helpers import results_to_json, update_assistant_message
from agents.knowledge_rag.knowledge.retrieval import RetrievalResult
from agents.base import AgentRegistry

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# 연결 유지 신호(11/06 반입 전 검사) — AWS ALB는 기본 60초 동안 바이트가 안 오면 끊는다. 검색·LLM 첫 글자까지 그보다 길 수 있어
# 그 사이 SSE 주석 줄을 보낸다(화면 파서는 'data:' 줄만 읽어 무시). 15초 = ALB·nginx 기본 유휴 시간보다 충분히 짧게.
_KEEPALIVE_SEC = 15
_SSE_PING = ": ping\n\n"
_STREAM_END = object()


async def _with_keepalive(events, interval: float = _KEEPALIVE_SEC):
    """`events`(비동기 이터레이터)를 그대로 넘기되, `interval`초 동안 다음 이벤트가 없으면 None(= 핑)을 낸다.
    이벤트는 태스크 하나가 끝까지 읽어 큐로 넘긴다(제너레이터를 여러 태스크가 번갈아 돌리지 않게). 소비자가 그만두면(연결 끊김) 그 태스크를 취소한다."""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for ev in events:
                await queue.put(ev)
        except BaseException as e:   # noqa: BLE001 — 오류도 소비자 쪽에서 원래대로 다시 올린다
            await queue.put(e)
            return
        await queue.put(_STREAM_END)

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield None
                continue
            if item is _STREAM_END:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        if not task.done():
            task.cancel()
        # 안쪽(에이전트)이 취소 정리를 끝낼 때까지 기다린다 — 안 기다리면 바깥 안전장치(generating → completed)가 먼저 돌고,
        # 늦게 커밋된 에이전트 UPDATE가 상태를 generating으로 되돌린다(v2.219 독립 리뷰 M1). 순서 보장은 aclose·취소 1회 경로만:
        # 실제 연결 끊김(Starlette/anyio 취소 범위)은 취소를 거듭 보내 정리 await와 안전장치가 둘 다 끊긴다 — 예전(같은 태스크)과 같고,
        # 남은 generating은 다음 요청의 _cleanup_ghost_messages가 정리(재리뷰 L1, 백로그 0-6).
        with contextlib.suppress(BaseException):
            await task


def _resolve_user_credentials(user: dict) -> Optional[dict]:
    """사용자별 LLM 자격증명이 있으면 dict 반환, 없으면 None (시스템 .env fallback).

    InHouse OAuth2 트리플: {client_id, client_secret, user_id}
    """
    return get_user_llm_credentials(user)


# ── DB 헬퍼 (라우터 전용) ─────────────────────────────────────────────────────

async def _get_or_create_conversation(
    namespace: str, question: str, conversation_id: Optional[int],
    user_id: int, agent_type: str,
) -> tuple[int, Optional[str]]:
    """(conv_id, inhouse_conv_id) 반환. inhouse_conv_id는 사내 LLM 측 대화 ID.

    같은 대화방을 다른 에이전트로 이어서 쓰면 검색 맥락·결과 렌더링이 섞이므로,
    기존 대화방의 agent_type이 요청과 다르면 거부한다.
    """
    async with get_conn() as conn:
        if conversation_id:
            row = await conn.fetchrow(
                "SELECT id, inhouse_conv_id, agent_type FROM ops_conversation WHERE id = $1 AND user_id = $2",
                conversation_id, user_id,
            )
            if row:
                if row["agent_type"] != agent_type:
                    raise HTTPException(
                        status_code=409,
                        detail=f"이 대화방은 '{row['agent_type']}' 에이전트로 시작되었습니다. 새 대화를 시작해주세요.",
                    )
                return row["id"], row["inhouse_conv_id"]
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise HTTPException(status_code=404, detail=f"namespace '{namespace}'를 찾을 수 없습니다.")
        row = await conn.fetchrow(
            "INSERT INTO ops_conversation (namespace_id, title, user_id, agent_type) VALUES ($1, $2, $3, $4) RETURNING id, inhouse_conv_id",
            ns_id, question[:200], user_id, agent_type,
        )
        return row["id"], row["inhouse_conv_id"]


async def _save_user_message(conversation_id: int, question: str) -> int:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO ops_message (conversation_id, role, content) VALUES ($1, $2, $3) RETURNING id",
            conversation_id, "user", question,
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


# ── Chat 엔드포인트 ──────────────────────────────────────────────────────────

@router.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, user: dict = Depends(get_current_user)):
    await log_cross_part_namespace_read(req.namespace, user)
    user_creds = _resolve_user_credentials(user)
    conv_id, inhouse_conv_id = await _get_or_create_conversation(req.namespace, req.question, req.conversation_id, user["id"], req.agent_type)
    await _cleanup_ghost_messages(conv_id)

    user_msg_id = await _save_user_message(conv_id, req.question)
    msg_id = await _pre_create_assistant_message(conv_id, None, [])

    # ── AgentRegistry를 통한 위임 ──
    agent = AgentRegistry.get(req.agent_type)
    agent_context = {
        "namespace": req.namespace,
        "msg_id": msg_id,
        "user_msg_id": user_msg_id,
        "w_vector": req.w_vector,
        "w_keyword": req.w_keyword,
        "top_k": req.top_k,
        "user_credentials": user_creds,
        "inhouse_conv_id": inhouse_conv_id,
        "clarify_scope": req.clarify_scope,
        "suggest_of": req.suggest_of,
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
            # aclosing — 화면이 끊으면 안쪽 스트림 태스크까지 바로 멈춘다(GC를 기다리며 LLM이 계속 돌지 않게)
            async with contextlib.aclosing(_with_keepalive(agent.stream_chat(req.question, dict(user), conv_id, agent_context))) as events:
                async for event in events:
                    yield _SSE_PING if event is None else _sse(event)
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


# ── Conversations CRUD ───────────────────────────────────────────────────────

@router.get("/api/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    namespace: str = Query(...), agent_type: str = Query("knowledge_rag"),
    user: dict = Depends(get_current_user),
):
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT c.id, n.name AS namespace, c.title, c.trimmed, c.agent_type, c.created_at::text
            FROM ops_conversation c
            JOIN ops_namespace n ON c.namespace_id = n.id
            WHERE c.namespace_id = $1 AND c.user_id = $2 AND c.agent_type = $3
            ORDER BY c.created_at DESC LIMIT 50
            """,
            ns_id, user["id"], agent_type,
        )
    return [ConversationResponse(**dict(r)) for r in rows]


@router.post("/api/conversations", response_model=ConversationResponse, status_code=201)
async def create_conversation(body: ConversationCreate, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, body.namespace)
        if ns_id is None:
            raise HTTPException(status_code=404, detail=f"namespace '{body.namespace}'를 찾을 수 없습니다.")
        row = await conn.fetchrow(
            """
            INSERT INTO ops_conversation (namespace_id, title, user_id, agent_type) VALUES ($1, $2, $3, $4)
            RETURNING id, $5::text AS namespace, title, trimmed, agent_type, created_at::text
            """,
            ns_id, body.title[:200] if body.title else "", user["id"], body.agent_type, body.namespace,
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
                   EXISTS(SELECT 1 FROM ops_improvement_item i WHERE i.message_id = m.id) AS has_feedback
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
