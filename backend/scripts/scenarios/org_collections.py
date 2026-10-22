"""조직 컬렉션 실서버 시나리오 (2026-10-22, v2.160 #83) — 팀·파트를 만들면 조직 컬렉션이 생기고, 담당·종류 거부·삭제 거부가 실제 서버에서 맞나.

실제 서버(localhost:8000)에 사용자별 토큰으로 요청해 상태 코드 + DB 전/후 값을 본다. LLM은 부르지 않는다.
자기가 만든 것(__scn_org_ 접두사 팀·파트·사용자)만 쓰고 끝나면 지운다 — 기존 팀·파트·컬렉션·지식은 건드리지 않는다.
쓰기 권한 확인 = 등록 전 확인(POST /api/knowledge/bulk/replace-check — 수정 권한 검사만 거치고 아무것도 안 씀, domain_permissions.py와 같음).
실행(컨테이너 안): python scripts/scenarios/org_collections.py   → 마지막 줄 "결과: 통과 n/n", 실패 시 종료 코드 1
"""
import asyncio
import sys

import httpx

sys.path.insert(0, "/app")
from core.database import get_conn, init_pool  # noqa: E402
from core.security import create_access_token  # noqa: E402

API = "http://localhost:8000"
P = "__scn_org_"
results: list[bool] = []


def chk(name: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"  [{'OK' if ok else 'XX'}] {name}: {got} (기대 {want})")


def tok(uid: int, name: str, role: str) -> dict:
    return {"Authorization": "Bearer " + create_access_token({"sub": str(uid), "username": name, "role": role})}


def edit_probe(h: httpx.AsyncClient, ns: str, headers: dict):
    return h.post("/api/knowledge/bulk/replace-check", json={"namespace": ns}, headers=headers)


async def db(sql: str, *args):
    async with get_conn() as c:
        return await c.fetchval(sql, *args)


async def add_user(name: str, part_id: int) -> int:
    # 가입·승인 흐름은 domain_permissions.py가 본다 — 여기선 바로 일반 사용자(비밀번호 로그인 안 함, 토큰만)
    return await db("INSERT INTO ops_user (username, hashed_password, part_id, role) VALUES ($1, 'x', $2, 'user') RETURNING id", P + name, part_id)


