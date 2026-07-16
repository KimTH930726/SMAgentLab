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
        if ns_id is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="namespace를 찾을 수 없습니다.")

        await conn.execute(
            "INSERT INTO ops_feedback (knowledge_id, namespace_id, question, is_positive, message_id) VALUES ($1,$2,$3,$4,$5)",
            body.knowledge_id, ns_id, body.question, body.is_positive, body.message_id,
        )

        if body.knowledge_id:
            weight_delta = 0.1 if body.is_positive else -0.1
            bound = 5.0 if body.is_positive else 0.0
            # 동적 SQL(f-string 함수명 삽입) 대신 CASE로 분기 — 파라미터화된 단일 쿼리
            await conn.execute(
                """
                UPDATE rag_knowledge
                SET base_weight = CASE WHEN $1
                    THEN LEAST(base_weight + $2, $3)
                    ELSE GREATEST(base_weight + $2, $3)
                END
                WHERE id = $4
                """,
                body.is_positive, weight_delta, bound, body.knowledge_id,
            )

        # resolved_knowledge_id가 함께 오면(= 나빠요 후 지식 등록으로 교정) is_positive 값과
        # 무관하게 항상 해결 처리 — 잘못된 답변을 지적하고 올바른 지식을 등록한 것이므로
        new_status = "resolved" if (body.is_positive or body.resolved_knowledge_id) else "unresolved"

        # resolved로 전이될 때만 resolved_at을 지금 시각으로 찍는다 — 정렬 기준을
        # "질문한 시각"이 아니라 "해결된 시각"으로 쓰기 위함. resolved가 아니면(예:
        # 부정 피드백으로 unresolved 전환) NULL로 되돌려 이전에 해결됐던 흔적이
        # 남지 않게 한다.
        resolved_at_expr = "NOW()" if new_status == "resolved" else "NULL"

        if body.message_id is not None:
            # message_id로 정확히 매칭 — 질문 텍스트 매칭은 중복 질문/공백 차이에 취약해서
            # message_id가 있으면 우선 사용
            await conn.execute(
                f"""
                UPDATE ops_query_log
                SET status = $3, resolved_knowledge_id = COALESCE($4, resolved_knowledge_id),
                    resolved_at = {resolved_at_expr}
                WHERE namespace_id = $1 AND message_id = $2
                """,
                ns_id, body.message_id, new_status, body.resolved_knowledge_id,
            )
        else:
            # message_id가 없는 과거 호출 호환용 — 질문 텍스트로 가장 최근 1건 매칭
            await conn.execute(
                f"""
                UPDATE ops_query_log SET status = $3, resolved_knowledge_id = COALESCE($4, resolved_knowledge_id),
                    resolved_at = {resolved_at_expr}
                WHERE namespace_id = $1 AND question = $2
                  AND id = (
                      SELECT id FROM ops_query_log
                      WHERE namespace_id = $1 AND question = $2
                      ORDER BY created_at DESC LIMIT 1
                  )
                """,
                ns_id, body.question, new_status, body.resolved_knowledge_id,
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
