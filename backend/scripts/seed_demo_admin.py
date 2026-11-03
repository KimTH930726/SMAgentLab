"""관리 화면 예시 데이터(11/03, 사용자 "AI 감사 로그랑 사용자 피드백 테스트 데이터 — 빈 화면이라 뭔 화면인지 감이 안 온다").

품질 › AI 감사 로그(ops_ai_decision)와 품질 › 사용자 피드백(ops_user_feedback)에 화면을 이해할 만큼만 가상 줄을 넣는다.
모양은 실제로 쓰는 쪽과 같게: 등록 위치(agents/knowledge_rag/knowledge/router._finalize_location) · 되묻기(service/decision/ledger.clarify_*) ·
원문 확인(service/policy/source_check.ledger_outcome) · 다른 컬렉션 제안(service/chat/collection_suggest) · 피드백(service/user_feedback/router).
글은 전부 지어낸 운영 문장 — 지식·컨플루언스 원문을 쓰지 않는다. 대상 id(target_ids)는 비워 둔다(실제 지식·카드에 이어 붙지 않게).

표시(지울 때 이것만 지운다):
  · AI 판단 원장: signals.demo = true AND model = 'demo-seed'
  · 사용자 피드백: body가 '[예시] '로 시작 AND context.demo = true (화면엔 [예시]만 보이고 demo 칸은 안 보임)
다른 기능과 안 엉키게: 채팅 대화 id는 'demo-…'(되묻기 닫기는 숫자 대화 id로만 찾음), 메시지 id는 음수(컬렉션 제안 누름은 실제 메시지 id로만 찾음),
  위치 판정은 확정된 줄만(확정 안 된 위치 줄은 하루 뒤 자동 정리 대상이라 넣지 않음).
주의: 번복 줄이 자가 개선 진단(scripts/eval_improve.py — 운영 번복 → 개선 후보)의 입력이 된다 — 진단 전에 --clean.

실행(컨테이너): python scripts/seed_demo_admin.py            # 넣을 것 미리보기(아무것도 안 바꿈)
               python scripts/seed_demo_admin.py --apply    # 넣기(이미 넣은 예시가 있으면 멈춤)
               python scripts/seed_demo_admin.py --clean    # 지울 개수만 보기
               python scripts/seed_demo_admin.py --clean --apply   # 예시만 지우기
"""
import argparse
import asyncio
import json
import random
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

MODEL = "demo-seed"
FB_PREFIX = "[예시] "
DEC_WHERE = "signals->>'demo' = 'true' AND model = 'demo-seed'"
FB_WHERE = "body LIKE '[예시] %' AND context->>'demo' = 'true'"

# ── 등록 위치: (AI가 고른 시스템 코드, 사람이 넣은 곳 코드 또는 None(그대로), 번복 이유, 확신, AI 이유 문장)
LOCATION = [
    ("SYS-DLV", None, None, True, "배송 지연 보상 기준을 다루는 운영 안내라 딜리버스 시스템에 맞음"),
    ("SYS-HP", None, None, True, "홈페이지 로그인 오류 대응 절차라 홈페이지 시스템"),
    ("SYS-APP", None, None, True, "앱 푸시 미수신 문의 처리 순서 — 앱 시스템"),
    ("SYS-OST", None, None, False, "온라인스토어 주문 취소 환불 일정 안내 — 온라인스토어 시스템으로 보임"),
    ("SYS-EXT", None, None, True, "외부 결제사 점검 공지 대응 — 외부서비스 시스템"),
    ("SYS-DLV", None, None, True, "라이더 배정 지연 시 고객 안내 문구 — 딜리버스 시스템"),
    ("SYS-HP", None, None, False, "회원 정보 변경 화면 오류 문의 — 홈페이지 시스템 후보"),
    ("SYS-APP", None, None, True, "앱 버전 강제 업데이트 안내 — 앱 시스템"),
    ("SYS-OST", None, None, True, "온라인스토어 재고 동기화 지연 대응 — 온라인스토어 시스템"),
    ("SYS-DLV", None, None, False, "배달 가능 지역 변경 반영 확인 — 딜리버스 시스템 후보"),
    ("SYS-EXT", None, None, True, "제휴사 API 응답 지연 시 재시도 기준 — 외부서비스 시스템"),
    ("SYS-HP", None, None, True, "공지사항 게시 오류 처리 — 홈페이지 시스템"),
    ("SYS-APP", None, None, True, "앱 로그인 세션 만료 문의 — 앱 시스템"),
    ("SYS-OST", None, None, True, "선물하기 주문 배송지 수정 가능 시점 — 온라인스토어 시스템"),
    ("SYS-DLV", None, None, True, "배달 주문 부분 취소 처리 순서 — 딜리버스 시스템"),
    ("SYS-HP", None, None, False, "이벤트 페이지 노출 오류 — 홈페이지 시스템 후보"),
    ("SYS-EXT", None, None, True, "외부 본인인증 실패 문의 대응 — 외부서비스 시스템"),
    ("SYS-APP", None, None, True, "앱 결제 수단 등록 실패 문의 — 앱 시스템"),
    ("SYS-OST", None, None, False, "온라인스토어 쿠폰 적용 오류 문의 — 온라인스토어 시스템 후보"),
    # 번복 5건 — 이유 틀림 2 · 둘 다 맞음 1 · 상황이 바뀜 1 · 이유 안 남김 1 (하나는 확신인데 틀림)
    ("SYS-HP", "SYS-APP", "wrong", True, "회원 등급 조회 화면 오류 — 홈페이지 시스템으로 판단"),
    ("SYS-OST", "SYS-DLV", "wrong", False, "주문 상태 미변경 문의 — 온라인스토어 시스템으로 판단"),
    ("SYS-EXT", "SYS-HP", "both_ok", False, "간편 로그인 연동 오류 — 외부서비스 시스템으로 판단"),
    ("SYS-DLV", "SYS-OST", "context_changed", False, "픽업 주문 알림 지연 — 딜리버스 시스템으로 판단(담당 이관 전 기준)"),
    ("SYS-APP", "SYS-EXT", None, False, "앱 내 제휴 쿠폰 노출 오류 — 앱 시스템으로 판단"),
]

