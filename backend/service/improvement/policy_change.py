"""정책 추가·폐기·분류 옮기기 제안(C3 2단계, 2026-10-27 — docs/tech/policy-lifecycle-design.md §3).

막힌 일: 화면에서 정책을 바꾸는 길이 원문·비고 수정 제안뿐이라, 새 정책·폐기·다른 분류로 옮기기는 파일을 다시 올려야 했다
(사용자 10/23 "정책은 이 시스템이 기준 — 이후 추가·수정·삭제는 시스템에서").
규칙(사용자 10/26): 모두 정정 요청에 제안으로 올라가고 승인해야 반영 · 폐기 = 상태만(deprecated, 지우지 않음) · 추가·옮기기는 분류(대·중·소)도
새로 쓸 수 있음 · 확정은 지금 정정 요청 승인 권한 그대로.
- 추가: 대상 정책 없음. 승인하면 새 logical_id(트리거) + 원천 키 `sys:<logical_id>`(골든 정답·JSON 내려받기가 이 키로 찾는다) + 조각 1개(원문 통째).
- 폐기: 그 정책 현행 → deprecated, 검색에서 빠짐. 그 정책을 정답으로 쓰는 평가 문항 수를 제안 창·승인 카드에 보인다
  (승인하면 검색 측정은 실패로, 답 측정은 판정불가로 센다 — 골든 정리 필요).
- 옮기기: 새 버전(분류 경로만 다름) — 원천 키·logical_id를 이어받아 골든 정답이 그대로. 임베딩 머리에 분류 경로가 들어가 조각을 다시 임베딩.
폐기·옮기기는 "정책당 대기 정정 1건" 잠금을 같이 쓴다(service._claim_policy_slot) — 수정 제안과 폐기 제안이 동시에 대기하면 하나가 다른 하나를 덮는다.
service.approve가 이 모듈을 함수 안에서 부르므로(지연 import) 여기서 service를 위에서 import해도 순환이 없다.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Optional

import asyncpg

from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service
from service.improvement.service import (
    _IN_SCOPE_2, ConflictError, _claim_policy_slot, _close_superseded, _duplicate_message, _norm_remark,
    _pending_policy_correction, _write_item,
)
from service.policy.visibility import PIPELINE_WHOLE, is_searchable, searchable_sql

logger = logging.getLogger(__name__)

ADD, RETIRE, MOVE = "policy_add", "policy_retire", "policy_move"
CHANGES = {"add": ADD, "retire": RETIRE, "move": MOVE}
SOURCE = "policy_direct"   # 정책 화면에서 사람이 쓴 제안(AI 없음) — 정정 요청의 출처 거르기 "정책 직접 수정"
SYS_KEY_PREFIX = "sys:"
# 추가는 대상 정책이 없어 "정책당 대기 1건" 잠금이 안 걸린다 — 같은 (컬렉션, 분류, 이름)을 동시에 승인하면 둘 다 "같은 이름 없음"을 보고 2개가 생긴다
# (리뷰 L1 10/27). 추가·옮기기 승인이 그 자리(컬렉션·분류·이름)를 이 키로 잠근 뒤 확인한다(_lock_slot). 키 목록은 service._POLICY_LOCK_KEY 주석.
_ADD_LOCK_KEY = 7303
MAX_DEPTH = 3              # 대·중·소
_STALE = "정책을 찾을 수 없거나 그 사이 바뀌었어요 — 새로고침하세요."
_GONE_ON_APPROVE = "정책이 이미 바뀌었거나 현행이 아니에요 — 이 제안은 반려하세요."
_CHUNKS_SQL = "SELECT id, chunk_text, chunk_idx FROM policy_chunk WHERE policy_item_id = $1 ORDER BY chunk_idx"
_ITEM_SQL = f"SELECT * FROM policy_item i WHERE i.id = $1 AND i.namespace_id {_IN_SCOPE_2}"


def clean_path(path) -> list[str]:
    """분류 경로 — 칸마다 앞뒤 공백을 지우고 빈 칸은 뺀다. 1~3단계만."""
    out = [str(p).strip() for p in (path or []) if str(p or "").strip()]
    if not out:
        raise ValueError("분류를 적어 주세요(대분류는 꼭).")
    if len(out) > MAX_DEPTH:
        raise ValueError(f"분류는 {MAX_DEPTH}단계(대·중·소)까지예요.")
    if any(len(p) > 200 for p in out):
        raise ValueError("분류 이름이 너무 길어요(200자까지).")
    return out


def golden_uses(source_key: Optional[str]) -> dict:
    """이 정책을 정답으로 쓰는 골든 문항(승인된 것) — 서버에 골든 폴더가 없으면 checked=False. 파일 읽기라 부르는 쪽이 스레드로."""
    from service.eval.report import GOLDEN_DIR, _golden
    checked = GOLDEN_DIR.exists()
    if not source_key or not checked:
        return {"n": 0, "qids": [], "checked": checked}
    qids = sorted(q for q, g in _golden().items() if any(x.get("source_key") == source_key for x in g.get("gold") or []))
    return {"n": len(qids), "qids": qids, "checked": True}


async def _uses(conn, source_key: Optional[str]) -> dict:
    """골든 파일 + 시험지에 넣은 실사용 문항(질문 글 없이 번호만). 번호는 20개까지만 싣는다."""
    files = await asyncio.to_thread(golden_uses, source_key)
    real = [f"REAL-{r['id']}" for r in await conn.fetch(
        "SELECT id FROM eval_question_candidate WHERE status = 'added' AND gold->>'kind' = 'policy' AND gold->>'source_key' = $1 ORDER BY id",
        source_key)] if source_key else []
    qids = files["qids"] + real
    return {"n": len(qids), "qids": qids[:20], "checked": files["checked"]}


async def uses_of(namespace: str, item_id: int) -> dict:
    """폐기 제안 창의 경고 — 이 정책을 정답으로 쓰는 평가 문항."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        key = await conn.fetchval(f"SELECT source_key FROM policy_item i WHERE i.id = $1 AND i.namespace_id {_IN_SCOPE_2}",
                                  item_id, ns_id) if ns_id else None
        return await _uses(conn, key)


