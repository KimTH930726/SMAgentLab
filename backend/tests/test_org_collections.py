"""조직 컬렉션(kind='org_space', 2026-10-22 #83, collection-design §15-13 · org-space-design §3).

- 담당: 파트 컬렉션 = 그 파트(담당 표 한 줄), 팀 공통 = 그 팀의 **모든** 파트(담당 표 줄 없음, OWNER_ROWS_SQL) — 권한 규칙은 하나(edit_denied_reason)
- 팀·파트를 만들면 같은 트랜잭션에서 조직 컬렉션이 생기고, 지울 때 지식·용어가 있으면 거부(없으면 같이 지움)
- 조직 컬렉션엔 정책·시스템 정보 쓰기 거부(400), 업무 컬렉션 관리 API(이름·담당·상위·삭제)로는 못 바꿈
- 검색 범위: 조직 컬렉션을 고르면 그 컬렉션 하나, 업무 컬렉션 + 내 조직 컬렉션은 설정(chat.org_space_scope) 꺼짐이 기본

test_namespace_tree.py의 sqlite 어댑터(같은 모양의 표에서 실제 SQL을 돌림)를 그대로 쓴다.
"""
import pytest
from fastapi import HTTPException

from tests.test_namespace_tree import _SqliteConn, _patch_conn, _user, admin_service, auth_service, database, dep


@pytest.fixture
def db():
    c = _SqliteConn()
    c.db.execute("INSERT INTO ops_team (id, name) VALUES (1, '스타벅스CSP팀'), (2, '다른팀'), (3, '빈팀')")
    c.db.execute("INSERT INTO ops_part (id, name, team_id) VALUES (7, '온라인서비스', 1), (8, '온라인스토어', 1), (9, '다른팀파트', 2)")
    return c


def _org_team(db, ns_id, team_id):
    db.db.execute("INSERT INTO ops_namespace (id, name, kind, owner_team_id) VALUES (?, ?, 'org_space', ?)",
                  (ns_id, database.org_team_key(team_id), team_id))


def _org_part(db, ns_id, part_id):
    db.db.execute("INSERT INTO ops_namespace (id, name, kind, owner_part_id) VALUES (?, ?, 'org_space', ?)",
                  (ns_id, database.org_part_key(part_id), part_id))
    db.db.execute("INSERT INTO ops_namespace_owner (namespace_id, part_id) VALUES (?, ?)", (ns_id, part_id))


def _org_rows(db):
    return {r[0]: r[1] for r in db.db.execute("SELECT name, owner_team_id FROM ops_namespace WHERE kind = 'org_space' ORDER BY name")}


# ── 담당(권한) — 한 곳의 SQL로 ──────────────────────────────────────────────────