# ── 되묻기: (선택지 범위들, 결과) 결과 = 범위 하나 · '*'(전부 보기) · 'typed'(직접 다시 물음) · None(아직 답 없음)
CLARIFY = [
    (["적립 기준", "등급 산정", "등급 혜택"], "적립 기준"),
    (["발급 조건", "사용 제한", "유효기간"], "사용 제한"),
    (["주문 취소", "부분 환불", "결제 수단 변경"], "부분 환불"),
    (["충전 한도", "잔액 이전", "분실 신고"], "잔액 이전"),
    (["배달 가능 지역", "최소 주문 금액", "배달비"], "배달비"),
    (["ref:VIP", "ref:일반"], "ref:VIP"),
    (["가입 조건", "탈퇴 처리", "휴면 전환"], "휴면 전환"),
    (["참여 조건", "보상 지급", "중복 참여"], "보상 지급"),
    (["ref:매장", "ref:온라인"], "ref:온라인"),
    (["제휴사 적립", "제휴 할인", "제휴 해지"], "제휴 할인"),
    (["영수증 재발행", "현금영수증", "세금계산서"], "현금영수증"),
    (["발급 조건", "사용 제한", "유효기간"], "유효기간"),
    # 번복 3건 — 직접 다시 물음 2 · 전부 보기 1
    (["적립 기준", "등급 산정", "등급 혜택"], "typed"),
    (["주문 취소", "부분 환불", "결제 수단 변경"], "typed"),
    (["충전 한도", "잔액 이전", "분실 신고"], "*"),
    # 아직 답 없음 1건(최근)
    (["배달 가능 지역", "최소 주문 금액", "배달비"], None),
]

# ── 원문 확인: (검사 종류, 검사 출처, 결과) 결과 = 'resolved' 또는 문제 없음 사유
SOURCE = [
    ("conflict", "v0_load_check", "resolved"), ("question", "v0_load_check", "resolved"), ("ref_error", "v0_load_check", "resolved"),
    ("question", "golden_set", "resolved"), ("conflict", "golden_set", "resolved"), ("question", "v0_load_check", "resolved"),
    ("ref_error", "golden_set", "resolved"), ("conflict", "v0_load_check", "resolved"),
    ("question", "golden_set", "source_ok"), ("conflict", "v0_load_check", "already_fixed"),   # 번복 2건(검사가 괜히 짚음)
]

# ── 다른 컬렉션 제안: (제안한 정책 컬렉션 코드, 관련도, 지금 컬렉션 최고 관련도, 눌렀나)
SUGGEST = [
    ("POL-CPN", 0.71, 0.48, True), ("POL-RWD", 0.66, 0.51, False), ("POL-DLV", 0.74, 0.42, True), ("POL-MBR", 0.63, 0.55, False),
    ("POL-CARD", 0.69, 0.47, True), ("POL-ORD", 0.62, 0.53, False), ("POL-OST", 0.68, 0.50, False), ("POL-PTN", 0.72, 0.44, True),
    ("POL-CHL", 0.61, 0.52, False), ("POL-CPN", 0.67, 0.49, False),
]

