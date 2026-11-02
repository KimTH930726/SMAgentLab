"""팀·파트와 컬렉션(11/02 조직 컬렉션 폐지 — 팀·파트는 로그인·권한·담당만).

- 불변식: 팀·파트를 만들거나 옮기거나 지워도 컬렉션(ops_namespace)·담당 줄은 저절로 생기거나 사라지지 않는다
- 담당은 담당 표(ops_namespace_owner) 그대로 — 팀에 든 파트라는 이유로 담당이 늘지 않는다
- 화면에 안 나오는 내부 자리(팀 적재 보관 'staging' · 폐지된 조직 컬렉션 남은 행 'org_space')는 목록·담당 지정·관리 API에서 빠진다
- 정책은 정책 컬렉션에만(시스템 컬렉션·내부 자리엔 400)

test_namespace_tree.py의 sqlite 어댑터(같은 모양의 표에서 실제 SQL을 돌림)를 그대로 쓴다.
"""
import random

import pytest
from fastapi import HTTPException

from tests.test_namespace_tree import _SqliteConn, _patch_conn, _user, admin_service, auth_service, database, dep


@pytest.fixture
def db():
    c = _SqliteConn()
    c.db.execute("INSERT INTO ops_team (id, name) VALUES (1, '스타벅스CSP팀'), (2, '다른팀'), (3, '빈팀')")
    c.db.execute("INSERT INTO ops_part (id, name, team_id) VALUES (7, '온라인서비스', 1), (8, '온라인스토어', 1), (9, '다른팀파트', 2)")
    return c


def _snapshot(db):
    return (db.db.execute("SELECT id, name, kind FROM ops_namespace ORDER BY id").fetchall(),
            db.db.execute("SELECT namespace_id, part_id FROM ops_namespace_owner ORDER BY 1, 2").fetchall())


def _hidden(db):
    """내부 자리 두 종류 — 팀 적재 보관(staging)과 내용이 남아 못 지운 옛 조직 컬렉션(org_space), 각각 파트 7 담당 줄 하나."""
    db.ns(900, "stage:import", kind=database.STAGING_KIND)
    db.ns(901, "org:part:7", owner=7, kind=database.RETIRED_ORG_KIND)


class TestTeamPartMakeNoCollections:
    @pytest.mark.asyncio
    async def test_random_team_part_ops_never_touch_collections(self, db):
        """무작위 팀·파트 만들기·옮기기·지우기 — 컬렉션 표·담당 표는 한 줄도 안 바뀐다(옛 #83은 팀·파트마다 컬렉션을 만들었다)."""
        db.ns(20, "딜리버스", owner=7)
        db.ns(30, "하위", parent=20)
        before = _snapshot(db)
        rnd = random.Random(5)
        with _patch_conn(auth_service, db):
            teams, parts = [1, 2, 3], [7, 8, 9]
            for i in range(60):
                op = rnd.choice(["team", "part", "move", "del_part", "del_team"])
                if op == "team":
                    t = await auth_service.create_team(f"팀{i}")
                    teams.append(t["id"])
                elif op == "part":
                    p = await auth_service.create_part(f"파트{i}", rnd.choice(teams))
                    parts.append(p["id"])
                elif op == "move" and parts:
                    await auth_service.set_part_team(rnd.choice(parts), rnd.choice(teams))
                elif op == "del_part" and parts:
                    pid = rnd.choice(parts)
                    if await auth_service.delete_part(pid):
                        parts.remove(pid)
                elif op == "del_team" and teams:
                    tid = rnd.choice(teams)
                    if await auth_service.delete_team(tid):
                        teams.remove(tid)
        assert _snapshot(db) == before

    @pytest.mark.asyncio
    async def test_delete_team_rules(self, db):
        with _patch_conn(auth_service, db):
            assert await auth_service.delete_team(3) is True       # 빈 팀
            assert await auth_service.delete_team(1) is False      # 파트가 남은 팀은 거부
            assert await auth_service.delete_team(999) is None
            # 내용 때문에 #106이 남긴 옛 팀 공통 조직 컬렉션(org:team:<id>)이 있으면 지우지 않는다 — FK 위반 500 대신 409(리뷰 M1)
            db.db.execute("INSERT INTO ops_team (id, name) VALUES (4, '옛팀')")
            db.db.execute("INSERT INTO ops_namespace (id, name, kind) VALUES (950, 'org:team:4', 'org_space')")
            assert await auth_service.delete_team(4) == "retired_org"
            db.db.execute("DELETE FROM ops_namespace WHERE id = 950")
            assert await auth_service.delete_team(4) is True

    @pytest.mark.asyncio
    async def test_delete_part_refused_while_it_owns_any_collection(self, db):
        """담당이 있으면 거부 — 화면에 안 나오는 옛 조직 컬렉션 남은 행의 담당도 센다(내용의 갈 곳이 정해질 때까지 지키는 쪽)."""
        db.ns(20, "딜리버스", owner=8)
        _hidden(db)
        with _patch_conn(auth_service, db):
            assert await auth_service.delete_part(8) is False
            assert await auth_service.delete_part(7) is False
            db.db.execute("DELETE FROM ops_namespace WHERE id = 901")
            assert await auth_service.delete_part(7) is True
        assert db.db.execute("SELECT COUNT(*) FROM ops_part WHERE id = 7").fetchone()[0] == 0