async def _same_name(conn, ns_id: int, path: list[str], name: str, exclude_logical: Optional[int] = None) -> bool:
    """같은 분류에 같은 이름의 현행 정책 — 추가·옮기기가 같은 정책을 둘 만들지 않게."""
    return bool(await conn.fetchval(
        f"SELECT 1 FROM policy_item i WHERE i.namespace_id = $1 AND i.category_path = $2::text[] AND i.policy_name = $3 "
        f"AND {searchable_sql('i')} AND i.logical_id IS DISTINCT FROM $4::int LIMIT 1", ns_id, path, name, exclude_logical))


async def _path_exists(conn, ns_id: int, path: list[str]) -> bool:
    """이 경로(또는 그 아래)에 현행 정책이 있나 — 없으면 승인 때 새 분류가 생긴다(카드에 "새 분류")."""
    return bool(await conn.fetchval(
        f"SELECT 1 FROM policy_item i WHERE i.namespace_id = $1 AND i.category_path[1:{len(path)}] = $2::text[] "
        f"AND {searchable_sql('i')} LIMIT 1", ns_id, path))


async def _draft(conn, ns_id: int, target_type: str, target_id: Optional[int], *, policy_name: Optional[str],
                 category_path: Optional[list], raw_body: Optional[str], remark: Optional[str]) -> tuple[Optional[int], dict, dict]:
    """(대상 정책 id, 원본 스냅샷, 제안) — 검사 실패는 ValueError(화면에 그대로)."""
    if target_type == ADD:
        name, body = (policy_name or "").strip(), (raw_body or "").strip()
        if not name or not body:
            raise ValueError("정책 이름과 원문을 적어 주세요.")
        path = clean_path(category_path)
        if await _same_name(conn, ns_id, path, name):
            raise ValueError("같은 분류에 같은 이름의 정책이 이미 있어요 — 그 정책을 고치세요.")
        dup = await conn.fetchval(   # 같은 분류·이름의 대기 추가 제안 — 둘 다 승인되면 두 번째가 막히지만, 처음부터 하나로(리뷰 L1)
            "SELECT id FROM ops_improvement_item WHERE status = 'pending' AND target_type = $1 AND namespace_id = $2 "
            "AND proposed->>'policy_name' = $3 AND proposed->'category_path' = $4::jsonb ORDER BY id LIMIT 1",
            ADD, ns_id, name, json.dumps(path, ensure_ascii=False))
        if dup:
            raise ConflictError(f"같은 정책 추가 제안이 이미 정정 #{dup} 검토 중이에요 — 정정 요청에서 확인하세요.", existing_id=dup)
        new_category = not await _path_exists(conn, ns_id, path)
        return None, {"category_path": path, "new_category": new_category}, \
            {"policy_name": name, "category_path": path, "raw_body": body, "remark": _norm_remark(remark)}
    if target_id is None:
        raise ValueError("정책을 지정해야 해요.")
    row = await conn.fetchrow(f"{_ITEM_SQL} AND {searchable_sql('i')}", target_id, ns_id)
    if not row:
        raise ValueError(_STALE)
    original = {"policy_name": row["policy_name"], "category_path": list(row["category_path"] or []),
                "logical_id": row["logical_id"], "version": row["version"]}
    if target_type == RETIRE:
        return row["id"], {**original, "golden": await _uses(conn, row["source_key"])}, {"retire": True}
    path = clean_path(category_path)
    if path == original["category_path"]:
        raise ValueError("지금 분류와 같아요 — 옮길 분류를 고르세요.")
    if await _same_name(conn, ns_id, path, row["policy_name"], exclude_logical=row["logical_id"]):
        raise ValueError("옮길 분류에 같은 이름의 정책이 이미 있어요.")
    return row["id"], {**original, "new_category": not await _path_exists(conn, ns_id, path)}, {"category_path": path}


