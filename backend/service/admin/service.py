"""관리 도메인 — 네임스페이스 CRUD 서비스."""
from __future__ import annotations

import logging
from typing import Optional

from core.database import ORG_KIND, ORG_NAME_PREFIX, OWNER_MIRROR_SYNC_SQL, get_conn, org_display_name
from service.policy.visibility import searchable_sql

logger = logging.getLogger(__name__)


async def list_namespaces() -> list[str]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT name FROM ops_namespace ORDER BY name"
        )
    return [r["name"] for r in rows]


async def list_namespaces_detail() -> list[dict]:
    """업무 영역 목록 — 트리(깊이 1단, category-removal-design §2-6): 상위 → 그 하위 순서.
    담당은 N개(ops_namespace_owner, 10/22 #82). owner_parts = 이 영역에 **직접** 지정된 담당들(수정 창·파트별 담당 지정이 이 값을 읽고 쓴다 —
    뜻을 바꾸면 하위 수정 창에 상위 담당이 미리 채워져 저장 때 직접 지정으로 굳었다, 리뷰 M3). effective_owner_parts = 실효 담당(하위에 자기 담당이
    없으면 상위 것 전부 — check_namespace_ownership과 같은 규칙, 프론트 권한 판단용), owner_inherited = 실효 담당을 상위에서 물려받았는지.
    owner_part(_id)·effective_owner_part(_id)는 옛 화면 호환용 — 담당 중 id가 가장 작은 것 하나(옛 칸 임시 거울과 같은 값).
    조직 컬렉션(kind='org_space', #83)도 같은 목록에 싣는다(이름 = id 키): display_name("팀 공통 · <팀>" / "<파트>")·team_id(묶기용 —
    팀 공통은 그 팀, 파트는 그 파트의 팀)·owner_team_id. 팀 공통의 실효 담당 = 그 팀의 모든 파트(권한 규칙 OWNER_ROWS_SQL과 같음).
    업무 컬렉션은 display_name = name, team_id = None."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT n.id, n.name, n.description, n.kind, n.owner_team_id,
                   n.parent_id, pn.name AS parent,
                   n.created_at::text,
                   n.created_by_user_id,
                   u.username AS created_by_username,
                   COALESCE(k.cnt, 0) AS knowledge_count,
                   COALESCE(g.cnt, 0) AS glossary_count,
                   COALESCE(pi.cnt, 0) AS policy_count
            FROM ops_namespace n
            LEFT JOIN ops_namespace pn ON pn.id = n.parent_id          -- 상위 영역(트리 깊이 1)
            LEFT JOIN ops_user u ON n.created_by_user_id = u.id
            LEFT JOIN (
                SELECT namespace_id, COUNT(*) AS cnt FROM rag_knowledge WHERE status = 'active' GROUP BY namespace_id
            ) k ON n.id = k.namespace_id
            LEFT JOIN (
                SELECT namespace_id, COUNT(*) AS cnt FROM rag_glossary GROUP BY namespace_id
            ) g ON n.id = g.namespace_id
            LEFT JOIN (   -- 지금 쓰이는 정책 — 채팅 검색 대상과 같은 기준(searchable_sql, v0 검토 대기 제외 10/21)
                SELECT namespace_id, COUNT(*) AS cnt FROM policy_item WHERE {searchable_sql('policy_item')} GROUP BY namespace_id
            ) pi ON n.id = pi.namespace_id
            ORDER BY COALESCE(pn.name, n.name), n.parent_id IS NOT NULL, n.name   -- 상위 바로 아래 하위(하위가 없으면 이름순 그대로)
            """
        )
        owner_rows = await conn.fetch("""SELECT o.namespace_id, p.id, p.name FROM ops_namespace_owner o
                                         JOIN ops_part p ON p.id = o.part_id ORDER BY p.name, p.id""")
        part_rows = await conn.fetch("SELECT id, name, team_id FROM ops_part ORDER BY name, id")
        team_names = {r["id"]: r["name"] for r in await conn.fetch("SELECT id, name FROM ops_team")}
    direct: dict[int, list[dict]] = {}
    for r in owner_rows:
        direct.setdefault(r["namespace_id"], []).append({"id": r["id"], "name": r["name"]})
    team_parts: dict[int, list[dict]] = {}
    for p in part_rows:
        if p["team_id"] is not None:
            team_parts.setdefault(p["team_id"], []).append({"id": p["id"], "name": p["name"]})
    part_team = {p["id"]: p["team_id"] for p in part_rows}
    out = []
    for r in rows:
        d = dict(r)
        own = direct.get(d["id"], [])
        if d["kind"] == ORG_KIND and d["owner_team_id"] is not None:   # 팀 공통: 담당 표 줄 없이 그 팀 파트 전부
            own = team_parts.get(d["owner_team_id"], [])
        inherited = not own and bool(d["parent_id"] and direct.get(d["parent_id"]))
        eff = direct.get(d["parent_id"], []) if inherited else own
        if d["kind"] == ORG_KIND:
            part = own[0] if d["owner_team_id"] is None and own else None
            d["team_id"] = d["owner_team_id"] if d["owner_team_id"] is not None else (part and part_team.get(part["id"]))
            d["team"] = team_names.get(d["team_id"])
            d["display_name"] = org_display_name(d["owner_team_id"], team_names.get(d["owner_team_id"]), part and part["name"])
        else:
            d["team_id"], d["team"], d["display_name"] = None, None, d["name"]
        first, eff_first = _first_owner(own), _first_owner(eff)
        d.update(owner_parts=own, effective_owner_parts=eff, owner_inherited=inherited,
                 owner_part_id=first and first["id"], owner_part=first and first["name"],
                 effective_owner_part_id=eff_first and eff_first["id"], effective_owner_part=eff_first and eff_first["name"])
        out.append(d)
    return out


