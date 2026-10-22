import re
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from core.config import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        command_timeout=60,
    )


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_conn() -> AsyncIterator[asyncpg.Connection]:
    assert _pool is not None, "DB pool is not initialized"
    async with _pool.acquire(timeout=settings.db_pool_acquire_timeout) as conn:
        yield conn


async def resolve_namespace_id(conn: asyncpg.Connection, namespace: str) -> int | None:
    """업무 영역 name → id. 없으면 None."""
    return await conn.fetchval("SELECT id FROM ops_namespace WHERE name = $1", namespace)


# 범위 조각에 끼울 수 있는 "영역 id 하나" SQL 식 — 자리표시($n)나 아래 코드 상수만. f-string으로 SQL에 들어가므로 값·사용자 입력이
# 섞이지 않게 함수 안에서 막는다(리뷰 L-b).
_ME_ID_PARAM = re.compile(r"^\$\d+$")
ME_BY_NAME_1 = "(SELECT id FROM ops_namespace WHERE name = $1)"   # 이름($1)으로 고른 영역
_ME_ID_CONSTANTS = frozenset({"c.namespace_id", ME_BY_NAME_1})


def _checked_me_id(me_id: str) -> str:
    if not (isinstance(me_id, str) and (_ME_ID_PARAM.fullmatch(me_id) or me_id in _ME_ID_CONSTANTS)):
        raise ValueError(f"범위 조각에 넣을 수 없는 SQL 식입니다: {me_id!r}")
    return me_id


def _child_kind_rule(me_id: str) -> str:
    # 테스트용 하위(kind='policy_test', E2E 임시)는 고른 영역도 테스트용일 때만 — 정책 범위(service/policy/scope.py)와 같은 규칙
    return (f"(s.kind <> 'policy_test' "
            f"OR EXISTS (SELECT 1 FROM ops_namespace m WHERE m.id = {me_id} AND m.kind = 'policy_test'))")


def scope_ids_subquery(me_id: str) -> str:
    """검색 범위 규칙 한 곳 — "영역 id 하나"를 가리키는 SQL 식(`$2`·`c.namespace_id` 같은 코드 상수만, 아니면 ValueError)을 받아
    "그 영역 자신 + 직계 하위 영역" id를 내는 서브쿼리 SQL을 돌려준다. `namespace_id IN (<이것>)`으로 쓴다.
    트리는 깊이 1단(상위의 상위 금지, 영역 생성·수정에서 막음)이라 재귀 없이 한 단계만 본다(category-removal-design.md §2-6).
    **읽기(검색) 범위**다 — 지식 조회는 누구나라 하위 담당이 달라도 넣는다. 쓰기(정정)는 scope_ids_same_owner_subquery."""
    me_id = _checked_me_id(me_id)
    return f"SELECT s.id FROM ops_namespace s WHERE s.id = {me_id} OR (s.parent_id = {me_id} AND {_child_kind_rule(me_id)})"


def scope_ids_same_owner_subquery(me_id: str) -> str:
    """정정(쓰기) 범위 — scope_ids_subquery 중 실효 담당이 고른 영역의 실효 담당과 같은 하위만(하위 담당이 비었거나 상위와 같은 파트).
    고른 영역은 최상위라 실효 담당 = 자기 칸. 다른 파트가 직접 맡은 하위는 빠진다 — 안 그러면 상위 담당 파트가 상위에서 신고·승인해
    다른 파트 하위의 지식·정책을 바꿀 수 있었다(권한은 신고 영역 담당만 봄, 리뷰 H1). 그 하위 근거는 그 하위 영역에서 신고한다.
    (하위를 고르면 자신만 — 하위의 하위는 없다.)"""
    me_id = _checked_me_id(me_id)
    # 담당 N개(10/22, #82): 하위가 들어가는 건 ① 하위에 자기 담당이 없음(상위 상속) ② 상위 담당이 있고 그 전부가 하위 담당에도 있음 —
    # 상위에서 쓸 수 있는 사람(상위 담당 파트 아무나)이 그 하위도 쓸 수 있을 때만. 상위 담당이 없으면(관리자만) 담당 있는 하위는 빠진다(예전과 같음).
    return (f"SELECT s.id FROM ops_namespace s WHERE s.id = {me_id} OR (s.parent_id = {me_id} AND {_child_kind_rule(me_id)} "
            f"AND (NOT EXISTS (SELECT 1 FROM ops_namespace_owner co WHERE co.namespace_id = s.id) "
            f"OR (EXISTS (SELECT 1 FROM ops_namespace_owner po WHERE po.namespace_id = {me_id}) "
            f"AND NOT EXISTS (SELECT 1 FROM ops_namespace_owner po WHERE po.namespace_id = {me_id} AND po.part_id NOT IN "
            f"(SELECT co.part_id FROM ops_namespace_owner co WHERE co.namespace_id = s.id)))))")


