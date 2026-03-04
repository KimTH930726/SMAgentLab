"""
/api/conversations  — 대화방 CRUD + 메시지 조회
"""
import json

from fastapi import APIRouter, HTTPException, Query

from database import get_conn
from models.api_models import ConversationCreate, ConversationResponse, MessageResponse

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(namespace: str = Query(...)):
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, namespace, title, created_at::text
            FROM ops_conversation
            WHERE namespace = $1
            ORDER BY created_at DESC
            LIMIT 50
            """,
            namespace,
        )
    return [ConversationResponse(**dict(r)) for r in rows]


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(body: ConversationCreate):
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "INSERT INTO ops_conversation (namespace, title) VALUES ($1, $2) RETURNING id, namespace, title, created_at::text",
            body.namespace, body.title[:200] if body.title else "",
        )
    return ConversationResponse(**dict(row))


@router.get("/{conv_id}/messages", response_model=list[MessageResponse])
async def get_messages(conv_id: int):
    async with get_conn() as conn:
        exists = await conn.fetchval("SELECT 1 FROM ops_conversation WHERE id = $1", conv_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Conversation not found")
        rows = await conn.fetch(
            """
            SELECT id, conversation_id, role, content, mapped_term, results, created_at::text
            FROM ops_message
            WHERE conversation_id = $1
            ORDER BY created_at ASC
            """,
            conv_id,
        )
    return [
        MessageResponse(
            id=r["id"],
            conversation_id=r["conversation_id"],
            role=r["role"],
            content=r["content"],
            mapped_term=r["mapped_term"],
            results=json.loads(r["results"]) if isinstance(r["results"], str) else r["results"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.delete("/{conv_id}", status_code=204)
async def delete_conversation(conv_id: int):
    async with get_conn() as conn:
        result = await conn.execute("DELETE FROM ops_conversation WHERE id = $1", conv_id)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Conversation not found")