# ── 사용자 피드백: (보낸 사람, 종류, 글, 화면, 오류 문맥, 상태, 관리자 메모)
FEEDBACK = [
    ("xogns09", "bug", "채팅에서 답변이 중간에 끊기고 '연결이 끊어졌어요'가 떠요. 다시 물으면 됩니다.", "/chat",
     {"message": "응답 스트림이 중간에 끊어졌습니다", "status": 502, "path": "/api/chat/stream", "source": "chat"}, "new", None),
    ("xogns08", "idea", "답변 근거 카드에 문서 수정일도 같이 보이면 최신 정보인지 판단하기 쉬울 것 같아요.", "/chat", None, "new", None),
    ("xogns09", "idea", "자주 묻는 질문 몇 개를 채팅 첫 화면에 버튼으로 두면 좋겠습니다. (배송 지연 보상 기준, 쿠폰 재발급 등)", "/chat", None, "new", None),
    ("admin", "bug", "지식 관리 목록에서 필터를 바꾼 뒤 뒤로 가기를 하면 필터가 초기화돼요.", "/admin/k.knowledge", None, "new", None),
    ("xogns08", "bug", "대화 제목을 바꾸고 새로고침하면 예전 제목으로 돌아가요.", "/chat",
     {"message": "저장하지 못했습니다", "status": 500, "path": "/api/conversations/rename", "source": "dialog"}, "planned", "다음 배포 때 같이 고칩니다"),
    ("xogns09", "idea", "답변을 복사할 때 근거 링크도 같이 복사되면 좋겠어요.", "/chat", None, "planned", None),
    ("xogns08", "bug", "정정 요청을 보낸 뒤 내 정정 목록에 바로 안 보이고 한참 뒤에 보여요.", "/chat", None, "in_progress", "원인 확인 중 — 목록 캐시 시간"),
    ("admin", "idea", "사용자 피드백 목록에서 보낸 사람 파트로 거를 수 있으면 좋겠습니다.", "/admin/q.feedback", None, "in_progress", None),
    ("xogns09", "bug", "다크 모드에서 근거 카드 글씨가 배경과 비슷해서 잘 안 보여요.", "/chat", None, "done", "v2.199 반영 — 근거 카드 글자색 조정"),
    ("xogns08", "idea", "답변 아래 '도움이 됐어요' 버튼이 있으면 좋겠어요.", "/chat", None, "done", "v2.198 반영 — 답변 아래 피드백 버튼 추가"),
    ("xogns09", "idea", "채팅 답변을 엑셀로 내려받는 기능이 있으면 좋겠습니다.", "/chat", None, "declined",
     "답변 원문이 밖으로 나가면 보안 검토가 필요해 지금은 하지 않아요 — 복사로 대신해 주세요"),
    ("xogns08", "bug", "채팅 답이 가끔 중간에 끊겨요.", "/chat", None, "duplicate", None),   # 첫 건과 같음
]


def _days_ago(rng, max_days: float, min_days: float = 0.0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=rng.uniform(min_days, max_days))


async def _lookups(conn) -> tuple[dict, dict]:
    ns = {r["code"]: (r["id"], r["name"]) for r in await conn.fetch("SELECT id, code, name FROM ops_namespace WHERE code IS NOT NULL")}
    users = {r["username"]: r["id"] for r in await conn.fetch("SELECT id, username FROM ops_user")}
    return ns, users