# ── 업무 컬렉션 담당(N개, 2026-10-22 #82, collection-design §15-8) ─────────────────────────
# 담당의 단일 기준은 ops_namespace_owner. 담당 파트 중 어느 파트 사람이든 쓰기·승인(주 담당 없음), 담당이 없으면 관리자만.
# 하위 영역은 자기 담당이 하나도 없으면 상위 담당 전부를 물려받는다(트리 깊이 1단) — 자기 담당이 하나라도 있으면 그것만.
# 조직 컬렉션(kind='org_space', #83, org-space-design §3): 파트 컬렉션 = 담당 표에 그 파트 한 줄(업무 컬렉션과 같은 모양),
# 팀 공통 컬렉션 = 담당 표에 줄 없이 owner_team_id 팀의 **모든 파트**(팀에 파트가 생기거나 옮겨 가면 저절로 따라간다).
# 두 경우를 아래 OWNER_ROWS_SQL 한 곳에서 (영역 id, 파트 id) 줄로 합친다 — 수정·조회·읽기 기록 검사와 질의 기록 일괄 삭제가 이것만 본다.
_ALIAS = re.compile(r"^[a-z]\w{0,10}$")

ORG_KIND = "org_space"
ORG_TEAM_PREFIX = "org:team:"   # 조직 컬렉션 이름 = id 기반 키(org:team:<팀 id> / org:part:<파트 id>) — 데이터에 팀·파트 이름을 넣지 않는다
ORG_PART_PREFIX = "org:part:"
ORG_NAME_PREFIX = "org:"        # 업무 컬렉션 이름은 이 글자로 시작할 수 없다(DB CHECK ops_namespace_org_shape)

# 실효 담당 계산의 원천 줄(namespace_id, part_id) — 직접 담당 + 팀 공통 조직 컬렉션의 그 팀 파트들
OWNER_ROWS_SQL = ("(SELECT ow.namespace_id, ow.part_id FROM ops_namespace_owner ow "
                  "UNION ALL SELECT tn.id, tp.id FROM ops_namespace tn JOIN ops_part tp ON tp.team_id = tn.owner_team_id "
                  f"WHERE tn.kind = '{ORG_KIND}')")


def effective_owner_ns_sql(alias: str) -> str:
    """실효 담당을 가진 영역 id SQL 식 — `{alias}`(ops_namespace 별칭)에 자기 담당이 있으면 자신, 없으면 상위(없으면 NULL).
    `JOIN {OWNER_ROWS_SQL} o ON o.namespace_id = <이것>`으로 실효 담당 파트들을 얻는다(sqlite 시험에서도 그대로 돈다).
    팀 공통 조직 컬렉션은 담당 표 줄이 없어도 OWNER_ROWS_SQL에 그 팀 파트 줄이 있어 자신이 된다(조직 컬렉션은 상위가 늘 없음)."""
    if not _ALIAS.fullmatch(alias):
        raise ValueError(f"별칭으로 쓸 수 없는 글자입니다: {alias!r}")
    return (f"CASE WHEN EXISTS (SELECT 1 FROM {OWNER_ROWS_SQL} x WHERE x.namespace_id = {alias}.id) "
            f"THEN {alias}.id ELSE {alias}.parent_id END")


# 실효 담당 파트 id들(이름 $1) — 수정·조회·읽기 기록 검사가 같이 쓴다
EFFECTIVE_OWNERS_SQL = (f"SELECT o.part_id FROM ops_namespace n JOIN {OWNER_ROWS_SQL} o ON o.namespace_id = {effective_owner_ns_sql('n')} "
                        "WHERE n.name = $1 ORDER BY o.part_id")

