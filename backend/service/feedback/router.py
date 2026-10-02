"""POST /api/feedback — 좋아요/싫어요 피드백 처리."""

from fastapi import APIRouter, Depends

from core.database import get_conn, resolve_namespace_id
from core.dependencies import get_current_user
from service.feedback.schemas import FeedbackCreate
from service.improvement import service as improvement

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

        # 👎의 base_weight 자동 감점(-0.1)은 제거(2026-10-01) — 사람 확인 없는 자동 반영이었다. "답변 틀림"은
        # 개선 원장에 신호 1건으로만 남고(아래 record_answer_signal), 지식은 담당자 승인으로만 바뀐다. 👍 +0.1은 범위 밖.
        if body.knowledge_id and body.is_positive:
            await conn.execute(
                "UPDATE rag_knowledge SET base_weight = LEAST(base_weight + 0.1, 5.0) WHERE id = $1",
                body.knowledge_id,
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

    # "답변 틀림" → 개선 원장에 1건(옛 리뷰 신호 대체, 2026-10-02). 근거 전부를 후보로 올려두고, 이어서 한 줄 의견이
    # 오면 그 건을 채워 AI가 틀린 근거를 고른다. 본인 대화의 이 파트 메시지일 때만(남의 질문·답변이 원장에 실리지 않게).
    if not body.is_positive and body.message_id is not None:
        async with get_conn() as conn:
            owned = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM ops_message m JOIN ops_conversation c ON c.id = m.conversation_id "
                "WHERE m.id = $1 AND c.user_id = $2 AND c.namespace_id = $3)", body.message_id, user.get("id"), ns_id)
        if owned is True:
            await improvement.record_answer_signal(ns_id, body.message_id, user.get("id"))

    return {"status": "ok"}
