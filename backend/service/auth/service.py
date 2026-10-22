"""인증/계정 도메인 — 서비스 로직."""
from __future__ import annotations

import asyncio
from typing import Optional

import asyncpg

from core.database import (ORG_KIND, OWNER_MIRROR_SYNC_SQL, create_org_collection, delete_org_collection, get_conn,
                           org_part_key, org_team_key)
from core.dependencies import invalidate_user_cache
from core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    encrypt_api_key, encrypt_dict,
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
    llm_credentials: Optional[dict] = None,
) -> dict:
    """사용자 등록. 실패 시 RegisterError 발생.

    가입은 조회 전용(viewer)으로 시작하고 관리자가 사용자 관리에서 승인해야 일반 사용자가 된다(2026-10-20 사용자 결정, 리뷰 M3) —
    가입은 인증 없이 열려 있고 파트는 고를 수 있어서, 바로 user가 되면 그 파트 담당 업무 영역의 지식·정책을 곧장 고칠 수 있었다.

    llm_credentials: {client_id, client_secret, user_id} 트리플 (선택)
    """
    # bcrypt는 의도적으로 느려서(~100-300ms) 동기 호출하면 이벤트루프가 그 동안
    # 다른 모든 요청(채팅 스트리밍 등)을 막는다 — 스레드로 오프로드
    hashed = await asyncio.to_thread(hash_password, password)

    # LLM 자격증명 암호화 (Fernet 키 미설정 시 무시)
    encrypted_creds = None
    if llm_credentials:
        try:
            encrypted_creds = encrypt_dict(llm_credentials)
        except ValueError:
            raise RegisterError("서버에 암호화 키가 설정되지 않아 자격증명을 등록할 수 없습니다.", 500)

    async with get_conn() as conn:
        # 파트 id 조회 + 사용자 중복 확인. 예전엔 관리자가 속한 파트를 가입 대상에서 뺐다(관리자 파트로 가입해 그 파트 소유 영역의
        # 권한을 얻는 걸 막으려고). 2026-10-20부터 슈퍼어드민은 역할(role)이고 파트에 속하지 않으며, 담당이 없는 영역은 관리자만
        # 고친다 — 그 전제가 사라져 제외 규칙도 뺐다(남겨 두면 관리자를 실제 파트에 넣는 순간 그 파트 가입이 막힌다).
        check = await conn.fetchrow(
            """
            SELECT
                (SELECT id FROM ops_part p WHERE p.name = $1) AS part_id,
                EXISTS(SELECT 1 FROM ops_user WHERE username = $2) AS user_exists
            """, part, username,
        )
        if check["part_id"] is None:
            raise RegisterError("존재하지 않는 파트(부서)입니다.")
        if check["user_exists"]:
            raise RegisterError("이미 사용 중인 아이디입니다.")

        part_id = check["part_id"]
        row = await conn.fetchrow(
            """
            INSERT INTO ops_user (username, hashed_password, encrypted_llm_credentials, part_id, role)
            VALUES ($1, $2, $3, $4, 'viewer')
            RETURNING id, username, role, part_id, is_active, encrypted_llm_credentials, created_at::text
            """,
            username, hashed, encrypted_creds, part_id,
        )
        result = dict(row)
        result["part"] = part
    return result


class OrgCollectionInUse(Exception):
    """팀·파트의 조직 컬렉션에 지식·용어가 있어 지울 수 없음(라우터가 409/400으로) — 사유를 담는다(#83)."""
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class LoginError(Exception):
    """로그인 실패 사유를 담는 예외."""
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