async def main() -> int:
    await init_pool()
    admin_id = await db("SELECT id FROM ops_user WHERE role = 'admin' ORDER BY id LIMIT 1")
    A = tok(admin_id, "admin", "admin")
    q_counts = """SELECT (SELECT COUNT(*) FROM ops_team), (SELECT COUNT(*) FROM ops_part), (SELECT COUNT(*) FROM ops_namespace),
                         (SELECT COUNT(*) FROM ops_namespace_owner), (SELECT COUNT(*) FROM rag_knowledge), (SELECT COUNT(*) FROM ops_user)"""
    async with get_conn() as c:
        before = tuple(await c.fetchrow(q_counts))
    async with httpx.AsyncClient(base_url=API, timeout=60) as h:
        try:
            print("① 팀을 만들면 팀 공통 조직 컬렉션이 같이 생긴다")
            r = await h.post("/api/auth/teams", json={"name": f"{P}팀A"}, headers=A)
            chk("팀 A 만들기", r.status_code, 201)
            ta = r.json()["id"]
            chk("DB에 org:team:<A>", await db("SELECT owner_team_id FROM ops_namespace WHERE name = $1 AND kind = 'org_space'", f"org:team:{ta}"), ta)
            tb = (await h.post("/api/auth/teams", json={"name": f"{P}팀B"}, headers=A)).json()["id"]

            print("② 팀 아래 파트를 만들면 파트 조직 컬렉션(담당 = 그 파트 한 줄)")
            r = await h.post("/api/auth/parts", json={"name": f"{P}파트1", "team_id": ta}, headers=A)
            chk("파트 1(팀 A)", r.status_code, 201)
            p1 = r.json()["id"]
            p2 = (await h.post("/api/auth/parts", json={"name": f"{P}파트2", "team_id": ta}, headers=A)).json()["id"]
            p3 = (await h.post("/api/auth/parts", json={"name": f"{P}파트3", "team_id": tb}, headers=A)).json()["id"]
            chk("DB에 org:part:<1>", await db("SELECT kind FROM ops_namespace WHERE name = $1", f"org:part:{p1}"), "org_space")
            chk("파트 1 컬렉션 담당 = 파트 1",
                await db("SELECT array_agg(o.part_id) FROM ops_namespace_owner o JOIN ops_namespace n ON n.id = o.namespace_id WHERE n.name = $1",
                         f"org:part:{p1}"), [p1])
            chk("팀 공통엔 담당 표 줄 없음",
                await db("SELECT COUNT(*) FROM ops_namespace_owner o JOIN ops_namespace n ON n.id = o.namespace_id WHERE n.name = $1",
                         f"org:team:{ta}"), 0)
            detail = {d["name"]: d for d in (await h.get("/api/namespaces/detail", headers=A)).json()}
            d1, dt = detail.get(f"org:part:{p1}", {}), detail.get(f"org:team:{ta}", {})
            chk("목록: 파트 화면 이름·팀", (d1.get("display_name"), d1.get("team_id"), d1.get("kind")), (f"{P}파트1", ta, "org_space"))
            chk("목록: 팀 공통 화면 이름·담당(팀 파트 전부)",
                (dt.get("display_name"), sorted(p["id"] for p in dt.get("effective_owner_parts", []))), (f"팀 공통 · {P}팀A", sorted([p1, p2])))
            parts = {p["id"]: p for p in (await h.get("/api/auth/parts/all", headers=A)).json()}
            chk("파트 담당 업무 목록에 조직 컬렉션 안 보임", parts.get(p1, {}).get("domains"), [])

            print("③ 쓰기 권한 — 파트 사람은 자기 파트 + 자기 팀 공통, 다른 팀은 403")
            u1 = await add_user("u1", p1)
            u2 = await add_user("u2", p2)
            u3 = await add_user("u3", p3)
            U1, U2, U3 = tok(u1, P + "u1", "user"), tok(u2, P + "u2", "user"), tok(u3, P + "u3", "user")
            chk("파트1 → 파트1 컬렉션", (await edit_probe(h, f"org:part:{p1}", U1)).status_code, 200)
            chk("파트1 → 팀 A 공통", (await edit_probe(h, f"org:team:{ta}", U1)).status_code, 200)
            chk("파트2(같은 팀) → 팀 A 공통", (await edit_probe(h, f"org:team:{ta}", U2)).status_code, 200)
            chk("파트2(같은 팀) → 파트1 컬렉션", (await edit_probe(h, f"org:part:{p1}", U2)).status_code, 403)
            chk("다른 팀(파트3) → 팀 A 공통", (await edit_probe(h, f"org:team:{ta}", U3)).status_code, 403)
            chk("다른 팀(파트3) → 파트1 컬렉션", (await edit_probe(h, f"org:part:{p1}", U3)).status_code, 403)
            chk("관리자 → 팀 A 공통", (await edit_probe(h, f"org:team:{ta}", A)).status_code, 200)
            r = await h.put(f"/api/auth/parts/{p3}/team", json={"team_id": ta}, headers=A)
            chk("파트3을 팀 A로 옮기면 → 팀 A 공통 쓰기 됨(담당이 따라감)", (r.status_code, (await edit_probe(h, f"org:team:{ta}", U3)).status_code), (200, 200))
            await h.put(f"/api/auth/parts/{p3}/team", json={"team_id": tb}, headers=A)
            chk("되돌리면 다시 403", (await edit_probe(h, f"org:team:{ta}", U3)).status_code, 403)

            print("④ 조직 컬렉션엔 정책·VOC 안 됨 · 업무 컬렉션 관리 API로 못 바꿈")
            r = await h.post("/api/policy/json/preview", json={"namespace": f"org:part:{p1}", "content": "{}"}, headers=A)
            chk("정책 가져오기(미리보기) → 400", r.status_code, 400)
            r = await h.post("/api/policy/json/apply", json={"namespace": f"org:part:{p1}", "content": "{}", "doc_hash": "a", "plan_hash": "b"}, headers=A)
            chk("정책 가져오기(적용) → 400", r.status_code, 400)
            r = await h.post("/api/corrections", json={"namespace": f"org:part:{p1}", "target_type": "policy_narrative", "target_id": 1,
                                                       "source": "policy_direct", "proposed": {"raw_body": "x"}}, headers=U1)
            chk("정책 수정 제안 → 400", r.status_code, 400)
            chk("이름 바꾸기 → 400", (await h.patch(f"/api/namespaces/org:part:{p1}", json={"new_name": f"{P}x"}, headers=A)).status_code, 400)
            chk("담당 바꾸기 → 400", (await h.patch(f"/api/namespaces/org:part:{p1}/settings", json={"owner_part_ids": [p2]}, headers=A)).status_code, 400)
            chk("업무 컬렉션 API로 지우기 → 400", (await h.delete(f"/api/namespaces/org:team:{ta}", headers=A)).status_code, 400)
            chk("org: 이름 업무 컬렉션 만들기 → 400", (await h.post("/api/namespaces", json={"name": "org:fake"}, headers=A)).status_code, 400)
            chk("담당 그대로", await db("SELECT COUNT(*) FROM ops_namespace_owner o JOIN ops_namespace n ON n.id = o.namespace_id "
                                         "WHERE n.name = $1 AND o.part_id = $2", f"org:part:{p1}", p1), 1)

            print("⑤ 팀 공통에 지식이 있으면 팀 삭제 거부 · 없으면 같이 지움")
            ns_ta = await db("SELECT id FROM ops_namespace WHERE name = $1", f"org:team:{ta}")
            kid = await db("INSERT INTO rag_knowledge (namespace_id, content, status) VALUES ($1, $2, 'active') RETURNING id", ns_ta, P + "지식")
            for pid in (p1, p2):   # 팀을 비워야 지식 검사까지 간다(파트 있는 팀은 예전처럼 409)
                await h.put(f"/api/auth/parts/{pid}/team", json={"team_id": tb}, headers=A)
            r = await h.delete(f"/api/auth/teams/{ta}", headers=A)
            chk("지식 있는 팀 공통 → 팀 삭제 409", (r.status_code, "지식 1건" in r.text), (409, True))
            chk("팀·컬렉션·지식 그대로", (await db("SELECT COUNT(*) FROM ops_team WHERE id = $1", ta),
                                       await db("SELECT COUNT(*) FROM ops_namespace WHERE id = $1", ns_ta),
                                       await db("SELECT COUNT(*) FROM rag_knowledge WHERE id = $1", kid)), (1, 1, 1))
            await db("DELETE FROM rag_knowledge WHERE id = $1", kid)
            chk("지식 지운 뒤 팀 삭제 204", (await h.delete(f"/api/auth/teams/{ta}", headers=A)).status_code, 204)
            chk("팀 공통 컬렉션도 지워짐", await db("SELECT COUNT(*) FROM ops_namespace WHERE name = $1", f"org:team:{ta}"), 0)

            print("⑥ 파트 삭제 — 파트 컬렉션에 용어가 있으면 거부, 없으면 같이 지움")
            ns_p2 = await db("SELECT id FROM ops_namespace WHERE name = $1", f"org:part:{p2}")
            gid = await db("INSERT INTO rag_glossary (namespace_id, term, description) VALUES ($1, $2, 'x') RETURNING id", ns_p2, P + "용어")
            await db("DELETE FROM ops_user WHERE id = $1", u2)   # 사용자 있는 파트는 예전처럼 거부 — 내용 검사만 보려고 먼저 뺀다
            r = await h.delete(f"/api/auth/parts/{p2}", headers=A)
            chk("용어 있는 파트 → 삭제 400", (r.status_code, "용어 1건" in r.text), (400, True))
            await db("DELETE FROM rag_glossary WHERE id = $1", gid)
            chk("용어 지운 뒤 파트 삭제 204", (await h.delete(f"/api/auth/parts/{p2}", headers=A)).status_code, 204)
            chk("파트 컬렉션도 지워짐", await db("SELECT COUNT(*) FROM ops_namespace WHERE name = $1", f"org:part:{p2}"), 0)
        finally:
            async with get_conn() as conn:
                await conn.execute("DELETE FROM ops_user WHERE username LIKE $1", f"{P}%")
                part_ids = [r["id"] for r in await conn.fetch("SELECT id FROM ops_part WHERE name LIKE $1", f"{P}%")]
                tids = [r["id"] for r in await conn.fetch("SELECT id FROM ops_team WHERE name LIKE $1", f"{P}%")]
                await conn.execute("DELETE FROM rag_knowledge WHERE content LIKE $1", f"{P}%")
                await conn.execute("DELETE FROM rag_glossary WHERE term LIKE $1", f"{P}%")
                await conn.execute("DELETE FROM ops_namespace WHERE kind = 'org_space' AND (name = ANY($1::text[]) OR name = ANY($2::text[]))",
                                   [f"org:part:{i}" for i in part_ids], [f"org:team:{i}" for i in tids])
                await conn.execute("DELETE FROM ops_part WHERE id = ANY($1::int[])", part_ids)
                await conn.execute("DELETE FROM ops_team WHERE id = ANY($1::int[])", tids)
    async with get_conn() as c:
        after = tuple(await c.fetchrow(q_counts))
    chk("정리 후 개수 그대로(팀·파트·컬렉션·담당·지식·사용자)", after, before)
    # 구조 불변식(#83, 리뷰 10/22) — 조직 컬렉션은 실제 팀·파트에 1:1로 붙는다. SQL로 파트를 바로 지우면 담당 없는 고아가 남았다.
    async with get_conn() as c:
        bad_part = await c.fetchval("""SELECT count(*) FROM ops_namespace n WHERE n.kind = 'org_space' AND n.name LIKE 'org:part:%'
                                       AND (NOT EXISTS (SELECT 1 FROM ops_part p WHERE 'org:part:' || p.id = n.name)
                                            OR (SELECT count(*) FROM ops_namespace_owner o WHERE o.namespace_id = n.id) <> 1
                                            OR NOT EXISTS (SELECT 1 FROM ops_namespace_owner o WHERE o.namespace_id = n.id AND 'org:part:' || o.part_id = n.name))""")
        bad_team = await c.fetchval("""SELECT count(*) FROM ops_namespace n WHERE n.kind = 'org_space' AND n.name LIKE 'org:team:%'
                                       AND (n.owner_team_id IS NULL OR 'org:team:' || n.owner_team_id <> n.name
                                            OR EXISTS (SELECT 1 FROM ops_namespace_owner o WHERE o.namespace_id = n.id))""")
        missing = await c.fetchval("""SELECT (SELECT count(*) FROM ops_part p WHERE NOT EXISTS (SELECT 1 FROM ops_namespace n WHERE n.kind = 'org_space' AND n.name = 'org:part:' || p.id))
                                     + (SELECT count(*) FROM ops_team t WHERE NOT EXISTS (SELECT 1 FROM ops_namespace n WHERE n.kind = 'org_space' AND n.name = 'org:team:' || t.id))""")
    chk("조직 컬렉션 구조(고아·담당 줄·빠진 것 0)", (bad_part, bad_team, missing), (0, 0, 0))
    ok = sum(results)
    print(f"결과: 통과 {ok}/{len(results)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
