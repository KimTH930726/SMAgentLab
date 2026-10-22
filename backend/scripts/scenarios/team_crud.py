"""팀 관리 실서버 시나리오 (2026-10-22) — 팀 추가·이름·삭제, 팀 아래 파트 만들기·옮기기, 권한, 동시 삭제.

실제 서버(localhost:8000)에 요청해 상태 코드 + DB 전/후 값을 본다. LLM은 부르지 않는다.
자기가 만든 것(__scn_ 접두사)만 쓰고 끝나면 지운다 — 기존 팀·파트·사용자는 건드리지 않는다.
실행(컨테이너 안): python scripts/scenarios/team_crud.py   → 마지막 줄 "결과: 통과 n/n", 실패 시 종료 코드 1
"""
import asyncio
import sys

import httpx

sys.path.insert(0, "/app")
from core.database import get_conn, init_pool  # noqa: E402
from core.security import create_access_token  # noqa: E402

API = "http://localhost:8000"
P = "__scn_team_"
results: list[bool] = []


def chk(name: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"  [{'OK' if ok else 'XX'}] {name}: {got} (기대 {want})")


def tok(uid: int, name: str, role: str) -> dict:
    return {"Authorization": "Bearer " + create_access_token({"sub": str(uid), "username": name, "role": role})}


async def db(sql: str, *args):
    async with get_conn() as c:
        return await c.fetchval(sql, *args)


