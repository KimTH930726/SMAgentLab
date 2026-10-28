"""AI 감사 로그(품질 › AI 감사 로그, 10/27) — AI 판단 원장(ops_ai_decision)을 시간순 한 줄씩. 읽기만.

막힌 일: 화면이 "사람이 바꾼 판단"만 보여 줘서, AI가 무엇을 언제 어떤 기준으로 정했고 사람이 그대로 뒀는지를 거슬러 볼 수 없었다
(사용자 10/27 "감사 로그 쪽이니까"). 감사 로그 관례(언제 · 누가(AI/사람) · 무엇을 · 결과 · 기준, 사람 변경이 핵심 증거, 종류·사람으로 거르기)를 따른다.
원문은 싣지 않는다 — 값은 id·이름·숫자와 위치 판정의 AI 이유 한 문장(원장 규칙). 한 줄 설명은 순수 함수 describe(테스트).
"""
from __future__ import annotations

import json
from typing import Optional

from core.database import get_conn
from service.eval.override import OVERRIDE_SQL

KIND_NAME = {"knowledge_location": "등록 위치", "chat_clarify": "되묻기", "source_check": "원문 확인", "collection_suggest": "다른 컬렉션 제안"}
_NO_ISSUE = {"source_ok": "원문이 맞음", "already_fixed": "이미 고쳐짐", "duplicate": "중복", "other": "기타"}


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def describe(kind: str, ai: Optional[dict], final: Optional[dict], signals: Optional[dict], names: dict[int, str],
             changed: bool = False) -> dict:
    """(AI가 한 것, 사람·사용자가 한 것 또는 None(아직 확정 전)) 한 줄씩. names = 컬렉션 id → 이름, changed = 원장의 human_changed."""
    ai, final, signals = ai or {}, final, signals or {}
    ns = lambda i: names.get(i) if isinstance(i, int) else None   # noqa: E731
    if kind == "knowledge_location":
        where = ns(ai.get("namespace_id"))
        cand = ns(signals.get("proposed_id"))   # 확신이 없으면 값은 비우고 후보만 신호에(locate.py)
        did = (f"넣을 곳 → {where}" if where else f"후보 {cand} — 확신 없어 사람에게 물음" if cand else "정하지 못함 — 사람에게 물음")             + (f" · {ai['doc_type']}" if ai.get("doc_type") else "")
        where = where or cand
        if final is None:
            return {"ai": did, "human": None}
        to = ns(final.get("namespace_id")) or "?"
        # 같은 곳이어도 사람이 고른 방식이 다를 수 있다(원장 human_changed가 판정) — 결과만 적는다
        # choice는 옛 값·빈 값이 accept로 저장된다(router._finalize_location) — "그대로"는 원장이 바뀜이 아니라고 할 때만(리뷰 L1)
        return {"ai": did, "human": f"{to}에 등록" + (" (AI 후보 그대로)" if not changed and final.get("choice") == "accept" else "")}
    if kind == "chat_clarify":
        n = signals.get("n_options") or len(ai.get("options") or [])
        did = f"선택지 {n}개로 되물음"
        if final is None:
            return {"ai": did, "human": None}
        if final.get("superseded"):
            human = "다음 되묻기로 넘어감"
        elif final.get("typed"):
            human = "고르지 않고 직접 다시 물음"
        elif final.get("picked") == "*":
            human = "전부 보기"
        elif final.get("picked"):
            human = f"고름: {final['picked']}"
        else:
            human = "선택지 밖 값"
        return {"ai": did, "human": human}
    if kind == "source_check":
        did = f"원문 문제 짚음({ai.get('type') or '?'} · {ai.get('source') or '?'})"
        if final is None:
            return {"ai": did, "human": None}
        if final.get("issue"):
            return {"ai": did, "human": f"해결(정정 #{final['correction_id']} 승인)" if final.get("correction_id") else "해결"}
        return {"ai": did, "human": f"문제 없음: {_NO_ISSUE.get(final.get('reason'), final.get('reason') or '?')}"}
    if kind == "collection_suggest":
        did = f"{ai.get('namespace') or '?'}에서 찾아보기 제안" + (f" (관련도 {signals['score']})" if signals.get("score") is not None else "")
        return {"ai": did, "human": "눌러서 다시 물음" if final and final.get("picked") else None}
    return {"ai": KIND_NAME.get(kind, kind), "human": None if final is None else "확정"}