async def create(namespace: str, user: dict, *, change: str, target_id: Optional[int], policy_name: Optional[str],
                 category_path: Optional[list], raw_body: Optional[str], remark: Optional[str], reason: Optional[str]) -> dict:
    """제안 1건을 정정 원장에 — 정책 테이블은 그대로(승인 때만 바뀜). 폐기·옮기기는 그 정책에 대기 정정이 있으면 ConflictError."""
    target_type = CHANGES.get(change)
    if target_type is None:
        raise ValueError("알 수 없는 변경 종류예요.")
    reason = (reason or "").strip() or None
    if target_type == RETIRE and not reason:
        raise ValueError("폐기 이유를 적어 주세요.")
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"업무 영역을 찾을 수 없습니다: {namespace}")
        item_id, original, proposed = await _draft(conn, ns_id, target_type, target_id, policy_name=policy_name,
                                                   category_path=category_path, raw_body=raw_body, remark=remark)
        values = ("correction", SOURCE, ns_id, None, user.get("id"), target_type, item_id, None, reason,
                  json.dumps(original, ensure_ascii=False), json.dumps(proposed, ensure_ascii=False), "[]", None)
        new_id = None
        async with conn.transaction():
            existing = await _claim_policy_slot(conn, target_type, item_id)   # 추가는 대상이 없어 잠금·확인 없음
            # 위 확인은 잠금 전 — 그 사이 다른 승인으로 이 버전이 은퇴했으면 낡은 버전에 제안이 붙는다(service.create_policy_proposal과 같은 이유)
            if existing is None and item_id is not None and not await conn.fetchval(
                    f"SELECT 1 FROM policy_item i WHERE i.id = $1 AND i.namespace_id {_IN_SCOPE_2} AND {searchable_sql('i')}", item_id, ns_id):
                raise ValueError(_STALE)
            if existing is None:
                try:
                    new_id = await _write_item(conn, values, None)
                except asyncpg.UniqueViolationError:   # 같은 사람의 같은 대상 대기 건(유니크 인덱스)
                    existing = await _pending_policy_correction(conn, item_id) if item_id is not None else None
    if new_id is None:
        if existing is None:
            raise ConflictError("이미 같은 제안이 검토 중이에요 — 정정 요청에서 확인하세요.")
        raise ConflictError(_duplicate_message(existing), existing_id=existing)
    return {"id": new_id, "status": "pending", "kind": "correction", "target_type": target_type, "source": SOURCE,
            "original": original, "proposed": proposed}


async def _embed(name: str, path: list[str], text: str) -> list[float]:
    from service.policy.service import chunk_embed_text   # 머리 붙인 임베딩(가져오기·정정과 같은 규칙)
    return await embedding_service.embed(chunk_embed_text(name, path, text))