async def authenticate_user(username: str, password: str) -> dict:
    """로그인 인증. 실패 시 LoginError 발생."""
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT u.id, u.username, u.hashed_password, u.role, u.part_id,
                   p.name AS part,
                   u.is_active, u.encrypted_llm_credentials, u.encrypted_confluence_pat,
                   u.created_at::text
            FROM ops_user u
            LEFT JOIN ops_part p ON u.part_id = p.id
            WHERE u.username = $1
            """,
            username,
        )
    # 아이디가 없을 때와 비밀번호가 틀렸을 때 메시지를 구분하지 않는다 — 구분하면
    # 공격자가 응답만 보고 어떤 아이디가 실제로 존재하는지 알아낼 수 있다(계정 열거).
    if not row:
        raise LoginError("아이디 또는 비밀번호가 올바르지 않습니다.")
    if not await asyncio.to_thread(verify_password, password, row["hashed_password"]):
        raise LoginError("아이디 또는 비밀번호가 올바르지 않습니다.")
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
            """
            SELECT u.id, u.username, u.role, u.part_id,
                   p.name AS part,
                   u.is_active, u.encrypted_llm_credentials, u.encrypted_confluence_pat,
                   u.created_at::text
            FROM ops_user u
            LEFT JOIN ops_part p ON u.part_id = p.id
            WHERE u.id = $1
            """,
            user_id,
        )
    return dict(row) if row else None


async def list_users() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id, u.username, u.role, u.part_id,
                   p.name AS part,
                   u.is_active,
                   (u.encrypted_llm_credentials IS NOT NULL) AS has_llm_credentials,
                   (u.encrypted_confluence_pat IS NOT NULL) AS has_confluence_pat,
                   u.created_at::text
            FROM ops_user u
            LEFT JOIN ops_part p ON u.part_id = p.id
            ORDER BY u.created_at DESC
            """
        )
    return [dict(r) for r in rows]


async def update_user(user_id: int, role: Optional[str] = None, part: Optional[str] = None, is_active: Optional[bool] = None) -> Optional[dict]:
    """보낸 칸만 바꾼다(COALESCE 한 문장) — 읽은 값 전체를 다시 쓰면 승인과 동시에 바뀐 소속을 옛 값으로 덮었다(리뷰 L2).
    없는 파트는 400 — 예전엔 소속을 조용히 비우고 응답엔 그 이름을 돌려줬다(막 쓰기 테스트 10/20)."""
    async with get_conn() as conn, conn.transaction():
        new_part_id = None
        if part is not None:
            # KEY SHARE: 이 파트를 지우는 쪽(delete_part, FOR UPDATE)과 순서를 맞춘다
            new_part_id = await conn.fetchval("SELECT id FROM ops_part WHERE name = $1 FOR KEY SHARE", part)
            if new_part_id is None:
                raise ValueError(f"없는 파트입니다: {part}")
        row = await conn.fetchrow(
            """
            UPDATE ops_user u SET role = COALESCE($2, u.role), part_id = COALESCE($3, u.part_id),
                                  is_active = COALESCE($4, u.is_active)
            WHERE u.id = $1
            RETURNING u.id, u.username, u.role, u.part_id, u.is_active,
                      (u.encrypted_llm_credentials IS NOT NULL) AS has_llm_credentials, u.created_at::text,
                      (SELECT name FROM ops_part p WHERE p.id = u.part_id) AS part
            """,
            user_id, role, new_part_id, is_active,
        )
    if not row:
        return None
    invalidate_user_cache(user_id)
    return dict(row)


async def delete_user(user_id: int) -> bool:
    async with get_conn() as conn:
        result = await conn.execute("DELETE FROM ops_user WHERE id = $1", user_id)
    invalidate_user_cache(user_id)
    return "DELETE 1" in result


async def change_password(user_id: int, current_password: str, new_password: str) -> bool:
    """비밀번호 변경. 현재 비밀번호 검증 후 변경."""
    async with get_conn() as conn:
        row = await conn.fetchrow("SELECT hashed_password FROM ops_user WHERE id = $1", user_id)
        if not row:
            return False
        if not await asyncio.to_thread(verify_password, current_password, row["hashed_password"]):
            return False
        new_hashed = await asyncio.to_thread(hash_password, new_password)
        await conn.execute("UPDATE ops_user SET hashed_password = $2 WHERE id = $1", user_id, new_hashed)
    return True


async def update_llm_credentials(user_id: int, credentials: dict) -> bool:
    """사용자의 LLM 자격증명을 Fernet 암호화하여 저장.

    credentials: {client_id, client_secret, user_id} 트리플
    """
    encrypted = encrypt_dict(credentials)
    async with get_conn() as conn:
        result = await conn.execute(
            "UPDATE ops_user SET encrypted_llm_credentials = $2 WHERE id = $1",
            user_id, encrypted,
        )
    invalidate_user_cache(user_id)
    return "UPDATE 1" in result


async def delete_llm_credentials(user_id: int) -> bool:
    async with get_conn() as conn:
        result = await conn.execute(
            "UPDATE ops_user SET encrypted_llm_credentials = NULL WHERE id = $1",
            user_id,
        )
    invalidate_user_cache(user_id)
    return "UPDATE 1" in result