class TestOwners:
    @pytest.mark.asyncio
    async def test_team_collection_owned_by_every_part_of_team(self, db):
        _org_team(db, 100, 1)
        with _patch_conn(dep, db):
            assert await dep._effective_owner_ids("org:team:1") == [7, 8]
            await dep.check_namespace_ownership("org:team:1", _user(part_id=7))
            await dep.check_namespace_ownership("org:team:1", _user(part_id=8))
            with pytest.raises(HTTPException) as e:
                await dep.check_namespace_ownership("org:team:1", _user(part_id=9))   # 다른 팀 파트
            assert e.value.status_code == 403
            await dep.check_namespace_ownership("org:team:1", _user(role="admin", part_id=None))

    @pytest.mark.asyncio
    async def test_team_owners_follow_part_moves(self, db):
        """팀 공통 담당은 저장값이 아니라 그 팀의 지금 파트 — 파트가 팀을 옮기면 담당도 따라간다(따로 고칠 데이터 없음)."""
        _org_team(db, 100, 1)
        _org_team(db, 101, 2)
        db.db.execute("UPDATE ops_part SET team_id = 1 WHERE id = 9")
        with _patch_conn(dep, db):
            assert await dep._effective_owner_ids("org:team:1") == [7, 8, 9]
            assert await dep._effective_owner_ids("org:team:2") == []
            with pytest.raises(HTTPException):   # 파트 없는 팀 공통 = 담당 없음 → 관리자만(업무 컬렉션과 같은 규칙)
                await dep.check_namespace_ownership("org:team:2", _user(part_id=9))

    @pytest.mark.asyncio
    async def test_part_collection_owned_by_that_part_only(self, db):
        _org_part(db, 200, 7)
        with _patch_conn(dep, db):
            assert await dep._effective_owner_ids("org:part:7") == [7]
            await dep.check_namespace_ownership("org:part:7", _user(part_id=7))
            with pytest.raises(HTTPException):   # 같은 팀 다른 파트도 남의 파트 컬렉션은 못 쓴다
                await dep.check_namespace_ownership("org:part:7", _user(part_id=8))
            with pytest.raises(HTTPException):
                await dep.check_namespace_ownership("org:part:7", _user(role="viewer", part_id=7))

    @pytest.mark.asyncio
    async def test_business_owners_unchanged(self, db):
        """업무 컬렉션 담당은 예전 그대로 — 팀 공통 줄이 섞여 들지 않는다."""
        db.ns(20, "딜리버스", owner=7)
        db.ns(30, "하위", parent=20)
        _org_team(db, 100, 1)
        with _patch_conn(dep, db):
            assert await dep._effective_owner_ids("딜리버스") == [7]
            assert await dep._effective_owner_ids("하위") == [7]

    @pytest.mark.asyncio
    async def test_bulk_query_log_delete_sees_team_owners(self, db):
        """질의 기록 일괄 삭제(admin/router.py)도 같은 원천 — 라우터 원문 SQL을 그대로 꺼내 돌린다."""
        import re
        from tests.test_namespace_tree import ROOT
        src = (ROOT / "service" / "admin" / "router.py").read_text(encoding="utf-8")
        body = src[src.index("async def bulk_delete_query_logs"):]
        sql = re.search(r'conn\.fetch\(\s*f"""(.*?)"""', body, re.S).group(1)
        sql = sql.replace("{effective_owner_ns_sql('n')}", database.effective_owner_ns_sql("n")).replace("{OWNER_ROWS_SQL}", database.OWNER_ROWS_SQL)
        _org_team(db, 100, 1)
        db.db.execute("INSERT INTO ops_query_log (id, namespace_id) VALUES (1, 100)")
        parts = {r["part_id"] for r in await db.fetch(sql, [1])}
        assert parts == {7, 8}