def build_decisions(ns: dict, users: dict, seed: int = 1103) -> list[dict]:
    """넣을 판단 줄(순수 — 미리보기·넣기가 같은 것). 날짜는 최근 30일에 흩는다."""
    rng = random.Random(seed)
    people = [users[u] for u in ("admin", "xogns09", "xogns08") if u in users] or [None]
    out: list[dict] = []
    codes_sys = [c for c in ns if c.startswith("SYS-")]

    for i, (ai_c, fin_c, why, conf, reason) in enumerate(LOCATION):
        ai_id = ns[ai_c][0]
        # 이웃 5건 — 확신이면 5중 4~5가 AI와 같고, 아니면 흩어짐
        if conf:
            nb = [ai_id] * rng.choice([4, 5])
        else:
            nb = [ai_id] * rng.choice([1, 2, 3])
        while len(nb) < 5:
            nb.append(ns[rng.choice([c for c in codes_sys if c != ai_c])][0])
        rng.shuffle(nb)
        nb_pairs = [[n, round(rng.uniform(0.55, 0.72), 4)] for n in nb]
        nb_pairs.sort(key=lambda x: -x[1])
        counts = Counter(nb)
        top, n_top = counts.most_common(1)[0]
        final_id = ns[fin_c][0] if fin_c else ai_id
        final = {"namespace_id": final_id, "choice": "other" if fin_c else "accept", "suggested": False}
        if why:
            final["override_reason"] = why
        u = rng.choice(people)
        at = _days_ago(rng, 30, 0.2)
        out.append({"kind": "knowledge_location", "ai_value": {"doc_type": "지식 문서", "namespace_id": ai_id}, "confident": conf,
                    "signals": {"ask": not conf, "vote": [top, n_top, 5], "reason": reason, "fallback": "", "llm_fail": "",
                                "text_len": rng.randint(180, 1600), "neighbors": nb_pairs, "preset_id": None, "proposed_id": ai_id,
                                "suggest_move": False, "preset_missing": False},
                    "prompt_version": "locate-v3", "target_type": "knowledge", "user": u, "at": at,
                    "final": final, "changed": bool(fin_c),
                    "verdict": why if why in ("wrong", "context_changed") else None})

    for i, (opts, res) in enumerate(CLARIFY):
        u = rng.choice(people)
        at = _days_ago(rng, 0.4) if res is None else _days_ago(rng, 30, 0.5)
        if res is None:
            final = None
        elif res == "typed":
            final = {"typed": True}
        else:
            final = {"picked": res}
        out.append({"kind": "chat_clarify", "ai_value": {"options": opts}, "confident": False,
                    "signals": {"conversation_id": f"demo-{i + 1}", "message_id": -(1000 + i), "n_options": len(opts)},
                    "prompt_version": "clarify-v2.134", "target_type": "message", "user": u, "at": at,
                    "final": final, "changed": False, "verdict": None})

    for i, (typ, src, res) in enumerate(SOURCE):
        u = users.get("admin", people[0])
        final = {"issue": True, "resolution": "resolved"} if res == "resolved" else {"issue": False, "reason": res}
        out.append({"kind": "source_check", "ai_value": {"issue": True, "type": typ, "source": src}, "confident": False,
                    "signals": {"item_id": None}, "prompt_version": f"source-check:{src}", "target_type": "source_check",
                    "user": u, "at": _days_ago(rng, 30, 0.5), "final": final, "changed": not final["issue"], "verdict": None})

    for i, (code, score, here, picked) in enumerate(SUGGEST):
        u = rng.choice(people)
        out.append({"kind": "collection_suggest", "ai_value": {"namespace": ns[code][1]}, "confident": False,
                    "signals": {"conversation_id": f"demo-s{i + 1}", "message_id": -(2000 + i), "score": score, "here": here},
                    "prompt_version": "suggest-c2-0.6", "target_type": "message", "user": u, "at": _days_ago(rng, 30, 0.3),
                    "final": {"picked": True} if picked else None, "changed": False, "verdict": None})
    for d in out:
        d["signals"]["demo"] = True
    return out


def build_feedback(users: dict, seed: int = 1103) -> list[dict]:
    rng = random.Random(seed + 1)
    out = []
    n = len(FEEDBACK)
    for i, (who, kind, body, page, ctx, status, note) in enumerate(FEEDBACK):
        at = datetime.now(timezone.utc) - timedelta(days=(n - i) * 2.2 + rng.uniform(0, 1))   # 오래된 것부터 — 첫 건이 가장 오래됨
        out.append({"reporter": users.get(who), "kind": kind, "body": FB_PREFIX + body, "page": page,
                    "context": {**(ctx or {}), "demo": True}, "status": status, "note": note, "at": at,
                    "decided_at": None if status == "new" else at + timedelta(hours=rng.uniform(3, 40)),
                    "decided_by": None if status == "new" else users.get("admin")})
    return out


async def count_demo(conn) -> tuple[dict, dict]:
    dec = {r["kind"]: r["n"] for r in await conn.fetch(f"SELECT kind, count(*) n FROM ops_ai_decision WHERE {DEC_WHERE} GROUP BY kind")}
    fb = {r["status"]: r["n"] for r in await conn.fetch(f"SELECT status, count(*) n FROM ops_user_feedback WHERE {FB_WHERE} GROUP BY status")}
    return dec, fb