def _first_owner(parts: list[dict]) -> Optional[dict]:
    """옛 단일 담당 칸 호환용 — id가 가장 작은 담당(옛 칸 임시 거울 OWNER_MIRROR_SYNC_SQL과 같은 기준)."""
    return min(parts, key=lambda p: p["id"]) if parts else None


async def replace_namespace_owners(conn, ns_id: int, part_ids: list[int]) -> None:
    """영역 담당 파트를 통째로 바꾼다(호출부 트랜잭션 안, 영역 행을 FOR UPDATE로 잡은 뒤). 없는 파트가 하나라도 있으면 ValueError(→ 400)
    — 아무것도 안 바꾼다. 옛 칸(owner_part_id) 임시 거울도 같이 맞춘다."""
    wanted = sorted(set(part_ids))
    if wanted:
        found = {r["id"] for r in await conn.fetch("SELECT id FROM ops_part WHERE id = ANY($1::int[]) FOR KEY SHARE", wanted)}
        missing = [str(i) for i in wanted if i not in found]
        if missing:
            raise ValueError(f"없는 파트입니다: {', '.join(missing)}")
    await conn.execute("DELETE FROM ops_namespace_owner WHERE namespace_id = $1 AND NOT (part_id = ANY($2::int[]))", ns_id, wanted)
    if wanted:
        await conn.execute("""INSERT INTO ops_namespace_owner (namespace_id, part_id)
                              SELECT $1, id FROM ops_part WHERE id = ANY($2::int[]) ON CONFLICT DO NOTHING""", ns_id, wanted)
    await conn.execute(OWNER_MIRROR_SYNC_SQL, [ns_id])   # 임시 거울(옛 칸 삭제 때 같이 지움)


