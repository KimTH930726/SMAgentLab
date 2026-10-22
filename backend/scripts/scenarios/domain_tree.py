"""업무 영역 트리 실서버 시나리오 (2026-10-22, ④ C1) — 하위 영역 만들기·규칙·담당 상속·삭제 막기·범위 SQL을 실제 Postgres로.

단위 테스트는 sqlite 흉내라 `FOR UPDATE OF n`·id 순 잠금·범위 서브쿼리를 실제 DB로 못 본다 → 여기서 HTTP + DB 전/후 값으로 본다.
LLM은 부르지 않는다. 자기가 만든 것(__tree_ 접두사)만 쓰고 끝나면 지운다.
실행(컨테이너 안): python scripts/scenarios/domain_tree.py   → 마지막 줄 "결과: 통과 n/n", 실패 시 종료 코드 1
"""
import asyncio
import sys

import httpx

sys.path.insert(0, "/app")
from core.database import ME_BY_NAME_1, get_conn, init_pool, namespace_scope_ids, scope_ids_same_owner_subquery  # noqa: E402
from core.security import create_access_token  # noqa: E402

API = "http://localhost:8000"
P = "__tree_"
results: list[bool] = []


def chk(name: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"  [{'OK' if ok else 'XX'}] {name}: {got} (기대 {want})")


def tok(uid: int, name: str, role: str) -> dict:
    return {"Authorization": "Bearer " + create_access_token({"sub": str(uid), "username": name, "role": role})}


async def ns_row(name: str):
    async with get_conn() as c:
        return await c.fetchrow("""SELECT id, parent_id, (SELECT COUNT(*) FROM ops_namespace_owner o WHERE o.namespace_id = n.id) AS owner_count
                                   FROM ops_namespace n WHERE name = $1""", name)   # 담당은 담당 표(N개, #82)


def edit_probe(h, ns: str, headers: dict):
    """영역 수정 권한 확인 — 예전엔 업무구분 만들기 API(10/22 업무구분 폐지로 삭제). 같은 검사(check_namespace_ownership)를 거치고
    아무것도 쓰지 않는 등록 전 확인(replace-check)으로 바꿨다 — 허용 200 / 거부 403."""
    return h.post("/api/knowledge/bulk/replace-check", json={"namespace": ns}, headers=headers)


