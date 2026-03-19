"""POST /api/feedback — 좋아요/싫어요 피드백 처리."""
from fastapi import APIRouter, Depends

from core.database import get_conn, resolve_namespace_id
from core.dependencies import get_current_user
from service.feedback.schemas import FeedbackCreate
from shared.embedding import embedding_service

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", status_code=201)
async def submit_feedback(body: FeedbackCreate, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, body.namespace)

        await conn.execute(
            "INSERT INTO ops_feedback (knowledge_id, namespace_id, question, is_positive, message_id) VALUES ($1,$2,$3,$4,$5)",
            body.knowledge_id, ns_id, body.question, body.is_positive, body.message_id,
        )

        if body.knowledge_id:
            weight_delta = 0.1 if body.is_positive else -0.1
            clamp_fn = "LEAST" if body.is_positive else "GREATEST"
            bound = 5.0 if body.is_positive else 0.0
            await conn.execute(
                f"UPDATE rag_knowledge SET base_weight = {clamp_fn}(base_weight + $1, $2) WHERE id = $3",
                weight_delta, bound, body.knowledge_id,
            )

        new_status = "resolved" if body.is_positive else "unresolved"
        # namespace_id 기반으로 query_log 업데이트
        await conn.execute(
            """
            UPDATE ops_query_log SET status = $3
            WHERE namespace_id = $1 AND question = $2
              AND id = (
                  SELECT id FROM ops_query_log
                  WHERE namespace_id = $1 AND question = $2
                  ORDER BY created_at DESC LIMIT 1
              )
            """,
            ns_id, body.question, new_status,
        )

        if body.is_positive and body.answer:
            embedding = await embedding_service.embed(body.question)
            await conn.execute(
                """
                INSERT INTO rag_fewshot (namespace_id, question, answer, knowledge_id, embedding,
                                         created_by_part, created_by_user_id, status)
                VALUES ($1, $2, $3, $4, $5::vector, $6, $7, 'candidate')
                """,
                ns_id, body.question, body.answer, body.knowledge_id,
                str(embedding), user["part"], user["id"],
            )

    return {"status": "ok"}
