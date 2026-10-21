"""정책서 화면 조회(2026-10-21, v0 재구축 2단계) — 정책 목록·상세·버전, 참고표, 문서 개요. 읽기 전용.

막힌 일: 옛 항목 브라우저(browse.py)는 엑셀 분해 결과(파라미터·서술 조각·위험도·미분류) 기준이라 v0(원문 통째)엔 빈 칸만 보였다.
여기는 v0 기준으로 "지금 쓰이는 정책 1건 = 1줄"(문서 순서), 상세는 원문·비고·용어·버전 이력·검토 중 정정을 한 번에 준다.
쓰기는 없다 — 내용 변경은 전부 정정 원장의 제안(improvement/service.create_policy_proposal)이고 정정 요청에서 승인할 때만 바뀐다.
"""
from __future__ import annotations

import json
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from service.policy.visibility import POLICY_TARGETS, open_correction_sql

# 대상 종류·"검토 중 정정" 조건은 정정 원장(improvement/service._pending_policy_correction)과 같은 함수를 쓴다(10/21 리뷰 L10)
_POLICY_TARGETS = list(POLICY_TARGETS)


async def _ns_id(conn, namespace: str) -> int:
    ns_id = await resolve_namespace_id(conn, namespace)
    if ns_id is None:
        raise ValueError(f"업무 영역을 찾을 수 없습니다: {namespace}")
    return ns_id


