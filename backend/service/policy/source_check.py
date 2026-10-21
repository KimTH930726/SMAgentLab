"""원문 확인(2026-10-21, v0 재구축 3단계) — 정책서 원문 자체에 대한 질문·문제(담당자에게 묻는 것)와 제안 연결 확인.

막힌 일: 적재 점검(담당자 질문 4)·골든셋 작성(서로 어긋나는 정책 쌍·참고표 오류)에서 찾은 "원문 쪽 문제"가 eval_out 메모에만 있어
담당자가 볼 곳이 없었다. 이건 내용 수정(정정)이 아니라 원문에 대한 질문이다 — 고칠 게 정해지면 정책 화면의 수정 제안 → 정정 요청에서 승인.
그래서 정정 요청 큐에는 안 보이고(목록·배지·내 신고에서 kind로 뺀다) 정책서 › 원문 확인에서 [해결](메모)/[닫기](사유)만 한다.

저장: 새 테이블 없이 정정 원장(ops_improvement_item)을 kind='source_check'로 재사용(대상 (종류, id) 패턴 그대로).
  target_type 'policy'(target_id = 정책 logical_id, 버전 불변) 또는 'ref_table'(참고표 logical_id)
  original {type: question|conflict|ref_error, policies: [logical_id…], ref_tables: [logical_id…], source_keys: {...}, hash}
  user_input = 문제 설명(원문을 인용하지 않고 우리 말로), source = v0_load_check | golden_set
  처리: 해결 = status approved, 닫기 = rejected — 메모는 ai_verdict {resolution, note}(이 원장의 "판정" 칸).
제안 연결(ops_link status='suggested', 적재기가 규칙으로 추정한 정책→절 참조)도 같은 화면에서 [확정](active)/[거절](rejected).
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

from core.database import get_conn, resolve_namespace_id

logger = logging.getLogger(__name__)

KIND = "source_check"
SOURCES = ("v0_load_check", "golden_set")
TYPES = ("question", "conflict", "ref_error")
LINK_SOURCE = "v0_rule"          # 적재기의 제안 연결 출처(scripts/load_policy_v0.py add_suggested_links)
_RESOLUTION_STATUS = {"resolved": "approved", "dismissed": "rejected"}


# ── 씨앗(적재 스크립트가 쓰는 순수 함수) ─────────────────────────────────────────

def seed_hash(source: str, type_: str, policies: list[str], ref_tables: list[str], seed_id: str = "") -> str:
    """같은 확인 항목인지 — 출처·종류·대상 원천 키(정렬)·씨앗 id로. 설명 문구는 빼서 말만 다듬어 다시 넣어도 중복이 안 생긴다.
    씨앗 id는 같은 표에 문제가 둘인 경우(종류·대상이 같음)를 가르려고 둔다."""
    payload = [source, type_, sorted(policies), sorted(ref_tables), seed_id or ""]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:32]


def validate_seed(rec: dict) -> list[str]:
    """씨앗 한 줄의 문제 목록(빈 목록 = 통과)."""
    errs = []
    if rec.get("source") not in SOURCES:
        errs.append(f"source는 {SOURCES} 중 하나")
    if rec.get("type") not in TYPES:
        errs.append(f"type은 {TYPES} 중 하나")
    if not str(rec.get("description") or "").strip():
        errs.append("description 비어 있음")
    pols, tabs = rec.get("policies") or [], rec.get("ref_tables") or []
    if not isinstance(pols, list) or not isinstance(tabs, list):
        errs.append("policies·ref_tables는 목록")
    elif not pols and not tabs:
        errs.append("policies·ref_tables 중 하나는 있어야 함")
    if rec.get("type") == "conflict" and len(pols) < 2:
        errs.append("conflict는 정책 둘 이상")
    return errs


# ── 조회 ─────────────────────────────────────────────────────────────────────

def _json(v):
    return json.loads(v) if isinstance(v, str) else v


def _status_cond(status: Optional[str]) -> Optional[list[str]]:
    return {"open": ["pending"], "done": ["approved", "rejected"]}.get(status or "")


async def list_checks(namespace: str, status: Optional[str] = None) -> dict:
    """그 업무 영역의 원문 확인 항목 + 제안 연결. status: open(처리 전) | done(해결·닫힘) | None(전부)."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"업무 영역을 찾을 수 없습니다: {namespace}")
        st = _status_cond(status)
        args: list = [ns_id, KIND]
        cond = ""
        if st:
            args.append(st)
            cond = " AND i.status = ANY($3::text[])"
        rows = await conn.fetch(
            "SELECT i.id, i.source, i.target_type, i.target_id, i.user_input, i.original, i.status, i.ai_verdict, "
            "i.created_at, i.decided_at, du.username AS decided_by FROM ops_improvement_item i "
            "LEFT JOIN ops_user du ON du.id = i.decided_by "
            "WHERE i.namespace_id = $1 AND i.kind = $2" + cond + " ORDER BY (i.status = 'pending') DESC, i.id", *args)
        originals = [(_json(r["original"]) or {}) for r in rows]
        pol_ids = sorted({int(x) for o in originals for x in (o.get("policies") or [])})
        tab_ids = sorted({int(x) for o in originals for x in (o.get("ref_tables") or [])})
        pols = {r["logical_id"]: r for r in await conn.fetch(
            "SELECT logical_id, policy_name, category_path FROM policy_item "
            "WHERE namespace_id = $1 AND status = 'active' AND logical_id = ANY($2::int[])", ns_id, pol_ids)} if pol_ids else {}
        tabs = {r["logical_id"]: r for r in await conn.fetch(
            "SELECT id, logical_id, title FROM ref_table WHERE namespace_id = $1 AND status = 'active' AND logical_id = ANY($2::int[])",
            ns_id, tab_ids)} if tab_ids else {}

        link_status = {"open": ["suggested"], "done": ["active", "rejected"]}.get(status or "", ["suggested", "active", "rejected"])
        links = await conn.fetch(
            "SELECT l.id, l.from_key, l.relation, l.to_type, l.to_key, l.status, i.policy_name, i.category_path "
            "FROM ops_link l JOIN policy_item i ON i.logical_id::text = l.from_key AND i.status = 'active' AND i.namespace_id = $1 "
            "WHERE l.from_type = 'policy' AND l.source = $2 AND l.status = ANY($3::text[]) ORDER BY (l.status = 'suggested') DESC, l.id",
            ns_id, LINK_SOURCE, link_status)
        target_ns = {}
        ns_keys = {int(k.split("|", 1)[0]) for k in (l["to_key"] for l in links) if "|" in k and k.split("|", 1)[0].isdigit()}
        if ns_keys:
            target_ns = {r["id"]: r["name"] for r in await conn.fetch(
                "SELECT id, name FROM ops_namespace WHERE id = ANY($1::int[])", sorted(ns_keys))}

    checks = []
    for r, o in zip(rows, originals):
        verdict = _json(r["ai_verdict"]) or {}
        checks.append({
            "id": r["id"], "source": r["source"], "type": o.get("type"), "description": r["user_input"],
            "status": "open" if r["status"] == "pending" else ("resolved" if r["status"] == "approved" else "dismissed"),
            "note": verdict.get("note"),
            "policies": [{"logical_id": lid, "policy_name": pols[lid]["policy_name"] if lid in pols else None,
                          "category_path": list(pols[lid]["category_path"] or []) if lid in pols else []}
                         for lid in (int(x) for x in (o.get("policies") or []))],
            "ref_tables": [{"logical_id": lid, "id": tabs[lid]["id"] if lid in tabs else None,
                            "title": tabs[lid]["title"] if lid in tabs else None}
                           for lid in (int(x) for x in (o.get("ref_tables") or []))],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
            "decided_by": r["decided_by"],
        })
    out_links = []
    for l in links:
        head, _, path = l["to_key"].partition("|")
        out_links.append({
            "id": l["id"], "status": l["status"], "relation": l["relation"],
            "policy": {"logical_id": int(l["from_key"]), "policy_name": l["policy_name"], "category_path": list(l["category_path"] or [])},
            "target": {"type": l["to_type"], "namespace": target_ns.get(int(head)) if head.isdigit() else None,
                       "path": path.split(" > ") if path else []},
        })
    return {"checks": checks, "links": out_links}