async def update_confluence_pat(user_id: int, plain_pat: str) -> bool:
    encrypted = encrypt_api_key(plain_pat)
    async with get_conn() as conn:
        result = await conn.execute(
            "UPDATE ops_user SET encrypted_confluence_pat = $2 WHERE id = $1",
            user_id, encrypted,
        )
    invalidate_user_cache(user_id)
    return "UPDATE 1" in result


async def delete_confluence_pat(user_id: int) -> bool:
    async with get_conn() as conn:
        result = await conn.execute(
            "UPDATE ops_user SET encrypted_confluence_pat = NULL WHERE id = $1",
            user_id,
        )
    invalidate_user_cache(user_id)
    return "UPDATE 1" in result


# ── 파트 CRUD ────────────────────────────────────────────────────────────────

async def list_parts(with_domains: bool = False) -> list[dict]:
    """파트 목록(조직: 팀 > 파트, 2026-10-20). 팀이 없는 파트는 team=None.
    with_domains: 파트가 담당하는 업무 영역 이름들(관리자 화면용 — 가입 화면의 공개 목록엔 싣지 않는다)."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id, p.name, p.created_at::text, t.name AS team, p.team_id,
                   (SELECT COUNT(*) FROM ops_user u WHERE u.part_id = p.id) AS user_count,
                   CASE WHEN $1 THEN COALESCE((SELECT array_agg(n.name ORDER BY n.name) FROM ops_namespace n
                                              JOIN ops_namespace_owner o ON o.namespace_id = n.id
                                              WHERE o.part_id = p.id AND n.kind <> 'org_space'), '{}') END AS domains
            FROM ops_part p
            LEFT JOIN ops_team t ON t.id = p.team_id
            ORDER BY t.name NULLS LAST, p.name
            """, with_domains,
        )
    return [{**dict(r), "domains": list(r["domains"] or [])} for r in rows]


async def set_part_domains(part_id: int, names: list[str]) -> Optional[dict]:
    """이 파트가 담당하는 업무 영역을 통째로 정한다(N:N — 한 파트가 여러 영역, 영역 하나에 담당 파트 여럿, 10/22 #82). 한 트랜잭션:
    목록에 있는 영역엔 이 파트를 담당으로 **더한다**(다른 파트 담당은 그대로 — 함께 담당), 이 파트가 맡던 영역 중 목록에서 빠진 것은
    이 파트만 뺀다(다른 담당이 없으면 담당 없음 → 관리자만 수정). 없는 영역 이름이 하나라도 있으면 아무것도 바꾸지 않는다. 파트가 없으면 None.
    업무 컬렉션만 다룬다 — 조직 컬렉션(#83)의 담당은 팀·파트에서 정해져 여기서 더하거나 빼지 않는다(조직 컬렉션 이름은 "없는 영역")."""
    wanted = sorted(set(names))
    async with get_conn() as conn, conn.transaction():
        if await conn.fetchval("SELECT id FROM ops_part WHERE id = $1 FOR KEY SHARE", part_id) is None:
            return None
        # 바뀔 수 있는 영역 행을 id 순으로 잠근다(영역 수정 창 update_namespace_settings도 영역 행을 먼저 잡는다 — 담당 표 바꾸기가 엇갈리지 않게)
        rows = await conn.fetch(
            """SELECT id, name FROM ops_namespace WHERE kind <> 'org_space' AND (name = ANY($1::text[])
               OR id IN (SELECT namespace_id FROM ops_namespace_owner WHERE part_id = $2)) ORDER BY id FOR UPDATE""", wanted, part_id)
        found = {r["name"] for r in rows}
        missing = [n for n in wanted if n not in found]
        if missing:
            raise ValueError(f"없는 업무 영역입니다: {', '.join(missing)}")
        # 지우기는 위에서 잠근 영역으로만 — 잠근 뒤 다른 요청이 커밋한 담당(이 SELECT가 못 본 영역)까지 지우면 그 영역의 옛 칸 거울을
        # 못 맞춰 표와 칸이 어긋났다(리뷰 10/22). 못 본 영역은 그 요청의 결과를 그대로 둔다.
        locked_ids = [r["id"] for r in rows]
        await conn.execute("""DELETE FROM ops_namespace_owner WHERE part_id = $1 AND namespace_id = ANY($3::int[])
                              AND namespace_id IN (SELECT id FROM ops_namespace WHERE NOT (name = ANY($2::text[])))""",
                           part_id, wanted, locked_ids)
        await conn.execute("""INSERT INTO ops_namespace_owner (namespace_id, part_id)
                              SELECT id, $1 FROM ops_namespace WHERE name = ANY($2::text[]) AND kind <> 'org_space'
                              ON CONFLICT DO NOTHING""", part_id, wanted)
        await conn.execute(OWNER_MIRROR_SYNC_SQL, locked_ids)   # 옛 칸 임시 거울(#82 — 옛 칸 삭제 때 같이 지움)
        domains = [r["name"] for r in await conn.fetch(
            """SELECT n.name FROM ops_namespace n JOIN ops_namespace_owner o ON o.namespace_id = n.id
               WHERE o.part_id = $1 AND n.kind <> 'org_space' ORDER BY n.name""", part_id)]
    return {"part_id": part_id, "domains": domains}


