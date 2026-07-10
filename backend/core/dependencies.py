"""공통 의존성 — 인증, 권한 체크."""
from __future__ import annotations

import time

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from core.database import get_conn
from core.security import decode_token

_bearer_scheme = HTTPBearer()

# 매 요청마다 ops_user를 조회하지 않도록 짧은 TTL로 캐시 — 프론트에서 페이지 로드 시
# 여러 API를 동시 호출하는 버스트 패턴을 흡수한다. TTL이 짧아(수 초) 계정 비활성화/권한
# 변경이 반영되는 데 걸리는 지연은 access token 만료 주기(30분)에 비해 무시할 수준.
_USER_CACHE_TTL = 5.0
_user_cache: dict[int, tuple[float, dict]] = {}


def invalidate_user_cache(user_id: int) -> None:
    """사용자 정보 변경(비활성화/권한 변경 등) 시 캐시를 즉시 무효화."""
    _user_cache.pop(user_id, None)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    """JWT Access Token → 사용자 정보 반환.

    반환 dict에는 part (name string) 및 part_id (int) 모두 포함.
    """
    payload = decode_token(credentials.credentials)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 토큰입니다.")

    uid = int(payload.get("sub") or 0)
    if not uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="토큰에 사용자 정보가 없습니다.")

    cached = _user_cache.get(uid)
    if cached and time.monotonic() - cached[0] < _USER_CACHE_TTL:
        return cached[1]

    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT u.id, u.username, u.role, u.part_id,
                   p.name AS part,
                   u.is_active, u.encrypted_llm_credentials, u.encrypted_confluence_pat,
                   u.created_at
            FROM ops_user u
            LEFT JOIN ops_part p ON u.part_id = p.id
            WHERE u.id = $1
            """,
            uid,
        )

    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자를 찾을 수 없습니다.")
    if not row["is_active"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="비활성화된 계정입니다.")

    user = dict(row)
    _user_cache[uid] = (time.monotonic(), user)
    return user


async def get_current_admin(user: dict = Depends(get_current_user)) -> dict:
    """Admin 전용 엔드포인트 의존성."""
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="관리자 권한이 필요합니다.")
    return user


def check_part_ownership(resource_part: str | None, user: dict) -> None:
    """리소스의 created_by_part와 현재 사용자의 part를 비교.

    Admin이면 무조건 통과, 같은 파트면 통과, 다르면 403.
    """
    if user["role"] == "admin":
        return
    if resource_part is None or resource_part != user["part"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="다른 파트의 데이터를 수정/삭제할 수 없습니다.",
        )


async def check_namespace_ownership(namespace: str, user: dict) -> None:
    """네임스페이스의 owner_part_id와 현재 사용자의 part_id를 비교.

    Admin이면 무조건 통과, 같은 파트면 통과, 다르면 403.
    owner_part_id가 없으면(NULL) 모든 로그인 사용자 허용.
    """
    if user["role"] == "admin":
        return

    async with get_conn() as conn:
        owner_part_id = await conn.fetchval(
            "SELECT owner_part_id FROM ops_namespace WHERE name = $1", namespace,
        )

    # owner_part_id가 NULL이면 공통 파트 — 모든 로그인 사용자 허용
    if owner_part_id is None:
        return
    # 다른 파트면 403
    if owner_part_id != user["part_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="이 네임스페이스에 대한 권한이 없습니다.",
        )
