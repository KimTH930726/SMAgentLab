"""AI 판단 원장 실서버·실DB 시나리오 (2026-10-23, v2.163 #85) — 원장 규칙이 실제 DB에서 지켜지나(전/후 값).

1) POST /api/knowledge/locate(실서버, 판정 LLM 1회 — 가상 문장) → 원장 한 줄, 원문 안 남음, 조회 전용 403
2) 확정: 남의 줄은 확정 안 됨 · 두 번 확정 안 됨 · "AI가 틀렸나요?"는 바꾼 사람만
3) 채팅 되묻기: 물음 → 고른 범위가 선택지에 없으면 그 글자를 남기지 않음 · 열린 줄이 둘이면 옛 줄은 "넘어감"으로 닫힘
자기가 만든 것(__scn_ai_ 사용자·이 실행의 원장 줄)만 쓰고 끝나면 지운다.
실행(컨테이너 안): python scripts/scenarios/ai_decision_ledger.py   → 마지막 줄 "결과: 통과 n/n", 실패 시 종료 코드 1
"""
import asyncio
import json
import sys

import httpx

sys.path.insert(0, "/app")
from core.database import get_conn, init_pool  # noqa: E402
from core.security import create_access_token  # noqa: E402
from service.decision import ledger  # noqa: E402

API = "http://localhost:8000"
P = "__scn_ai_"
TEXT = f"{P} 딜리버스 배차 화면에서 주문 상태 코드 30은 라이더 배정 대기를 뜻한다(가상 예시)."
results: list[bool] = []


def chk(name: str, got, want) -> None:
    ok = got == want
    results.append(ok)
    print(f"  [{'OK' if ok else 'XX'}] {name}: {got} (기대 {want})")


def tok(uid: int, name: str, role: str) -> dict:
    return {"Authorization": "Bearer " + create_access_token({"sub": str(uid), "username": name, "role": role})}


