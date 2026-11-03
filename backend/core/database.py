import re
from contextlib import asynccontextmanager
from typing import AsyncIterator, Sequence

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


POLICY_COLLECTION_KNOWLEDGE_MSG = "정책 컬렉션엔 운영 지식을 넣을 수 없어요 — 시스템 컬렉션에 넣어 주세요(정책서는 정책 컬렉션)."


async def resolve_knowledge_namespace_id(conn: asyncpg.Connection, namespace: str) -> int | None:
    """지식을 넣을 컬렉션 name → id(없으면 None). 정책 컬렉션이면 ValueError — 운영 지식은 시스템에만(11/02 D, taxonomy-standard.md).
    지식 등록·적재·동기화가 모두 지나는 길목(knowledge/service.create_knowledge·bulk_create_knowledge)이 부른다 — 화면·라우터 검사만으론
    API·옛 소스 연결·적재 규칙이 그대로 정책 컬렉션에 썼다(11/03 독립 리뷰 M1, E2E 잔재 6건으로 재현)."""
    row = await conn.fetchrow("SELECT id, kind, area FROM ops_namespace WHERE name = $1", namespace)
    if row is None:
        return None
    if row["kind"] == "domain" and row["area"] == "policy":
        raise ValueError(POLICY_COLLECTION_KNOWLEDGE_MSG)
    return row["id"]


# FK 없이 컬렉션 id를 든 칸 — 컬렉션을 지우면 NULL로(지운 id를 가리키지 않게, 키 정리 5단계 collection-key-plan.md §2-2)
LOOSE_NAMESPACE_REFS = (("rag_import_page", "final_namespace_id"), ("rag_import_page", "ai_namespace_id"),
                        ("rag_import_page", "proposed_namespace_id"), ("rag_import_page", "sample_ns"),
                        ("rag_import_batch", "default_namespace_id"), ("eval_question_candidate", "namespace_id"))


# 컬렉션을 지우면 FK CASCADE로 같이 사라지는 "살아 있는 내용" — 하나라도 있으면 삭제를 막는다(11/03 설계 리뷰 #1: 관리자 한 번 클릭에
# 원문·정책·용어가 복구 불가로 지워졌다). 이력(지운·폐기된 지식, 대화·질의 기록, 끝난 정정, VOC)은 막지 않는다 — 세면 한 번이라도 쓴
# 컬렉션은 화면에서 영영 못 지웠다(재리뷰 M1: 질의 기록·대화는 지울 화면도 없다). (표, 화면 이름, 살아 있는 줄 조건) — 코드 상수
NAMESPACE_CONTENT_TABLES = (
    ("rag_knowledge", "운영 지식", "status IN ('active', 'pending_review')"),
    ("rag_source_document", "원문", "status IN ('active', 'draft')"),
    ("policy_item", "정책", "status <> 'deprecated' AND status <> 'rejected'"),
    ("policy_doc_overview", "정책서 개요", "TRUE"), ("ref_table", "참고표", "TRUE"), ("ref_common_code", "공통코드", "TRUE"),
    ("ref_db_column", "DB 컬럼", "TRUE"), ("rag_glossary", "용어", "TRUE"),
    ("ops_improvement_item", "검토 중 정정", "status = 'pending'"),
)


async def namespace_content_counts(conn: asyncpg.Connection, ns_id: int) -> dict[str, int]:
    """이 컬렉션의 살아 있는 내용 {화면 이름: 행 수}(0인 것 빼고) — 컬렉션 삭제 409 판정."""
    out = {}
    for t, label, live in NAMESPACE_CONTENT_TABLES:
        n = await conn.fetchval(f"SELECT count(*) FROM {t} WHERE namespace_id = $1 AND {live}", ns_id)
        if n:
            out[label] = n
    return out


async def release_namespace_refs(conn: asyncpg.Connection, ns_id: int,
                                 loose: Sequence[tuple[str, str]] = LOOSE_NAMESPACE_REFS) -> None:
    """지울 컬렉션을 FK 없이 가리키는 것 정리(트랜잭션 안에서, 컬렉션 행 삭제 직전) — 컬렉션 삭제·#106 공용.
    넣을 곳이 비는 자동·검토 줄은 검토로(조용히 적재에서 빠지지 않게) · FK 없는 id 칸 NULL · 연결 표(글자 키) 줄 · 용어 수집 상태 설정 줄.
    loose = 실제로 있는 칸만(기동 중 마이그레이션은 칸이 없을 수 있다). 표·칸 이름은 코드 상수."""
    if ("rag_import_page", "final_namespace_id") in loose:
        await conn.execute("UPDATE rag_import_page SET status = 'review', reason = 'low_confidence', decided_by_user_id = NULL "
                           "WHERE final_namespace_id = $1 AND status IN ('auto', 'review')", ns_id)
    for t, c in loose:
        await conn.execute(f"UPDATE {t} SET {c} = NULL WHERE {c} = $1", ns_id)
    await conn.execute("""DELETE FROM ops_link WHERE (from_type = 'domain' AND from_key = $1) OR (to_type = 'domain' AND to_key = $1)
                          OR (from_type = 'section' AND from_key LIKE $1 || '|%') OR (to_type = 'section' AND to_key LIKE $1 || '|%')""",
                       str(ns_id))
    await conn.execute("DELETE FROM ops_system_config WHERE key = ANY($1::text[])",
                       [f"glossary_mining_last_id:{ns_id}", f"glossary_gap_done:{ns_id}"])


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
# 실효 담당의 원천 줄(영역 id, 파트 id)은 아래 OWNER_ROWS_SQL 한 곳 — 수정·조회·읽기 기록 검사와 질의 기록 일괄 삭제가 이것만 본다.
# (조직 컬렉션 #83의 "팀 공통 = 그 팀 모든 파트" 줄은 11/02 조직 컬렉션 폐지로 뺐다 — 팀·파트는 로그인·권한·담당만.)
_ALIAS = re.compile(r"^[a-z]\w{0,10}$")