# ── 처리 ─────────────────────────────────────────────────────────────────────

async def check_namespace(item_id: int) -> Optional[str]:
    """권한 확인용 — 원문 확인 항목의 업무 영역(정정 건은 못 집는다)."""
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT n.name FROM ops_improvement_item i JOIN ops_namespace n ON n.id = i.namespace_id "
            "WHERE i.id = $1 AND i.kind = $2", item_id, KIND)


async def decide_check(item_id: int, user: dict, resolution: str, note: str) -> dict:
    """[해결](resolved) / [닫기](dismissed) — 메모 필수. 처리 전 건만(두 번 눌러도 한 번)."""
    if resolution not in _RESOLUTION_STATUS:
        raise ValueError("알 수 없는 처리입니다.")
    note = (note or "").strip()
    if not note:
        raise ValueError("메모를 적어 주세요." if resolution == "resolved" else "닫는 이유를 적어 주세요.")
    verdict = {"resolution": resolution, "note": note, "by": user.get("id"), "by_name": user.get("username")}
    async with get_conn() as conn:
        res = await conn.execute(
            "UPDATE ops_improvement_item SET status = $2, decided_by = $3, decided_at = NOW(), ai_verdict = $4::jsonb "
            "WHERE id = $1 AND kind = $5 AND status = 'pending'",
            item_id, _RESOLUTION_STATUS[resolution], user.get("id"), json.dumps(verdict, ensure_ascii=False), KIND)
    if res.endswith(" 0"):
        raise ValueError("이미 처리됐거나 없는 항목이에요 — 새로고침하세요.")
    return {"id": item_id, "status": resolution}


async def link_namespace(link_id: int) -> Optional[str]:
    """제안 연결의 업무 영역 = 연결을 건 정책(현행)의 영역."""
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT n.name FROM ops_link l JOIN policy_item i ON i.logical_id::text = l.from_key AND i.status = 'active' "
            "JOIN ops_namespace n ON n.id = i.namespace_id WHERE l.id = $1 AND l.from_type = 'policy' AND l.source = $2 LIMIT 1",
            link_id, LINK_SOURCE)


async def decide_link(link_id: int, user: dict, confirm: bool) -> dict:
    """[확정] → active / [거절] → rejected. 제안(suggested) 상태인 것만."""
    new_status = "active" if confirm else "rejected"
    async with get_conn() as conn:
        res = await conn.execute(
            "UPDATE ops_link SET status = $2 WHERE id = $1 AND status = 'suggested' AND source = $3", link_id, new_status, LINK_SOURCE)
    if res.endswith(" 0"):
        raise ValueError("이미 처리됐거나 없는 제안 연결이에요 — 새로고침하세요.")
    # ops_link엔 처리자 칸이 없다 — 누가 바꿨는지는 로그로 남긴다(감사 칸은 연결 편집 화면이 생길 때)
    logger.info("[원문 확인] 제안 연결 %s → %s (user=%s)", link_id, new_status, user.get("id"))
    return {"id": link_id, "status": new_status}