async def main() -> int:
    await init_pool()
    async with get_conn() as c:
        admin_id = await c.fetchval("SELECT id FROM ops_user WHERE role = 'admin' ORDER BY id LIMIT 1")
    A = tok(admin_id, "admin", "admin")
    created_users: list[int] = []
    async with httpx.AsyncClient(base_url=API, timeout=60) as h:
        try:
            # 준비: 파트 2개, 사용자 2명(일반), 상위 영역(파트1 담당)
            for n in ("p1", "p2"):
                assert (await h.post("/api/auth/parts", json={"name": P + n}, headers=A)).status_code == 201
            users = {}
            for n, part in (("u1", "p1"), ("u2", "p2")):
                r = await h.post("/api/auth/register", json={"username": P + n, "password": "Passw0rd!", "part": P + part})
                assert r.status_code == 201, f"가입 실패 {n}: {r.status_code} {r.text}"
                users[n] = r.json()["id"]
                created_users.append(users[n])
                await h.put(f"/api/auth/users/{users[n]}", json={"role": "user"}, headers=A)
            U1, U2 = tok(users["u1"], P + "u1", "user"), tok(users["u2"], P + "u2", "user")
            assert (await h.post("/api/namespaces", json={"name": P + "top"}, headers=A)).status_code == 200
            await h.patch(f"/api/namespaces/{P}top/settings", json={"owner_part": P + "p1"}, headers=A)
            top = await ns_row(P + "top")

            # ① 하위 만들기 + 목록에 상위·물려받은 담당
            r = await h.post("/api/namespaces", json={"name": P + "sub", "parent_id": top["id"]}, headers=A)
            sub = await ns_row(P + "sub")
            chk("하위 만들기", (r.status_code, sub["parent_id"], sub["owner_count"]), (200, top["id"], 0))
            row = next(x for x in (await h.get("/api/namespaces/detail", headers=A)).json() if x["name"] == P + "sub")
            chk("목록: 상위·직접 담당 없음·물려받은 담당", (row.get("parent"), row.get("owner_parts"), [x["name"] for x in row.get("effective_owner_parts", [])],
                                                     row.get("owner_inherited")), (P + "top", [], [P + "p1"], True))

            # ② 깊이·자기 자신·하위 있는 영역 규칙 → 400, DB 그대로
            r = await h.post("/api/namespaces", json={"name": P + "grand", "parent_id": sub["id"]}, headers=A)
            chk("손자(깊이 2) 만들기 → 400 + 안 생김", (r.status_code, await ns_row(P + "grand")), (400, None))
            r = await h.patch(f"/api/namespaces/{P}top/settings", json={"parent_id": top["id"]}, headers=A)
            chk("자기 자신을 상위로 → 400", r.status_code, 400)
            assert (await h.post("/api/namespaces", json={"name": P + "other"}, headers=A)).status_code == 200
            r = await h.patch(f"/api/namespaces/{P}top/settings", json={"parent_id": (await ns_row(P + 'other'))["id"]}, headers=A)
            chk("하위 있는 영역을 다른 영역 아래로 → 400 + 그대로", (r.status_code, (await ns_row(P + "top"))["parent_id"]), (400, None))

            # ③ 담당 상속: 하위 담당 비면 상위 담당 파트가 수정, 다른 파트 403
            chk("상속: 상위 담당 파트가 하위 수정", (await edit_probe(h, P + "sub", U1)).status_code, 200)
            chk("상속: 다른 파트는 하위 수정 불가", (await edit_probe(h, P + "sub", U2)).status_code, 403)
            chk("상속: 상위 담당 파트가 하위 통계 조회", (await h.get(f"/api/stats/namespace/{P}sub", headers=U1)).status_code, 200)
            chk("상속: 다른 파트는 하위 통계 불가", (await h.get(f"/api/stats/namespace/{P}sub", headers=U2)).status_code, 403)

            # ④ 하위에 다른 파트를 직접 지정하면 그게 우선
            await h.patch(f"/api/namespaces/{P}sub/settings", json={"owner_part": P + "p2"}, headers=A)
            chk("직접 지정: 파트2 수정 가능", (await edit_probe(h, P + "sub", U2)).status_code, 200)
            chk("직접 지정: 상위 담당 파트1은 불가", (await edit_probe(h, P + "sub", U1)).status_code, 403)

            # ⑤ 범위 SQL(실제 Postgres): 검색 = 자신 + 하위, 정정 = 담당 같은 하위만
            async with get_conn() as c:
                scope = await namespace_scope_ids(c, P + "top")
                fix = [r["id"] for r in await c.fetch(f"SELECT id FROM ops_namespace WHERE id IN ({scope_ids_same_owner_subquery('$1')}) ORDER BY id", top["id"])]
                by_name = await c.fetchval(f"SELECT count(*) FROM ops_namespace WHERE id = {ME_BY_NAME_1}", P + "top")
            chk("검색 범위 = 상위 + 하위(자신 맨 앞)", scope, [top["id"], sub["id"]])
            chk("정정 범위 = 다른 파트 직접 하위 제외", fix, [top["id"]])
            chk("이름 조각 실행", by_name, 1)
            await h.patch(f"/api/namespaces/{P}sub/settings", json={"clear_owner": True}, headers=A)
            async with get_conn() as c:
                fix = [r["id"] for r in await c.fetch(f"SELECT id FROM ops_namespace WHERE id IN ({scope_ids_same_owner_subquery('$1')}) ORDER BY id", top["id"])]
            chk("정정 범위 = 담당 비운(상속) 하위 포함", fix, [top["id"], sub["id"]])

            # ⑥ 상위 해제·재지정(잠금 문장 실제 실행)
            r = await h.patch(f"/api/namespaces/{P}sub/settings", json={"clear_parent": True}, headers=A)
            chk("상위 해제 → 최상위", (r.status_code, (await ns_row(P + "sub"))["parent_id"]), (200, None))
            r = await h.patch(f"/api/namespaces/{P}sub/settings", json={"parent_id": top["id"]}, headers=A)
            chk("상위 다시 지정", (r.status_code, (await ns_row(P + "sub"))["parent_id"]), (200, top["id"]))

            # ⑦ 하위 있는 영역 삭제 → 409 + 둘 다 남음, 하위 먼저 지우면 상위 삭제 가능
            r = await h.delete(f"/api/namespaces/{P}top", headers=A)
            chk("하위 있는 영역 삭제 → 409 + 그대로", (r.status_code, await ns_row(P + "top") is not None, await ns_row(P + "sub") is not None), (409, True, True))
            chk("하위 삭제", (await h.delete(f"/api/namespaces/{P}sub", headers=A)).status_code, 204)
            chk("그다음 상위 삭제", (await h.delete(f"/api/namespaces/{P}top", headers=A)).status_code, 204)
        finally:
            # 정리 — 만든 이름만 정확히(LIKE의 _는 아무 글자와 맞아 남의 영역까지 지울 수 있다 — 영역 삭제는 CASCADE, 리뷰 L7). 하위 먼저.
            ns_names = [P + x for x in ("sub", "grand", "top", "other")]
            part_names, user_names = [P + "p1", P + "p2"], [P + "u1", P + "u2"]
            async with get_conn() as c:
                await c.execute("DELETE FROM ops_namespace WHERE name = ANY($1::text[]) AND parent_id IS NOT NULL", ns_names)
                await c.execute("DELETE FROM ops_namespace WHERE name = ANY($1::text[])", ns_names)
                await c.execute("DELETE FROM ops_user WHERE id = ANY($1::int[]) OR username = ANY($2::text[])", created_users, user_names)
                # 파트의 조직 컬렉션(v2.160 #83)도 — 파트만 지우면 담당 없는 조직 컬렉션이 남는다
                await c.execute("DELETE FROM ops_namespace WHERE kind = 'org_space' AND name IN "
                                "(SELECT 'org:part:' || id FROM ops_part WHERE name = ANY($1::text[]))", part_names)
                await c.execute("DELETE FROM ops_part WHERE name = ANY($1::text[])", part_names)
                left = await c.fetchval("""SELECT (SELECT count(*) FROM ops_namespace WHERE name = ANY($1::text[]))
                                                + (SELECT count(*) FROM ops_part WHERE name = ANY($2::text[]))
                                                + (SELECT count(*) FROM ops_user WHERE username = ANY($3::text[]))""", ns_names, part_names, user_names)
            print("정리 후 남은 시나리오 데이터:", left)
    print("결과:", "통과" if all(results) else "실패", f"{sum(results)}/{len(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
