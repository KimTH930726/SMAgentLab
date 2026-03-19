"""인증/계정 도메인 — API 라우터."""
from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import get_current_user, get_current_admin
from core.security import decode_token
from service.auth import service
from service.auth.service import RegisterError, LoginError
from service.auth.schemas import (
    RegisterRequest, LoginRequest, TokenResponse,
    RefreshRequest, AccessTokenResponse,
    PasswordChangeRequest, ApiKeyUpdateRequest,
    UserOut, UserAdminUpdate,
    PartCreate, PartOut,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _to_user_out(row: dict) -> UserOut:
    return UserOut(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        part=row["part"],
        is_active=row["is_active"],
        has_api_key=bool(row.get("encrypted_llm_api_key") or row.get("has_api_key")),
        created_at=row["created_at"],
    )


# ── 공개 엔드포인트 ─────────────────────────────────────────────────────────

@router.post("/register", response_model=UserOut, status_code=201)
async def register(body: RegisterRequest):
    try:
        user = await service.register_user(
            username=body.username,
            password=body.password,
            part=body.part,
            llm_api_key=body.llm_api_key,
        )
    except RegisterError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    return _to_user_out(user)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    try:
        user = await service.authenticate_user(body.username, body.password)
    except LoginError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=e.detail)
    tokens = service.create_tokens(user)
    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        user=_to_user_out(user),
    )


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh_token(body: RefreshRequest):
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 리프레시 토큰입니다.")

    user = await service.get_user_by_id(int(payload["sub"]))
    if not user or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자를 찾을 수 없습니다.")

    tokens = service.create_tokens(user)
    return AccessTokenResponse(access_token=tokens["access_token"])


@router.get("/parts", response_model=list[PartOut])
async def get_parts():
    """파트 목록 조회 (가입 폼용, 인증 불필요 — admin 전용 파트 제외)."""
    return await service.list_parts(exclude_admin_parts=True)


@router.get("/parts/all", response_model=list[PartOut])
async def get_all_parts(admin: dict = Depends(get_current_admin)):
    """파트 목록 전체 조회 (관리자 전용, admin 파트 포함)."""
    return await service.list_parts(exclude_admin_parts=False)


# ── 인증 필요 엔드포인트 ─────────────────────────────────────────────────────

@router.get("/me", response_model=UserOut)
async def get_me(user: dict = Depends(get_current_user)):
    return _to_user_out(user)


@router.put("/me/password")
async def change_my_password(body: PasswordChangeRequest, user: dict = Depends(get_current_user)):
    success = await service.change_password(user["id"], body.current_password, body.new_password)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="현재 비밀번호가 올바르지 않습니다.")
    return {"status": "ok"}


@router.put("/me/api-key")
async def update_my_api_key(body: ApiKeyUpdateRequest, user: dict = Depends(get_current_user)):
    success = await service.update_api_key(user["id"], body.llm_api_key)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API Key 업데이트 실패")
    return {"status": "ok"}


# ── Admin 전용 ───────────────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserOut])
async def list_users(admin: dict = Depends(get_current_admin)):
    return await service.list_users()


@router.put("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: int, body: UserAdminUpdate, admin: dict = Depends(get_current_admin)):
    if user_id == admin["id"] and body.role is not None and body.role != "admin":
        raise HTTPException(status_code=400, detail="자기 자신의 관리자 권한은 해제할 수 없습니다.")
    updated = await service.update_user(user_id, role=body.role, part=body.part, is_active=body.is_active)
    if not updated:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    return updated


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, admin: dict = Depends(get_current_admin)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="자기 자신은 삭제할 수 없습니다.")
    success = await service.delete_user(user_id)
    if not success:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")


# ── 파트 관리 (Admin) ────────────────────────────────────────────────────────

@router.post("/parts", response_model=PartOut, status_code=201)
async def create_part(body: PartCreate, admin: dict = Depends(get_current_admin)):
    part = await service.create_part(body.name)
    if not part:
        raise HTTPException(status_code=409, detail="이미 존재하는 파트입니다.")
    return part


@router.patch("/parts/{part_id}", response_model=PartOut)
async def rename_part_endpoint(part_id: int, body: PartCreate, admin: dict = Depends(get_current_admin)):
    result = await service.rename_part(part_id, body.name.strip())
    if result is None:
        raise HTTPException(status_code=409, detail="이미 존재하는 파트 이름입니다.")
    return result


@router.delete("/parts/{part_id}", status_code=204)
async def delete_part(part_id: int, admin: dict = Depends(get_current_admin)):
    success = await service.delete_part(part_id)
    if not success:
        raise HTTPException(status_code=400, detail="해당 파트에 소속된 사용자가 있어 삭제할 수 없습니다.")
