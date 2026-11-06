"""AI 감사 로그(품질 › AI 감사 로그, 10/27) — AI 판단 원장(ops_ai_decision)을 시간순 한 줄씩. 읽기만.

막힌 일: 화면이 "사람이 바꾼 판단"만 보여 줘서, AI가 무엇을 언제 어떤 기준으로 정했고 사람이 그대로 뒀는지를 거슬러 볼 수 없었다
(사용자 10/27 "감사 로그 쪽이니까"). 감사 로그 관례(언제 · 누가(AI/사람) · 무엇을 · 결과 · 기준, 사람 변경이 핵심 증거, 종류·사람으로 거르기)를 따른다.
원문은 싣지 않는다 — 값은 id·이름·숫자와 위치 판정의 AI 이유 한 문장(원장 규칙). 한 줄 설명은 순수 함수 describe(테스트).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from core.database import get_conn
from service.eval.override import AUTO_SQL, KIND_NAME, OVERRIDE_SQL, REVIEWED_KINDS, REVIEW_SQL



def _j(v):
    return json.loads(v) if isinstance(v, str) else v


# 원문 확인 화면(SourceCheckTab)과 같은 이름 — 내부 코드(conflict · v0_load_check)가 로그에 그대로 보이던 것(11/05 UI 검수)
_SC_TYPE = {"question": "담당자 질문", "conflict": "정책끼리 어긋남", "ref_error": "참고표 오류"}
_SC_SOURCE = {"v0_load_check": "적재 점검", "golden_set": "골든셋 작성"}


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
        fid = final.get("namespace_id")   # 지운 컬렉션이면 이름이 없다 — "?" 대신 그 사실을(11/05 UI 검수)
        to = ns(fid) or ("삭제된 컬렉션" if isinstance(fid, int) else "미지정")
        # 같은 곳이어도 사람이 고른 방식이 다를 수 있다(원장 human_changed가 판정) — 결과만 적는다
        # choice는 옛 값·빈 값이 accept로 저장된다(router._finalize_location) — "그대로"는 원장이 바뀜이 아니라고 할 때만(리뷰 L1)
        return {"ai": did, "human": f"{to}에 등록" + (" (AI 후보 그대로)" if not changed and final.get("choice") == "accept" else "")}
    if kind == "chat_clarify":
        n = signals.get("n_options") or len(ai.get("options") or [])
        did = f"선택지 {n}개로 되물음"
        if final is None:
            return {"ai": did, "human": None}
        if final.get("superseded"):
            human = "다음 되묻기로 대체"
        elif final.get("typed"):
            human = "직접 재질문"
        elif final.get("picked") == "*":
            human = "전부 보기"
        elif final.get("picked"):
            human = f"고름: {final['picked']}"
        else:
            human = "선택지 밖 값"
        return {"ai": did, "human": human}
    if kind == "source_check":
        did = f"원문 문제 짚음 · {_SC_TYPE.get(ai.get('type'), ai.get('type') or '종류 모름')} · {_SC_SOURCE.get(ai.get('source'), ai.get('source') or '채널 모름')}"
        if final is None:
            return {"ai": did, "human": None}
        if final.get("issue"):
            return {"ai": did, "human": f"해결(정정 #{final['correction_id']} 승인)" if final.get("correction_id") else "해결"}
        from service.policy.source_check import NO_ISSUE_REASONS   # 한 곳 정의(11/05) — 쓸 때 불러온다(시험의 가짜 모듈과 충돌 방지)
        return {"ai": did, "human": f"오탐: {NO_ISSUE_REASONS.get(final.get('reason'), final.get('reason') or '사유 없음')}"}
    if kind == "collection_suggest":
        did = f"{ai.get('namespace') or '?'}에서 찾아보기 제안" + (f" (관련도 {signals['score']})" if signals.get("score") is not None else "")
        return {"ai": did, "human": "눌러서 다시 물음" if final and final.get("picked") else None}
    if kind == "duplicate_check":
        sim = ai.get("similarity")
        did = f"기존 #{ai.get('duplicate_of') or '?'}와 중복 의심" + (f" (유사도 {sim * 100:.1f}%)" if isinstance(sim, (int, float)) else "")
        if final is None:
            return {"ai": did, "human": None}
        act = final.get("action")
        human = {"approve": "그대로 저장(중복 아님)", "reject": "반려(중복 맞음)"}.get(act) or (
            f"#{final.get('target_id')}에 덮어쓰기" if act == "merge" else str(act))
        return {"ai": did, "human": human}
    return {"ai": KIND_NAME.get(kind, kind), "human": None if final is None else "확정"}


async def decision_log(*, days: int, kind: Optional[str], changed_only: bool, limit: int = 50, before_id: Optional[int] = None,
                       day: Optional[date] = None, review: Optional[str] = None, ids: Optional[list[int]] = None) -> dict:
    """최신순 한 페이지 — before_id보다 작은 것부터(다음 페이지). changed_only = 번복만(override.is_override와 같은 규칙 — 저장 칸이 아니라 값으로)."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""SELECT d.id, d.kind, d.created_at, d.changed_at, d.confident, {OVERRIDE_SQL} AS overridden, {REVIEW_SQL} AS review,
                      d.ai_value, d.final_value, d.signals, d.signals->>'reason' AS ai_reason, d.ai_verdict, d.verdict_reason, d.prompt_version,
                      d.spot_result, d.spot_reason, d.spot_at, su.username AS spot_by,
                      cu.username AS created_by, hu.username AS changed_by
               FROM ops_ai_decision d
               LEFT JOIN ops_user cu ON cu.id = d.created_by_user_id
               LEFT JOIN ops_user hu ON hu.id = d.changed_by_user_id
               LEFT JOIN ops_user su ON su.id = d.spot_by_user_id
               WHERE d.created_at > NOW() - make_interval(days => $1)
                 -- 다음 되묻기로 대체된 줄(superseded)은 요약(override.load)처럼 뺀다 — 칩 건수와 목록이 같은 집합(리뷰 M2)
                 AND NOT (COALESCE(d.final_value, '{{}}'::jsonb) ? 'superseded')
                 AND ($7::text IS NULL OR {REVIEW_SQL} = $7) AND ($8::bigint[] IS NULL OR d.id = ANY($8::bigint[]))
                 AND ($2::text IS NULL OR d.kind = $2) AND (NOT $3 OR {OVERRIDE_SQL}) AND ($5::bigint IS NULL OR d.id < $5)
                 AND ($6::date IS NULL OR (d.created_at AT TIME ZONE 'Asia/Seoul')::date = $6)   -- 화면 날짜(KST)와 같게(리뷰 M1)
               ORDER BY d.id DESC LIMIT $4""", days, kind, changed_only, limit + 1, before_id, day, review, ids)   # 하나 더 — 다음 페이지가 있는지(리뷰 L3)
        more = len(rows) > limit
        rows = rows[:limit]
        ids = sorted({v for r in rows for v in ((_j(r["ai_value"]) or {}).get("namespace_id"), (_j(r["final_value"]) or {}).get("namespace_id"),
                                                (_j(r["signals"]) or {}).get("proposed_id")) if isinstance(v, int)})
        names = {r["id"]: r["name"] for r in await conn.fetch("SELECT id, name FROM ops_namespace WHERE id = ANY($1::int[])", ids)} if ids else {}
        # 이 번복으로 만든 개선 후보(가장 최근 진단) — 후보 대상 = 번복한 판정 D-<id>(service/eval/override.candidates)
        cand_of: dict[int, dict] = {}
        from service.eval.improve import LATEST_KNOWLEDGE_RUN_SQL   # 번복 후보는 업무지식 축 진단에서(10/29) — 정책서 진단 뒤에도 그대로
        for c in await conn.fetch("SELECT id, title, status, qids FROM eval_candidate WHERE cause = 'override_location' "
                                  "AND run_id = " + LATEST_KNOWLEDGE_RUN_SQL):
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
                      "candidate": cand_of.get(r["id"]),
                      # 사람 검토 결과(수용·번복·자동 적용·규칙 적용·검토 대기, override.REVIEW_SQL) + 표본 감사(11/04)
                      "review": r["review"], "spot": r["spot_result"], "spot_reason": r["spot_reason"], "spot_by": r["spot_by"],
                      "spot_at": r["spot_at"].isoformat() if r["spot_at"] else None})
    return {"items": items, "next_before": items[-1]["id"] if more and items else None}


# ── 표본 감사(spot check, 11/04) ───────────────────────────────────────────
# 사람 손 없이 적용됐거나 수용된 판단을 다시 봐서 정확도·형식적 승인을 확인 — 관리자가 검토하는 종류만(되묻기·제안은 사용자 선택이라 제외)
SPOT_SQL = ("(d.kind IN (" + ", ".join(f"'{k}'" for k in REVIEWED_KINDS) + f") AND (({AUTO_SQL}) OR {REVIEW_SQL} = 'accept'))")   # 모듈 상수만(입력 아님)


async def spot_sample(*, days: int, kind: Optional[str], n: int) -> dict:
    """아직 표본 감사를 안 한 자동 적용·수용 판단에서 무작위 n건 — 감사 로그 줄과 같은 모양 + 대상(지식이면 첫 줄)."""
    async with get_conn() as conn:
        picked = [r["id"] for r in await conn.fetch(
            f"""SELECT d.id FROM ops_ai_decision d WHERE d.created_at > NOW() - make_interval(days => $1) AND d.spot_result IS NULL
                  AND ($2::text IS NULL OR d.kind = $2) AND NOT (COALESCE(d.final_value, '{{}}'::jsonb) ? 'superseded') AND {SPOT_SQL}
                ORDER BY random() LIMIT $3""", days, kind, n)]
        left = await conn.fetchval(
            f"""SELECT count(*) FROM ops_ai_decision d WHERE d.created_at > NOW() - make_interval(days => $1) AND d.spot_result IS NULL
                  AND ($2::text IS NULL OR d.kind = $2) AND NOT (COALESCE(d.final_value, '{{}}'::jsonb) ? 'superseded') AND {SPOT_SQL}""", days, kind)
    if not picked:
        return {"items": [], "left": 0}
    page = await decision_log(days=days, kind=kind, changed_only=False, limit=len(picked), ids=picked)
    got = {it["id"]: it for it in page["items"]}
    return {"items": [got[i] for i in picked if i in got], "left": int(left)}


async def spot_mark(decision_id: int, *, result: str, reason: Optional[str], user_id: int) -> bool:
    """표본 감사 결과 — 자동 적용·수용 판단에만(규칙 적용·대기·번복은 감사 대상 아님). 다시 매기면 덮어쓴다(마지막 검토자)."""
    async with get_conn() as conn:
        r = await conn.execute(
            f"""UPDATE ops_ai_decision d SET spot_result = $2, spot_reason = $3, spot_by_user_id = $4, spot_at = NOW()
                WHERE d.id = $1 AND {SPOT_SQL}""", decision_id, result, reason if result == "overridden" else None, user_id)
    return r.endswith(" 1")