class TestOwnersStayOnOwnerTable:
    @pytest.mark.asyncio
    async def test_team_membership_does_not_grant_ownership(self, db):
        """같은 팀 파트라도 담당 표에 없으면 담당이 아니다 — 하위는 상위 담당을 물려받는다(그대로)."""
        db.ns(20, "딜리버스", owner=7)
        db.ns(30, "하위", parent=20)
        db.ns(40, "담당없음")
        with _patch_conn(dep, db):
            assert await dep._effective_owner_ids("딜리버스") == [7]
            assert await dep._effective_owner_ids("하위") == [7]
            assert await dep._effective_owner_ids("담당없음") == []
            await dep.check_namespace_ownership("하위", _user(part_id=7))
            with pytest.raises(HTTPException) as e:
                await dep.check_namespace_ownership("딜리버스", _user(part_id=8))   # 같은 팀(1) 다른 파트
            assert e.value.status_code == 403
        assert database.OWNER_ROWS_SQL == "(SELECT ow.namespace_id, ow.part_id FROM ops_namespace_owner ow)"

    @pytest.mark.asyncio
    async def test_set_part_domains_ignores_hidden_kinds(self, db):
        db.ns(20, "딜리버스")
        _hidden(db)
        with _patch_conn(auth_service, db):
            out = await auth_service.set_part_domains(7, ["딜리버스"])
            assert out["domains"] == ["딜리버스"]
            for name in ("stage:import", "org:part:7"):
                with pytest.raises(ValueError, match="없는 업무 영역"):
                    await auth_service.set_part_domains(7, [name])
            assert (await auth_service.set_part_domains(7, []))["domains"] == []
        assert db.owners_of("org:part:7") == [7]   # 내부 자리 담당 줄은 건드리지 않는다
        assert db.owners_of("stage:import") == []


class TestAdminHidesInternalKinds:
    @pytest.mark.asyncio
    async def test_lists_exclude_hidden_and_have_no_team_fields(self, db):
        db.ns(20, "딜리버스", owner=7)
        _hidden(db)
        with _patch_conn(admin_service, db):
            detail = await admin_service.list_namespaces_detail()
            names = await admin_service.list_namespaces()
        assert [r["name"] for r in detail] == ["딜리버스"] and names == ["딜리버스"]
        assert not {"team_id", "team", "owner_team_id"} & set(detail[0])
        assert detail[0]["display_name"] == "딜리버스"

    @pytest.mark.asyncio
    async def test_admin_api_refuses_hidden_kinds(self, db):
        _hidden(db)
        db.ns(20, "딜리버스")

        class _Body:
            owner_part_ids = [8]
            owner_part = None
            clear_owner = False
            parent_id = None
            clear_parent = False
        with _patch_conn(admin_service, db):
            for name, nid in (("stage:import", 900), ("org:part:7", 901)):
                assert (await admin_service.rename_namespace(name, "새이름"))[0] == 400
                assert (await admin_service.delete_namespace(name))[0] == 400
                with pytest.raises(ValueError):
                    await admin_service.update_namespace_settings(name, _Body())
                with pytest.raises(ValueError, match="상위 영역이 될 수 없"):
                    await admin_service.create_namespace(f"하위{nid}", parent_id=nid)
            created = await admin_service.create_namespace("org:자유")   # "org:" 예약은 풀림 — 평범한 이름
        assert created and created["name"] == "org:자유"
        assert db.owners_of("org:part:7") == [7]


class TestPolicyOnlyInPolicyCollections:
    @pytest.mark.asyncio
    async def test_reject_policy_outside_policy_collection(self, db):
        db.ns(20, "회원")
        db.ns(21, "홈페이지 시스템")
        db.db.execute("UPDATE ops_namespace SET area = 'system' WHERE id = 21")
        _hidden(db)
        with _patch_conn(dep, db):
            assert await dep.reject_policy_outside_policy_collection("회원") is None
            assert await dep.reject_policy_outside_policy_collection("없는이름") is None
            for name in ("홈페이지 시스템", "stage:import", "org:part:7"):
                with pytest.raises(HTTPException) as e:
                    await dep.reject_policy_outside_policy_collection(name)
                assert e.value.status_code == 400 and "정책 컬렉션" in e.value.detail


def _real_database():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("_real_db_scope", Path(__file__).resolve().parent.parent / "core" / "database.py")
    real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real)
    return real


def test_no_org_collection_helpers_left():
    """조직 컬렉션 만들기·지우기·채팅 범위 함수가 다시 생기지 않게(옛 #83 — 팀·파트마다 컬렉션을 만들었다)."""
    real = _real_database()
    for name in ("create_org_collection", "delete_org_collection", "org_space_scope_extra_ids", "org_team_key", "org_part_key",
                 "ORG_KIND", "ORG_NAME_PREFIX", "ORG_SPACE_SCOPE_KEY"):
        assert not hasattr(real, name), name


@pytest.mark.asyncio
async def test_knowledge_scope_extra_ids_off_and_unknown():
    """검색 범위 실험(11/02): 범위를 안 주면 DB를 보지 않고 빈 값(채팅 그대로), 모르는 값은 조용히 넘기지 않고 거절."""
    real = _real_database()
    assert await real.knowledge_scope_extra_ids(None, "회원", None, [0.1]) == []
    with pytest.raises(ValueError):
        await real.knowledge_scope_extra_ids(None, "회원", "all", [0.1])
