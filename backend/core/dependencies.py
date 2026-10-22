"""공통 의존성 — 인증, 권한 체크."""
from __future__ import annotations

import logging
import time

from typing import Iterable, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from core.database import EFFECTIVE_OWNERS_SQL, ORG_KIND, get_conn
from core.security import decode_token

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer()

# 매 요청마다 ops_user를 조회하지 않도록 짧은 TTL로 캐시 — 프론트에서 페이지 로드 시
# 여러 API를 동시 호출하는 버스트 패턴을 흡수한다. TTL이 짧아(수 초) 계정 비활성화/권한
# 변경이 반영되는 데 걸리는 지연은 access token 만료 주기(30분)에 비해 무시할 수준.
_USER_CACHE_TTL = 5.0
_user_cache: dict[int, tuple[float, dict]] = {}

# 실효 담당 파트들 — 담당은 N개(ops_namespace_owner, 10/22 #82). 하위 영역은 자기 담당이 없으면 상위 담당 전부를 물려받는다
# (트리 깊이 1단, category-removal-design §2-6). SQL은 core/database.py 한 곳(EFFECTIVE_OWNERS_SQL·effective_owner_ns_sql) —
# 수정·조회·읽기 기록 3곳과 여러 영역을 한 번에 보는 질의 기록 일괄 삭제(admin/router.py)가 같이 쓴다.
# 조직 컬렉션(#83)도 같은 SQL — 파트 컬렉션 = 그 파트, 팀 공통 = 그 팀의 모든 파트(OWNER_ROWS_SQL). 규칙(edit_denied_reason)은 그대로 하나.


async def _effective_owner_ids(namespace: str) -> list[int]:
    async with get_conn() as conn:
        return [r["part_id"] for r in await conn.fetch(EFFECTIVE_OWNERS_SQL, namespace)]


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

    try:
        uid = int(payload.get("sub") or 0)
    except (TypeError, ValueError):
        uid = 0
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


async def check_namespace_ownership(namespace: str, user: dict) -> None:
    """업무 영역(namespace)의 담당 파트들과 사용자 소속 파트(part_id)를 비교 — 사람(파트) → 담당 영역으로 매번 계산.

    관리자는 통과, 조회 전용은 403, 담당 파트 중 하나에 속하면 통과(담당 N개, 주 담당 없음 — 10/22). 담당이 정해지지 않은 영역은
    관리자만(2026-10-20 사용자 결정 — 예전엔 "공통"이라 누구나 고칠 수 있었다. 정책서 영역 대부분이 아직 담당 미지정이라 아무나 고치면 안 된다).
    """
    owner_part_ids = await _effective_owner_ids(namespace)   # 하위 영역은 상위 담당 상속
    reason = edit_denied_reason(owner_part_ids, user)
    if reason:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=reason)


def edit_denied_reason(owner_part_ids: Iterable[int], user: dict) -> Optional[str]:
    """업무 영역 수정 규칙(한 곳 — 프론트 utils/domainAccess.ts와 같음). owner_part_ids = 실효 담당 파트들(상속 반영). 막히면 이유, 되면 None.
    여러 영역을 한 번에 검사하는 경로(질의 기록 일괄 삭제 등)도 이 함수를 쓴다 — 따로 쓰면 규칙이 어긋난다(리뷰 M1)."""
    owners = set(owner_part_ids)
    if user["role"] == "admin":
        return None
    if user["role"] == "viewer":
        return "조회 전용 계정은 이 업무 영역을 수정할 수 없습니다."
    if not owners:
        return "담당 파트가 정해지지 않은 업무 영역은 관리자만 수정할 수 있습니다."
    if user.get("part_id") not in owners:
        return "이 업무 영역의 담당 파트가 아닙니다."
    return None


async def reject_org_collection(namespace: str, what: str) -> None:
    """조직 컬렉션엔 지식 문서·용어만 둔다(collection-design §2, #83) — 정책·시스템 정보(참조데이터) 쓰기 경로가 부른다. 조직 컬렉션이면 400.
    없는 이름은 그냥 지나간다(그 뒤 검사가 404 등으로 다룬다)."""
    async with get_conn() as conn:
        kind = await conn.fetchval("SELECT kind FROM ops_namespace WHERE name = $1", namespace)
    if kind == ORG_KIND:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"조직 컬렉션엔 {what}을(를) 둘 수 없어요 — 정책·시스템 정보는 업무 컬렉션에 둡니다.")


async def check_knowledge_read(namespace: str, user: dict) -> None:
    """지식(정책서·참고 데이터 등) 조회 — 로그인한 사용자 누구나, 다른 파트 담당 영역도(2026-10-21 사용자 결정: 다른 파트 지식이 궁금할 수 있다).
    수정은 담당 파트·관리자만(check_namespace_ownership). 사람 데이터(질의 기록·VOC 메일)는 check_namespace_read로 따로 좁힌다.
    규칙이 바뀌면 여기 한 곳만 고친다."""
    return None


async def check_namespace_read(namespace: str, user: dict) -> None:
    """사람 데이터 조회(질의 기록 통계·VOC 메일) 권한 — 수정 규칙과 분리(리뷰 M5, 2026-10-20). 지식은 check_knowledge_read(누구나).
    수정은 "담당 없으면 관리자만"으로 좁혔지만 조회는 예전 그대로: 조회 전용 계정 X, 담당 없는 영역은 누구나,
    담당이 있으면 그 파트 사람과 관리자. 채팅 검색은 이 검사를 쓰지 않는다(log_cross_part_namespace_read)."""
    if user["role"] == "admin":
        return
    if user["role"] == "viewer":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="조회 전용 계정은 이 화면을 볼 수 없습니다.")
    owner_part_ids = await _effective_owner_ids(namespace)   # 하위 영역은 상위 담당 상속
    if owner_part_ids and user.get("part_id") not in owner_part_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="이 업무 영역의 담당 파트가 아닙니다.")


async def log_cross_part_namespace_read(namespace: str, user: dict) -> None:
    """채팅/검색(읽기) 경로 전용 — 파트가 다른 네임스페이스를 조회해도 막지는 않고
    기록만 한다(거버넌스 선제 설계 검토 §4 P0 발견: 쓰기 경로엔 `check_namespace_ownership`
    이 있지만 읽기·채팅 경로엔 아무 권한 검사가 없어 로그인만 하면 누구나 아무 네임스페이스나
    조회 가능했음). 강제 차단부터 배포하면 지금까지 있었을지 모르는 정당한 cross-part
    조회 사용 패턴을 갑자기 막아버릴 위험이 있어(예전 업무구분 키워드 전용 분기 때와 동일한
    이유로) 먼저 로그 온리로 실측한 뒤에 강제 전환 여부를 판단한다."""
    if user["role"] == "admin":
        return
    owner_part_ids = await _effective_owner_ids(namespace)   # 하위 영역은 상위 담당 상속
    if owner_part_ids and user["part_id"] not in owner_part_ids:
        logger.warning(
            "[읽기ACL 미강제] user_id=%s(role=%s, part_id=%s)가 다른 파트 소유 네임스페이스 "
            "'%s'(담당 part_id=%s)를 조회함 — 지금은 차단 안 함, 실측용 기록",
            user["id"], user["role"], user["part_id"], namespace, owner_part_ids,
        )