async def _prepare(item: dict, stored: dict) -> dict:
    """임베딩은 트랜잭션 밖에서 미리 — 임베딩 동안 행 잠금을 쥐지 않게(service._prepare_policy와 같은 이유).
    추가 = {"emb": 원문 임베딩}, 옮기기 = {조각 id: (글, 새 분류로 만든 임베딩)}, 폐기 = {}."""
    if item["target_type"] == ADD:
        return {"emb": await _embed(stored["policy_name"], stored["category_path"], stored["raw_body"])}
    if item["target_type"] != MOVE:
        return {}
    async with get_conn() as conn:
        old = await conn.fetchrow(_ITEM_SQL, item["target_id"], item["namespace_id"])
        if not old:
            raise ValueError(_GONE_ON_APPROVE)
        chunks = await conn.fetch(_CHUNKS_SQL, old["id"])
    return {c["id"]: (c["chunk_text"], await _embed(old["policy_name"], stored["category_path"], c["chunk_text"])) for c in chunks}


async def approve(item: dict, approver: dict, expected: Optional[dict], stored: Optional[dict]) -> dict:
    """승인 — 화면에 보인 안(expected)과 저장된 안이 같을 때만. 반영은 한 트랜잭션, 정책 캐시는 끝나고 비운다."""
    if not stored:
        raise ValueError("제안 내용이 비어 있어요 — 반려하세요.")
    if expected is not None and expected != stored:
        raise ConflictError("그 사이 제안이 바뀌었어요 — 새로고침해서 확인한 뒤 승인하세요.")
    t, ns_id = item["target_type"], item["namespace_id"]
    prepared = await _prepare(item, stored)
    async with get_conn() as conn:
        async with conn.transaction():
            locked = await conn.fetchrow("SELECT status, proposed FROM ops_improvement_item WHERE id = $1 FOR UPDATE", item["id"])
            if not locked or locked["status"] != "pending":
                raise ConflictError("그 사이 다른 사람이 먼저 처리한 제안이에요 — 새로고침해서 확인하세요.")
            if (json.loads(locked["proposed"]) if isinstance(locked["proposed"], str) else locked["proposed"]) != stored:
                raise ConflictError("그 사이 제안이 바뀌었어요 — 새로고침해서 확인한 뒤 승인하세요.")
            if t == ADD:
                new_id = await _apply_add(conn, ns_id, stored, approver, prepared["emb"])
            elif t == RETIRE:
                new_id = await _apply_retire(conn, ns_id, item["target_id"])
            else:
                new_id = await _apply_move(conn, ns_id, item["target_id"], stored["category_path"], approver, prepared)
            await conn.execute(
                "UPDATE ops_improvement_item SET status = 'approved', applied_target_id = $2, decided_by = $3, decided_at = NOW() WHERE id = $1",
                item["id"], new_id, approver.get("id"))
            superseded = await _close_superseded(conn, item, approver) if item.get("target_id") else 0
    try:   # 정책 색인(키워드·분류)·답변 캐시 — 실패해도 반영은 그대로(로그)
        from service.policy.caches import invalidate_policy_caches
        await invalidate_policy_caches()
    except Exception:
        logger.warning("정책 변경 승인 후 정책 캐시 비우기 실패 (item=%s)", item["id"], exc_info=True)
    return {"id": item["id"], "status": "approved", "applied_target_id": new_id, "superseded": superseded}


async def _lock_slot(conn, ns_id: int, path: list[str], name: str) -> None:
    """같은 (컬렉션, 분류, 이름) 자리를 쓰는 승인끼리 줄 세우기 — 추가끼리·추가와 옮기기가 동시에 "같은 이름 없음"을 보지 않게(재리뷰 10/27)."""
    await conn.execute("SELECT pg_advisory_xact_lock($1, hashtext($2))", _ADD_LOCK_KEY,
                       json.dumps([ns_id, path, name], ensure_ascii=False))


async def _lock_current(conn, ns_id: int, item_id: int):
    old = await conn.fetchrow(f"{_ITEM_SQL} FOR UPDATE", item_id, ns_id)
    if not old or not is_searchable(old["status"], old["pipeline_version"]):
        raise ValueError(_GONE_ON_APPROVE)
    return old