# 팀 적재 원문 초안·읽기 제한 기록 보관 자리(#105, 11/02) — 화면 목록·검색·넣을 곳 후보에 안 나온다(kind가 domain이 아님)
STAGING_KIND = "staging"
STAGING_NAME = "stage:import"
STAGING_NAME_PREFIX = "stage:"   # 예약 — 사람이 이 이름으로 컬렉션을 만들 수 없다
# 폐지된 조직 컬렉션(#83 → 11/02 폐지, #106이 빈 행을 지움). 내용(용어·지식 등)이 남아 못 지운 행만 남는다 — 사람이 옮길 곳을 정할
# 때까지(scripts/move_org_to_system.py) 화면 목록·담당 목록에 안 나온다. 새로 만드는 코드는 없다.
RETIRED_ORG_KIND = "org_space"
HIDDEN_KINDS = (STAGING_KIND, RETIRED_ORG_KIND)   # 화면 목록·담당 목록에서 빼고, 이름·담당·상위를 못 고치고 못 지우는 내부 자리
HIDDEN_KINDS_SQL = "(" + ", ".join(f"'{k}'" for k in HIDDEN_KINDS) + ")"   # `kind NOT IN {HIDDEN_KINDS_SQL}` — 코드 상수만

# 실효 담당 계산의 원천 줄(namespace_id, part_id) — 담당 표 그대로
OWNER_ROWS_SQL = "(SELECT ow.namespace_id, ow.part_id FROM ops_namespace_owner ow)"


def effective_owner_ns_sql(alias: str) -> str:
    """실효 담당을 가진 영역 id SQL 식 — `{alias}`(ops_namespace 별칭)에 자기 담당이 있으면 자신, 없으면 상위(없으면 NULL).
    `JOIN {OWNER_ROWS_SQL} o ON o.namespace_id = <이것>`으로 실효 담당 파트들을 얻는다(sqlite 시험에서도 그대로 돈다)."""
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
    **검색 경로만** 쓴다 — 등록·수정·통계는 resolve_namespace_id(고른 영역 자신). 규칙은 scope_ids_subquery."""
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


# ── 검색 범위 실험(11/02 시스템 축 — "보관은 한 곳, 찾는 곳은 연결된 묶음". 평가 게이트 전엔 측정 옵션만, 채팅 기본값은 그대로) ───────
# linked  = 받침 연결(ops_link 'supports', #105)로 이어진 컬렉션 — 업무를 고르면 그 업무를 받치는 시스템들(하위 업무는 상위의 연결도),
#           시스템을 고르면 그 시스템이 받치는 업무들. 조직과 무관(개편에 안 흔들림), 질문자와 무관(누가 물어도 같은 범위)
# similar = linked + 질문과 가까운 다른 업무 컬렉션 상위 n개(쿼리 라우팅: 활성 지식 조각의 최고 유사도로 컬렉션 순위)
KNOWLEDGE_SCOPES = ("linked", "similar")


async def linked_scope_ids(conn: asyncpg.Connection, namespace: str) -> list[int]:
    rows = await conn.fetch(
        """SELECT DISTINCT o.id FROM ops_namespace me
           JOIN ops_link l ON l.relation = 'supports' AND l.status = 'active' AND l.from_type = 'domain' AND l.to_type = 'domain'
                AND (l.to_key IN (me.id::text, me.parent_id::text) OR l.from_key = me.id::text)
           JOIN ops_namespace o ON o.id::text = CASE WHEN l.from_key = me.id::text THEN l.to_key ELSE l.from_key END
           WHERE me.name = $1 AND o.id <> me.id
           ORDER BY o.id""", namespace)
    return [r["id"] for r in rows]


async def similar_domain_scope_ids(conn: asyncpg.Connection, namespace: str, query_vec: Sequence[float], n: int = 2,
                                   pool: int = 30) -> list[int]:
    # 이미 검색 범위인 곳(자신 + 직계 하위)은 빼고 고른다 — 안 빼면 하위가 자리를 차지해 실제로 늘어나는 업무가 줄었다(리뷰 11/02 M2)
    in_scope = await namespace_scope_ids(conn, namespace)
    rows = await conn.fetch(
        """SELECT namespace_id, max(s) AS s FROM (
               SELECT k.namespace_id, 1 - (k.embedding <=> $1::vector) AS s FROM rag_knowledge k
               JOIN ops_namespace n ON n.id = k.namespace_id
               WHERE k.status = 'active' AND n.kind = 'domain' AND n.id <> ALL($2::int[])
               ORDER BY k.embedding <=> $1::vector LIMIT $3) t
           GROUP BY namespace_id ORDER BY s DESC LIMIT $4""",
        "[" + ",".join(str(float(x)) for x in query_vec) + "]", in_scope, pool, n)
    return [r["namespace_id"] for r in rows]


async def knowledge_scope_extra_ids(conn: asyncpg.Connection, namespace: str, scope: str | None,
                                    query_vec: Sequence[float]) -> list[int]:
    """검색 범위 실험 옵션의 추가 컬렉션 id — scope 없음이면 [](지금과 같음)."""
    if not scope:
        return []
    if scope not in KNOWLEDGE_SCOPES:
        raise ValueError(f"알 수 없는 검색 범위: {scope}")
    ids = await linked_scope_ids(conn, namespace)
    if scope == "similar":
        ids += [i for i in await similar_domain_scope_ids(conn, namespace, query_vec) if i not in ids]
    return ids