def _like(q: str) -> str:
    """ILIKE 패턴 — 사용자가 친 %·_는 글자 그대로(ESCAPE '\\')."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def list_policies(namespace: str, q: Optional[str] = None) -> list[dict]:
    """지금 쓰이는(active) 정책 1건 = 1줄, 문서 순서(source_file·source_row). q = 정책명·원문 부분 일치."""
    async with get_conn() as conn:
        ns_id = await _ns_id(conn, namespace)
        args: list = [ns_id, _POLICY_TARGETS]
        cond = ""
        if q and q.strip():
            args.append(_like(q.strip()))
            cond = " AND (i.policy_name ILIKE $3 ESCAPE '\\' OR i.raw_body ILIKE $3 ESCAPE '\\')"
        rows = await conn.fetch(
            "SELECT i.id AS item_id, i.logical_id, i.version, i.category_path, i.policy_name, "
            "(COALESCE(btrim(i.remark), '') <> '') AS has_remark, "
            f"({open_correction_sql('i.id', '$2')}) AS open_correction_id "
            "FROM policy_item i WHERE i.namespace_id = $1 AND i.status = 'active'" + cond +
            " ORDER BY i.source_file NULLS LAST, i.source_row NULLS LAST, i.id", *args)
    return [{**dict(r), "category_path": list(r["category_path"] or [])} for r in rows]


def _origin(correction_id: Optional[int]) -> str:
    return f"정정 #{correction_id}" if correction_id else "적재"


async def get_policy(namespace: str, logical_id: int) -> Optional[dict]:
    """지금 쓰이는 버전의 원문·비고·파라미터·용어 + 버전 이력 + 검토 중 정정. 현행 버전이 없으면 None(404)."""
    async with get_conn() as conn:
        ns_id = await _ns_id(conn, namespace)
        cur = await conn.fetchrow(
            "SELECT i.id AS item_id, i.logical_id, i.version, i.status, i.category_path, i.policy_name, i.raw_body, i.remark, "
            "i.source_file, (SELECT c.id FROM policy_chunk c WHERE c.policy_item_id = i.id ORDER BY c.chunk_idx, c.id LIMIT 1) AS chunk_id "
            "FROM policy_item i WHERE i.namespace_id = $1 AND i.logical_id = $2 AND i.status = 'active' ORDER BY i.id DESC LIMIT 1",
            ns_id, logical_id)
        if not cur:
            return None
        params = await conn.fetch(
            "SELECT id, name, condition, value, unit, external_source FROM policy_param WHERE policy_item_id = $1 ORDER BY id",
            cur["item_id"])
        # 정책 —사용→ 용어: from_key = 정책 logical_id, to_key = rag_glossary.id(적재기 load_policy_v0, 10/21 DB 확인)
        terms = await conn.fetch(
            "SELECT DISTINCT g.id, g.term, g.description FROM ops_link l JOIN rag_glossary g ON g.id::text = l.to_key "
            "WHERE l.from_type = 'policy' AND l.relation = 'uses' AND l.to_type = 'term' AND l.status = 'active' "
            "AND l.from_key = $1 ORDER BY g.term", str(logical_id))
        versions = await conn.fetch(
            "SELECT i.id AS item_id, i.version, i.status, i.created_at, "
            "(SELECT o.id FROM ops_improvement_item o WHERE o.status = 'approved' AND o.applied_target_id = i.id "
            " AND o.target_type = ANY($3::text[]) ORDER BY o.id LIMIT 1) AS correction_id "
            "FROM policy_item i WHERE i.namespace_id = $1 AND i.logical_id = $2 ORDER BY i.version DESC, i.id DESC",
            ns_id, logical_id, _POLICY_TARGETS)
        open_row = await conn.fetchrow(
            open_correction_sql("$1", "$2", cols="o.id, o.source"), cur["item_id"], _POLICY_TARGETS)
    return {
        "item_id": cur["item_id"], "chunk_id": cur["chunk_id"], "logical_id": cur["logical_id"], "version": cur["version"],
        "status": cur["status"], "category_path": list(cur["category_path"] or []), "policy_name": cur["policy_name"],
        "raw_body": cur["raw_body"], "remark": cur["remark"], "source_file": cur["source_file"],
        "params": [dict(p) for p in params],
        "terms": [dict(t) for t in terms],
        "versions": [{"item_id": v["item_id"], "version": v["version"], "status": v["status"],
                      "created_at": v["created_at"].isoformat() if v["created_at"] else None,
                      "origin": _origin(v["correction_id"])} for v in versions],
        "open_correction": {"id": open_row["id"], "source": open_row["source"]} if open_row else None,
    }


async def list_ref_tables(namespace: str) -> list[dict]:
    async with get_conn() as conn:
        ns_id = await _ns_id(conn, namespace)
        rows = await conn.fetch(
            "SELECT t.id, t.logical_id, t.version, t.title, t.category_path, jsonb_array_length(t.columns) AS n_columns, "
            "(SELECT count(*) FROM ref_table_row r WHERE r.ref_table_id = t.id) AS n_rows "
            "FROM ref_table t WHERE t.namespace_id = $1 AND t.status = 'active' ORDER BY t.source_key NULLS LAST, t.id", ns_id)
    return [{**dict(r), "category_path": list(r["category_path"] or []), "n_rows": int(r["n_rows"])} for r in rows]


def _jsonb(v):
    return json.loads(v) if isinstance(v, str) else v


async def get_ref_table(namespace: str, table_id: int) -> Optional[dict]:
    """원표 그대로 — 열(이름·역할·결정 열 여부) + 행({열 이름: 값}, 행 번호 순)."""
    async with get_conn() as conn:
        ns_id = await _ns_id(conn, namespace)
        t = await conn.fetchrow(
            "SELECT id, logical_id, version, title, category_path, columns FROM ref_table "
            "WHERE id = $1 AND namespace_id = $2 AND status = 'active'", table_id, ns_id)
        if not t:
            return None
        rows = await conn.fetch("SELECT row_no, cells FROM ref_table_row WHERE ref_table_id = $1 ORDER BY row_no, id", table_id)
    return {"id": t["id"], "logical_id": t["logical_id"], "version": t["version"], "title": t["title"],
            "category_path": list(t["category_path"] or []), "columns": _jsonb(t["columns"]) or [],
            "rows": [_jsonb(r["cells"]) or {} for r in rows]}


async def list_overviews(namespace: str) -> list[dict]:
    """문서 개요(서문 — 문서 목적·적용 대상), 문서당 1건."""
    async with get_conn() as conn:
        ns_id = await _ns_id(conn, namespace)
        rows = await conn.fetch(
            "SELECT id, title, body, source_key, source_file FROM policy_doc_overview "
            "WHERE namespace_id = $1 AND status = 'active' ORDER BY source_key NULLS LAST, id", ns_id)
    return [dict(r) for r in rows]