# ── 만들기·지우기 — 팀·파트와 같은 트랜잭션 ─────────────────────────────────────────

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_create_team_and_part_create_org_collections(self, db):
        with _patch_conn(auth_service, db):
            team = await auth_service.create_team("새팀")
            part = await auth_service.create_part("새파트", team["id"])
            assert await auth_service.create_team("새팀") is None   # 겹치면 아무것도 안 만든다
        assert _org_rows(db) == {f"org:team:{team['id']}": team["id"], f"org:part:{part['id']}": None}
        assert db.owners_of(f"org:part:{part['id']}") == [part["id"]]
        assert db.owners_of(f"org:team:{team['id']}") == []   # 팀 공통은 담당 표 줄 없음
        assert db.mirror_of(f"org:part:{part['id']}") == part["id"]   # 옛 칸 임시 거울도 같은 값

    @pytest.mark.asyncio
    async def test_create_is_idempotent(self, db):
        with _patch_conn(auth_service, db):
            await database.create_org_collection(db, part_id=7)
            await database.create_org_collection(db, part_id=7)
            await database.create_org_collection(db, team_id=1)
            await database.create_org_collection(db, team_id=1)
        assert _org_rows(db) == {"org:part:7": None, "org:team:1": 1}
        assert db.owners_of("org:part:7") == [7]
        with pytest.raises(ValueError):
            await database.create_org_collection(db, team_id=1, part_id=7)

    @pytest.mark.asyncio
    async def test_delete_part_refused_when_org_has_knowledge(self, db):
        _org_part(db, 200, 7)
        db.db.execute("INSERT INTO rag_knowledge (id, namespace_id, status, content) VALUES (1, 200, 'active', 'x')")
        with _patch_conn(auth_service, db), pytest.raises(auth_service.OrgCollectionInUse, match="지식 1건"):
            await auth_service.delete_part(7)
        assert _org_rows(db) == {"org:part:7": None}
        assert db.db.execute("SELECT COUNT(*) FROM ops_part WHERE id = 7").fetchone()[0] == 1

    @pytest.mark.asyncio
    async def test_delete_part_refused_when_org_has_glossary_or_pending(self, db):
        _org_part(db, 200, 7)
        _org_part(db, 201, 8)
        db.db.execute("INSERT INTO rag_glossary (id, namespace_id) VALUES (1, 200)")
        db.db.execute("INSERT INTO rag_knowledge (id, namespace_id, status, content) VALUES (2, 201, 'pending_review', 'x')")
        with _patch_conn(auth_service, db):
            with pytest.raises(auth_service.OrgCollectionInUse, match="용어 1건"):
                await auth_service.delete_part(7)
            with pytest.raises(auth_service.OrgCollectionInUse, match="지식 1건"):   # 검토 대기도 지우면 사라지는 내용
                await auth_service.delete_part(8)

    @pytest.mark.asyncio
    async def test_delete_empty_part_removes_its_org_collection(self, db):
        """자기 조직 컬렉션의 담당 한 줄은 "담당 업무가 있어 삭제 불가"로 세지 않는다 — 지운 지식만 있으면 같이 지운다."""
        _org_part(db, 200, 7)
        db.db.execute("INSERT INTO rag_knowledge (id, namespace_id, status, content) VALUES (3, 200, 'deleted', 'x')")
        with _patch_conn(auth_service, db):
            assert await auth_service.delete_part(7) is True
        assert _org_rows(db) == {}
        assert db.db.execute("SELECT COUNT(*) FROM ops_part WHERE id = 7").fetchone()[0] == 0

    @pytest.mark.asyncio
    async def test_delete_part_keeps_business_refusal(self, db):
        _org_part(db, 200, 7)
        db.ns(20, "딜리버스", owner=7)
        with _patch_conn(auth_service, db):
            assert await auth_service.delete_part(7) is False   # 업무 컬렉션 담당은 예전처럼 거부
        assert _org_rows(db) == {"org:part:7": None}

    @pytest.mark.asyncio
    async def test_delete_team(self, db):
        _org_team(db, 100, 3)
        _org_team(db, 101, 2)
        db.db.execute("UPDATE ops_part SET team_id = NULL WHERE id = 9")
        db.db.execute("INSERT INTO rag_glossary (id, namespace_id) VALUES (1, 101)")
        with _patch_conn(auth_service, db):
            assert await auth_service.delete_team(3) is True
            with pytest.raises(auth_service.OrgCollectionInUse):
                await auth_service.delete_team(2)
            assert await auth_service.delete_team(1) is False   # 파트가 남은 팀은 예전처럼 거부
        assert _org_rows(db) == {"org:team:2": 2}

    @pytest.mark.asyncio
    async def test_set_part_domains_never_touches_org(self, db):
        """파트 담당 업무 저장은 업무 컬렉션만 — 자기 파트 컬렉션 담당 줄을 지우거나 남의 조직 컬렉션에 담당을 넣지 못한다."""
        _org_part(db, 200, 7)
        _org_part(db, 201, 8)
        db.ns(20, "딜리버스")
        with _patch_conn(auth_service, db):
            out = await auth_service.set_part_domains(7, ["딜리버스"])
            assert out["domains"] == ["딜리버스"]
            assert db.owners_of("org:part:7") == [7]
            with pytest.raises(ValueError, match="없는 업무 영역"):
                await auth_service.set_part_domains(7, ["org:part:8"])
            out = await auth_service.set_part_domains(7, [])   # 다 빼도 자기 파트 컬렉션 담당은 남는다
            assert out["domains"] == []
        assert db.owners_of("org:part:7") == [7]
        assert db.owners_of("org:part:8") == [8]
        # (list_parts의 담당 업무 목록 제외는 array_agg라 sqlite로 못 돌린다 — 실서버 시나리오 org_collections.py에서 확인)


# ── 목록·관리 API ───────────────────────────────────────────────────────────────