async def decision_log(*, days: int, kind: Optional[str], changed_only: bool, limit: int = 50, before_id: Optional[int] = None) -> dict:
    """최신순 한 페이지 — before_id보다 작은 것부터(다음 페이지). changed_only = 번복만(override.is_override와 같은 규칙 — 저장 칸이 아니라 값으로)."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""SELECT d.id, d.kind, d.created_at, d.changed_at, d.confident, {OVERRIDE_SQL} AS overridden, d.ai_value, d.final_value, d.signals,
                      d.signals->>'reason' AS ai_reason, d.ai_verdict, d.verdict_reason, d.prompt_version,
                      cu.username AS created_by, hu.username AS changed_by
               FROM ops_ai_decision d
               LEFT JOIN ops_user cu ON cu.id = d.created_by_user_id
               LEFT JOIN ops_user hu ON hu.id = d.changed_by_user_id
               WHERE d.created_at > NOW() - make_interval(days => $1)
                 AND ($2::text IS NULL OR d.kind = $2) AND (NOT $3 OR {OVERRIDE_SQL}) AND ($5::bigint IS NULL OR d.id < $5)
               ORDER BY d.id DESC LIMIT $4""", days, kind, changed_only, limit + 1, before_id)   # 하나 더 — 다음 페이지가 있는지(리뷰 L3)
        more = len(rows) > limit
        rows = rows[:limit]
        ids = sorted({v for r in rows for v in ((_j(r["ai_value"]) or {}).get("namespace_id"), (_j(r["final_value"]) or {}).get("namespace_id"),
                                                (_j(r["signals"]) or {}).get("proposed_id")) if isinstance(v, int)})
        names = {r["id"]: r["name"] for r in await conn.fetch("SELECT id, name FROM ops_namespace WHERE id = ANY($1::int[])", ids)} if ids else {}
        # 이 번복으로 만든 개선 후보(가장 최근 진단) — 후보 대상 = 번복한 판정 D-<id>(service/eval/override.candidates)
        cand_of: dict[int, dict] = {}
        for c in await conn.fetch("SELECT id, title, status, qids FROM eval_candidate WHERE cause = 'override_location' "
                                  "AND run_id = (SELECT max(id) FROM eval_improvement_run)"):
            for q in _j(c["qids"]) or []:
                if isinstance(q, str) and q.startswith("D-") and q[2:].isdigit():
                    cand_of[int(q[2:])] = {"id": c["id"], "title": c["title"], "status": c["status"]}
    items = []
    for r in rows:
        final = _j(r["final_value"])
        d = describe(r["kind"], _j(r["ai_value"]), final, _j(r["signals"]), names, changed=bool(r["overridden"]))
        items.append({"id": r["id"], "kind": r["kind"], "kind_name": KIND_NAME.get(r["kind"], r["kind"]),
                      "at": r["created_at"].isoformat(), "decided_at": r["changed_at"].isoformat() if r["changed_at"] else None,
                      "confident": r["confident"], "changed": bool(r["overridden"]), "open": final is None,
                      "ai": d["ai"], "ai_reason": r["ai_reason"], "human": d["human"], "asked_by": r["created_by"], "changed_by": r["changed_by"],
                      "rule": r["prompt_version"] or None, "verdict": r["ai_verdict"], "verdict_reason": r["verdict_reason"],
                      # 번복 순간에 사람이 고른 이유(지식 등록, 선택) — 감사 로그는 묻지 않고 보여 주기만(10/27)
                      # 옛 줄은 감사 로그에서 받은 답(ai_verdict)으로 — override.summarize와 같은 규칙(리뷰 L2)
                      "override_reason": ((final or {}).get("override_reason")
                                          or (r["ai_verdict"] if r["ai_verdict"] in ("wrong", "context_changed") else None)) if r["overridden"] else None,
                      "candidate": cand_of.get(r["id"])})
    return {"items": items, "next_before": items[-1]["id"] if more and items else None}