async def _apply_add(conn, ns_id: int, p: dict, approver: dict, emb: list[float]) -> int:
    await _lock_slot(conn, ns_id, p["category_path"], p["policy_name"])
    if await _same_name(conn, ns_id, p["category_path"], p["policy_name"]):
        raise ValueError("그 사이 같은 분류에 같은 이름의 정책이 생겼어요 — 이 제안은 반려하세요.")
    # 원천 키 = 시스템 키(골든 정답·JSON 내려받기/가져오기가 이 키로 찾는다). logical_id는 트리거가 자기 id로 채운다 → 한 번에 RETURNING
    new_id = await conn.fetchval("""
        INSERT INTO policy_item (namespace_id, category_path, policy_name, raw_body, remark, content_hash, status, version,
                                 parse_status, reviewed_at, reviewed_by, pipeline_version)
        VALUES ($1, $2::text[], $3, $4, $5, $6, 'active', 1, 'parsed', NOW(), $7, $8) RETURNING id
    """, ns_id, p["category_path"], p["policy_name"], p["raw_body"], p.get("remark"),
        hashlib.sha256(p["raw_body"].encode("utf-8")).hexdigest(), approver.get("id"), PIPELINE_WHOLE)
    await conn.execute("UPDATE policy_item SET source_key = $2::text || logical_id WHERE id = $1", new_id, SYS_KEY_PREFIX)
    await conn.execute("INSERT INTO policy_chunk (policy_item_id, chunk_text, embedding, chunk_idx) VALUES ($1, $2, $3::vector, 0)",
                       new_id, p["raw_body"], str(emb))
    return new_id


async def _apply_retire(conn, ns_id: int, item_id: int) -> int:
    await _lock_current(conn, ns_id, item_id)
    await conn.execute("UPDATE policy_item SET status = 'deprecated', updated_at = NOW() WHERE id = $1", item_id)
    return item_id


async def _apply_move(conn, ns_id: int, item_id: int, path: list[str], approver: dict, prepared: dict) -> int:
    old = await _lock_current(conn, ns_id, item_id)
    chunks = await conn.fetch(_CHUNKS_SQL, item_id)
    if {c["id"]: c["chunk_text"] for c in chunks} != {cid: text for cid, (text, _) in prepared.items()}:
        raise ValueError("승인 준비 중 정책이 바뀌었어요 — 다시 승인해 주세요.")
    await _lock_slot(conn, ns_id, path, old["policy_name"])   # 정책 행 FOR UPDATE → 자리 잠금(추가 쪽은 자리 잠금만이라 교착 없음)
    if await _same_name(conn, ns_id, path, old["policy_name"], exclude_logical=old["logical_id"]):
        raise ValueError("그 사이 옮길 분류에 같은 이름의 정책이 생겼어요 — 이 제안은 반려하세요.")
    next_version = await conn.fetchval("SELECT COALESCE(MAX(version), 0) + 1 FROM policy_item WHERE logical_id = $1", old["logical_id"])
    # 원본을 먼저 내린다 — "정책당 사용 중 1개" 고유 인덱스(#80)가 즉시 검사(service._apply_policy와 같은 순서)
    await conn.execute("UPDATE policy_item SET status = 'deprecated', updated_at = NOW() WHERE id = $1", old["id"])
    new_id = await conn.fetchval("""
        INSERT INTO policy_item
            (namespace_id, system_key, category_path, policy_name, raw_body, remark, source_file, source_sheet,
             source_row, content_hash, status, logical_id, version, supersedes_id, parse_status,
             unresolved_segments, reviewed_at, reviewed_by, pipeline_version, source_key)
        VALUES ($1, $2, $3::text[], $4, $5, $6, $7, $8, $9, $10, 'active', $11, $12, $13, $14, $15, NOW(), $16, $17, $18)
        RETURNING id
    """, old["namespace_id"], old["system_key"], path, old["policy_name"], old["raw_body"], old["remark"],
        old["source_file"], old["source_sheet"], old["source_row"], old["content_hash"], old["logical_id"],
        next_version, old["id"], old["parse_status"], old["unresolved_segments"],
        approver.get("id"), old["pipeline_version"], old["source_key"])   # 원천 키 유지 — 골든 정답이 그대로
    await conn.execute(
        "INSERT INTO policy_param (policy_item_id, name, condition, value, unit, external_source) "
        "SELECT $1, name, condition, value, unit, external_source FROM policy_param WHERE policy_item_id = $2 ORDER BY id",
        new_id, old["id"])
    await conn.executemany(
        "INSERT INTO policy_chunk (policy_item_id, chunk_text, embedding, chunk_idx) VALUES ($1, $2, $3::vector, $4)",
        [(new_id, prepared[c["id"]][0], str(prepared[c["id"]][1]), c["chunk_idx"]) for c in chunks])
    return new_id
