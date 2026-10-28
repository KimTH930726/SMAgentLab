"""평가 게이트 › 적용 이력(10/27 밤, docs/tech/self-improvement-loop-design.md §3-4) — 무엇을 언제 어떤 측정으로 바꿨나 · 효과 · 결과를 날짜순.

Langfuse 버전 이력·실험 비교의 자리(바꾼 것의 이력과 그 효과). 새 표 없이 이미 있는 기록 두 곳에서 만든다:
① 기법 대장 근거(service/eval/techniques.py의 evidence — 날짜·측정 번호·효과 한 줄, 채택·보류 결정의 근거)
② 개선 후보의 사람 결정(eval_candidate — 운영 반영·안 하기·나중에, 결정 시각·사유·잰 측정). 같은 후보 키는 가장 최근 결정 하나.
RAG 파이프라인 메뉴(옛 답 기법)는 이걸로 흡수 — 항목 설명은 기법 키로 설명 창을 연다.
"""
from __future__ import annotations

import json
from typing import Optional

from core.database import get_conn

RESULT_NAME = {"adopted": "운영 반영", "rejected": "안 함", "held": "나중에", "approved": "실험 중"}


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def from_registry(techs: list[dict]) -> list[dict]:
    """대장 근거 → 줄(순수 함수). 결과는 그 기법의 지금 상태(꺼짐·안 함이면 그 글자, 아니면 None — 근거 한 줄이 채택을 뜻하진 않는다)."""
    out = []
    for t in techs:
        now = _now(t)
        for e in t.get("evidence", []):
            out.append({"date": e["date"], "stage": t["stage"], "technique": t["key"], "technique_name": t["name"], "what": e["note"],
                        "gate_run_id": e.get("gate_run_id"), "result": None, "now": now, "source": "registry"})
    return out


def _now(t: dict) -> Optional[str]:
    """기법의 지금 상태 한 마디 — 값에 '안 함'이 적혔으면 안 함, '꺼짐'·'기본 꺼짐'이면 꺼짐, 아니면 None(쓰는 중이라 단정하지 않음)."""
    vals = [v["value"] for v in t.get("values", [])]
    if any(v.startswith("안 함") for v in vals):
        return "안 함"
    if any(v == "꺼짐" or "기본 꺼짐" in v for v in vals):
        return "꺼짐"
    return None


def from_decisions(rows: list[dict], stage_of: dict[str, str], name_of: dict[str, str]) -> list[dict]:
    """후보 결정 → 줄(순수 함수). 같은 key는 가장 최근 결정 하나(진단을 다시 돌려 이어받은 같은 결정이 겹치지 않게) —
    단 운영 반영(adopted)은 그 뒤 다른 결정이 있어도 따로 남긴다(무엇을 운영에 바꿨나가 이력의 핵심, 리뷰 10/27 L3)."""
    latest: dict[tuple, dict] = {}
    for r in rows:
        if r["status"] not in RESULT_NAME or not r["reviewed_at"]:
            continue
        k = (r["key"], r["status"] == "adopted")
        cur = latest.get(k)
        if cur is None or r["reviewed_at"] > cur["reviewed_at"]:
            latest[k] = r
    out = []
    for r in latest.values():
        meas = list(r.get("measured_gate_run_ids") or [])
        out.append({"date": r["reviewed_at"].date().isoformat(), "stage": stage_of.get(r["technique"], "기타"), "technique": r["technique"],
                    "technique_name": name_of.get(r["technique"], r["technique"]), "what": r["title"],
                    "gate_run_id": r.get("adopted_gate_run_id") or (meas[-1] if meas else None), "result": RESULT_NAME[r["status"]],
                    "reason": r.get("review_reason"), "candidate_id": r["id"], "source": "decision"})
    return out


async def history(limit: int = 200) -> list[dict]:
    from service.eval.techniques import as_rows
    techs = as_rows()
    stage_of = {t["key"]: t["stage"] for t in techs}
    name_of = {t["key"]: t["name"] for t in techs}
    async with get_conn() as conn:
        rows = [dict(r) for r in await conn.fetch(
            """SELECT id, key, technique, title, status, reviewed_at, review_reason, measured_gate_run_ids, adopted_gate_run_id
               FROM eval_candidate WHERE reviewed_at IS NOT NULL""")]
        gates = {r["id"]: {"label": r["label"], "verdict": r["verdict"]} for r in await conn.fetch("SELECT id, label, verdict FROM eval_gate_run")}
    items = from_registry(techs) + from_decisions(rows, stage_of, name_of)
    for it in items:
        g: Optional[dict] = gates.get(it["gate_run_id"]) if it.get("gate_run_id") else None
        it["gate"] = {"id": it["gate_run_id"], **g} if g else None
    items.sort(key=lambda x: (x["date"], x["source"] == "decision"), reverse=True)
    return items[:limit]