async def _check_parent(conn, parent_id: int, child_id: Optional[int]) -> None:
    """상위 영역 지정 검사(트리 깊이 1단, category-removal-design §2-6) — 어기면 ValueError(→ 400).
    상위 행을 FOR UPDATE로 잡는다: 같은 때 "상위가 다른 영역의 하위가 되는" 요청·상위 삭제와 순서가 갈리지 않게(둘 다 그 행을 잡는다)."""
    if child_id is not None and parent_id == child_id:
        raise ValueError("자기 자신을 상위 영역으로 지정할 수 없습니다.")
    parent = await conn.fetchrow("SELECT id, parent_id, kind FROM ops_namespace WHERE id = $1 FOR UPDATE", parent_id)
    if parent is None:
        raise ValueError("없는 상위 영역입니다.")
    if parent["kind"] == ORG_KIND:   # 조직 컬렉션은 트리에 안 든다 — 하위가 생기면 검색 범위가 "그 컬렉션 하나"가 아니게 된다(#83)
        raise ValueError("조직 컬렉션은 상위 영역이 될 수 없습니다.")
    if parent["parent_id"] is not None:
        raise ValueError("상위 영역은 최상위 영역이어야 합니다(하위 영역은 한 단계까지).")
    if child_id is not None:
        n = await conn.fetchval("SELECT COUNT(*) FROM ops_namespace WHERE parent_id = $1", child_id)
        if n:
            raise ValueError(f"하위 영역이 {n}개 있는 영역은 다른 영역의 하위가 될 수 없습니다.")


async def create_namespace(
    name: str, description: str = "",
    owner_part: str | None = None, created_by_user_id: int | None = None,
    parent_id: int | None = None,
) -> dict | None:
    """parent_id(선택) = 상위 영역. 하위 영역은 담당을 비워 두면 상위 담당을 물려받는다(check_namespace_ownership).
    업무 컬렉션만 만든다 — "org:"로 시작하는 이름은 조직 컬렉션 키라 ValueError(→ 400, 조직 컬렉션은 팀·파트를 만들 때 생긴다)."""
    if name.startswith(ORG_NAME_PREFIX):
        raise ValueError(f'"{ORG_NAME_PREFIX}"로 시작하는 이름은 조직 컬렉션용이라 쓸 수 없습니다.')
    async with get_conn() as conn:
        async with conn.transaction():
            if parent_id is not None:
                await _check_parent(conn, parent_id, child_id=None)
            # owner_part name → id 변환
            owner_part_id = None
            if owner_part:
                owner_part_id = await conn.fetchval(
                    "SELECT id FROM ops_part WHERE name = $1", owner_part
                )
            row = await conn.fetchrow(
                """
                INSERT INTO ops_namespace (name, description, created_by_user_id, parent_id)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (name) DO NOTHING
                RETURNING id, name, description, parent_id, created_at::text
                """,
                name, description, created_by_user_id, parent_id,
            )
            if row is None:   # 같은 이름이면 설명을 조용히 덮어쓰던 것(리뷰 L1)
                return None
            if owner_part_id is not None:   # 담당은 담당 표에(#82) — 옛 칸은 임시 거울로 같이 맞춘다
                await replace_namespace_owners(conn, row["id"], [owner_part_id])
            # owner_part name 보충
            result = dict(row)
            result["owner_part"] = owner_part
    return result


class NamespaceNotFound(Exception):
    """업무 영역이 없음(라우터가 404로) — LookupError를 쓰면 KeyError와 섞인다."""


async def _forget_cached_answers(*names: str) -> None:
    """이름이 바뀌거나 지워진 영역의 답변 캐시를 비운다 — 캐시 키가 영역 이름이라, 그 이름이 나중에 다른 영역에 다시 쓰이면
    옛 답이 새 영역에 샌다(이름 바꾸기·지우기 공통). 상위 영역 이름도 같이(상위 검색 범위에 하위 지식이 든다 — 트리, 리뷰 M2).
    지운 영역은 DB에서 상위를 못 찾으므로 호출부가 상위 이름을 같이 넘긴다."""
    from shared import cache as sem_cache
    await sem_cache.invalidate_namespace_family(*names)


NAME_TAKEN = "이미 있는 업무 영역 이름입니다."
ORG_NOT_EDITABLE = "조직 컬렉션은 팀·파트에서 정해져 여기서 이름·담당·상위를 바꾸거나 지울 수 없습니다 — 조직·권한에서 팀·파트를 고치세요."


