"""실사용 문항 — 정정 요청이 승인된 채팅 질문을 시험지 후보로 (2026-10-26, 사용자 "실사용 질문을 근거로 한 자가 개선 루프").

실사용 질문엔 정답이 없어 그대로는 채점할 수 없다. 사람이 신고 → 담당자가 정정 승인한 질문은 "고친 정책 = 정답"이 생긴다 →
승인되는 순간 시험지 후보 한 줄(`eval_question_candidate`), 관리자가 [시험지에 넣기]를 누르면 골든 측정이 "실사용(real)" 층으로 함께 잰다.
실사용 층은 잠근 시험지(LOCKED_TYPES) — 진단·묶음·후보를 만들 때 보지 않고 판정 때만 본다(같은 문항으로 고치고 채점하지 않게).
질문 글은 이미 질의 기록(ops_query_log)에 있는 사용자 질문 그대로 — 새로 원문을 만드는 건 없다.
정답이 정책인 것만 정책 골든에 들어간다(지식 정정은 후보로 쌓되 지식 측정 연결은 다음 — 백로그 0-7).
"""
from __future__ import annotations

import logging
from typing import Optional

from core.database import get_conn

logger = logging.getLogger(__name__)

POLICY_TARGETS = ("policy_param", "policy_narrative")
STATUSES = ("proposed", "added", "dismissed")


async def from_correction(item: dict, applied_target_id: Optional[int]) -> Optional[int]:
    """정정 승인 직후(best-effort — 실패해도 승인은 그대로, 로그만). 채팅 신고만 — 채팅 메시지에서 온 신고만 message_id를 가진다
    (정책서 화면 제안·JSON 가져오기·적재 점검은 없음). source로 거르면 안 된다 — 채팅 신고도 source가 채워진다(chat_answer_auto 등,
    NOT NULL — 10/26 리뷰 H1: source None 검사로 0건). 같은 정정은 한 번만(improvement_item_id 고유)."""
    if not item.get("message_id"):
        return None
    try:
        async with get_conn() as conn:
            q = await conn.fetchrow("SELECT question, namespace_id FROM ops_query_log WHERE message_id = $1 ORDER BY id DESC LIMIT 1",
                                    item["message_id"])
            if not q or not (q["question"] or "").strip():
                return None
            gold: dict = {"target_type": item["target_type"]}
            if item["target_type"] in POLICY_TARGETS and applied_target_id:
                p = await conn.fetchrow("SELECT logical_id, source_key FROM policy_item WHERE id = $1", applied_target_id)
                if p:
                    gold.update(kind="policy", logical_id=p["logical_id"], source_key=p["source_key"])
            elif applied_target_id:
                gold.update(kind="knowledge", id=applied_target_id)
            import json
            return await conn.fetchval(
                """INSERT INTO eval_question_candidate (improvement_item_id, namespace_id, question, gold)
                   VALUES ($1, $2, $3, $4::jsonb) ON CONFLICT (improvement_item_id) DO NOTHING RETURNING id""",
                item["id"], q["namespace_id"] or item.get("namespace_id"), q["question"].strip(), json.dumps(gold, ensure_ascii=False))
    except Exception:
        logger.warning("정정 승인 → 시험지 후보 만들기 실패(item=%s) — 승인은 반영됨", item.get("id"), exc_info=True)
        return None


async def list_candidates(status: Optional[str]) -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """SELECT c.id, c.created_at, c.status, c.question, c.gold, c.improvement_item_id, c.decided_at, n.name AS namespace,
                      u.username AS decided_by, i.user_input AS report
               FROM eval_question_candidate c
               LEFT JOIN ops_namespace n ON n.id = c.namespace_id
               LEFT JOIN ops_user u ON u.id = c.decided_by_user_id
               LEFT JOIN ops_improvement_item i ON i.id = c.improvement_item_id
               WHERE ($1::text IS NULL OR c.status = $1) ORDER BY c.created_at DESC LIMIT 200""", status)
    import json
    out = []
    for r in rows:
        d = dict(r)
        d["gold"] = json.loads(d["gold"]) if isinstance(d["gold"], str) else d["gold"]
        d["qid"] = f"REAL-{d['id']}"
        d["measurable"] = (d["gold"] or {}).get("kind") == "policy" and bool((d["gold"] or {}).get("source_key"))
        out.append(d)
    return out


async def decide(cand_id: int, *, status: str, user_id: int) -> Optional[dict]:
    """[시험지에 넣기] = added · [빼기] = dismissed · [되돌리기] = proposed."""
    if status not in STATUSES:
        raise ValueError(f"status는 {STATUSES} 중 하나")
    async with get_conn() as conn:
        r = await conn.execute(
            """UPDATE eval_question_candidate SET status = $2, decided_by_user_id = $3,
                      decided_at = CASE WHEN $2 = 'proposed' THEN NULL ELSE NOW() END WHERE id = $1""",
            cand_id, status, None if status == "proposed" else user_id)
    return {"id": cand_id, "status": status} if r.endswith(" 1") else None


async def real_golden(conn) -> list[dict]:
    """시험지에 넣은 실사용 문항 → 골든 파일 줄과 같은 모양(eval_golden_v0·eval_answers가 파일 줄에 더해 쓴다). 정답이 정책인 것만.
    같은 질문은 하나만(먼저 넣은 것) — 두 사람이 같은 질문을 신고해 둘 다 넣어도 측정이 멈추지 않게(10/26 리뷰 M1). 골든 파일과 겹치는
    질문은 부르는 쪽(로더)이 건너뛰고 개수를 보인다."""
    import json
    rows = await conn.fetch(
        """SELECT c.id, c.question, c.gold, n.name AS ns FROM eval_question_candidate c
           LEFT JOIN ops_namespace n ON n.id = c.namespace_id WHERE c.status = 'added' ORDER BY c.id""")
    out, seen = [], set()
    for r in rows:
        g = json.loads(r["gold"]) if isinstance(r["gold"], str) else r["gold"]
        if (g or {}).get("kind") != "policy" or not g.get("source_key") or not r["ns"] or r["question"] in seen:
            continue
        seen.add(r["question"])
        out.append({"qid": f"REAL-{r['id']}", "verdict": "approved", "layer": "real", "expect": "answer", "query": r["question"],
                    "doc": r["ns"], "gold": [{"kind": "policy", "source_key": g["source_key"]}], "answer_facts": []})
    return out