class TestAdmin:
    @pytest.mark.asyncio
    async def test_detail_display_name_team_and_owners(self, db):
        db.ns(20, "딜리버스", owner=7)
        _org_team(db, 100, 1)
        _org_part(db, 200, 8)
        _org_part(db, 201, 9)
        with _patch_conn(admin_service, db):
            by = {r["name"]: r for r in await admin_service.list_namespaces_detail()}
        assert (by["딜리버스"]["display_name"], by["딜리버스"]["team_id"], by["딜리버스"]["kind"]) == ("딜리버스", None, "domain")
        t = by["org:team:1"]
        assert (t["display_name"], t["team_id"], t["owner_team_id"], t["kind"]) == ("팀 공통 · 스타벅스CSP팀", 1, 1, "org_space")
        assert sorted(p["id"] for p in t["effective_owner_parts"]) == [7, 8]   # 팀 공통 = 그 팀 파트 전부(권한 규칙과 같음)
        p = by["org:part:8"]
        assert (p["display_name"], p["team_id"], p["owner_team_id"]) == ("온라인스토어", 1, None)
        assert p["effective_owner_parts"] == [{"id": 8, "name": "온라인스토어"}]
        assert (by["org:part:9"]["team_id"], by["org:part:9"]["team"]) == (2, "다른팀")
        assert by["딜리버스"]["team"] is None

    @pytest.mark.asyncio
    async def test_business_admin_api_refuses_org(self, db):
        _org_part(db, 200, 7)
        db.ns(20, "딜리버스")
        with _patch_conn(admin_service, db):
            assert (await admin_service.rename_namespace("org:part:7", "새이름"))[0] == 400
            assert (await admin_service.rename_namespace("딜리버스", "org:team:9"))[0] == 400
            assert (await admin_service.delete_namespace("org:part:7"))[0] == 400
            with pytest.raises(ValueError):
                await admin_service.create_namespace("org:part:99")
            with pytest.raises(ValueError, match="상위 영역이 될 수 없"):
                await admin_service.create_namespace("하위", parent_id=200)

            class _Body:
                owner_part_ids = [8]
                owner_part = None
                clear_owner = False
                parent_id = None
                clear_parent = False
            with pytest.raises(ValueError):
                await admin_service.update_namespace_settings("org:part:7", _Body())
        assert db.owners_of("org:part:7") == [7]
        assert _org_rows(db) == {"org:part:7": None}


# ── 종류 거부 · 검색 범위 ────────────────────────────────────────────────────────

class TestKindAndScope:
    @pytest.mark.asyncio
    async def test_reject_org_collection(self, db):
        _org_part(db, 200, 7)
        db.ns(20, "딜리버스")
        with _patch_conn(dep, db):
            with pytest.raises(HTTPException) as e:
                await dep.reject_org_collection("org:part:7", "정책")
            assert e.value.status_code == 400 and "조직 컬렉션" in e.value.detail
            assert await dep.reject_org_collection("딜리버스", "정책") is None
            assert await dep.reject_org_collection("없는이름", "정책") is None

    @pytest.mark.asyncio
    async def test_org_collection_scope_is_itself(self, db):
        _org_part(db, 200, 7)
        assert await database.namespace_scope_ids(db, "org:part:7") == [200]

    @pytest.mark.asyncio
    async def test_org_space_scope_flag_off_by_default(self, db):
        db.ns(20, "딜리버스")
        _org_team(db, 100, 1)
        _org_part(db, 200, 7)
        _org_part(db, 201, 9)
        assert await database.org_space_scope_extra_ids(db, "딜리버스", 7) == []   # 설정 없음 = 꺼짐
        db.db.execute("INSERT INTO ops_system_config (key, value) VALUES ('chat.org_space_scope', 'false')")
        assert await database.org_space_scope_extra_ids(db, "딜리버스", 7) == []
        db.db.execute("UPDATE ops_system_config SET value = 'true' WHERE key = 'chat.org_space_scope'")
        assert await database.org_space_scope_extra_ids(db, "딜리버스", 7) == [100, 200]   # 내 파트 + 내 팀 공통(남의 파트 201 제외)
        assert await database.org_space_scope_extra_ids(db, "org:part:7", 7) == []          # 조직 컬렉션을 직접 고르면 더하지 않음
        assert await database.org_space_scope_extra_ids(db, "딜리버스", None) == []         # 파트 없는 슈퍼어드민

    def test_display_name(self):
        assert database.org_display_name(1, "스타벅스CSP팀", None) == "팀 공통 · 스타벅스CSP팀"
        assert database.org_display_name(None, None, "온라인서비스 파트") == "온라인서비스 파트"
