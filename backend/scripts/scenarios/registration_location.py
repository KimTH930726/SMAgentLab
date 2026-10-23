"""지식 등록 넣을 곳(E2, 2026-10-23 v2.165) 실서버 시나리오 — 권한·제안(승인 대기)·원장 확정·초안 옮기기가 실제 서버·DB에서 맞나.

1) 담당이 아닌 사용자: 판정 없이 등록 → 403 / 판정 받고 등록 → 201 + 승인 대기 + 원장 "suggested" / 같은 판정 다시 → 403 / 남의 판정 → 403
   + 제안은 담당 용어집에 자동 용어를 만들지 않는다(리뷰 H1)
2) 담당이 아닌 사용자의 묶음 등록(/bulk): 원문·컨플루언스 페이지 연결을 떼고 전부 승인 대기 — 담당이 같은 페이지로 넣어 둔 조각은
   그대로 사용 중(연결을 떼는 이유, 리뷰) · 201건 이상은 400
3) 관리자: 미리보기 초안(보던 곳 A) → 다른 곳 B로 등록 → 초안이 B로 옮겨지고 조각이 원문에 연결 · 원장 choice=other, 고른 곳 기록
실서버(localhost:8000), 판정 LLM은 설정대로(로컬). 자기가 만든 것(__scn_loc_ 접두 사용자·이 실행의 지식·원장·초안)만 쓰고 끝나면 지운다.
실행(컨테이너 안): python scripts/scenarios/registration_location.py   → 마지막 줄 "결과: 통과 n/n"
"""
import asyncio
import json
import sys
import time

import httpx

sys.path.insert(0, "/app")
from core.database import get_conn, init_pool  # noqa: E402
from core.security import create_access_token  # noqa: E402

API = "http://localhost:8000"
P = "__scn_loc_"
results: list[bool] = []


def chk(name: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"  [{'OK' if ok else 'XX'}] {name}: {got} (기대 {want})")


def tok(uid: int, name: str, role: str) -> dict:
    return {"Authorization": "Bearer " + create_access_token({"sub": str(uid), "username": name, "role": role})}


