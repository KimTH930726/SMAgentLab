"""정책서 JSON 내려받기·가져오기(2026-10-21, v0 재구축 3단계, 관리자 전용 — 문서 개정본용, policy-v0-design.md §7 1단계).

막힌 일: 정책서 개정본이 오면 바뀐 정책을 한 건씩 직접 수정으로 올려야 했다. 파일 하나로 "무엇이 바뀌었나"를 보고 바뀐 것만
정정 요청에 올린다. **정책 테이블엔 직접 쓰지 않는다** — 바뀐 정책마다 정정 원장 제안 1건(source json_import)을 만들고, 반영은
다른 제안과 똑같이 품질 › 정정 요청의 승인(일괄 승인 포함)에서만.

형식
- 내려받기 = 얇은 형식 `opslens/policy-v0/1` {schema, domain, exported_at, policies:[{source_key, category_path, policy_name, raw_body,
  remark, order, version}]} — 지금 쓰이는(active) 버전, 문서 순서. 원천 키(source_key)가 없는 정책은 짝을 지을 수 없어 빼고 로그.
- 가져오기 = 위 얇은 형식, 또는 팀원 원본 v0 폴더의 policies.jsonl 내용(한 줄 = 정책 1건). 팀원 형식은 적재기와 같은 변환
  (v0_convert.build_policies — 원문·비고 정리 규칙을 한 곳에)을 거친다. zip·폴더 통째는 받지 않는다(정책 비교엔 policies.jsonl만 필요).
- 비교 단위 = 원천 키. 원문·비고만 비교·반영한다(정책명·절 경로 변경은 가져오지 않고 미리보기에 따로 보여 준다).
  unchanged(같음) · changed(원문/비고 다름) · added(파일에만 — 지금은 새 정책 제안 경로가 없어 보고만) · missing(DB에만 — 보고만, 지우지 않음)
  · blocked(이미 검토 중 정정이 있음 — 한 정책엔 대기 정정 하나).
- 반영 가드: 미리보기의 doc_hash(파일 그대로의 해시)·plan_hash(바뀐 목록 + 각 정책의 지금 원문 해시)를 반영 때 다시 계산해
  다르면 409 — 미리보기 뒤 파일이나 정책이 바뀌었으면 보지 못한 안이 올라가지 않게.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from service.policy.visibility import POLICY_TARGETS, SOURCE_JSON_IMPORT, open_correction_sql

logger = logging.getLogger(__name__)

SCHEMA = "opslens/policy-v0/1"
# 글자 — 14개 문서 전체 policies.jsonl도 수 MB. 요청 본문(JSON 문자열 안의 파일 내용)은 nginx client_max_body_size(50M,
# frontend-react/nginx.conf) 안에 들어와야 한다 — 한글은 UTF-8 3바이트·따옴표/줄바꿈은 이스케이프로 2바이트라 2천만 자면 최대 60MB를
# 넘어 nginx가 HTML 413으로 먼저 끊었다(10/21 리뷰). 1천2백만 자 × 3바이트 = 36MB(여유 14MB).
MAX_CONTENT = 12_000_000


class ImportConflict(Exception):
    """미리보기 이후 파일·정책이 바뀜 — 라우터가 409로."""


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _body(s) -> str:
    return str(s or "").replace("\r\n", "\n").replace("\r", "\n")


def _remark(s) -> Optional[str]:
    v = _body(s).strip()
    return v or None


def _body_key(s) -> list[str]:
    """원문 "같음" 기준 — 정정 원장의 같음 판정(draft._unmarked_equal: 줄 안 띄어쓰기·빈 줄·앞뒤 공백 무시, 줄 구조는 비교)과 맞춘다.
    다르면 편집기가 줄 끝 공백만 지운 파일이 미리보기엔 "바뀜"인데 반영 땐 "바뀐 내용 없음"으로 전부 실패했다(10/21 리뷰 L1)."""
    return [re.sub(r"[ \t]+", "", ln) for ln in _body(s).strip().splitlines() if ln.strip()]


def content_hash(raw_body, remark) -> str:
    """원문·비고 비교용 해시(앞 16자) — 미리보기의 before/after와 plan_hash에 같이 쓴다. 비고는 앞뒤 공백만(원장 _remark_changed와 같은 기준)."""
    return _sha(json.dumps([_body_key(raw_body), _remark(remark)], ensure_ascii=False))[:16]


# ── 내려받기 ─────────────────────────────────────────────────────────────────

async def export_domain(namespace: str) -> dict:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"업무 영역을 찾을 수 없습니다: {namespace}")
        rows = await conn.fetch(
            "SELECT source_key, category_path, policy_name, raw_body, remark, version FROM policy_item "
            "WHERE namespace_id = $1 AND status = 'active' ORDER BY source_file NULLS LAST, source_row NULLS LAST, id", ns_id)
    keyed = [r for r in rows if r["source_key"]]
    if len(keyed) != len(rows):
        logger.warning("[정책 JSON] %s: 원천 키 없는 정책 %d건은 내려받기에서 뺌", namespace, len(rows) - len(keyed))
    return {
        "schema": SCHEMA, "domain": namespace, "exported_at": datetime.now(timezone.utc).isoformat(),
        "policies": [{"source_key": r["source_key"], "category_path": list(r["category_path"] or []), "policy_name": r["policy_name"],
                      "raw_body": r["raw_body"], "remark": r["remark"], "order": i + 1, "version": r["version"]}
                     for i, r in enumerate(keyed)],
    }


# ── 가져오기: 파일 읽기 ───────────────────────────────────────────────────────

def parse_import(content: str) -> dict:
    """파일 내용 → {format: thin|team, domain, records:[{source_key, raw_body, remark, has_remark, policy_name, category_path,
    doc_id, part}]}. 형식을 모르거나 키가 겹치거나 원문이 비면 ValueError(무엇이 문제인지)."""
    if not content or not content.strip():
        raise ValueError("파일이 비어 있어요.")
    if len(content) > MAX_CONTENT:
        raise ValueError(f"파일이 너무 커요({MAX_CONTENT // 10_000:,}만 자 초과).")
    doc = None
    try:
        doc = json.loads(content)
    except json.JSONDecodeError:
        pass
    if isinstance(doc, dict) and "schema" not in doc and "policy_id" in doc:
        doc = None    # 정책 한 줄짜리 팀원 policies.jsonl — 통째 JSON으로도 읽히지만 팀원 형식(10/21 리뷰 L5)
    if isinstance(doc, dict):
        if doc.get("schema") != SCHEMA:
            raise ValueError(f"모르는 형식이에요 — 내려받은 JSON({SCHEMA}) 또는 팀원 v0 폴더의 policies.jsonl만 받아요.")
        if not isinstance(doc.get("policies"), list):
            raise ValueError("policies 목록이 없어요.")
        recs = []
        for i, p in enumerate(doc["policies"], 1):
            if not isinstance(p, dict) or not str(p.get("source_key") or "").strip():
                raise ValueError(f"{i}번째 정책에 source_key가 없어요.")
            recs.append({"source_key": str(p["source_key"]).strip(), "raw_body": _body(p.get("raw_body")),
                         "remark": _remark(p.get("remark")), "has_remark": "remark" in p,
                         "policy_name": p.get("policy_name"), "category_path": p.get("category_path"),
                         "doc_id": None, "part": None})
        out = {"format": "thin", "domain": doc.get("domain"), "records": recs}
    else:
        out = {"format": "team", "domain": None, "records": _parse_team(content)}
    seen, dup = set(), set()
    for r in out["records"]:
        if not r["raw_body"].strip():
            raise ValueError(f"{r['source_key']}: 원문이 비어 있어요 — 정책을 지우는 건 가져오기로 하지 않아요.")
        (dup if r["source_key"] in seen else seen).add(r["source_key"])
    if dup:
        raise ValueError(f"같은 source_key가 두 번 이상: {', '.join(sorted(dup)[:5])}")
    return out


def _parse_team(content: str) -> list[dict]:
    """팀원 policies.jsonl → 적재기와 같은 변환(v0_convert.build_policies)으로 원문·비고를 정리."""
    from service.policy.v0_convert import Plan, build_policies, part_name
    lines = []
    for n, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        try:
            p = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f"{n}번째 줄이 JSON이 아니에요 — 내려받은 JSON 또는 팀원 policies.jsonl만 받아요.")
        if not isinstance(p, dict) or not all(k in p for k in ("policy_id", "document_id", "source_text")):
            raise ValueError(f"{n}번째 줄에 policy_id·document_id·source_text가 없어요 — 팀원 policies.jsonl 형식이 아니에요.")
        p = {"section_path": [], "title": "", **p}
        p["source_ref"] = p.get("source_ref") or {}
        lines.append(p)
    if not lines:
        raise ValueError("정책이 한 건도 없어요.")
    docs = sorted({p["document_id"] for p in lines})
    plan = Plan(parts=[])
    build_policies({"documents": [{"document_id": d, "source_file": ""} for d in docs], "policies": lines}, plan)
    part_of = {p["policy_id"]: part_name(p["domain"]) if p.get("domain") else None for p in lines}
    return [{"source_key": r.source_key, "raw_body": _body(r.raw_body), "remark": _remark(r.remark), "has_remark": True,
             "policy_name": r.policy_name, "category_path": r.category_path, "doc_id": r.doc_id, "part": part_of.get(r.source_key)}
            for r in plan.policies]


# ── 가져오기: 미리보기(비교) ───────────────────────────────────────────────────

async def _current(conn, ns_id: int) -> list:
    return await conn.fetch(
        "SELECT i.id AS item_id, i.logical_id, i.source_key, i.policy_name, i.category_path, i.raw_body, i.remark, "
        f"({open_correction_sql('i.id', '$2')}) AS open_correction_id "
        "FROM policy_item i WHERE i.namespace_id = $1 AND i.status = 'active' AND i.source_key IS NOT NULL", ns_id, list(POLICY_TARGETS))


def classify(namespace: str, parsed: dict, current: list) -> dict:
    """DB 현행(current 행들)과 파일 비교 — 순수 함수(테스트가 바로 부른다). 바뀐 것의 새 원문·비고는 _apply용으로 함께 돌려준다."""
    cur = {r["source_key"]: r for r in current}
    recs = parsed["records"]
    other = 0
    if parsed["format"] == "thin":
        if parsed.get("domain") and parsed["domain"] != namespace:
            raise ValueError(f"파일의 업무 영역({parsed['domain']})이 지금 고른 영역({namespace})과 달라요.")
    else:
        # 팀원 파일은 14개 문서가 다 들어 있다 — 이 영역 것만: 영역 이름이 같거나, 이 영역에 이미 있는 정책과 같은 문서
        docs = {r["doc_id"] for r in recs if r["source_key"] in cur}
        mine = [r for r in recs if r["doc_id"] in docs or r["part"] == namespace]
        other = len(recs) - len(mine)
        recs = mine
        if not recs:
            raise ValueError(f"파일에 이 업무 영역({namespace})의 정책이 없어요.")
    unchanged, changed, added, blocked, ignored = 0, [], [], [], []
    seen = set()
    for r in recs:
        seen.add(r["source_key"])
        c = cur.get(r["source_key"])
        if c is None:
            added.append({"source_key": r["source_key"], "policy_name": r.get("policy_name")})
            continue
        new_remark = r["remark"] if r["has_remark"] else _remark(c["remark"])
        before, after = content_hash(c["raw_body"], c["remark"]), content_hash(r["raw_body"], new_remark)
        if parsed["format"] == "thin":
            diff_fields = [f for f, v in (("policy_name", r.get("policy_name")), ("category_path", r.get("category_path")))
                           if v is not None and v != (list(c[f] or []) if f == "category_path" else c[f])]
            if diff_fields:
                ignored.append({"source_key": r["source_key"], "logical_id": c["logical_id"], "fields": diff_fields})
        if before == after:
            unchanged += 1
            continue
        base = {"source_key": r["source_key"], "logical_id": c["logical_id"], "item_id": c["item_id"],
                "policy_name": c["policy_name"], "before_hash": before, "after_hash": after}
        if c["open_correction_id"]:
            blocked.append({**base, "correction_id": c["open_correction_id"]})
        else:
            changed.append({**base, "body_changed": _body_key(c["raw_body"]) != _body_key(r["raw_body"]),
                            "remark_changed": _remark(c["remark"]) != new_remark,
                            "_proposed": {"raw_body": r["raw_body"], "remark": new_remark or ""}})
    missing = [{"source_key": k, "logical_id": c["logical_id"], "policy_name": c["policy_name"]}
               for k, c in cur.items() if k not in seen]
    plan_hash = _sha(json.dumps([[c["source_key"], c["before_hash"], c["after_hash"]] for c in changed]
                                + [["blocked", b["source_key"], b["correction_id"]] for b in blocked], ensure_ascii=False))[:32]
    return {"format": parsed["format"], "domain": namespace, "total": len(recs), "other_domain": other,
            "unchanged": unchanged, "changed": changed, "added": added, "missing": missing, "blocked": blocked,
            "ignored_fields": ignored, "plan_hash": plan_hash}


def _public(result: dict) -> dict:
    return {**result, "changed": [{k: v for k, v in c.items() if not k.startswith("_")} for c in result["changed"]]}


async def _diff(namespace: str, content: str) -> dict:
    parsed = parse_import(content)
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"업무 영역을 찾을 수 없습니다: {namespace}")
        current = await _current(conn, ns_id)
    out = classify(namespace, parsed, current)
    out["doc_hash"] = _sha(content)
    return out


async def preview(namespace: str, content: str) -> dict:
    return _public(await _diff(namespace, content))


async def apply(namespace: str, user: dict, content: str, doc_hash: str, plan_hash: str, filename: Optional[str] = None) -> dict:
    """바뀐 정책마다 정정 제안 1건(source json_import). 정책 테이블은 그대로 — 승인은 정정 요청에서."""
    if _sha(content) != doc_hash:
        raise ImportConflict("파일이 미리보기 때와 달라요 — 다시 미리보기 하세요.")
    result = await _diff(namespace, content)
    if result["plan_hash"] != plan_hash:
        raise ImportConflict("미리보기 뒤에 정책이나 정정 상태가 바뀌었어요 — 다시 미리보기 하세요.")
    from service.improvement import service as improvement
    note = f"JSON 가져오기{f' — {filename}' if filename else ''}"[:200]
    created, blocked, failed = [], [], []
    for c in result["changed"]:
        try:
            r = await improvement.create_policy_proposal(
                namespace, user, item_id=c["item_id"], mode=SOURCE_JSON_IMPORT, proposed=c["_proposed"], user_input=note)
            created.append({"source_key": c["source_key"], "logical_id": c["logical_id"], "correction_id": r["id"]})
        except improvement.ConflictError as e:   # 미리보기와 반영 사이 한 건씩 생긴 대기 정정 — 그 건만 빼고 계속
            blocked.append({"source_key": c["source_key"], "logical_id": c["logical_id"], "correction_id": e.existing_id})
        except ValueError as e:
            failed.append({"source_key": c["source_key"], "logical_id": c["logical_id"], "reason": str(e)})
    if failed:
        logger.warning("[정책 JSON] %s: 제안 못 만든 정책 %d건", namespace, len(failed))
    return {"created": created, "blocked": result["blocked"] + blocked, "failed": failed,
            "added_skipped": len(result["added"]), "missing": len(result["missing"]), "unchanged": result["unchanged"]}