async def rename_namespace(old_name: str, new_name: str) -> tuple[int, str]:
    """(상태, 사유). 없는 영역 404, 같은 이름·이미 있는 이름 409 — 예전엔 없는 영역도 409 "이미 있음"이었다."""
    import asyncpg
    async with get_conn() as conn:
        kind = await conn.fetchval("SELECT kind FROM ops_namespace WHERE name = $1", old_name)
        if kind is None:
            return 404, "업무 영역을 찾을 수 없습니다."
        if kind == ORG_KIND or new_name.startswith(ORG_NAME_PREFIX):   # 조직 컬렉션 이름은 id 키 — 화면 이름은 팀·파트 이름을 바꾸면 따라간다(#83)
            return 400, ORG_NOT_EDITABLE
        if old_name == new_name:
            return 409, "지금 이름과 같습니다."
        try:
            result = await conn.execute("UPDATE ops_namespace SET name = $2 WHERE name = $1", old_name, new_name)
        except asyncpg.exceptions.UniqueViolationError:   # 확인과 변경 사이 동시 요청(막 쓰기 테스트: 500)
            result = "UPDATE 0"
    if result != "UPDATE 1":
        return 409, NAME_TAKEN
    await _forget_cached_answers(old_name, new_name)
    return 200, ""


async def delete_namespace(name: str) -> tuple[int, str]:
    """(상태, 사유). 연결 표의 글자 키 연결도 같이 지운다."""
    async with get_conn() as conn:
        async with conn.transaction():
            # 상위 이름은 캐시 비우기용 — 지운 뒤엔 DB에서 못 찾는다
            row = await conn.fetchrow("""SELECT n.id, n.kind, p.name AS parent FROM ops_namespace n
                                         LEFT JOIN ops_namespace p ON p.id = n.parent_id WHERE n.name = $1 FOR UPDATE OF n""", name)
            if row is None:
                return 404, "업무 영역을 찾을 수 없습니다."
            if row["kind"] == ORG_KIND:   # 조직 컬렉션은 팀·파트를 지울 때만 같이 지운다(지식이 있으면 거부, auth/service.py)
                return 400, ORG_NOT_EDITABLE
            # 하위가 있으면 막는다 — FK SET NULL로 하위가 조용히 최상위가 되고 담당 상속이 끊긴다(트리, category-removal-design §2-6).
            # 하위 생성·이동도 상위 행을 FOR UPDATE로 잡아(_check_parent) 이 확인과 엇갈리지 않는다.
            children = await conn.fetchval("SELECT COUNT(*) FROM ops_namespace WHERE parent_id = $1", row["id"])
            if children:
                return 409, f"하위 영역 {children}개를 먼저 옮기거나 지우세요."
            # 연결 표는 영역을 글자 키로 가리켜 FK로 같이 안 지워진다 — 남으면 정책 범위가 조용히 "전부"로 바뀐다(리뷰 L6)
            await conn.execute("""DELETE FROM ops_link WHERE (from_type = 'domain' AND from_key = $1) OR (to_type = 'domain' AND to_key = $1)
                                  OR (from_type = 'section' AND from_key LIKE $1 || '|%') OR (to_type = 'section' AND to_key LIKE $1 || '|%')""",
                               str(row["id"]))
            # 나머지는 ON DELETE CASCADE로 자식 테이블 자동 삭제
            await conn.execute("DELETE FROM ops_namespace WHERE id = $1", row["id"])
    await _forget_cached_answers(name, row["parent"])
    return 204, ""