async def create_part(name: str, team_id: Optional[int] = None) -> Optional[dict]:
    """파트 만들기. 이름이 겹치면 None. team_id가 있으면 그 팀 아래(없는 팀이면 ValueError) — 팀 행을 KEY SHARE로 잡아
    같은 순간 팀 삭제(FOR UPDATE)와 엇갈려 팀 없는 파트가 생기지 않게 한다. 같은 트랜잭션에서 그 파트의 조직 컬렉션도 만든다(#83)."""
    async with get_conn() as conn, conn.transaction():
        if team_id is not None and await conn.fetchval("SELECT id FROM ops_team WHERE id = $1 FOR KEY SHARE", team_id) is None:
            raise ValueError("없는 팀입니다.")
        existing = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops_part WHERE name = $1)", name)
        if existing:
            return None
        row = await conn.fetchrow(   # 확인~저장 사이 같은 이름이 끼면 아래 ON CONFLICT로 None(겹침) — 예전엔 UNIQUE 위반 500
            "INSERT INTO ops_part (name, team_id) VALUES ($1, $2) ON CONFLICT (name) DO NOTHING "
            "RETURNING id, name, created_at::text, team_id, (SELECT t.name FROM ops_team t WHERE t.id = team_id) AS team",
            name, team_id,
        )
        if row:
            await create_org_collection(conn, part_id=row["id"])
    return dict(row) if row else None


async def set_part_team(part_id: int, team_id: int) -> Optional[dict]:
    """파트를 다른 팀으로 옮긴다. 파트가 없으면 None, 팀이 없으면 ValueError. 사람·담당 업무는 파트에 붙어 있어 같이 따라간다."""
    async with get_conn() as conn, conn.transaction():
        if await conn.fetchval("SELECT id FROM ops_team WHERE id = $1 FOR KEY SHARE", team_id) is None:
            raise ValueError("없는 팀입니다.")
        row = await conn.fetchrow(
            "UPDATE ops_part SET team_id = $2 WHERE id = $1 "
            "RETURNING id, name, created_at::text, team_id, (SELECT t.name FROM ops_team t WHERE t.id = $2) AS team", part_id, team_id)
    return dict(row) if row else None


# ── 팀 (조직: 팀 > 파트, 10/22 — 팀이 7개로 늘어 관리 화면 필요) ──────────────────────

async def list_teams() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """SELECT t.id, t.name, t.created_at::text, (SELECT COUNT(*) FROM ops_part p WHERE p.team_id = t.id) AS part_count
               FROM ops_team t ORDER BY t.name""")
    return [dict(r) for r in rows]


async def create_team(name: str) -> Optional[dict]:
    """이름이 겹치면 None. 같은 트랜잭션에서 그 팀의 팀 공통 조직 컬렉션도 만든다(#83)."""
    async with get_conn() as conn, conn.transaction():
        row = await conn.fetchrow(
            "INSERT INTO ops_team (name) VALUES ($1) ON CONFLICT (name) DO NOTHING RETURNING id, name, created_at::text", name)
        if row:
            await create_org_collection(conn, team_id=row["id"])
    return {**dict(row), "part_count": 0} if row else None


