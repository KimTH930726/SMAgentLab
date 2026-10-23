"""AI 판단 원장 DB 어댑터(collection-design §16, #85 `ops_ai_decision`) — 판단 1건 = 1줄, 사람이 확정·변경하면 같은 줄에.

판단을 내리는 쪽(지식 등록 위치·채팅 되묻기, 나중에 정책 위험도·VOC 묶음)은 이 함수들만 부른다 — 표를 직접 쓰지 않는다
(VOC가 별도 에이전트로 떨어져도 같은 원장·같은 집계, §16-④). 집계 계산은 trustkit.decision.ledger.summarize.
원문은 남기지 않는다 — 값은 id·이름·숫자만.
"""
import json
from typing import Any, Optional

from trustkit.decision.ledger import VERDICTS, summarize


async def record(conn, kind: str, *, ai_value: Any, confident: bool, signals: dict, user_id: Optional[int],
                 model: str = "", prompt_version: str = "", target_type: str = "", target_ids: Optional[list[int]] = None) -> int:
    """AI 판단 한 줄을 남기고 id를 돌려준다(확정 전 — final_value 없음)."""
    return await conn.fetchval(
        """INSERT INTO ops_ai_decision (kind, ai_value, confident, signals, model, prompt_version, created_by_user_id, target_type, target_ids)
           VALUES ($1, $2::jsonb, $3, $4::jsonb, $5, $6, $7, NULLIF($8, ''), $9::bigint[]) RETURNING id""",
        kind, json.dumps(ai_value, ensure_ascii=False), confident, json.dumps(signals, ensure_ascii=False),
        model, prompt_version, user_id, target_type, target_ids or [])


async def finalize(conn, decision_id: int, *, kind: str, final_value: Any, changed: bool, user_id: Optional[int],
                   target_type: str = "", target_ids: Optional[list[int]] = None) -> bool:
    """사람이 확정한 값 — 고른 값이 AI 값과 다르면 changed=True(부르는 쪽이 비교 기준을 안다).
    판단을 낸 사람만, 아직 확정 전인 줄만 확정한다(남의 판단 id를 끼워 넣거나 두 번 확정하지 못하게). 확정했으면 True.
    대상(target)을 안 넘기면 원래 값 그대로."""
    r = await conn.execute(
        """UPDATE ops_ai_decision SET final_value = $3::jsonb, human_changed = $4, changed_by_user_id = $5, changed_at = NOW(),
                  target_type = COALESCE(NULLIF($6, ''), target_type),
                  target_ids = CASE WHEN cardinality($7::bigint[]) > 0 THEN $7::bigint[] ELSE target_ids END
           WHERE id = $1 AND kind = $2 AND final_value IS NULL AND created_by_user_id IS NOT DISTINCT FROM $5""",
        decision_id, kind, json.dumps(final_value, ensure_ascii=False), changed, user_id, target_type, target_ids or [])
    return r.endswith(" 1")


CLARIFY_KIND = "chat_clarify"


async def clarify_asked(conn, *, conversation_id: int, message_id: int, clarify: dict, user_id: Optional[int]) -> int:
    """채팅 되묻기 한 번 = 한 줄 — 무엇을 물었나(선택지 범위·개수, 글은 남기지 않음). 다음 질문이 이 줄을 닫는다(clarify_answered)."""
    scopes = [o.get("scope") for o in clarify.get("options", [])]
    # 대화 id는 글자로 — 열린 줄 찾기가 부분 인덱스(식 signals->>'conversation_id')를 그대로 타게(리뷰 10/23: ::bigint 비교는 인덱스 밖)
    return await record(conn, CLARIFY_KIND, ai_value={"options": scopes}, confident=False,
                        signals={"conversation_id": str(conversation_id), "message_id": message_id, "n_options": len(scopes)},
                        user_id=user_id, prompt_version="clarify-v2.134", target_type="message", target_ids=[message_id])


async def clarify_answered(conn, *, conversation_id: int, picked_scope: Optional[str], user_id: Optional[int]) -> bool:
    """같은 대화의 열린 되묻기 줄을 닫는다 — 선택지를 골랐으면 그 범위("*" = 전부 보기), 안 고르고 직접 물었으면 typed.
    "AI가 틀렸다"는 없다(묻기는 답이 아님) — 리포트는 고른 분포("전부 보기"가 많으면 괜히 물은 것)·직접 입력 비율을 본다."""
    rows = await conn.fetch(
        """SELECT id, ai_value FROM ops_ai_decision WHERE kind = $1 AND final_value IS NULL AND signals->>'conversation_id' = $2
           ORDER BY id DESC""", CLARIFY_KIND, str(conversation_id))
    if not rows:
        return False
    # 옛 열린 줄(닫기가 한 번 실패한 뒤 새 되묻기가 생긴 경우)은 "다음 되묻기로 넘어감"으로 닫는다 — 영원히 확정 전으로 남지 않게
    for r in rows[1:]:
        await finalize(conn, r["id"], kind=CLARIFY_KIND, final_value={"superseded": True}, changed=False, user_id=user_id)
    # 고른 범위는 물었던 선택지 중 하나일 때만 남긴다 — 클라이언트가 보낸 글자를 그대로 원장에 넣지 않는다(원문 저장 안 함, 리뷰 10/23)
    options = (_j(rows[0]["ai_value"]) or {}).get("options") or []
    if picked_scope:
        value = {"picked": picked_scope} if picked_scope in options else {"picked_unknown": True}
    else:
        value = {"typed": True}
    return await finalize(conn, rows[0]["id"], kind=CLARIFY_KIND, final_value=value, changed=False, user_id=user_id)


async def set_verdict(conn, decision_id: int, *, verdict: str, reason: str = "", user_id: Optional[int]) -> bool:
    """사람이 AI 값을 바꾼 줄에 "AI가 틀렸나요?" 답 — wrong / context_changed / unknown."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict는 {VERDICTS} 중 하나")
    # 바꾼 사람만 답한다(남의 줄에 답을 끼워 넣지 못하게, 리뷰 10/23)
    r = await conn.execute(
        """UPDATE ops_ai_decision SET ai_verdict = $2, verdict_reason = NULLIF($3, ''), verdict_by_user_id = $4
           WHERE id = $1 AND human_changed AND changed_by_user_id IS NOT DISTINCT FROM $4""", decision_id, verdict, reason[:200], user_id)
    return r.endswith(" 1")


async def report(conn, *, since_days: int = 90) -> dict:
    """종류별 집계(운영 리포트 숫자)."""
    rows = await conn.fetch(
        """SELECT kind, confident, ai_value, final_value, human_changed, ai_verdict, verdict_reason FROM ops_ai_decision
           WHERE created_at > NOW() - make_interval(days => $1)""", since_days)
    return summarize({**dict(r), "ai_value": _j(r["ai_value"]), "final_value": _j(r["final_value"])} for r in rows)


def _j(v):
    return json.loads(v) if isinstance(v, str) else v
