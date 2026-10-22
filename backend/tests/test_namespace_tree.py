"""업무 영역 트리(깊이 1단, 2026-10-22 category-removal-design §2-6 = 커밋 C1) — 하위 포함 검색 범위·담당 상속·생성/수정/삭제 검사.

SQL 자체를 시험하려고 sqlite 메모리 DB에 같은 모양의 표를 만들고, asyncpg 문법($n·::cast·= ANY(배열)·FOR UPDATE)만 바꿔 실제로 돌린다.
(가짜 연결에 "이 SQL이 불렸다"만 단언하면 COALESCE·JOIN 방향이 틀려도 통과한다.) pgvector가 필요한 지식 검색 본문 SQL만 가짜 연결로 본다.
conftest가 core.*·service.*를 가짜로 바꿔 두므로 실제 파일을 경로로 읽는다.
"""
import importlib.util
import json
import re
import sqlite3
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


database = _load("_tree_real_database", "core/database.py")
dep = _load("_tree_real_dependencies", "core/dependencies.py")
admin_service = _load("_tree_real_admin_service", "service/admin/service.py")
auth_service = _load("_tree_real_auth_service", "service/auth/service.py")
scope = _load("_tree_real_policy_scope", "service/policy/scope.py")
real_cache = _load("_tree_real_cache", "shared/cache.py")
from agents.knowledge_rag.knowledge import retrieval  # noqa: E402

# 정정 원장(개선) 서비스 — test_improvement.py와 같은 방식으로 draft를 먼저 등록
sys.modules.setdefault("service.improvement", MagicMock())
if "service.improvement.draft" not in sys.modules:
    sys.modules["service.improvement.draft"] = _load("service.improvement.draft", "service/improvement/draft.py")
sys.modules["service.improvement"].draft = sys.modules["service.improvement.draft"]
improvement = _load("_tree_real_improvement", "service/improvement/service.py")