async def insert(conn, decisions: list[dict], feedback: list[dict]) -> None:
    for d in sorted(decisions, key=lambda x: x["at"]):   # id 순 = 시간 순(감사 로그는 id 최신순으로 줄을 세운다)
        decided = d["final"] is not None
        await conn.execute(
            """INSERT INTO ops_ai_decision (kind, ai_value, confident, signals, model, prompt_version, created_by_user_id, created_at,
                                            target_type, target_ids, final_value, human_changed, changed_by_user_id, changed_at,
                                            ai_verdict, verdict_by_user_id)
               VALUES ($1, $2::jsonb, $3, $4::jsonb, $5, $6, $7, $8, $9, '{}', $10::jsonb, $11, $12, $13, $14, $15)""",
            d["kind"], json.dumps(d["ai_value"], ensure_ascii=False), d["confident"], json.dumps(d["signals"], ensure_ascii=False),
            MODEL, d["prompt_version"], d["user"], d["at"], d["target_type"],
            json.dumps(d["final"], ensure_ascii=False) if decided else None, d["changed"],
            d["user"] if decided else None, d["at"] + timedelta(minutes=3) if decided else None,
            d["verdict"], d["user"] if d["verdict"] else None)
    ids = []
    for f in feedback:
        ids.append(await conn.fetchval(
            """INSERT INTO ops_user_feedback (kind, body, page, reporter_user_id, status, admin_note, decided_by_user_id, decided_at,
                                              created_at, context)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb) RETURNING id""",
            f["kind"], f["body"], f["page"], f["reporter"], f["status"], f["note"], f["decided_by"], f["decided_at"], f["at"],
            json.dumps(f["context"], ensure_ascii=False)))
    # 중복 건은 첫 건(같은 증상)을 가리킨다
    for f, fid in zip(feedback, ids):
        if f["status"] == "duplicate":
            await conn.execute("UPDATE ops_user_feedback SET duplicate_of = $2 WHERE id = $1", fid, ids[0])


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="실제로 넣기(--clean과 같이 쓰면 실제로 지우기)")
    ap.add_argument("--clean", action="store_true", help="예시 줄만 지우기(--apply 없으면 지울 개수만)")
    a = ap.parse_args()

    from core.database import get_conn, init_pool
    await init_pool()
    async with get_conn() as conn:
        dec_now, fb_now = await count_demo(conn)
        if a.clean:
            print(f"지울 예시 — AI 판단 원장 {sum(dec_now.values())}줄 {dict(dec_now)} · 사용자 피드백 {sum(fb_now.values())}건 {dict(fb_now)}")
            if not a.apply:
                print("(미리보기 — 지우려면 --clean --apply)")
                return
            async with conn.transaction():
                # 예시 아닌 피드백이 예시를 중복으로 가리키면 FK가 SET NULL — 다른 줄은 지우지 않는다
                r1 = await conn.execute(f"DELETE FROM ops_ai_decision WHERE {DEC_WHERE}")
                r2 = await conn.execute(f"DELETE FROM ops_user_feedback WHERE {FB_WHERE}")
            print(f"지움 — 원장 {r1.split()[-1]}줄 · 피드백 {r2.split()[-1]}건")
            return

        ns, users = await _lookups(conn)
        need = {c for c, *_ in LOCATION} | {c for _, c, *_ in LOCATION if c} | {c for c, *_ in SUGGEST}
        missing = sorted(need - set(ns))
        if missing:
            print(f"멈춤: 컬렉션 코드가 없음 {missing}")
            sys.exit(1)
        decisions = build_decisions(ns, users)
        feedback = build_feedback(users)
        from service.eval.override import is_override
        ov = Counter(d["kind"] for d in decisions
                     if d["final"] and is_override({"kind": d["kind"], "final": d["final"], "ai": d["ai_value"], "signals": d["signals"]}))
        print(f"AI 판단 원장 {len(decisions)}줄: {dict(Counter(d['kind'] for d in decisions))}")
        print(f"  확정 {sum(1 for d in decisions if d['final'])} · 번복(화면 규칙) {dict(ov)} · 확신 {sum(d['confident'] for d in decisions)}")
        print(f"사용자 피드백 {len(feedback)}건: {dict(Counter(f['status'] for f in feedback))}")
        if dec_now or fb_now:
            print(f"멈춤: 이미 넣은 예시가 있음(원장 {sum(dec_now.values())} · 피드백 {sum(fb_now.values())}) — 먼저 --clean --apply")
            sys.exit(1)
        if not a.apply:
            print("(미리보기 — 넣으려면 --apply)")
            return
        async with conn.transaction():
            await insert(conn, decisions, feedback)
        dec_after, fb_after = await count_demo(conn)
        assert sum(dec_after.values()) == len(decisions) and sum(fb_after.values()) == len(feedback), (dec_after, fb_after)
        print(f"넣음 — 원장 {sum(dec_after.values())}줄 · 피드백 {sum(fb_after.values())}건")


if __name__ == "__main__":
    asyncio.run(main())