async def update_namespace_settings(name: str, body) -> dict:
    """담당 파트·상위 영역 지정(관리자) — 업무 컬렉션 수정 창·스크립트가 쓴다.
    담당(N개, #82): owner_part_ids = 담당 파트 목록으로 통째로 교체([] = 담당 없음 → 관리자만). 옛 단일 칸(owner_part 이름 · clear_owner)도
    호환으로 받는다(그 파트 하나로 교체 / 해제) — 둘 다 오면 owner_part_ids가 우선. 상위 영역: parent_id = 지정(검사는 _check_parent),
    clear_parent = 최상위로. 보낸 칸만 바꾼다."""
    set_parent = body.parent_id is not None and not body.clear_parent
    async with get_conn() as conn:
        async with conn.transaction():
            if set_parent:
                # 이 영역과 새 상위 두 행을 id 오름차순으로 잠근다 — "X를 Y 아래로"·"Y를 X 아래로"가 동시에 오면 잠금 순서가 엇갈려
                # 교착(500)이 났다(리뷰 L1). _check_parent가 상위를 다시 FOR UPDATE로 읽어도 이미 잡은 잠금이라 기다리지 않는다.
                locked = await conn.fetch("SELECT id, name, parent_id, kind FROM ops_namespace WHERE name = $1 OR id = $2 ORDER BY id FOR UPDATE",
                                          name, body.parent_id)
                ns = next((r for r in locked if r["name"] == name), None)
            else:
                ns = await conn.fetchrow("SELECT id, parent_id, kind FROM ops_namespace WHERE name = $1 FOR UPDATE", name)
            if ns is None:
                raise NamespaceNotFound("업무 영역을 찾을 수 없습니다.")
            if ns["kind"] == ORG_KIND:
                raise ValueError(ORG_NOT_EDITABLE)   # 조직 컬렉션 담당·상위는 팀·파트에서 정해진다(#83) → 400
            owner_ids = getattr(body, "owner_part_ids", None)
            if owner_ids is None and (body.clear_owner or body.owner_part is not None):   # 옛 단일 칸 — 빈 문자열 = 해제(재리뷰 L1)
                owner_ids = []
                if body.owner_part and not body.clear_owner:
                    owner = await conn.fetchval("SELECT id FROM ops_part WHERE name = $1 FOR KEY SHARE", body.owner_part)
                    if owner is None:
                        raise ValueError(f"없는 파트입니다: {body.owner_part}")
                    owner_ids = [owner]
            if owner_ids is not None:
                await replace_namespace_owners(conn, ns["id"], owner_ids)
            new_parent = None if body.clear_parent else body.parent_id if set_parent else ns["parent_id"]
            parent_changed = new_parent != ns["parent_id"]
            old_parent_name = None
            if parent_changed and ns["parent_id"] is not None:   # 캐시 비우기용(옛 상위 검색 범위에서 이 영역 지식이 빠진다)
                old_parent_name = await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", ns["parent_id"])
            if body.clear_parent:
                await conn.execute("UPDATE ops_namespace SET parent_id = NULL WHERE id = $1", ns["id"])
            elif set_parent:
                await _check_parent(conn, body.parent_id, child_id=ns["id"])
                await conn.execute("UPDATE ops_namespace SET parent_id = $2 WHERE id = $1", ns["id"], body.parent_id)
            row = await conn.fetchrow("""SELECT n.name, pn.name AS parent FROM ops_namespace n
                                         LEFT JOIN ops_namespace pn ON pn.id = n.parent_id WHERE n.id = $1""", ns["id"])
            owners = [dict(r) for r in await conn.fetch("""SELECT p.id, p.name FROM ops_namespace_owner o JOIN ops_part p ON p.id = o.part_id
                                                           WHERE o.namespace_id = $1 ORDER BY p.name, p.id""", ns["id"])]
    if parent_changed:   # 자신 + 옛 상위 + 새 상위(새 상위는 family가 DB에서 찾는다) — 검색 범위가 바뀐 영역들
        await _forget_cached_answers(row["name"], old_parent_name)
    first = _first_owner(owners)
    return {**dict(row), "owner_parts": owners, "owner_part": first and first["name"]}


# 업무구분 AI 추천·자동 생성(추천·"분류 확인 필요" 자동 생성, 업무구분 추천 프롬프트)은 10/22 업무구분 폐지로 삭제
