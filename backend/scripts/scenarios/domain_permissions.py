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


# 영역 수정 권한 확인용 요청 — 예전엔 업무구분 만들기 API였다(10/22 업무구분 폐지로 API 삭제). 같은 수정 권한 검사
# (check_namespace_ownership)를 거치면서 아무것도 쓰지 않고 LLM도 안 부르는 등록 전 확인(replace-check)으로 바꿨다 — 허용 200 / 거부 403.
def edit_probe(h: httpx.AsyncClient, ns: str, headers: dict):
    return h.post("/api/knowledge/bulk/replace-check", json={"namespace": ns}, headers=headers)


def tok(uid: int, name: str, role: str) -> dict:
    return {"Authorization": "Bearer " + create_access_token({"sub": str(uid), "username": name, "role": role})}


async def main() -> int:
    await init_pool()
    async with get_conn() as c:
        admin_id = await c.fetchval("SELECT id FROM ops_user WHERE role = 'admin' ORDER BY id LIMIT 1")
        ownerless = await c.fetchval("""SELECT name FROM ops_namespace n WHERE kind = 'domain' AND parent_id IS NULL
                                         AND NOT EXISTS (SELECT 1 FROM ops_namespace_owner o WHERE o.namespace_id = n.id) ORDER BY id LIMIT 1""")
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
            r = await edit_probe(h, P + "ns", V1)
            chk("승인 전 담당 영역 수정", r.status_code, 403)
            for n in ("u1", "u2"):
                await h.put(f"/api/auth/users/{users[n]}", json={"role": "user"}, headers=A)
            U1, U2 = tok(users["u1"], P + "u1", "user"), tok(users["u2"], P + "u2", "user")

            # 수정 규칙
            chk("담당 파트 수정", (await edit_probe(h, P + "ns", U1)).status_code, 200)
            chk("다른 파트 수정", (await edit_probe(h, P + "ns", U2)).status_code, 403)
            chk("담당 없는 영역 수정(일반)", (await edit_probe(h, ownerless, U1)).status_code, 403)

            # (없는 업무구분 이름 변경 → 404 확인은 10/22 업무구분 관리 API 삭제로 뺐다)

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

            # 담당 업무 영역 통째 저장(N:N, #82): 붙이면 그 파트가 함께 담당(다른 파트 담당 유지), 빼면 그 파트만 빠짐, 없는 영역이 섞이면 아무것도 안 바뀜
            await h.post("/api/namespaces", json={"name": P + "ns2"}, headers=A)
            async with get_conn() as c:
                p2 = await c.fetchval("SELECT id FROM ops_part WHERE name = $1", P + "p2")
            r = await h.put(f"/api/auth/parts/{p2}/domains", json={"domains": [P + "ns", P + "ns2"]}, headers=A)
            chk("파트2가 영역 2개 맡기(파트1 것은 함께 담당)", (r.status_code, r.json().get("domains")), (200, [P + "ns", P + "ns2"]))
            chk("  → 파트2 사람 수정", (await edit_probe(h, P + "ns2", U2)).status_code, 200)
            chk("  → 함께 담당: 파트2 사람도 ns 수정", (await edit_probe(h, P + "ns", U2)).status_code, 200)
            chk("  → 파트1 사람도 그대로 수정(옮겨 오지 않음)", (await edit_probe(h, P + "ns", U1)).status_code, 200)
            async with get_conn() as c:
                ns_owners = [r["part_id"] for r in await c.fetch(
                    "SELECT o.part_id FROM ops_namespace_owner o JOIN ops_namespace n ON n.id = o.namespace_id WHERE n.name = $1 ORDER BY 1", P + "ns")]
                mirror = await c.fetchval("SELECT owner_part_id FROM ops_namespace WHERE name = $1", P + "ns")
            chk("  → 담당 표 2줄 + 옛 칸 거울 = 가장 작은 id", (len(ns_owners), mirror), (2, min(ns_owners)))
            r = await h.put(f"/api/auth/parts/{p2}/domains", json={"domains": [P + "ns", "없는영역__"]}, headers=A)
            async with get_conn() as c:
                # 업무 컬렉션만 센다 — 파트마다 조직 컬렉션(#83)의 담당 줄이 하나 더 있다
                owned = await c.fetchval("""SELECT count(*) FROM ops_namespace_owner o JOIN ops_namespace n ON n.id = o.namespace_id
                                            WHERE o.part_id = $1 AND n.kind = 'domain'""", p2)
            chk("없는 영역 섞이면 400 + 그대로(2개)", (r.status_code, owned), (400, 2))
            r = await h.put(f"/api/auth/parts/{p2}/domains", json={"domains": [P + "ns"]}, headers=A)
            chk("하나 빼기 → 그 영역은 관리자만", (await edit_probe(h, P + "ns2", U2)).status_code, 403)
            r = await h.put(f"/api/auth/parts/{p2}/domains", json={"domains": []}, headers=A)
            chk("파트2 전부 빼기 → 파트2는 ns 못 고침", (await edit_probe(h, P + "ns", U2)).status_code, 403)
            chk("  → 파트1은 그대로 담당", (await edit_probe(h, P + "ns", U1)).status_code, 200)

            # 업무 컬렉션 수정 창: 담당 목록 통째 교체(owner_part_ids)
            async with get_conn() as c:
                p1_id = await c.fetchval("SELECT id FROM ops_part WHERE name = $1", P + "p1")
            r = await h.patch(f"/api/namespaces/{P}ns/settings", json={"owner_part_ids": [p1_id, p2]}, headers=A)
            chk("담당 2개로 교체", (r.status_code, sorted(x["id"] for x in r.json().get("owner_parts", []))), (200, sorted([p1_id, p2])))
            chk("  → 두 파트 모두 수정", ((await edit_probe(h, P + "ns", U1)).status_code, (await edit_probe(h, P + "ns", U2)).status_code), (200, 200))
            r = await h.patch(f"/api/namespaces/{P}ns/settings", json={"owner_part_ids": [p2, 2 ** 30]}, headers=A)
            chk("없는 파트 섞이면 400", r.status_code, 400)
            r = await h.get("/api/namespaces/detail", headers=A)
            row = next(x for x in r.json() if x["name"] == P + "ns")
            chk("  → 목록 owner_parts 그대로 2개", len(row["owner_parts"]), 2)
            r = await h.patch(f"/api/namespaces/{P}ns/settings", json={"owner_part_ids": [p1_id]}, headers=A)
            chk("파트1만 남기기 → 파트2 못 고침", (await edit_probe(h, P + "ns", U2)).status_code, 403)
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
                # 파트의 조직 컬렉션(v2.160 #83)도 — 파트만 지우면 담당 없는 조직 컬렉션이 남는다
                await c.execute("DELETE FROM ops_namespace WHERE kind = 'org_space' AND name IN "
                                "(SELECT 'org:part:' || id FROM ops_part WHERE name LIKE $1)", P + "%")
                await c.execute("DELETE FROM ops_part WHERE name LIKE $1", P + "%")
                left = await c.fetchval("""SELECT (SELECT count(*) FROM ops_namespace WHERE name LIKE $1)
                                                + (SELECT count(*) FROM ops_part WHERE name LIKE $1)
                                                + (SELECT count(*) FROM ops_user WHERE username LIKE $1)""", P + "%")
            print("정리 후 남은 시나리오 데이터:", left)
    # 불변식(#82 과도기, 리뷰 10/22): 옛 칸 owner_part_id = 담당 표의 가장 작은 part_id — 어긋나면 다음 기동 복사가 지운 담당을 되살리던 구조
    async with get_conn() as c:
        mismatch = await c.fetchval("""SELECT count(*) FROM ops_namespace n WHERE n.owner_part_id IS DISTINCT FROM
                                       (SELECT min(part_id) FROM ops_namespace_owner o WHERE o.namespace_id = n.id)""")
    chk("옛 칸 거울 = 담당 표(전체 영역)", mismatch, 0)
    print("결과:", "통과" if all(results) else "실패", f"{sum(results)}/{len(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
