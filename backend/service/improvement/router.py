"""/api/corrections — 근거 정정·지식 누락 신호(개선 원장). 신고는 로그인 사용자 누구나(신호일 뿐),
승인·반려는 관리자(담당자 지정은 다음 단계)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.dependencies import get_current_admin, get_current_user
from service.improvement import service
from service.improvement.schemas import ApproveBody, CorrectionCreate, RejectBody, RetargetBody, SeenBody

router = APIRouter(prefix="/api/corrections", tags=["corrections"])


def _ids(csv: Optional[str]) -> list[int]:
    if not csv:
        return []
    try:
        return [int(x) for x in csv.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="id 목록 형식이 잘못됐습니다.")


@router.post("", status_code=201)
async def create_correction(body: CorrectionCreate, user: dict = Depends(get_current_user)):
    try:
        return await service.create_item(
            body.namespace, user, target_type=body.target_type, target_id=body.target_id,
            target_sub_id=body.target_sub_id, message_id=body.message_id, user_input=body.user_input.strip(),
        )
    except service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/status")
async def correction_status(knowledge_ids: Optional[str] = Query(default=None),
                            policy_item_ids: Optional[str] = Query(default=None),
                            user: dict = Depends(get_current_user)):
    return await service.pending_status(_ids(knowledge_ids), _ids(policy_item_ids))


@router.get("/mine")
async def my_corrections(unseen: bool = Query(default=False), user: dict = Depends(get_current_user)):
    return await service.list_mine(user["id"], unseen)


@router.post("/mine/seen")
async def mark_my_corrections_seen(body: SeenBody, user: dict = Depends(get_current_user)):
    return {"updated": await service.mark_seen(user["id"], body.ids)}


@router.get("/pending-count")
async def correction_pending_count(admin: dict = Depends(get_current_admin)):
    return await service.pending_count()


@router.get("")
async def list_corrections(namespace: Optional[str] = Query(default=None),
                           status: Optional[str] = Query(default=None, pattern="^(pending|approved|rejected)$"),
                           admin: dict = Depends(get_current_admin)):
    return await service.list_items(namespace, status)


@router.post("/{item_id}/approve")
async def approve_correction(item_id: int, body: ApproveBody, admin: dict = Depends(get_current_admin)):
    return await service.approve(item_id, admin, body.proposed)


@router.post("/{item_id}/reject")
async def reject_correction(item_id: int, body: RejectBody, admin: dict = Depends(get_current_admin)):
    return await service.reject(item_id, admin, body.reason.strip())


@router.post("/{item_id}/retarget")
async def retarget_correction(item_id: int, body: RetargetBody, admin: dict = Depends(get_current_admin)):
    """AI가 고른 대상이 틀렸을 때 담당자가 바꾼다 — 그 대상 기준으로 수정안을 다시 만든다."""
    try:
        return await service.retarget(item_id, admin, body.key)
    except service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