async def main() -> int:
    await init_pool()
    run = str(int(time.time()))
    made_k: list[int] = []
    made_d: list[int] = []
    made_docs: list[int] = []
    async with get_conn() as c:
        g_start = await c.fetchval("SELECT coalesce(max(id), 0) FROM rag_glossary")   # 이번 실행이 만든 용어(자동 추출 포함) 정리 기준
        # 관리자도 시나리오 전용 임시 계정 — 정리(이번 실행 사용자가 만든 용어 삭제)가 실제 관리자 것을 건드리지 않게(재리뷰 N3)
        admin = await c.fetchrow("INSERT INTO ops_user (username, hashed_password, role) VALUES ($1, 'x', 'admin') RETURNING id, username", P + "admin")
        u1 = await c.fetchval("INSERT INTO ops_user (username, hashed_password, role) VALUES ($1, 'x', 'user') RETURNING id", P + "u1")
        u2 = await c.fetchval("INSERT INTO ops_user (username, hashed_password, role) VALUES ($1, 'x', 'user') RETURNING id", P + "u2")
        # 담당 있는 업무 컬렉션 둘(A: 미리보기 곳, B: 넣을 곳) — 임시 사용자(파트 없음)는 둘 다 담당 아님
        owned = [r["name"] for r in await c.fetch(
            "SELECT DISTINCT n.name FROM ops_namespace n JOIN ops_namespace_owner o ON o.namespace_id = n.id "
            "WHERE n.kind = 'domain' ORDER BY n.name LIMIT 2")]
    U1, U2, A = tok(u1, P + "u1", "user"), tok(u2, P + "u2", "user"), tok(admin["id"], admin["username"], "admin")
    ns_a, ns_b = owned[0], owned[1]
    text = f"{P}{run} 가상 절차 — 배차 화면 상태 코드 설명(시나리오용 가상 문장)."
    try:
        async with httpx.AsyncClient(base_url=API, timeout=180) as h:
            print(f"1) 담당 아닌 사용자 단건 — 넣을 곳 {ns_b}")
            r = await h.post("/api/knowledge", json={"namespace": ns_b, "content": text}, headers=U1)
            chk("판정 없이 → 403", r.status_code, 403)
            loc = (await h.post("/api/knowledge/locate", json={"text": text}, headers=U1)).json()
            made_d.append(loc["decision_id"])
            r = await h.post("/api/knowledge", json={"namespace": ns_b, "content": text, "decision_id": loc["decision_id"],
                                                     "location_choice": "other"}, headers=U1)
            chk("판정 받고 → 201", r.status_code, 201)
            kid = r.json()["id"]
            made_k.append(kid)
            chk("응답 = 승인 대기", r.json().get("pending_review"), True)
            async with get_conn() as c:
                st = await c.fetchval("SELECT status FROM rag_knowledge WHERE id = $1", kid)
                fin = json.loads(await c.fetchval("SELECT final_value FROM ops_ai_decision WHERE id = $1", loc["decision_id"]))
                tgt = await c.fetchval("SELECT target_ids FROM ops_ai_decision WHERE id = $1", loc["decision_id"])
            chk("DB 상태 승인 대기(검색에 안 나옴)", st, "pending_review")
            chk("원장 확정: 제안·고른 방법", (fin.get("suggested"), fin.get("choice")), (True, "other"))
            chk("원장 대상 = 그 지식", list(tgt), [kid])
            r = await h.post("/api/knowledge", json={"namespace": ns_b, "content": text + " 다시", "decision_id": loc["decision_id"]}, headers=U1)
            chk("같은 판정 다시 → 403", r.status_code, 403)
            loc2 = (await h.post("/api/knowledge/locate", json={"text": text}, headers=U1)).json()
            made_d.append(loc2["decision_id"])
            r = await h.post("/api/knowledge", json={"namespace": ns_b, "content": text, "decision_id": loc2["decision_id"]}, headers=U2)
            chk("남의 판정 → 403", r.status_code, 403)
            await asyncio.sleep(8)   # 자동 용어 추출은 백그라운드(LLM) — 돌았다면 끝났을 시간
            async with get_conn() as c:
                g_u1 = await c.fetchval("SELECT count(*) FROM rag_glossary WHERE id > $1 AND created_by_user_id = $2", g_start, u1)
            chk("제안은 용어집에 안 씀(H1)", g_u1, 0)

            print("2) 담당 아닌 사용자 묶음 — 원문·페이지 연결 떼고 승인 대기, 담당의 같은 페이지 조각은 그대로")
            page_id = f"{P}{run}page"
            r = await h.post("/api/knowledge/bulk", headers=A, json={   # 담당(관리자)이 먼저 그 페이지로 넣어 둔 조각
                "namespace": ns_b, "source_file": P + "owner", "source_type": "confluence",
                "items": [{"content": f"{text} 담당 조각 — 컨플루언스 페이지 교체 확인용", "confluence_page_id": page_id, "confluence_version": 1}]})
            ojob = r.json()["job_id"]
            for _ in range(60):
                if (await h.get(f"/api/knowledge/ingestion-jobs/{ojob}", headers=A)).json()["status"] != "processing":
                    break
                await asyncio.sleep(1)
            async with get_conn() as c:
                owner_ids = [x["id"] for x in await c.fetch("SELECT id FROM rag_knowledge WHERE ingestion_job_id = $1", ojob)]
            made_k += owner_ids
            r = await h.post("/api/knowledge/bulk", headers=U1, json={
                "namespace": ns_b, "source_file": P + "bulk", "source_type": "paste_split", "decision_id": loc2["decision_id"],
                "items": [{"content": f"{text} 묶음 {i}", "source_document_id": 999999, "confluence_page_id": page_id} for i in range(201)]})
            chk("제안 묶음 201건 → 400(상한)", r.status_code, 400)
            r = await h.post("/api/knowledge/bulk", headers=U1, json={
                "namespace": ns_b, "source_file": P + "bulk", "source_type": "paste_split", "decision_id": loc2["decision_id"],
                "items": [{"content": f"{text} 묶음 {i}", "source_document_id": 999999, "confluence_page_id": page_id} for i in range(2)]})
            chk("묶음 → 201", r.status_code, 201)
            job = r.json()["job_id"]
            for _ in range(60):
                job_row = (await h.get(f"/api/knowledge/ingestion-jobs/{job}", headers=A)).json()
                if job_row["status"] != "processing":
                    break
                await asyncio.sleep(1)
            async with get_conn() as c:
                rows = await c.fetch("SELECT id, status, source_document_id, confluence_page_id FROM rag_knowledge WHERE ingestion_job_id = $1", job)
            made_k += [x["id"] for x in rows]
            chk("job 끝남 · 승인 대기 2", (job_row["status"], job_row["pending_chunks"]), ("completed", 2))
            chk("조각 전부 승인 대기", sorted({x["status"] for x in rows}), ["pending_review"])
            chk("원문·페이지 연결 뗌", all(x["source_document_id"] is None and x["confluence_page_id"] is None for x in rows), True)
            async with get_conn() as c:
                ost = sorted({x["status"] for x in await c.fetch("SELECT status FROM rag_knowledge WHERE id = ANY($1::bigint[])", owner_ids)})
            chk("담당의 같은 페이지 조각은 그대로 사용 중", (len(owner_ids), ost), (1, ["active"]))

            print(f"3) 관리자 — 미리보기 {ns_a} → 넣을 곳 {ns_b}, 초안 따라 옮겨짐")
            body = f"# {P}{run} 제목\n\n{text} 첫째 단락.\n\n## 둘째\n\n{text} 둘째 단락."
            prev = (await h.post("/api/knowledge/import/text-split/preview", headers=A,
                                 json={"raw_text": body, "strategy": "section", "namespace": ns_a})).json()
            doc = prev.get("source_document_id")
            if doc:
                made_docs.append(doc)
            chk("미리보기 초안 생김", bool(doc), True)
            loc3 = (await h.post("/api/knowledge/locate", json={"text": body}, headers=A)).json()
            made_d.append(loc3["decision_id"])
            rc = await h.post("/api/knowledge/bulk/replace-check", headers=A,
                              json={"namespace": ns_b, "source_document_ids": [doc], "confluence_page_ids": []})
            chk("교체 확인(초안 먼저 옮김) → 200", rc.status_code, 200)
            items = [{"content": ch, "heading_path": hp, "source_document_id": doc} for ch, hp in zip(prev["chunks"], prev["heading_paths"])]
            r = await h.post("/api/knowledge/bulk", headers=A, json={"namespace": ns_b, "items": items, "source_file": P + "doc",
                                                                     "source_type": "paste_split", "decision_id": loc3["decision_id"],
                                                                     "location_choice": "other"})
            job3 = r.json()["job_id"]
            for _ in range(60):
                if (await h.get(f"/api/knowledge/ingestion-jobs/{job3}", headers=A)).json()["status"] != "processing":
                    break
                await asyncio.sleep(1)
            async with get_conn() as c:
                ns_b_id = await c.fetchval("SELECT id FROM ops_namespace WHERE name = $1", ns_b)
                d_ns, d_st = await c.fetchrow("SELECT namespace_id, status FROM rag_source_document WHERE id = $1", doc)
                ks = await c.fetch("SELECT id, namespace_id, source_document_id FROM rag_knowledge WHERE ingestion_job_id = $1", job3)
                fin3 = json.loads(await c.fetchval("SELECT final_value FROM ops_ai_decision WHERE id = $1", loc3["decision_id"]))
                ai3 = await c.fetchval("SELECT ai_value->>'namespace_id' FROM ops_ai_decision WHERE id = $1", loc3["decision_id"])
                ch3 = await c.fetchval("SELECT human_changed FROM ops_ai_decision WHERE id = $1", loc3["decision_id"])
            made_k += [x["id"] for x in ks]
            chk("초안이 넣을 곳으로 옮겨지고 사용 중", (d_ns, d_st), (ns_b_id, "active"))
            chk("조각 전부 넣을 곳 + 원문 연결", all(x["namespace_id"] == ns_b_id and x["source_document_id"] == doc for x in ks) and len(ks) > 0, True)
            chk("원장: 고른 곳·제안 아님·choice", (fin3["namespace_id"], fin3["suggested"], fin3["choice"]), (ns_b_id, False, "other"))
            chk("원장: AI와 다르면 changed", ch3, str(ns_b_id) != (ai3 or ""))
    finally:
        async with get_conn() as c:
            await c.execute("DELETE FROM rag_knowledge WHERE id = ANY($1::bigint[])", made_k)
            await c.execute("DELETE FROM rag_knowledge WHERE content LIKE $1", f"{P}{run}%")
            await c.execute("DELETE FROM rag_source_document WHERE id = ANY($1::int[])", made_docs)
            await c.execute("DELETE FROM rag_ingestion_job WHERE source_file LIKE $1", f"{P}%")
            await c.execute("DELETE FROM ops_ai_decision WHERE id = ANY($1::bigint[])", made_d)
            # 이번 실행이 만든 용어 — 자동 추출(관리자 등록 뒤)이 만든 것 포함. 시작 뒤 생긴 것 중 두 컬렉션·이 실행 사용자 것만
            g_del = await c.execute("DELETE FROM rag_glossary WHERE id > $1 AND namespace_id IN (SELECT id FROM ops_namespace WHERE name = ANY($2::text[])) "
                                    "AND created_by_user_id = ANY($3::int[])", g_start, [ns_a, ns_b], [u1, u2, admin["id"]])
            print(f"  정리: 용어 {g_del}")
            await c.execute("DELETE FROM ops_user WHERE id = ANY($1::int[])", [u1, u2, admin["id"]])
            left = await c.fetchval("SELECT count(*) FROM rag_knowledge WHERE content LIKE $1", f"{P}{run}%")
            print(f"  정리: 지식 남은 {left} · 원장 {len(made_d)}줄 · 초안 {len(made_docs)} · 임시 사용자 삭제")
    print(f"결과: 통과 {sum(results)}/{len(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