# **임시 거울**(옛 칸 ops_namespace.owner_part_id 삭제 승인 전까지): 담당 표를 바꾼 같은 트랜잭션에서 옛 칸을 "담당 중 가장 작은 part_id"
# (없으면 NULL)로 맞춘다 — 놓친 옛 읽기 경로가 있어도 크게 어긋나지 않게. 옛 칸을 지울 때 이 문장과 호출부도 같이 지운다. ($1 = 영역 id 배열)
OWNER_MIRROR_SYNC_SQL = ("UPDATE ops_namespace SET owner_part_id = (SELECT MIN(o.part_id) FROM ops_namespace_owner o "
                         "WHERE o.namespace_id = ops_namespace.id) WHERE id = ANY($1::int[])")


async def namespace_scope_ids(conn: asyncpg.Connection, namespace: str) -> list[int]:
    """검색 범위 — 고른 업무 영역 자신 + 직계 하위 영역 id(자신이 맨 앞, 하위는 id순). 없으면 [].
    **검색 경로만** 쓴다 — 등록·수정·통계는 resolve_namespace_id(고른 영역 자신). 규칙은 scope_ids_subquery.
    조직 컬렉션(#83)은 하위가 없어(상위 지정에서 막음) 늘 [자신] — 조직 컬렉션을 고르면 지식 검색은 그 컬렉션 하나."""
    rows = await conn.fetch(
        f"""
        SELECT n.id FROM ops_namespace n
        WHERE n.id IN ({scope_ids_subquery(ME_BY_NAME_1)})
        ORDER BY (n.name = $1) DESC, n.id
        """,
        namespace,
    )
    return [r["id"] for r in rows]


async def namespace_names_with_parents(conn: asyncpg.Connection, names: list[str]) -> list[str]:
    """받은 영역 이름 + 그 상위 영역 이름(중복 없이, 받은 순서 → 상위). 답변 캐시 비우기용 — 하위에 쓴 지식은 상위 검색 범위에도 든다.
    DB에 없는 이름(방금 지운 영역 등)도 그대로 돌려준다."""
    rows = await conn.fetch(
        "SELECT p.name FROM ops_namespace n JOIN ops_namespace p ON p.id = n.parent_id WHERE n.name = ANY($1::text[]) ORDER BY p.name",
        list(names),
    )
    return list(dict.fromkeys([n for n in names if n] + [r["name"] for r in rows]))


# ── 조직 컬렉션(#83, org-space-design §3) — 이름 키·화면 이름·만들기/지우기 ─────────────────────

def org_team_key(team_id: int) -> str:
    return f"{ORG_TEAM_PREFIX}{int(team_id)}"


def org_part_key(part_id: int) -> str:
    return f"{ORG_PART_PREFIX}{int(part_id)}"


def org_display_name(owner_team_id: int | None, team_name: str | None, part_name: str | None) -> str:
    """조직 컬렉션 화면 이름 — 조회 때 만든다(팀·파트 이름이 바뀌어도 데이터는 그대로). 팀 공통 = "팀 공통 · <팀>", 파트 = "<파트>"."""
    if owner_team_id is not None:
        return f"팀 공통 · {team_name or '(이름 없는 팀)'}"
    return part_name or "(이름 없는 파트)"


async def create_org_collection(conn: asyncpg.Connection, *, team_id: int | None = None, part_id: int | None = None) -> None:
    """팀·파트를 만든 같은 트랜잭션에서 그 조직 컬렉션을 만든다(멱등 — 이미 있으면 그대로). 둘 중 하나만 넘긴다.
    파트 컬렉션은 담당 표에 그 파트 한 줄 + 옛 칸 임시 거울(owner_part_id)도 같은 값. 팀 공통은 담당 표 줄 없음(OWNER_ROWS_SQL)."""
    if (team_id is None) == (part_id is None):
        raise ValueError("team_id와 part_id 중 하나만 넘깁니다.")
    if team_id is not None:
        await conn.execute(
            "INSERT INTO ops_namespace (name, description, kind, owner_team_id) VALUES ($1, '', $2, $3) ON CONFLICT (name) DO NOTHING",
            org_team_key(team_id), ORG_KIND, team_id)
        return
    ns_id = await conn.fetchval(
        "INSERT INTO ops_namespace (name, description, kind, owner_part_id) VALUES ($1, '', $2, $3) "
        "ON CONFLICT (name) DO NOTHING RETURNING id", org_part_key(part_id), ORG_KIND, part_id)
    if ns_id is not None:
        await conn.execute("INSERT INTO ops_namespace_owner (namespace_id, part_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", ns_id, part_id)


