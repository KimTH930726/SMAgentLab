"""POST /api/feedback — "답변 틀림" 신고 접수."""

from fastapi import APIRouter, Depends, HTTPException

from core.database import get_conn, resolve_namespace_id
from core.dependencies import get_current_user
from service.feedback.schemas import FeedbackCreate
from service.improvement import service as improvement

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", status_code=201)
async def submit_feedback(body: FeedbackCreate, user: dict = Depends(get_current_user)):
    """"답변 틀림" → 개선 원장에 1건(옛 리뷰 신호 대체). 근거 전부를 후보로 올려두고, 이어서 한 줄 의견이 오면 그 건을
    채워 AI가 틀린 근거를 고른다. 본인 대화의 이 파트 메시지일 때만(남의 질문·답변이 원장에 실리지 않게).

    👍는 받지만 아무것도 바꾸지 않는다(2026-10-02) — 예전엔 가중치 +0.1·질의 "해결"까지 했는데, 누를수록 오르는 무검증
    자동 반영이었고 해결/미해결 구분도 없앴다. 지식 가중치 입력도 전부 없앴다(v2.121). 채팅에선 버튼도 뺐다(옛 화면 호환용)."""
    if body.is_positive or body.message_id is None:
        return {"status": "ok"}
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, body.namespace)
        if ns_id is None:
            raise HTTPException(status_code=404, detail="namespace를 찾을 수 없습니다.")
        owned = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM ops_message m JOIN ops_conversation c ON c.id = m.conversation_id "
            "WHERE m.id = $1 AND c.user_id = $2 AND c.namespace_id = $3)", body.message_id, user.get("id"), ns_id)
    if owned is True:
        # 원장 1건이 이 신고의 유일한 기록(ops_feedback 제거) — 저장이 실패했는데 201을 주면 사용자는 접수된 줄 안다
        if await improvement.record_answer_signal(ns_id, body.message_id, user.get("id")) is None:
            raise HTTPException(status_code=503, detail="신고를 저장하지 못했습니다. 잠시 후 다시 눌러 주세요.")
    return {"status": "ok"}