# ── sqlite로 asyncpg 흉내 ─────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE ops_team (id INTEGER PRIMARY KEY, name TEXT UNIQUE, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE ops_part (id INTEGER PRIMARY KEY, name TEXT UNIQUE, team_id INTEGER REFERENCES ops_team(id), created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE ops_user (id INTEGER PRIMARY KEY, username TEXT, part_id INTEGER);
CREATE TABLE ops_voc_routing (id INTEGER PRIMARY KEY, namespace_id INTEGER, part_id INTEGER);
CREATE TABLE ops_system_config (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE ops_namespace (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT DEFAULT '', kind TEXT NOT NULL DEFAULT 'domain',
    owner_part_id INTEGER REFERENCES ops_part(id) ON DELETE SET NULL,
    parent_id INTEGER REFERENCES ops_namespace(id) ON DELETE SET NULL,
    owner_team_id INTEGER REFERENCES ops_team(id),
    created_by_user_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE ops_namespace_owner (
    namespace_id INTEGER NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
    part_id INTEGER NOT NULL REFERENCES ops_part(id) ON DELETE CASCADE,
    PRIMARY KEY (namespace_id, part_id));
CREATE TABLE ops_link (id INTEGER PRIMARY KEY, relation TEXT, status TEXT, from_type TEXT, from_key TEXT, to_type TEXT, to_key TEXT);
CREATE TABLE ops_query_log (id INTEGER PRIMARY KEY, namespace_id INTEGER REFERENCES ops_namespace(id) ON DELETE CASCADE);
CREATE TABLE rag_knowledge (id INTEGER PRIMARY KEY, namespace_id INTEGER, status TEXT, content TEXT, category TEXT, heading_path TEXT);
CREATE TABLE rag_glossary (id INTEGER PRIMARY KEY, namespace_id INTEGER);
CREATE TABLE rag_knowledge_category (id INTEGER PRIMARY KEY, namespace_id INTEGER);
CREATE TABLE policy_item (id INTEGER PRIMARY KEY, namespace_id INTEGER, status TEXT, pipeline_version TEXT,
    policy_name TEXT, category_path TEXT, raw_body TEXT, remark TEXT);
CREATE TABLE policy_chunk (id INTEGER PRIMARY KEY, policy_item_id INTEGER, chunk_text TEXT, chunk_idx INTEGER DEFAULT 0);
CREATE TABLE ops_message (id INTEGER PRIMARY KEY, results TEXT, metadata TEXT);
CREATE TABLE ops_voc_cluster (id INTEGER PRIMARY KEY, namespace_id INTEGER);
"""


class _SqliteConn:
    """asyncpg 연결처럼 쓰는 sqlite 어댑터 — 문법만 옮기고 의미(JOIN·COALESCE·정렬)는 그대로 sqlite가 계산한다."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(_SCHEMA)
        self.sqls: list[str] = []

    def _tr(self, sql: str, args):
        self.sqls.append(sql)
        sql = re.sub(r"=\s*ANY\(\$(\d+)(::\w+\[\])?\)", r"IN (SELECT value FROM json_each(?\1))", sql)
        sql = re.sub(r"::\w+(\[\])?", "", sql)
        sql = re.sub(r"\bFOR (UPDATE|KEY SHARE|NO KEY UPDATE|SHARE)( OF \w+)?", "", sql)
        sql = re.sub(r"\$(\d+)", r"?\1", sql)
        return sql, [json.dumps(a) if isinstance(a, (list, tuple)) else a for a in args]

    async def fetch(self, sql, *args):
        return [dict(r) for r in self.db.execute(*self._tr(sql, args)).fetchall()]

    async def fetchrow(self, sql, *args):
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def fetchval(self, sql, *args):
        row = self.db.execute(*self._tr(sql, args)).fetchone()
        return row[0] if row else None

    async def execute(self, sql, *args):
        cur = self.db.execute(*self._tr(sql, args))
        return f"{sql.split()[0].upper()} {cur.rowcount}"

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield
        return tx()

    # 시험 데이터 넣기 — owner = 파트 id 하나 또는 목록(담당 N개, #82). 담당 표에 넣고 옛 칸은 임시 거울(가장 작은 id)로
    def ns(self, id_, name, owner=None, parent=None, kind="domain"):
        owners = [] if owner is None else [owner] if isinstance(owner, int) else list(owner)
        self.db.execute("INSERT INTO ops_namespace (id, name, owner_part_id, parent_id, kind) VALUES (?,?,?,?,?)",
                        (id_, name, min(owners) if owners else None, parent, kind))
        for o in owners:
            self.db.execute("INSERT INTO ops_namespace_owner (namespace_id, part_id) VALUES (?, ?)", (id_, o))

    def owners_of(self, name):
        return sorted(r[0] for r in self.db.execute(
            "SELECT o.part_id FROM ops_namespace_owner o JOIN ops_namespace n ON n.id = o.namespace_id WHERE n.name = ?", (name,)))

    def mirror_of(self, name):
        return self.db.execute("SELECT owner_part_id FROM ops_namespace WHERE name = ?", (name,)).fetchone()[0]

    def parent_of(self, name):
        return self.db.execute("SELECT parent_id FROM ops_namespace WHERE name = ?", (name,)).fetchone()[0]


def _patch_conn(mod, conn):
    @asynccontextmanager
    async def get_conn():
        yield conn
    return patch.object(mod, "get_conn", get_conn)


@pytest.fixture
def db():
    c = _SqliteConn()
    c.db.execute("INSERT INTO ops_part (id, name) VALUES (7, '온라인서비스'), (8, '다른파트'), (9, '세번째파트')")
    return c


@pytest.fixture(autouse=True)
def cleared(db):
    """답변 캐시 비우기 기록 — 실제 shared/cache.py의 invalidate_namespace_family가 sqlite에서 상위를 찾고, Redis 대신 이름만 모은다."""
    names: list[str] = []

    async def inv(n):
        names.append(n)
        return 1

    @asynccontextmanager
    async def get_conn():
        yield db
    dbmod = sys.modules["core.database"]
    with patch.object(real_cache, "invalidate_namespace", inv), \
         patch.object(dbmod, "get_conn", get_conn), \
         patch.object(dbmod, "namespace_names_with_parents", database.namespace_names_with_parents, create=True), \
         patch.dict(sys.modules, {"shared": MagicMock(cache=real_cache), "shared.cache": real_cache}):
        yield names


def _user(role="user", part_id=7):
    return {"id": 1, "username": "u", "role": role, "part_id": part_id, "part": "p"}


# ── 검색 범위: namespace_scope_ids ─────────────────────────────────────────────

class TestScopeIds:
    @pytest.mark.asyncio
    async def test_parent_includes_direct_children_self_first(self, db):
        db.ns(20, "제휴서비스")
        db.ns(5, "쿠팡이츠", parent=20)        # 하위 id가 상위보다 작아도 자신이 맨 앞
        db.ns(30, "배달의민족", parent=20)
        db.ns(40, "딜리버스")
        assert await database.namespace_scope_ids(db, "제휴서비스") == [20, 5, 30]

    @pytest.mark.asyncio
    async def test_child_is_only_itself(self, db):
        """하위를 고르면 하위만 — 상위·형제 지식은 안 들어온다(설계 "안 하는 것": 하위에서 상위 포함)."""
        db.ns(20, "제휴서비스")
        db.ns(30, "배달의민족", parent=20)
        db.ns(31, "쿠팡이츠", parent=20)
        assert await database.namespace_scope_ids(db, "배달의민족") == [30]

    @pytest.mark.asyncio
    async def test_no_children_is_same_as_before(self, db):
        """하위가 0인 지금 상태 = 예전 resolve_namespace_id 하나와 같은 범위(동작 변화 0)."""
        db.ns(40, "딜리버스")
        db.ns(41, "온라인스토어")
        assert await database.namespace_scope_ids(db, "딜리버스") == [40]
        assert await database.namespace_scope_ids(db, "없는영역") == []

    @pytest.mark.asyncio
    async def test_policy_test_child_only_under_policy_test_parent(self, db):
        """테스트용(E2E 임시) 하위는 실제 영역 검색에 섞이지 않는다 — 고른 영역도 테스트용일 때만(scope.py와 같은 규칙, 리뷰 L2)."""
        db.ns(20, "제휴서비스")
        db.ns(30, "e2e하위", parent=20, kind="policy_test")
        db.ns(31, "배달의민족", parent=20)
        db.ns(50, "e2e상위", kind="policy_test")
        db.ns(51, "e2e하위2", parent=50, kind="policy_test")
        assert await database.namespace_scope_ids(db, "제휴서비스") == [20, 31]
        assert await database.namespace_scope_ids(db, "e2e상위") == [50, 51]

    @pytest.mark.asyncio
    async def test_names_with_parents(self, db):
        db.ns(20, "제휴서비스")
        db.ns(30, "배달의민족", parent=20)
        assert await database.namespace_names_with_parents(db, ["배달의민족"]) == ["배달의민족", "제휴서비스"]
        assert await database.namespace_names_with_parents(db, ["제휴서비스", "지운영역", None]) == ["제휴서비스", "지운영역"]


# ── 지식 검색: ANY(ids) + BM25 캐시 키 ────────────────────────────────────────

def _fake_conn(rows=None):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=rows or [])
    return conn


class TestSearchKnowledgeScope:
    @pytest.mark.asyncio
    async def test_search_uses_any_of_scope_ids_in_all_three_places(self, monkeypatch):
        conn = _fake_conn()
        monkeypatch.setattr(retrieval, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(retrieval, "namespace_scope_ids", AsyncMock(return_value=[20, 30, 31]))
        await retrieval.search_knowledge("제휴서비스", [0.1], "질문")
        sql, *params = conn.fetch.call_args.args
        assert params[1] == [20, 30, 31]
        assert sql.count("namespace_id = ANY($2::int[])") == 3          # vector·keyword CTE·본문
        assert not re.search(r"namespace_id = \$2\b", sql)               # 단일 id 비교가 남지 않음

    @pytest.mark.asyncio
    async def test_unknown_namespace_returns_empty_without_query(self, monkeypatch):
        conn = _fake_conn()
        monkeypatch.setattr(retrieval, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(retrieval, "namespace_scope_ids", AsyncMock(return_value=[]))
        assert await retrieval.search_knowledge("없음", [0.1], "질문") == []
        conn.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bm25_cache_keyed_by_scope_tuple(self, monkeypatch):
        """키가 영역 id 하나면 하위가 생겨도 옛 문서 집합으로 점수를 낸다 — 범위 튜플마다 따로 만든다."""
        monkeypatch.setattr(retrieval, "_kw_cache", {})
        conn = _fake_conn([{"id": 1, "f": "", "h": "", "content": "배달 수수료 정산"}])
        assert await retrieval._keyword_ids(conn, [20], "수수료", 5) == [1]
        assert await retrieval._keyword_ids(conn, [20], "수수료", 5) == [1]
        assert conn.fetch.await_count == 1                                  # 같은 범위 = 캐시
        sql, ids = conn.fetch.await_args.args
        assert "namespace_id = ANY($1::int[])" in sql and ids == [20]
        await retrieval._keyword_ids(conn, [20, 30], "수수료", 5)           # 하위가 생긴 뒤 = 다른 키
        assert conn.fetch.await_count == 2
        assert set(retrieval._kw_cache) == {(20,), (20, 30)}


# ── 정책 범위: 직계 하위를 ord 0으로 ──────────────────────────────────────────

class TestPolicyScope:
    def _policy(self, db, ns_id, pid):
        db.db.execute("INSERT INTO policy_item (id, namespace_id, status) VALUES (?, ?, 'active')", (pid, ns_id))

    @pytest.mark.asyncio
    async def test_children_in_scope_even_when_linked(self, db):
        """"같은 업무" 연결이 있으면 "전부"가 빠진다 — 그때도 직계 하위 정책은 범위에 든다."""
        db.ns(20, "제휴서비스")
        db.ns(30, "배달의민족", parent=20)
        db.ns(50, "연결영역")
        db.ns(60, "무관영역")
        for ns_id, pid in ((20, 1), (30, 2), (50, 3), (60, 4)):
            self._policy(db, ns_id, pid)
        db.db.execute("INSERT INTO ops_link (relation, status, from_type, from_key, to_type, to_key) "
                      "VALUES ('same_work', 'active', 'domain', '20', 'domain', '50')")
        assert await scope.policy_namespace_ids(db, "제휴서비스") == [20, 30, 50]

    @pytest.mark.asyncio
    async def test_child_policy_test_kind_excluded_for_real_domain(self, db):
        db.ns(20, "제휴서비스")
        db.ns(30, "e2e임시", parent=20, kind="policy_test")
        db.ns(50, "연결영역")
        for ns_id, pid in ((20, 1), (30, 2), (50, 3)):
            self._policy(db, ns_id, pid)
        db.db.execute("INSERT INTO ops_link (relation, status, from_type, from_key, to_type, to_key) "
                      "VALUES ('same_work', 'active', 'domain', '20', 'domain', '50')")
        assert await scope.policy_namespace_ids(db, "제휴서비스") == [20, 50]

    @pytest.mark.asyncio
    async def test_no_children_unchanged(self, db):
        db.ns(20, "제휴서비스")
        db.ns(40, "딜리버스")
        self._policy(db, 20, 1)
        self._policy(db, 40, 2)
        assert await scope.policy_namespace_ids(db, "딜리버스") == [40, 20]   # 자신(ord 0) → 전부(ord 2)


# ── 담당 상속 ─────────────────────────────────────────────────────────────────

class TestOwnerInheritance:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("parent_owner, child_owner, user, allowed", [
        (7, None, _user(part_id=7), True),            # 하위 담당 비어 있음 → 상위 담당 파트는 수정 가능
        (7, None, _user(part_id=8), False),           # 다른 파트 403
        (7, None, _user(role="viewer"), False),       # 조회 전용 403
        (None, None, _user(part_id=7), False),        # 상위도 미지정 → 관리자만
        (None, None, _user(role="admin", part_id=None), True),
        (7, 8, _user(part_id=7), False),              # 하위에 직접 지정한 담당이 상위보다 우선
        (7, 8, _user(part_id=8), True),
    ])
    async def test_edit_rule_inherits_parent_owner(self, db, parent_owner, child_owner, user, allowed):
        db.ns(20, "제휴서비스", owner=parent_owner)
        db.ns(30, "배달의민족", owner=child_owner, parent=20)
        with _patch_conn(dep, db):
            if allowed:
                await dep.check_namespace_ownership("배달의민족", user)
            else:
                with pytest.raises(HTTPException) as e:
                    await dep.check_namespace_ownership("배달의민족", user)
                assert e.value.status_code == 403

    @pytest.mark.asyncio
    async def test_read_rule_inherits_parent_owner(self, db):
        db.ns(20, "제휴서비스", owner=7)
        db.ns(30, "배달의민족", parent=20)
        with _patch_conn(dep, db):
            await dep.check_namespace_read("배달의민족", _user(part_id=7))
            with pytest.raises(HTTPException):
                await dep.check_namespace_read("배달의민족", _user(part_id=8))   # 예전(자기 칸만)이면 "담당 없음 = 누구나"로 통과했다

    @pytest.mark.asyncio
    async def test_cross_part_read_log_uses_inherited_owner(self, db):
        db.ns(20, "제휴서비스", owner=7)
        db.ns(30, "배달의민족", parent=20)
        with _patch_conn(dep, db), patch.object(dep.logger, "warning") as warn:
            await dep.log_cross_part_namespace_read("배달의민족", _user(part_id=7))
            warn.assert_not_called()
            await dep.log_cross_part_namespace_read("배달의민족", _user(part_id=8))
            warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_query_log_delete_sql_inherits(self, db):
        """질의 기록 일괄 삭제(admin/router.py)의 담당 SQL — 라우터 원문 SQL을 그대로 꺼내 돌린다."""
        src = (ROOT / "service" / "admin" / "router.py").read_text(encoding="utf-8")
        body = src[src.index("async def bulk_delete_query_logs"):]
        sql = re.search(r'conn\.fetch\(\s*f"""(.*?)"""', body, re.S).group(1)
        sql = sql.replace("{effective_owner_ns_sql('n')}", database.effective_owner_ns_sql("n")).replace("{OWNER_ROWS_SQL}", database.OWNER_ROWS_SQL)
        db.ns(20, "제휴서비스", owner=[7, 9])
        db.ns(30, "배달의민족", parent=20)
        db.ns(40, "딜리버스", owner=8)
        db.ns(50, "담당없음")
        db.db.execute("INSERT INTO ops_query_log (id, namespace_id) VALUES (1, 30), (2, 40), (3, 20), (4, 50)")
        rows: dict = {}
        for r in await db.fetch(sql, [1, 2, 3, 4]):
            rows.setdefault(r["namespace"], set()).update({r["part_id"]} - {None})
        assert rows == {"배달의민족": {7, 9}, "딜리버스": {8}, "제휴서비스": {7, 9}, "담당없음": set()}
        assert dep.edit_denied_reason(rows["배달의민족"], _user(part_id=9)) is None   # 두 번째 담당 파트도
        assert dep.edit_denied_reason(rows["배달의민족"], _user(part_id=8)) is not None
        assert dep.edit_denied_reason(rows["담당없음"], _user(part_id=7)) is not None


# ── 담당 N개(#82, collection-design §15-8) ────────────────────────────────────

class TestMultiOwner:
    """담당 파트 중 어느 파트 사람이든 쓰기(주 담당 없음). 담당 없으면 관리자만. 하위에 자기 담당이 없으면 상위 담당 전부를 물려받는다.
    쓰기는 담당 표가 기준이고 옛 칸(owner_part_id)은 "가장 작은 part_id" 임시 거울 — 전/후 값을 표에서 직접 잰다."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("parent_owner, child_owner, user, allowed", [
        ([7, 8], None, _user(part_id=8), True),        # 상속: 상위의 두 번째 담당 파트도
        ([7, 8], None, _user(part_id=9), False),       # 어느 담당에도 없음 → 403
        (None, [7, 9], _user(part_id=9), True),        # 하위 직접 담당의 두 번째
        ([7], [8, 9], _user(part_id=7), False),        # 하위에 자기 담당이 있으면 상위 것은 안 물려받음
        (None, None, _user(part_id=7), False),         # 담당 없음 → 관리자만
        (None, None, _user(role="admin", part_id=None), True),
        ([7, 8], None, _user(role="viewer", part_id=7), False),
    ])
    async def test_edit_rule_any_owner(self, db, parent_owner, child_owner, user, allowed):
        db.ns(20, "제휴서비스", owner=parent_owner)
        db.ns(30, "배달의민족", owner=child_owner, parent=20)
        with _patch_conn(dep, db):
            if allowed:
                await dep.check_namespace_ownership("배달의민족", user)
            else:
                with pytest.raises(HTTPException) as e:
                    await dep.check_namespace_ownership("배달의민족", user)
                assert e.value.status_code == 403

    @pytest.mark.asyncio
    async def test_read_rule_and_log_any_owner(self, db):
        db.ns(20, "제휴서비스", owner=[7, 8])
        with _patch_conn(dep, db), patch.object(dep.logger, "warning") as warn:
            await dep.check_namespace_read("제휴서비스", _user(part_id=8))
            with pytest.raises(HTTPException):
                await dep.check_namespace_read("제휴서비스", _user(part_id=9))
            await dep.log_cross_part_namespace_read("제휴서비스", _user(part_id=8))
            warn.assert_not_called()
            await dep.log_cross_part_namespace_read("제휴서비스", _user(part_id=9))
            warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_settings_replace_owner_set(self, db, cleared):
        db.ns(20, "제휴서비스", owner=[7])
        db.ns(21, "딜리버스", owner=[8])
        with _patch_conn(admin_service, db):
            out = await admin_service.update_namespace_settings("제휴서비스", _settings(owner_part_ids=[9, 8, 8]))
        assert db.owners_of("제휴서비스") == [8, 9] and db.mirror_of("제휴서비스") == 8   # 7은 빠지고 8·9 — 거울 = 가장 작은 id
        assert db.owners_of("딜리버스") == [8]                                             # 다른 영역은 그대로
        assert [p["id"] for p in out["owner_parts"]] == [8, 9] and out["owner_part"] == "다른파트"
        assert cleared == []                                                               # 담당만 바꾸면 캐시 안 건드림
        with _patch_conn(admin_service, db):
            await admin_service.update_namespace_settings("제휴서비스", _settings(owner_part_ids=[]))
        assert db.owners_of("제휴서비스") == [] and db.mirror_of("제휴서비스") is None

    @pytest.mark.asyncio
    async def test_settings_unknown_part_changes_nothing(self, db):
        db.ns(20, "제휴서비스", owner=[7])
        with _patch_conn(admin_service, db), pytest.raises(ValueError, match="없는 파트"):
            await admin_service.update_namespace_settings("제휴서비스", _settings(owner_part_ids=[8, 999]))
        assert db.owners_of("제휴서비스") == [7] and db.mirror_of("제휴서비스") == 7

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kw, expect", [
        ({"owner_part": "다른파트"}, [8]),     # 옛 단일 칸 호환 — 그 파트 하나로 교체
        ({"owner_part": ""}, []),             # 빈 문자열 = 해제
        ({"clear_owner": True}, []),
        ({"owner_part": "다른파트", "owner_part_ids": [9]}, [9]),   # 둘 다 오면 목록이 우선
    ])
    async def test_settings_legacy_single_owner(self, db, kw, expect):
        db.ns(20, "제휴서비스", owner=[7, 9])
        with _patch_conn(admin_service, db):
            await admin_service.update_namespace_settings("제휴서비스", _settings(**kw))
        assert db.owners_of("제휴서비스") == expect and db.mirror_of("제휴서비스") == (min(expect) if expect else None)

    @pytest.mark.asyncio
    async def test_part_domains_adds_coowner_and_removes_only_self(self, db):
        """파트별 담당 지정: 체크 = 이 파트를 담당에 더함(다른 파트 담당 유지), 체크 해제 = 이 파트만 뺌."""
        db.ns(20, "제휴서비스", owner=[8])       # 다른 파트 담당 → 7이 함께 담당
        db.ns(21, "딜리버스", owner=[7, 8])      # 7이 빠져도 8은 남음
        db.ns(22, "혼자영역", owner=[7])         # 7이 빠지면 담당 없음
        db.ns(23, "무관", owner=[9])
        with _patch_conn(auth_service, db):
            out = await auth_service.set_part_domains(7, ["제휴서비스", "제휴서비스"])
        assert out == {"part_id": 7, "domains": ["제휴서비스"]}
        assert (db.owners_of("제휴서비스"), db.owners_of("딜리버스"), db.owners_of("혼자영역"), db.owners_of("무관")) == ([7, 8], [8], [], [9])
        assert (db.mirror_of("제휴서비스"), db.mirror_of("딜리버스"), db.mirror_of("혼자영역"), db.mirror_of("무관")) == (7, 8, None, 9)
        # 불변식: 다른 파트(8·9)의 담당 줄은 하나도 안 바뀐다
        assert db.db.execute("SELECT COUNT(*) FROM ops_namespace_owner WHERE part_id <> 7").fetchone()[0] == 3

    @pytest.mark.asyncio
    async def test_part_domains_unknown_domain_changes_nothing(self, db):
        db.ns(20, "제휴서비스", owner=[7])
        with _patch_conn(auth_service, db), pytest.raises(ValueError, match="없는 업무 영역"):
            await auth_service.set_part_domains(7, ["없는영역"])
        assert db.owners_of("제휴서비스") == [7]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("parent_owner, child_owner, included", [
        ([7, 8], None, True),        # 하위 담당 없음(상속)
        ([7, 8], [7, 8, 9], True),   # 상위 담당 전부가 하위에도 → 상위에서 쓸 수 있는 사람이 하위도 쓸 수 있음
        ([7, 8], [7], False),        # 상위 담당 8은 하위를 못 씀 → 빠짐
        (None, [8], False),          # 상위 담당 없음(관리자만) + 담당 있는 하위 → 빠짐(예전과 같음)
    ])
    async def test_write_scope_needs_parent_owners_in_child(self, db, parent_owner, child_owner, included):
        db.ns(20, "제휴서비스", owner=parent_owner)
        db.ns(30, "배달의민족", owner=child_owner, parent=20)
        ids = [r["id"] for r in await db.fetch(database.scope_ids_same_owner_subquery("$1"), 20)]
        assert sorted(ids) == ([20, 30] if included else [20])


# ── 목록 API: parent·실효 담당 ────────────────────────────────────────────────

class TestListDetail:
    @pytest.mark.asyncio
    async def test_parent_and_effective_owner(self, db):
        db.ns(20, "제휴서비스", owner=7)
        db.ns(30, "배달의민족", parent=20)
        db.ns(31, "쿠팡이츠", owner=8, parent=20)
        db.ns(10, "가딜리버스")
        with _patch_conn(admin_service, db):
            rows = await admin_service.list_namespaces_detail()
        by = {r["name"]: r for r in rows}
        assert [r["name"] for r in rows] == ["가딜리버스", "제휴서비스", "배달의민족", "쿠팡이츠"]   # 상위 바로 아래 하위
        assert (by["배달의민족"]["parent_id"], by["배달의민족"]["parent"]) == (20, "제휴서비스")
        # owner_part(_id) = 직접 지정 값 그대로(수정 창 미리 채우기용 — 리뷰 M3), 실효 담당은 effective_*
        assert by["배달의민족"]["owner_part_id"] is None and by["배달의민족"]["owner_part"] is None
        assert by["배달의민족"]["effective_owner_part"] == "온라인서비스"
        assert by["배달의민족"]["effective_owner_part_id"] == 7 and by["배달의민족"]["owner_inherited"]
        assert by["쿠팡이츠"]["owner_part"] == by["쿠팡이츠"]["effective_owner_part"] == "다른파트"
        assert not by["쿠팡이츠"]["owner_inherited"]
        assert by["제휴서비스"]["owner_part"] == by["제휴서비스"]["effective_owner_part"] == "온라인서비스"
        assert by["가딜리버스"]["effective_owner_part"] is None and by["가딜리버스"]["parent_id"] is None
        assert by["배달의민족"]["owner_parts"] == [] and by["배달의민족"]["effective_owner_parts"] == [{"id": 7, "name": "온라인서비스"}]

    @pytest.mark.asyncio
    async def test_multi_owner_lists(self, db):
        """담당 N개(#82) — owner_parts는 이름순 전부, 옛 단일 칸은 id가 가장 작은 하나(옛 칸 임시 거울과 같은 값)."""
        db.ns(20, "제휴서비스", owner=[9, 8])
        db.ns(30, "배달의민족", parent=20)
        db.ns(31, "쿠팡이츠", owner=[7], parent=20)
        with _patch_conn(admin_service, db):
            by = {r["name"]: r for r in await admin_service.list_namespaces_detail()}
        assert by["제휴서비스"]["owner_parts"] == [{"id": 8, "name": "다른파트"}, {"id": 9, "name": "세번째파트"}]
        assert by["제휴서비스"]["owner_part_id"] == 8 == db.mirror_of("제휴서비스")
        assert by["배달의민족"]["effective_owner_parts"] == by["제휴서비스"]["owner_parts"] and by["배달의민족"]["owner_inherited"]
        assert by["쿠팡이츠"]["effective_owner_parts"] == [{"id": 7, "name": "온라인서비스"}] and not by["쿠팡이츠"]["owner_inherited"]


# ── 생성·수정 검사(400)·삭제 409 ──────────────────────────────────────────────

def _settings(**kw):
    return types.SimpleNamespace(owner_part=kw.get("owner_part"), clear_owner=kw.get("clear_owner", False),
                                 owner_part_ids=kw.get("owner_part_ids"),
                                 parent_id=kw.get("parent_id"), clear_parent=kw.get("clear_parent", False))


class TestCreateUpdateDelete:
    @pytest.mark.asyncio
    async def test_create_child_under_top_level(self, db):
        db.ns(20, "제휴서비스")
        with _patch_conn(admin_service, db):
            out = await admin_service.create_namespace("배달의민족", parent_id=20)
        assert out["parent_id"] == 20 and db.parent_of("배달의민족") == 20

    @pytest.mark.asyncio
    async def test_create_under_child_rejected_depth_1(self, db):
        db.ns(20, "제휴서비스")
        db.ns(30, "배달의민족", parent=20)
        with _patch_conn(admin_service, db), pytest.raises(ValueError, match="최상위"):
            await admin_service.create_namespace("손자", parent_id=30)
        assert db.db.execute("SELECT COUNT(*) FROM ops_namespace WHERE name = '손자'").fetchone()[0] == 0

    @pytest.mark.asyncio
    async def test_create_with_missing_parent_rejected(self, db):
        with _patch_conn(admin_service, db), pytest.raises(ValueError, match="없는 상위"):
            await admin_service.create_namespace("배달의민족", parent_id=999)

    @pytest.mark.asyncio
    async def test_update_sets_parent(self, db, cleared):
        db.ns(20, "제휴서비스")
        db.ns(30, "배달의민족")
        with _patch_conn(admin_service, db):
            out = await admin_service.update_namespace_settings("배달의민족", _settings(parent_id=20))
        assert db.parent_of("배달의민족") == 20 and out["parent"] == "제휴서비스"
        assert set(cleared) == {"배달의민족", "제휴서비스"}                    # 자신 + 새 상위(검색 범위가 바뀜, 리뷰 M2)
        cleared.clear()
        with _patch_conn(admin_service, db):
            await admin_service.update_namespace_settings("배달의민족", _settings(clear_parent=True))
        assert db.parent_of("배달의민족") is None
        assert set(cleared) == {"배달의민족", "제휴서비스"}                    # 자신 + 옛 상위

    @pytest.mark.asyncio
    async def test_move_between_parents_clears_old_and_new(self, db, cleared):
        db.ns(20, "제휴서비스")
        db.ns(21, "외부서비스")
        db.ns(30, "배달의민족", parent=20)
        with _patch_conn(admin_service, db):
            await admin_service.update_namespace_settings("배달의민족", _settings(parent_id=21))
        assert db.parent_of("배달의민족") == 21
        assert set(cleared) == {"배달의민족", "제휴서비스", "외부서비스"}

    @pytest.mark.asyncio
    async def test_owner_only_change_does_not_touch_cache(self, db, cleared):
        db.ns(20, "제휴서비스")
        with _patch_conn(admin_service, db):
            await admin_service.update_namespace_settings("제휴서비스", _settings(owner_part="온라인서비스"))
        assert cleared == []

    @pytest.mark.asyncio
    async def test_parent_set_locks_both_rows_in_id_order(self, db):
        """"X를 Y 아래로"·"Y를 X 아래로"가 동시에 와도 교착이 없게 — 두 행을 한 문장에서 id 오름차순으로 잠근다(리뷰 L1).
        (잠금 대기 자체는 sqlite로 재현할 수 없어 첫 잠금 문장의 모양을 본다.)"""
        db.ns(20, "제휴서비스")
        db.ns(30, "배달의민족")
        with _patch_conn(admin_service, db):
            await admin_service.update_namespace_settings("배달의민족", _settings(parent_id=20))
        locks = [q for q in db.sqls if "FOR UPDATE" in q]
        assert "WHERE name = $1 OR id = $2 ORDER BY id FOR UPDATE" in locks[0]
        assert not any("WHERE name = $1 FOR UPDATE" in q for q in locks)   # 이 영역만 먼저 잡는 문장이 없다

    @pytest.mark.asyncio
    async def test_update_self_parent_rejected(self, db):
        db.ns(20, "제휴서비스")
        with _patch_conn(admin_service, db), pytest.raises(ValueError, match="자기 자신"):
            await admin_service.update_namespace_settings("제휴서비스", _settings(parent_id=20))
        assert db.parent_of("제휴서비스") is None

    @pytest.mark.asyncio
    async def test_update_parent_must_be_top_level(self, db):
        db.ns(20, "제휴서비스")
        db.ns(30, "배달의민족", parent=20)
        db.ns(40, "딜리버스")
        with _patch_conn(admin_service, db), pytest.raises(ValueError, match="최상위"):
            await admin_service.update_namespace_settings("딜리버스", _settings(parent_id=30))
        assert db.parent_of("딜리버스") is None

    @pytest.mark.asyncio
    async def test_domain_with_children_cannot_become_child(self, db):
        db.ns(20, "제휴서비스")
        db.ns(30, "배달의민족", parent=20)
        db.ns(40, "딜리버스")
        with _patch_conn(admin_service, db), pytest.raises(ValueError, match="하위 영역이 1개"):
            await admin_service.update_namespace_settings("제휴서비스", _settings(parent_id=40))
        assert db.parent_of("제휴서비스") is None

    @pytest.mark.asyncio
    async def test_delete_with_children_is_409_and_keeps_rows(self, db, cleared):
        db.ns(20, "제휴서비스")
        db.ns(30, "배달의민족", parent=20)
        db.ns(31, "쿠팡이츠", parent=20)
        with _patch_conn(admin_service, db):
            status, detail = await admin_service.delete_namespace("제휴서비스")
        assert status == 409 and "하위 영역 2개" in detail
        assert cleared == []
        assert db.db.execute("SELECT COUNT(*) FROM ops_namespace").fetchone()[0] == 3
        assert db.parent_of("배달의민족") == 20                       # 조용히 최상위가 되지 않음

    @pytest.mark.asyncio
    async def test_delete_child_then_parent_ok(self, db, cleared):
        db.ns(20, "제휴서비스")
        db.ns(30, "배달의민족", parent=20)
        with _patch_conn(admin_service, db):
            assert (await admin_service.delete_namespace("배달의민족"))[0] == 204
            assert cleared == ["배달의민족", "제휴서비스"]                 # 지운 하위 + 그 상위(지운 뒤엔 DB에서 못 찾아 미리 읽음)
            assert (await admin_service.delete_namespace("제휴서비스"))[0] == 204
        assert db.db.execute("SELECT COUNT(*) FROM ops_namespace").fetchone()[0] == 0


# ── VOC 커버리지 범위(리뷰 L5: SQL 실행) ─────────────────────────────────────

class TestVocCoverageScope:
    """VOC 반복 유형 커버리지는 search_knowledge가 아니라 자체 LATERAL SQL이다(설계 문서의 "자동으로 따라감" 전제와 다름).
    본문 SQL은 pgvector(<=>)·array_agg(...)[1]·json_build_object라 sqlite로 통째 실행이 안 된다 — 그래서 ① 두 SQL이 범위 조각을
    정확히 그 모양으로 품고 있는지 보고 ② 그 조각을 같은 상관 조건(c.namespace_id)으로 sqlite에서 실제로 돌린다."""

    def test_both_coverage_sqls_use_shared_scope_fragment(self):
        pd_src = (ROOT / "service" / "email_voc" / "pattern_detection.py").read_text(encoding="utf-8")
        assert "k.namespace_id = c.namespace_id" not in pd_src
        assert pd_src.count("WHERE k.namespace_id IN ({_VOC_SCOPE})") == 2
        assert '_VOC_SCOPE = scope_ids_subquery("c.namespace_id")' in pd_src

    @pytest.mark.asyncio
    async def test_fragment_correlated_on_cluster(self, db):
        db.ns(20, "제휴서비스")
        db.ns(30, "배달의민족", parent=20)
        db.ns(31, "e2e하위", parent=20, kind="policy_test")
        db.ns(40, "딜리버스")
        db.db.execute("INSERT INTO ops_voc_cluster (id, namespace_id) VALUES (1, 20), (2, 30), (3, 40)")
        db.db.execute("INSERT INTO rag_knowledge (id, namespace_id, status) VALUES (100, 20, 'active'), (101, 30, 'active'), "
                      "(102, 31, 'active'), (103, 40, 'active')")
        sql = (f"SELECT c.id AS cid, k.id AS kid FROM ops_voc_cluster c JOIN rag_knowledge k "
               f"ON k.namespace_id IN ({database.scope_ids_subquery('c.namespace_id')}) ORDER BY c.id, k.id")
        pairs = [(r["cid"], r["kid"]) for r in await db.fetch(sql)]
        assert pairs == [(1, 100), (1, 101), (2, 101), (3, 103)]   # 상위 클러스터 = 자신 + 하위(테스트용 하위 제외), 하위 = 자신만


# ── 정정 원장: 신고 대상 범위(리뷰 M1) ────────────────────────────────────────

class TestImprovementScope:
    """상위 영역에서 물으면 하위 지식·정책도 근거로 나온다 — 그 근거를 신고·정정 대상으로 받아야 하고, 무관한 영역 것은 계속 거부."""

    def _seed(self, db, parent_owner=None, child_owner=None):
        db.ns(20, "제휴서비스", owner=parent_owner)
        db.ns(30, "배달의민족", owner=child_owner, parent=20)
        db.ns(40, "딜리버스")
        db.db.execute("INSERT INTO rag_knowledge (id, namespace_id, status, content) VALUES "
                      "(100, 20, 'active', '상위 지식'), (101, 30, 'active', '하위 지식'), (103, 40, 'active', '무관 지식')")
        db.db.execute("INSERT INTO policy_item (id, namespace_id, status, policy_name, raw_body) VALUES "
                      "(500, 30, 'active', '하위 정책', '원문'), (501, 40, 'active', '무관 정책', '원문')")
        db.db.execute("INSERT INTO policy_chunk (id, policy_item_id, chunk_text) VALUES (900, 500, '조각'), (901, 501, '조각')")

    @pytest.mark.asyncio
    async def test_parent_report_accepts_child_knowledge_and_policy(self, db):
        self._seed(db)
        assert (await improvement._load_original(db, 20, "knowledge", 101, None, None))["content"] == "하위 지식"
        assert (await improvement._load_original(db, 20, "policy_narrative", 500, 900, None))["chunk_text"] == "조각"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ns_id, target_type, target_id, sub_id", [
        (20, "knowledge", 103, None),              # 상위 신고 + 무관 영역 지식 → 거부(권한 구멍 없음)
        (20, "policy_narrative", 501, 901),
        (30, "knowledge", 100, None),              # 하위 신고 + 상위 지식 → 거부(하위 범위는 자신만)
    ])
    async def test_out_of_scope_rejected(self, db, ns_id, target_type, target_id, sub_id):
        self._seed(db)
        with pytest.raises(ValueError):
            await improvement._load_original(db, ns_id, target_type, target_id, sub_id, None)

    @pytest.mark.asyncio
    async def test_candidates_keep_child_and_drop_unrelated(self, db):
        self._seed(db)
        db.db.execute("INSERT INTO ops_message (id, results, metadata) VALUES (1, ?, ?)",
                      (json.dumps([{"id": 100}, {"id": 101}, {"id": 103}]),
                       json.dumps({"policy_citations": [{"kind": "narrative", "item_id": 500, "chunk_id": 900},
                                                        {"kind": "narrative", "item_id": 501, "chunk_id": 901}]})))
        keys = [c["key"] for c in await improvement._load_candidates(db, 20, 1)]
        assert keys == ["k-100", "k-101", "pn-900"]   # 예전(영역 하나)엔 k-101·pn-900이 조용히 빠졌다

    @pytest.mark.asyncio
    async def test_apply_keeps_row_namespace(self):
        """상위 신고로 하위 지식을 고쳐도 새 버전은 원래 행의 영역(하위)에 — 신고한 영역으로 옮기지 않는다."""
        conn = MagicMock()
        old = {"id": 101, "namespace_id": 30, "status": "active", "base_weight": 1.0, "category": None, "created_by_user_id": 1,
               "source_file": None, "source_chunk_idx": None, "source_type": "manual", "ingestion_job_id": None,
               "logical_document_id": None, "version": 1, "owner": None, "confluence_page_id": None,
               "confluence_version": None, "heading_path": None}
        conn.fetchrow = AsyncMock(return_value=old)
        conn.fetchval = AsyncMock(return_value=200)
        conn.execute = AsyncMock()
        await improvement._apply_knowledge(conn, 20, 101, "새 본문", [0.1])
        sel_sql, *sel_args = conn.fetchrow.await_args.args
        assert database.scope_ids_same_owner_subquery("$2") in sel_sql and sel_args == [101, 20]
        ins_sql, *ins_args = conn.fetchval.await_args.args
        assert "INSERT INTO rag_knowledge" in ins_sql and ins_args[0] == 30


class TestImprovementOwnerScope:
    """정정(쓰기) 범위는 담당이 같은 하위만(리뷰 H1) — 권한은 신고 영역 담당만 보므로, 다른 파트가 직접 맡은 하위 근거를 상위에서
    고치게 두면 상위 담당 파트가 다른 파트 지식·정책을 바꿀 수 있었다. 검색(읽기) 범위는 그대로 넓다."""

    _seed = TestImprovementScope._seed

    @pytest.mark.asyncio
    @pytest.mark.parametrize("parent_owner, child_owner", [(7, None), (7, 7), (None, None)])
    async def test_same_owner_child_included(self, db, parent_owner, child_owner):
        """(1) 하위 담당 비어 있음(상속) (3) 하위 담당 = 상위와 같은 파트 → 상위 신고로 하위 근거 포함."""
        self._seed(db, parent_owner, child_owner)
        assert (await improvement._load_original(db, 20, "knowledge", 101, None, None))["content"] == "하위 지식"
        assert (await improvement._load_original(db, 20, "policy_narrative", 500, 900, None))["chunk_text"] == "조각"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("parent_owner, child_owner", [(7, 8), (None, 8)])
    async def test_other_owner_child_excluded_everywhere(self, db, parent_owner, child_owner):
        """(2) 다른 파트가 직접 맡은 하위 → 후보 제외·신고 거부·직접 제안 거부·승인(적용) 거부. 그 하위 영역에서 신고하면 된다."""
        self._seed(db, parent_owner, child_owner)
        with pytest.raises(ValueError):
            await improvement._load_original(db, 20, "knowledge", 101, None, None)
        with pytest.raises(ValueError):
            await improvement._load_original(db, 20, "policy_narrative", 500, 900, None)
        db.db.execute("INSERT INTO ops_message (id, results, metadata) VALUES (1, ?, ?)",
                      (json.dumps([{"id": 100}, {"id": 101}]),
                       json.dumps({"policy_citations": [{"kind": "narrative", "item_id": 500, "chunk_id": 900}]})))
        assert [c["key"] for c in await improvement._load_candidates(db, 20, 1)] == ["k-100"]
        # 직접 제안(정책 화면 policy_direct) — 첫 확인에서 거부, 원장에 아무것도 안 씀
        with _patch_conn(improvement, db), patch.object(improvement, "resolve_namespace_id", AsyncMock(return_value=20)),                 pytest.raises(ValueError, match="고칠 정책을 찾을 수 없거나"):
            await improvement.create_policy_proposal("제휴서비스", {"id": 1}, item_id=500, mode=improvement.SOURCE_POLICY_DIRECT,
                                                     proposed={"raw_body": "새 원문"}, user_input=None)
        # 승인(적용) — 원본 행을 범위로 다시 찾으므로 거부(신고 뒤에 하위 담당이 바뀐 경우도 여기서 막힌다)
        with pytest.raises(ValueError, match="원본 지식"):
            await improvement._apply_knowledge(db, 20, 101, "새 본문", [0.1])
        item = {"target_id": 500, "target_type": "policy_narrative", "target_sub_id": 900}
        with pytest.raises(ValueError, match="원본 정책"):
            await improvement._apply_policy(db, 20, item, {"chunk_text": "x", "raw_body": "x"}, {"id": 1}, {})
        with _patch_conn(improvement, db), pytest.raises(ValueError, match="원본 정책"):
            await improvement._prepare_policy(20, item, {"chunk_text": "x", "raw_body": "x"})
        # 그 하위 영역에서 신고하면 받는다
        assert (await improvement._load_original(db, 30, "knowledge", 101, None, None))["content"] == "하위 지식"

    @pytest.mark.asyncio
    async def test_search_scope_stays_wide(self, db):
        """읽기(검색) 범위는 담당과 무관하게 하위 포함 — 지식 조회는 누구나."""
        self._seed(db, 7, 8)
        assert await database.namespace_scope_ids(db, "제휴서비스") == [20, 30]


class TestScopeFragmentGuard:
    """범위 조각은 f-string으로 SQL에 들어간다 — 자리표시($n)·코드 상수만 받는다(리뷰 L-b)."""

    @pytest.mark.parametrize("ok", ["$1", "$2", "$12", "c.namespace_id", database.ME_BY_NAME_1])
    def test_allowed(self, ok):
        assert ok in database.scope_ids_subquery(ok) and ok in database.scope_ids_same_owner_subquery(ok)

    @pytest.mark.parametrize("bad", ["1", "$1; DROP TABLE x", "$", "c.namespace_id OR 1=1", "'제휴서비스'", "", None, 3])
    def test_rejected(self, bad):
        with pytest.raises(ValueError):
            database.scope_ids_subquery(bad)
        with pytest.raises(ValueError):
            database.scope_ids_same_owner_subquery(bad)


@pytest.mark.asyncio
async def test_cache_family_clears_parent_of_child(db, cleared):
    """쓰기 경로 공용 캐시 비우기 — 하위 이름을 주면 상위 이름도(리뷰 M2)."""
    db.ns(20, "제휴서비스")
    db.ns(30, "배달의민족", parent=20)
    await real_cache.invalidate_namespace_family("배달의민족")
    assert cleared == ["배달의민족", "제휴서비스"]
