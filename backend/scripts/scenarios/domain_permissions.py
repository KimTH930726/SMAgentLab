"""업무 영역·조직·권한 실서버 시나리오 (2026-10-20, v2.142) — 막 쓰기 테스트(API ~250요청·UI)가 찾은 결함의 재발 점검표.

실제 서버(localhost:8000)에 사용자별 토큰으로 요청해 상태 코드 + DB 전/후 값을 본다. LLM은 부르지 않는다.
자기가 만든 것(__scn_ 접두사)만 쓰고 끝나면 지운다 — 기존 영역·파트·사용자·데이터는 건드리지 않는다.
실행(컨테이너 안): python scripts/scenarios/domain_permissions.py   → 마지막 줄 "결과: 통과 n/n", 실패 시 종료 코드 1
"""
import asyncio
import sys

import httpx

sys.path.insert(0, "/app")
from core.database import get_conn, init_pool  # noqa: E402
from core.security import create_access_token  # noqa: E402

API = "http://localhost:8000"
P = "__scn_"
results: list[bool] = []


def chk(name: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"  [{'OK' if ok else 'XX'}] {name}: {got} (기대 {want})")


def tok(uid: int, name: str, role: str) -> dict:
    return {"Authorization": "Bearer " + create_access_token({"sub": str(uid), "username": name, "role": role})}


