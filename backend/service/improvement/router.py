"""/api/corrections — 개선 원장(정정 검토). 신고는 로그인 사용자 누구나(신호일 뿐).
처리(목록·승인·반려·대상 변경)는 그 파트 담당자 + 관리자(2026-10-02) — 옛 리뷰 신호를 파트 담당자가 처리하던 것과
맞췄다. 파트 담당자는 지식 베이스에서 자기 파트 지식을 이미 직접 고칠 수 있으므로 승인 권한도 같다."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.dependencies import check_namespace_ownership, get_current_user
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


async def _require_reviewer(namespace: str, user: dict) -> None:
    """정정 검토 권한 = 관리자 또는 그 네임스페이스 소유 파트 사용자. check_namespace_ownership은 소유 파트가 없는
    (공용) 네임스페이스를 아무 사용자에게나 열어 주는데, 여기선 다른 사람의 채팅 질문·답변이 담겨 있고 승인이 곧 지식
    변경이라 공용 네임스페이스는 관리자만 처리한다(/code-review)."""
    if user.get("role") == "admin":
        return
    await check_namespace_ownership(namespace, user)
    if not await service.is_part_owned_by(namespace, user):
        raise HTTPException(status_code=403, detail="이 파트의 담당자만 정정 검토를 처리할 수 있습니다.")


async def _require_item_owner(item_id: int, user: dict) -> None:
    ns = await service.item_namespace(item_id)
    if ns is None:
        raise HTTPException(status_code=404, detail="신고를 찾을 수 없습니다.")
    await _require_reviewer(ns, user)


@router.get("/pending-count")
async def correction_pending_count(namespace: Optional[str] = Query(default=None), user: dict = Depends(get_current_user)):
    """관리자는 전체(사이드바 배지), 파트 담당자는 자기 파트만."""
    if namespace is None:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="전체 건수는 관리자만 볼 수 있습니다.")
        return await service.pending_count()
    await _require_reviewer(namespace, user)
    n = (await service.pending_count())["by_namespace"].get(namespace, 0)
    return {"count": n, "by_namespace": {namespace: n}}


@router.get("")
async def list_corrections(namespace: Optional[str] = Query(default=None),
                           status: Optional[str] = Query(default=None, pattern="^(pending|approved|rejected)$"),
                           user: dict = Depends(get_current_user)):
    if namespace is None:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="파트를 지정하세요.")
    else:
        await _require_reviewer(namespace, user)
    return await service.list_items(namespace, status)


@router.post("/{item_id}/approve")
async def approve_correction(item_id: int, body: ApproveBody, user: dict = Depends(get_current_user)):
    await _require_item_owner(item_id, user)
    return await service.approve(item_id, user, body.proposed)


@router.post("/{item_id}/reject")
async def reject_correction(item_id: int, body: RejectBody, user: dict = Depends(get_current_user)):
    await _require_item_owner(item_id, user)
    return await service.reject(item_id, user, body.reason.strip())


@router.post("/{item_id}/analyze")
async def analyze_correction(item_id: int, user: dict = Depends(get_current_user)):
    """의견 없는 "답변 틀림"을 AI로 다시 추정 — 이관된 옛 신호처럼 분석을 못 받은 건에 담당자가 실행."""
    await _require_item_owner(item_id, user)
    try:
        return {"analyzed": await service.analyze_item(item_id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{item_id}/retarget")
async def retarget_correction(item_id: int, body: RetargetBody, user: dict = Depends(get_current_user)):
    """AI가 고른 대상이 틀렸거나 대상이 없을 때(답변 틀림만 누른 건) 담당자가 정한다 — 그 대상 기준으로 수정안 생성."""
    await _require_item_owner(item_id, user)
    try:
        return await service.retarget(item_id, user, body.key)
    except service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