async def main() -> int:
    await init_pool()
    made: list[int] = []
    async with get_conn() as c:
        admin = await c.fetchrow("SELECT id, username FROM ops_user WHERE role = 'admin' ORDER BY id LIMIT 1")
        u1 = await c.fetchval("INSERT INTO ops_user (username, hashed_password, role) VALUES ($1, 'x', 'user') RETURNING id", P + "u1")
        u2 = await c.fetchval("INSERT INTO ops_user (username, hashed_password, role) VALUES ($1, 'x', 'user') RETURNING id", P + "u2")
        uv = await c.fetchval("INSERT INTO ops_user (username, hashed_password, role) VALUES ($1, 'x', 'viewer') RETURNING id", P + "v")
        # 일반 사용자(팀에 든 파트 소속) — 조직 후보·이웃이 내 팀으로만 좁혀지는지
        part = await c.fetchrow("SELECT p.id, p.team_id FROM ops_part p JOIN ops_namespace n ON n.name = 'org:team:' || p.team_id "
                                "WHERE p.team_id IS NOT NULL ORDER BY p.id LIMIT 1")
        up = await c.fetchval("INSERT INTO ops_user (username, hashed_password, role, part_id) VALUES ($1, 'x', 'user', $2) RETURNING id",
                              P + "p", part["id"] if part else None)
        other_team_org = {r["name"] for r in await c.fetch(
            "SELECT n.name FROM ops_namespace n LEFT JOIN ops_part p ON p.id = n.owner_part_id "
            "WHERE n.kind = 'org_space' AND COALESCE(n.owner_team_id, p.team_id) IS DISTINCT FROM $1", part["team_id"] if part else None)}
    try:
        print("1) 위치 판정 엔드포인트(실서버)")
        async with httpx.AsyncClient(base_url=API, timeout=180) as h:
            r = await h.post("/api/knowledge/locate", json={"text": TEXT}, headers=tok(uv, P + "v", "viewer"))
            chk("조회 전용 → 403", r.status_code, 403)
            r = await h.post("/api/knowledge/locate", json={"text": "   "}, headers=tok(admin["id"], admin["username"], "admin"))
            chk("빈 내용 → 400", r.status_code, 400)
            r = await h.post("/api/knowledge/locate", json={"text": TEXT, "preset_namespace": "없는컬렉션"},
                             headers=tok(admin["id"], admin["username"], "admin"))
            chk("판정 → 200", r.status_code, 200)
            body = r.json()
            made.append(body["decision_id"])
            chk("없는 preset → 노란 줄에 표시", "넘어온 컬렉션을 찾지 못해" in body["fallback"], True)
            chk("응답 모양(제안·선택지·이웃 수)", all(k in body for k in ("proposed", "options", "neighbor_total", "ask")), True)
            if part:
                r = await h.post("/api/knowledge/locate", json={"text": TEXT}, headers=tok(up, P + "p", "user"))
                chk("일반 사용자 판정 → 200", r.status_code, 200)
                pb = r.json()
                made.append(pb["decision_id"])
                shown = {x["name"] for x in pb["options"] + pb["neighbors"] + ([pb["proposed"]] if pb["proposed"] else [])}
                chk("다른 팀 조직 컬렉션이 제안·선택지·이웃에 없음", sorted(shown & other_team_org), [])
                chk("쓸 수 있는지 표시가 있음", all("writable" in x for x in pb["options"]), True)
        async with get_conn() as c:
            row = await c.fetchrow("SELECT * FROM ops_ai_decision WHERE id = $1", body["decision_id"])
            chk("원장 한 줄(kind·확정 전)", (row["kind"], row["final_value"]), ("knowledge_location", None))
            blob = json.dumps([row["ai_value"], row["signals"], row["model"]], ensure_ascii=False)
            chk("원문이 원장에 없음", P in blob or "라이더 배정" in blob, False)
            chk("실패 종류·preset 표시가 signals에", all(k in json.loads(row["signals"]) for k in ("llm_fail", "preset_missing")), True)

            print("2) 확정 규칙(실DB)")
            did = await ledger.record(c, "scn_kind", ai_value={"namespace_id": 1}, confident=True, signals={}, user_id=u1)
            made.append(did)
            chk("남의 줄 확정 → 안 됨", await ledger.finalize(c, did, kind="scn_kind", final_value={"namespace_id": 2}, changed=True, user_id=u2), False)
            chk("종류가 다르면 확정 안 됨", await ledger.finalize(c, did, kind="other", final_value={"namespace_id": 2}, changed=True, user_id=u1), False)
            chk("내 줄 확정 → 됨", await ledger.finalize(c, did, kind="scn_kind", final_value={"namespace_id": 2}, changed=True, user_id=u1,
                                                   target_type="knowledge", target_ids=[11, 12]), True)
            chk("두 번 확정 → 안 됨", await ledger.finalize(c, did, kind="scn_kind", final_value={"namespace_id": 3}, changed=True, user_id=u1), False)
            fin = await c.fetchrow("SELECT final_value, human_changed, target_ids FROM ops_ai_decision WHERE id = $1", did)
            chk("확정 값 그대로(첫 확정)", (json.loads(fin["final_value"]), fin["human_changed"], list(fin["target_ids"])),
                ({"namespace_id": 2}, True, [11, 12]))
            chk("남이 'AI가 틀렸나요?' 답 → 안 됨", await ledger.set_verdict(c, did, verdict="wrong", reason="r", user_id=u2), False)
            chk("바꾼 사람 답 → 됨", await ledger.set_verdict(c, did, verdict="wrong", reason="업무 이름 없음", user_id=u1), True)

            print("3) 채팅 되묻기(실DB)")
            conv = -987654   # 실제 대화와 겹치지 않는 가짜 대화 id
            a1 = await ledger.clarify_asked(c, conversation_id=conv, message_id=1, clarify={"options": [{"scope": "A > x"}, {"scope": "*"}]}, user_id=u1)
            a2 = await ledger.clarify_asked(c, conversation_id=conv, message_id=2, clarify={"options": [{"scope": "B > y"}, {"scope": "*"}]}, user_id=u1)
            made += [a1, a2]
            chk("닫기(선택지에 없는 글자 보냄)", await ledger.clarify_answered(c, conversation_id=conv, picked_scope="주입된 원문 글자", user_id=u1), True)
            v1, v2 = [json.loads(await c.fetchval("SELECT final_value FROM ops_ai_decision WHERE id = $1", x)) for x in (a1, a2)]
            chk("새 줄 = 고른 글자 안 남김", v2, {"picked_unknown": True})
            chk("옛 줄 = 넘어감으로 닫힘", v1, {"superseded": True})
            a3 = await ledger.clarify_asked(c, conversation_id=conv, message_id=3, clarify={"options": [{"scope": "C > z"}, {"scope": "*"}]}, user_id=u1)
            made.append(a3)
            await ledger.clarify_answered(c, conversation_id=conv, picked_scope="C > z", user_id=u1)
            chk("고른 범위가 선택지면 그대로", json.loads(await c.fetchval("SELECT final_value FROM ops_ai_decision WHERE id = $1", a3)), {"picked": "C > z"})
            chk("열린 줄 없음 → 닫을 것 없음", await ledger.clarify_answered(c, conversation_id=conv, picked_scope=None, user_id=u1), False)
            chk("대화 id 글자 저장(인덱스 식과 같은 모양)",
                await c.fetchval("SELECT signals->>'conversation_id' FROM ops_ai_decision WHERE id = $1", a3), str(conv))
    finally:
        async with get_conn() as c:
            await c.execute("DELETE FROM ops_ai_decision WHERE id = ANY($1::bigint[])", made)
            # 만든 id로만 지운다 — LIKE '__scn_ai_%'의 _는 아무 글자라 남의 사용자가 걸릴 수 있다(재리뷰 10/23)
            await c.execute("DELETE FROM ops_user WHERE id = ANY($1::int[])", [u1, u2, uv, up])
            left = await c.fetchval("SELECT count(*) FROM ops_ai_decision WHERE id = ANY($1::bigint[])", made)
            print(f"  정리: 원장 줄 {len(made)}개 삭제(남은 {left}) · 임시 사용자 삭제")
    print(f"결과: 통과 {sum(results)}/{len(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