async def rename_team(team_id: int, new_name: str) -> Optional[dict]:
    """없는 팀이면 None, 이름이 다른 팀과 겹치면 ValueError. 파트·사람은 팀 id로 붙어 있어 따라 고칠 데이터가 없다."""
    async with get_conn() as conn:
        if await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops_team WHERE name = $1 AND id <> $2)", new_name, team_id):
            raise ValueError("이미 있는 팀 이름입니다.")
        try:
            row = await conn.fetchrow(
                """UPDATE ops_team SET name = $2 WHERE id = $1
                   RETURNING id, name, created_at::text, (SELECT COUNT(*) FROM ops_part p WHERE p.team_id = ops_team.id) AS part_count""",
                team_id, new_name)
        except asyncpg.UniqueViolationError:
            raise ValueError("이미 있는 팀 이름입니다.") from None
    return dict(row) if row else None


async def delete_team(team_id: int) -> Optional[bool]:
    """없는 팀이면 None, 파트가 남아 있으면 False(삭제 안 함 — FK가 SET NULL이라 그냥 지우면 파트가 조용히 팀 없음이 된다).
    팀 행을 FOR UPDATE로 잠가 확인~삭제 사이에 파트 만들기·옮기기(KEY SHARE)가 끼어들지 못하게.
    팀 공통 조직 컬렉션(#83)에 지식·용어가 있으면 OrgCollectionInUse(삭제 안 함), 없으면 같은 트랜잭션에서 컬렉션도 지운다."""
    async with get_conn() as conn, conn.transaction():
        if await conn.fetchval("SELECT id FROM ops_team WHERE id = $1 FOR UPDATE", team_id) is None:
            return None
        if await conn.fetchval("SELECT COUNT(*) FROM ops_part WHERE team_id = $1", team_id):
            return False
        reason = await delete_org_collection(conn, org_team_key(team_id))
        if reason:
            raise OrgCollectionInUse(reason)
        await conn.execute("DELETE FROM ops_team WHERE id = $1", team_id)
    return True


async def rename_part(part_id: int, new_name: str) -> Optional[dict]:
    async with get_conn() as conn:
        existing = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM ops_part WHERE name = $1 AND id != $2)", new_name, part_id,
        )
        if existing:
            return None
        old_name = await conn.fetchval("SELECT name FROM ops_part WHERE id = $1", part_id)
        if old_name is None:
            return None
        row = await conn.fetchrow(
            "UPDATE ops_part SET name = $2 WHERE id = $1 RETURNING id, name, created_at::text",
            part_id, new_name,
        )
        # 파트는 모두 id로 연결된다(사용자·담당 영역·VOC) — 이름만 바뀌고 따라 고칠 데이터가 없다(2026-10-20, 파트 이름 글자 컬럼 삭제)
    return dict(row) if row else None


async def delete_part(part_id: int) -> bool:
    async with get_conn() as conn, conn.transaction():
        # 행을 잠가 확인~삭제 사이에 이 파트로 사용자 이동·VOC 지정이 끼어들지 못하게(그쪽은 KEY SHARE를 잡아 기다린다)
        if await conn.fetchval("SELECT id FROM ops_part WHERE id = $1 FOR UPDATE", part_id) is None:
            return False
        # 해당 파트에 사용자가 있는지 확인
        user_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ops_user WHERE part_id = $1",
            part_id,
        )
        if user_count > 0:
            return False  # 사용자가 있으면 삭제 불가
        # 이 파트가 담당하는 업무 영역이 있는지 확인(담당 표, #82) — FK가 ON DELETE CASCADE라 체크 없이 지우면
        # 담당이 조용히 사라진다(담당 없는 영역은 관리자만 수정 — 담당 파트 사람들이 갑자기 못 고치게 됨)
        # 이 파트 자신의 조직 컬렉션(담당 한 줄, #83)은 세지 않는다 — 아래에서 내용 확인 뒤 같이 지운다
        namespace_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ops_namespace_owner o JOIN ops_namespace n ON n.id = o.namespace_id "
            "WHERE o.part_id = $1 AND n.kind <> $2",
            part_id, ORG_KIND,
        )
        if namespace_count > 0:
            return False
        # VOC 메일함 담당도 확인 — 연결(part_id)은 필수라 담당 파트를 먼저 바꿔야 한다(리뷰 M2: 지우면 VOC 화면·수집이 깨졌다)
        if await conn.fetchval("SELECT COUNT(*) FROM ops_voc_routing WHERE part_id = $1", part_id):
            return False
        reason = await delete_org_collection(conn, org_part_key(part_id))   # 지식·용어가 있으면 OrgCollectionInUse(삭제 안 함)
        if reason:
            raise OrgCollectionInUse(reason)
        result = await conn.execute("DELETE FROM ops_part WHERE id = $1", part_id)
    return "DELETE 1" in result