async def main() -> int:
    await init_pool()
    async with get_conn() as c:
        admin_id = await c.fetchval("SELECT id FROM ops_user WHERE role = 'admin' ORDER BY id LIMIT 1")
        ownerless = await c.fetchval("SELECT name FROM ops_namespace WHERE kind = 'domain' AND owner_part_id IS NULL ORDER BY id LIMIT 1")
    A = tok(admin_id, "admin", "admin")
    created_users: list[int] = []
    async with httpx.AsyncClient(base_url=API, timeout=60) as h:
        try:
            # 준비: 파트 2개, 영역 1개(파트1 담당), 사용자(가입→승인) 2명
            for n in ("p1", "p2"):
                r = await h.post("/api/auth/parts", json={"name": P + n}, headers=A)
                assert r.status_code == 201, r.text
            r = await h.post("/api/namespaces", json={"name": P + "ns"}, headers=A)
            chk("영역 만들기(관리자)", r.status_code, 200)
            r = await h.patch(f"/api/namespaces/{P}ns/settings", json={"owner_part": P + "p1"}, headers=A)
            chk("담당 지정", (r.status_code, r.json().get("owner_part")), (200, P + "p1"))
            users = {}
            for n, part in (("u1", "p1"), ("u2", "p2")):
                r = await h.post("/api/auth/register", json={"username": P + n, "password": "Passw0rd!", "part": P + part})
                chk(f"가입 {n} → 조회 전용", (r.status_code, r.json().get("role")), (201, "viewer"))
                users[n] = r.json()["id"]
                created_users.append(users[n])
            V1 = tok(users["u1"], P + "u1", "viewer")
            r = await h.post(f"/api/namespaces/{P}ns/categories", json={"name": "c"}, headers=V1)
            chk("승인 전 담당 영역 수정", r.status_code, 403)
            for n in ("u1", "u2"):
                await h.put(f"/api/auth/users/{users[n]}", json={"role": "user"}, headers=A)
            U1, U2 = tok(users["u1"], P + "u1", "user"), tok(users["u2"], P + "u2", "user")

            # 수정 규칙
            chk("담당 파트 수정", (await h.post(f"/api/namespaces/{P}ns/categories", json={"name": "c"}, headers=U1)).status_code, 201)
            chk("다른 파트 수정", (await h.post(f"/api/namespaces/{P}ns/categories", json={"name": "d"}, headers=U2)).status_code, 403)
            chk("담당 없는 영역 수정(일반)", (await h.post(f"/api/namespaces/{ownerless}/categories", json={"name": "x"}, headers=U1)).status_code, 403)

            # 없는 업무구분 이름 변경·삭제 → 지식을 건드리기 전에 404
            async with get_conn() as c:
                ns_id = await c.fetchval("SELECT id FROM ops_namespace WHERE name = $1", P + "ns")
                kid = await c.fetchval("INSERT INTO rag_knowledge (namespace_id, content, category, status) VALUES ($1, 'x', $2, 'active') RETURNING id",
                                       ns_id, P + "ghost")
            r = await h.patch(f"/api/namespaces/{P}ns/categories/{P}ghost", json={"name": "new"}, headers=U1)
            async with get_conn() as c:
                cat = await c.fetchval("SELECT category FROM rag_knowledge WHERE id = $1", kid)
            chk("없는 업무구분 이름 변경 → 404 + 지식 그대로", (r.status_code, cat), (404, P + "ghost"))

            # 조회 규칙: 통계·질의 기록은 담당 파트·관리자만, 조회 전용 X
            chk("다른 파트 통계 조회(사람 데이터)", (await h.get(f"/api/stats/namespace/{P}ns", headers=U2)).status_code, 403)
            chk("다른 파트 지식(정책 목록) 조회", (await h.get("/api/policy/policies", params={"namespace": P + "ns"}, headers=U2)).status_code, 200)   # 옛 /items는 v2.150 삭제
            chk("담당 파트 통계 조회", (await h.get(f"/api/stats/namespace/{P}ns", headers=U1)).status_code, 200)
            chk("영역 없이 지식 전체 조회(일반)", (await h.get("/api/knowledge", headers=U1)).status_code, 400)

            # 입력 검증
            for bad in ("", "   ", "a/b", "x" * 101):
                chk(f"이상한 영역 이름 {bad[:8]!r}", (await h.post("/api/namespaces", json={"name": bad}, headers=A)).status_code, 422)
            chk("같은 이름 영역", (await h.post("/api/namespaces", json={"name": P + "ns"}, headers=A)).status_code, 409)
            chk("없는 역할", (await h.put(f"/api/auth/users/{users['u2']}", json={"role": "superadmin"}, headers=A)).status_code, 422)
            r = await h.put(f"/api/auth/users/{users['u2']}", json={"part": "없는파트__"}, headers=A)
            async with get_conn() as c:
                still = await c.fetchval("SELECT p.name FROM ops_user u JOIN ops_part p ON p.id = u.part_id WHERE u.id = $1", users["u2"])
            chk("없는 파트로 이동 → 400 + 소속 그대로", (r.status_code, still), (400, P + "p2"))
            chk("범위 밖 숫자", (await h.post("/api/stats/query-logs/bulk-delete", json={"ids": [2 ** 40]}, headers=A)).status_code, 422)
            chk("자기 자신 비활성화", (await h.put(f"/api/auth/users/{admin_id}", json={"is_active": False}, headers=A)).status_code, 400)

            # 동시 요청: 같은 파트 10번 → 하나만 성공, 500 없음
            rs = await asyncio.gather(*[h.post("/api/auth/parts", json={"name": P + "dup"}, headers=A) for _ in range(10)])
            codes = sorted(r.status_code for r in rs)
            chk("같은 파트 동시 10번(성공 1·500 없음)", (codes.count(201), 500 in codes), (1, False))

            # 담당 업무 영역 통째 저장(1:N): 붙이면 그 파트가 수정, 빼면 관리자만, 없는 영역이 섞이면 아무것도 안 바뀜
            await h.post("/api/namespaces", json={"name": P + "ns2"}, headers=A)
            async with get_conn() as c:
                p2 = await c.fetchval("SELECT id FROM ops_part WHERE name = $1", P + "p2")
            r = await h.put(f"/api/auth/parts/{p2}/domains", json={"domains": [P + "ns", P + "ns2"]}, headers=A)
            chk("파트2가 영역 2개 맡기(1:N, 파트1 것 옮겨 옴)", (r.status_code, r.json().get("domains")), (200, [P + "ns", P + "ns2"]))
            chk("  → 파트2 사람 수정", (await h.post(f"/api/namespaces/{P}ns2/categories", json={"name": "e"}, headers=U2)).status_code, 201)
            chk("  → 파트1 사람은 이제 못 고침", (await h.post(f"/api/namespaces/{P}ns/categories", json={"name": "f"}, headers=U1)).status_code, 403)
            r = await h.put(f"/api/auth/parts/{p2}/domains", json={"domains": [P + "ns", "없는영역__"]}, headers=A)
            async with get_conn() as c:
                owned = await c.fetchval("SELECT count(*) FROM ops_namespace WHERE owner_part_id = $1", p2)
            chk("없는 영역 섞이면 400 + 그대로(2개)", (r.status_code, owned), (400, 2))
            r = await h.put(f"/api/auth/parts/{p2}/domains", json={"domains": [P + "ns"]}, headers=A)
            chk("하나 빼기 → 그 영역은 관리자만", (await h.post(f"/api/namespaces/{P}ns2/categories", json={"name": "g"}, headers=U2)).status_code, 403)
            chk("일반 사용자는 담당 저장 불가", (await h.put(f"/api/auth/parts/{p2}/domains", json={"domains": []}, headers=U2)).status_code, 403)
            r = await h.get("/api/auth/parts")
            chk("가입용 공개 목록엔 담당 영역 없음", all(not x.get("domains") for x in r.json()), True)
            await h.put(f"/api/auth/parts/{p2}/domains", json={"domains": []}, headers=A)

            # 쓰이는 파트 삭제 금지
            async with get_conn() as c:
                p1 = await c.fetchval("SELECT id FROM ops_part WHERE name = $1", P + "p1")
            chk("담당·소속 있는 파트 삭제", (await h.delete(f"/api/auth/parts/{p1}", headers=A)).status_code, 400)
        finally:
            async with get_conn() as c:   # 정리 — 만든 것만
                await c.execute("DELETE FROM ops_namespace WHERE name LIKE $1", P + "%")
                await c.execute("DELETE FROM ops_user WHERE id = ANY($1::int[])", created_users)
                await c.execute("DELETE FROM ops_part WHERE name LIKE $1", P + "%")
                left = await c.fetchval("""SELECT (SELECT count(*) FROM ops_namespace WHERE name LIKE $1)
                                                + (SELECT count(*) FROM ops_part WHERE name LIKE $1)
                                                + (SELECT count(*) FROM ops_user WHERE username LIKE $1)""", P + "%")
            print("정리 후 남은 시나리오 데이터:", left)
    print("결과:", "통과" if all(results) else "실패", f"{sum(results)}/{len(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