# 조직 컬렉션을 지우지 못하게 하는 내용 — 지금 쓰이는 지식(검토 대기 포함)·용어. 그 밖(지운 지식·대화·질의 기록)은 컬렉션과 같이 지워진다(CASCADE)
ORG_CONTENT_SQL = ("SELECT (SELECT COUNT(*) FROM rag_knowledge k WHERE k.namespace_id = n.id "
                   "AND k.status IN ('active', 'pending_review')) AS knowledge, "
                   "(SELECT COUNT(*) FROM rag_glossary g WHERE g.namespace_id = n.id) AS glossary "
                   "FROM ops_namespace n WHERE n.name = $1")


async def delete_org_collection(conn: asyncpg.Connection, key: str) -> str | None:
    """팀·파트를 지우는 같은 트랜잭션에서 그 조직 컬렉션을 지운다. 내용(지식·용어)이 있으면 지우지 않고 사유를 돌려준다(None = 지웠거나 없음).
    컬렉션 행을 FOR UPDATE로 잡아 확인~삭제 사이에 끼는 지식 등록(FK가 그 행을 KEY SHARE로 잡는다)과 순서를 맞춘다."""
    if await conn.fetchval("SELECT id FROM ops_namespace WHERE name = $1 AND kind = $2 FOR UPDATE", key, ORG_KIND) is None:
        return None
    row = await conn.fetchrow(ORG_CONTENT_SQL, key)
    if row and (row["knowledge"] or row["glossary"]):
        return f"조직 컬렉션에 지식 {row['knowledge']}건 · 용어 {row['glossary']}건이 있어 삭제할 수 없습니다 — 먼저 옮기거나 지워 주세요."
    await conn.execute("DELETE FROM ops_namespace WHERE name = $1 AND kind = $2", key, ORG_KIND)
    return None


# ── 채팅 범위: 업무 컬렉션을 골랐을 때 "내 조직 컬렉션 포함"(org-space-design §3, collection-design §15-11·13) ───────────
# 정확도 게이트(기존 업무 문항 나빠짐 0 + 조직 문항 근거 들어옴) 통과 전엔 **끔** — 설정 키가 없거나 'true'가 아니면 꺼짐(기본).
# 조직 컬렉션을 직접 고르면 검색 범위는 그 컬렉션 하나(namespace_scope_ids — 조직 컬렉션은 하위가 없다).
ORG_SPACE_SCOPE_KEY = "chat.org_space_scope"


async def org_space_scope_extra_ids(conn: asyncpg.Connection, namespace: str, part_id: int | None) -> list[int]:
    """업무 컬렉션을 골랐을 때 지식 검색에 더할 질문자의 조직 컬렉션 id(그 파트 + 그 파트 팀의 팀 공통). 꺼져 있으면(기본) [].
    조직 컬렉션을 직접 골랐으면 더하지 않는다. 파트 없는 사람(슈퍼어드민)도 []."""
    if part_id is None:
        return []
    flag = await conn.fetchval("SELECT value FROM ops_system_config WHERE key = $1", ORG_SPACE_SCOPE_KEY)
    if str(flag or "").strip().lower() != "true":
        return []
    rows = await conn.fetch(
        f"""SELECT o.id FROM ops_namespace o JOIN ops_part p ON p.id = $2
            WHERE o.kind = '{ORG_KIND}' AND (o.name = '{ORG_PART_PREFIX}' || p.id OR o.owner_team_id = p.team_id)
              AND EXISTS (SELECT 1 FROM ops_namespace me WHERE me.name = $1 AND me.kind <> '{ORG_KIND}')
            ORDER BY o.id""", namespace, part_id)
    return [r["id"] for r in rows]