async def main() -> int:
    await init_pool()
    admin_id = await db("SELECT id FROM ops_user WHERE role = 'admin' ORDER BY id LIMIT 1")
    user_id = await db("SELECT id FROM ops_user WHERE role = 'user' ORDER BY id LIMIT 1")
    A = tok(admin_id, "admin", "admin")
    teams_before = await db("SELECT COUNT(*) FROM ops_team")
    parts_before = await db("SELECT COUNT(*) FROM ops_part")
    org_before = await db("SELECT COUNT(*) FROM ops_namespace WHERE kind = 'org_space'")
    async with httpx.AsyncClient(base_url=API, timeout=60) as h:
        try:
            print("① 팀 만들기 · 이름 겹침 · 권한")
            r = await h.post("/api/auth/teams", json={"name": f"{P}A"}, headers=A)
            chk("팀 A 만들기", r.status_code, 201)
            ta = r.json()["id"]
            chk("DB에 팀 A", await db("SELECT name FROM ops_team WHERE id = $1", ta), f"{P}A")
            chk("같은 이름 다시", (await h.post("/api/auth/teams", json={"name": f"{P}A"}, headers=A)).status_code, 409)
            if user_id:
                U = tok(user_id, "user", "user")
                chk("일반 사용자는 팀 못 만듦", (await h.post("/api/auth/teams", json={"name": f"{P}X"}, headers=U)).status_code, 403)
                chk("일반 사용자는 팀 목록 못 봄", (await h.get("/api/auth/teams", headers=U)).status_code, 403)
            tb = (await h.post("/api/auth/teams", json={"name": f"{P}B"}, headers=A)).json()["id"]

            print("② 팀 아래 파트 만들기 · 없는 팀")
            r = await h.post("/api/auth/parts", json={"name": f"{P}파트1", "team_id": ta}, headers=A)
            chk("팀 A 아래 파트", r.status_code, 201)
            p1 = r.json()["id"]
            chk("DB 파트 팀 = A", await db("SELECT team_id FROM ops_part WHERE id = $1", p1), ta)
            chk("없는 팀에 파트", (await h.post("/api/auth/parts", json={"name": f"{P}파트2", "team_id": 999999}, headers=A)).status_code, 400)
            chk("없는 팀에 파트 → 생기지 않음", await db("SELECT COUNT(*) FROM ops_part WHERE name = $1", f"{P}파트2"), 0)

            print("③ 팀 삭제는 파트가 있으면 막힘 → 옮긴 뒤 삭제")
            chk("파트 있는 팀 A 삭제", (await h.delete(f"/api/auth/teams/{ta}", headers=A)).status_code, 409)
            chk("팀 A 그대로", await db("SELECT COUNT(*) FROM ops_team WHERE id = $1", ta), 1)
            chk("파트 팀 그대로 A(조용히 팀 없음이 안 됨)", await db("SELECT team_id FROM ops_part WHERE id = $1", p1), ta)
            r = await h.put(f"/api/auth/parts/{p1}/team", json={"team_id": tb}, headers=A)
            chk("파트를 팀 B로", r.status_code, 200)
            chk("응답에 팀 이름(team)", r.json().get("team"), f"{P}B")
            chk("DB 파트 팀 = B", await db("SELECT team_id FROM ops_part WHERE id = $1", p1), tb)
            chk("없는 팀으로 옮기기", (await h.put(f"/api/auth/parts/{p1}/team", json={"team_id": 999999}, headers=A)).status_code, 400)
            chk("없는 파트 옮기기", (await h.put("/api/auth/parts/999999/team", json={"team_id": tb}, headers=A)).status_code, 404)
            chk("빈 팀 A 삭제", (await h.delete(f"/api/auth/teams/{ta}", headers=A)).status_code, 204)
            chk("DB 팀 A 없음", await db("SELECT COUNT(*) FROM ops_team WHERE id = $1", ta), 0)

            print("④ 이름 바꾸기 · 목록")
            r = await h.patch(f"/api/auth/teams/{tb}", json={"name": f"{P}B2"}, headers=A)
            chk("팀 B 이름 바꾸기", r.status_code, 200)
            chk("파트 연결 유지", await db("SELECT team_id FROM ops_part WHERE id = $1", p1), tb)
            tc = (await h.post("/api/auth/teams", json={"name": f"{P}C"}, headers=A)).json()["id"]
            chk("다른 팀과 같은 이름으로", (await h.patch(f"/api/auth/teams/{tc}", json={"name": f"{P}B2"}, headers=A)).status_code, 409)
            lst = {t["name"]: t["part_count"] for t in (await h.get("/api/auth/teams", headers=A)).json()}
            chk("목록 파트 수", (lst.get(f"{P}B2"), lst.get(f"{P}C")), (1, 0))
            pub = {p["name"]: p.get("team_id") for p in (await h.get("/api/auth/parts")).json()}
            chk("공개 파트 목록에 team_id", pub.get(f"{P}파트1"), tb)

            print("⑤ 동시: 빈 팀 C 삭제 vs 팀 C에 파트 만들기 — 팀 없는 파트가 생기면 안 됨")
            d, c = await asyncio.gather(
                h.delete(f"/api/auth/teams/{tc}", headers=A),
                h.post("/api/auth/parts", json={"name": f"{P}파트3", "team_id": tc}, headers=A),
            )
            orphan = await db("SELECT COUNT(*) FROM ops_part WHERE name = $1 AND team_id IS NULL", f"{P}파트3")
            chk("팀 없는 파트 0", orphan, 0)
            team_alive = await db("SELECT COUNT(*) FROM ops_team WHERE id = $1", tc)
            part_made = await db("SELECT COUNT(*) FROM ops_part WHERE name = $1", f"{P}파트3")
            # 둘 중 하나만 이긴다: (삭제 204 + 파트 400) 또는 (파트 201 + 삭제 409)
            chk("결과가 둘 중 하나", (d.status_code, c.status_code, team_alive, part_made) in
                {(204, 400, 0, 0), (409, 201, 1, 1)}, True)
            print("⑥ 동시: 같은 이름 파트 두 번 · 두 팀을 같은 이름으로 — 500 없이 하나만 이긴다(리뷰 L1)")
            r1, r2 = await asyncio.gather(
                h.post("/api/auth/parts", json={"name": f"{P}동시", "team_id": tb}, headers=A),
                h.post("/api/auth/parts", json={"name": f"{P}동시", "team_id": tb}, headers=A),
            )
            chk("같은 이름 파트 동시 → 201 하나·409 하나", sorted([r1.status_code, r2.status_code]), [201, 409])
            chk("같은 이름 파트 1개만", await db("SELECT COUNT(*) FROM ops_part WHERE name = $1", f"{P}동시"), 1)
            td = (await h.post("/api/auth/teams", json={"name": f"{P}D"}, headers=A)).json()["id"]
            te = (await h.post("/api/auth/teams", json={"name": f"{P}E"}, headers=A)).json()["id"]
            r1, r2 = await asyncio.gather(
                h.patch(f"/api/auth/teams/{td}", json={"name": f"{P}같음"}, headers=A),
                h.patch(f"/api/auth/teams/{te}", json={"name": f"{P}같음"}, headers=A),
            )
            chk("두 팀 같은 이름 동시 → 200 하나·409 하나", sorted([r1.status_code, r2.status_code]), [200, 409])
        finally:
            async with get_conn() as conn:
                # 팀·파트의 조직 컬렉션(v2.160 #83)을 먼저 — 파트를 바로 지우면 컬렉션이 담당 없이 남고, 팀은 FK(owner_team_id) 때문에 안 지워진다
                await conn.execute("""DELETE FROM ops_namespace WHERE kind = 'org_space' AND (
                                          name IN (SELECT 'org:part:' || id FROM ops_part WHERE name LIKE $1)
                                          OR owner_team_id IN (SELECT id FROM ops_team WHERE name LIKE $1))""", f"{P}%")
                await conn.execute("DELETE FROM ops_part WHERE name LIKE $1", f"{P}%")
                await conn.execute("DELETE FROM ops_team WHERE name LIKE $1", f"{P}%")
    chk("정리 후 팀 수 그대로", await db("SELECT COUNT(*) FROM ops_team"), teams_before)
    chk("정리 후 조직 컬렉션 수 그대로", await db("SELECT COUNT(*) FROM ops_namespace WHERE kind = 'org_space'"), org_before)
    chk("정리 후 파트 수 그대로", await db("SELECT COUNT(*) FROM ops_part"), parts_before)
    ok = sum(results)
    print(f"결과: 통과 {ok}/{len(results)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
