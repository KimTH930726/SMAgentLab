"""인증/계정 도메인 — 서비스 로직."""
from __future__ import annotations

from typing import Optional

from core.database import get_conn
from core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    encrypt_api_key,
)


# ── 사용자 CRUD ──────────────────────────────────────────────────────────────

class RegisterError(Exception):
    """회원가입 실패 사유를 담는 예외."""
    def __init__(self, detail: str, status_code: int = 409):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


async def register_user(
    username: str,
    password: str,
    part: str,
    llm_api_key: Optional[str] = None,
) -> dict:
    """사용자 등록. 실패 시 RegisterError 발생."""
    hashed = hash_password(password)

    # LLM API Key 암호화 (Fernet 키 미설정 시 무시)
    encrypted_key = None
    if llm_api_key:
        try:
            encrypted_key = encrypt_api_key(llm_api_key)
        except ValueError:
            raise RegisterError("서버에 암호화 키가 설정되지 않아 API Key를 등록할 수 없습니다.", 500)

    async with get_conn() as conn:
        # 파트 존재 + 사용자 중복 확인 (단일 쿼리로 통합)
        check = await conn.fetchrow(
            """
            SELECT
                EXISTS(SELECT 1 FROM ops_part WHERE name = $1) AS part_exists,
                EXISTS(SELECT 1 FROM ops_user WHERE username = $2) AS user_exists
            """, part, username,
        )
        if not check["part_exists"]:
            raise RegisterError("존재하지 않는 파트(부서)입니다.")
        if check["user_exists"]:
            raise RegisterError("이미 사용 중인 아이디입니다.")

        row = await conn.fetchrow(
            """
            INSERT INTO ops_user (username, hashed_password, encrypted_llm_api_key, part)
            VALUES ($1, $2, $3, $4)
            RETURNING id, username, role, part, is_active, encrypted_llm_api_key, created_at::text
            """,
            username, hashed, encrypted_key, part,
        )
    return dict(row)


class LoginError(Exception):
    """로그인 실패 사유를 담는 예외."""
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


async def authenticate_user(username: str, password: str) -> dict:
    """로그인 인증. 실패 시 LoginError 발생."""
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, hashed_password, role, part, is_active, encrypted_llm_api_key, created_at::text FROM ops_user WHERE username = $1",
            username,
        )
    if not row:
        raise LoginError("존재하지 않는 아이디입니다.")
    if not verify_password(password, row["hashed_password"]):
        raise LoginError("비밀번호가 올바르지 않습니다.")
    if not row["is_active"]:
        raise LoginError("비활성화된 계정입니다. 관리자에게 문의하세요.")
    return dict(row)


def create_tokens(user: dict) -> dict:
    """Access + Refresh 토큰 생성."""
    token_data = {"sub": str(user["id"]), "username": user["username"], "role": user["role"]}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
    }


async def get_user_by_id(user_id: int) -> Optional[dict]:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, role, part, is_active, encrypted_llm_api_key, created_at::text FROM ops_user WHERE id = $1",
            user_id,
        )
    return dict(row) if row else None


async def list_users() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, username, role, part, is_active,
                   (encrypted_llm_api_key IS NOT NULL) AS has_api_key,
                   created_at::text
            FROM ops_user ORDER BY created_at DESC
            """
        )
    return [dict(r) for r in rows]


async def update_user(user_id: int, role: Optional[str] = None, part: Optional[str] = None, is_active: Optional[bool] = None) -> Optional[dict]:
    async with get_conn() as conn:
        current = await conn.fetchrow("SELECT * FROM ops_user WHERE id = $1", user_id)
        if not current:
            return None

        new_role = role if role is not None else current["role"]
        new_part = part if part is not None else current["part"]
        new_active = is_active if is_active is not None else current["is_active"]

        row = await conn.fetchrow(
            """
            UPDATE ops_user SET role = $2, part = $3, is_active = $4
            WHERE id = $1
            RETURNING id, username, role, part, is_active,
                      (encrypted_llm_api_key IS NOT NULL) AS has_api_key,
                      created_at::text
            """,
            user_id, new_role, new_part, new_active,
        )
    return dict(row) if row else None


async def delete_user(user_id: int) -> bool:
    async with get_conn() as conn:
        result = await conn.execute("DELETE FROM ops_user WHERE id = $1", user_id)
    return "DELETE 1" in result


async def change_password(user_id: int, current_password: str, new_password: str) -> bool:
    """비밀번호 변경. 현재 비밀번호 검증 후 변경."""
    async with get_conn() as conn:
        row = await conn.fetchrow("SELECT hashed_password FROM ops_user WHERE id = $1", user_id)
        if not row:
            return False
        if not verify_password(current_password, row["hashed_password"]):
            return False
        new_hashed = hash_password(new_password)
        await conn.execute("UPDATE ops_user SET hashed_password = $2 WHERE id = $1", user_id, new_hashed)
    return True


async def update_api_key(user_id: int, plain_key: str) -> bool:
    encrypted = encrypt_api_key(plain_key)
    async with get_conn() as conn:
        result = await conn.execute(
            "UPDATE ops_user SET encrypted_llm_api_key = $2 WHERE id = $1",
            user_id, encrypted,
        )
    return "UPDATE 1" in result


# ── 파트 CRUD ────────────────────────────────────────────────────────────────

async def list_parts() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch("SELECT id, name, created_at::text FROM ops_part ORDER BY name")
    return [dict(r) for r in rows]


async def create_part(name: str) -> Optional[dict]:
    async with get_conn() as conn:
        existing = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops_part WHERE name = $1)", name)
        if existing:
            return None
        row = await conn.fetchrow(
            "INSERT INTO ops_part (name) VALUES ($1) RETURNING id, name, created_at::text",
            name,
        )
    return dict(row)


async def delete_part(part_id: int) -> bool:
    async with get_conn() as conn:
        # 해당 파트에 사용자가 있는지 확인
        user_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ops_user u JOIN ops_part p ON u.part = p.name WHERE p.id = $1",
            part_id,
        )
        if user_count > 0:
            return False  # 사용자가 있으면 삭제 불가
        result = await conn.execute("DELETE FROM ops_part WHERE id = $1", part_id)
    return "DELETE 1" in result
