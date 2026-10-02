"""정책 승인 대기 큐 자동 통과 — 위험도 낮음만 규칙으로 통과시키고, 사람은 위험한 것만 본다(2026-10-01).

- 낮음(`risk.LOW_RISK_RULE`)은 자동 통과(active)하되 `review_source='auto_rule'`로 사람 승인과 구분, 되돌리기 가능.
- 그중 표본(기본 10%)은 사람 확인 큐에 남긴다(`review_sample`). 표본이 반려되면 그 규칙을 자동으로 멈춘다(Soft) —
  자동 통과 기준을 데이터로 조정하는 첫 형태. 재개는 사람이 확인한 뒤에.
- 검색 동작은 안 바뀐다 — 채팅 검색은 rejected·deprecated만 빼므로 pending_review↔active 이동은 결과에 영향 없음.
- 모든 결정은 `policy_review_log`에 등급·근거 스냅샷과 함께 남는다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from service.policy import risk

logger = logging.getLogger(__name__)

CONFIG_KEY = "policy_auto_review"
DEFAULT_CONFIG = {"sample_rate": 0.1, "rules": {risk.LOW_RISK_RULE: {"enabled": True, "paused_at": None,
                                                                     "paused_reason": None}}}

_PENDING_SQL = f"""
    SELECT p.id, p.logical_id, p.namespace_id, p.parse_status, p.review_sample, p.review_rule, n.name AS namespace,
           {risk.RISK_FEATURES_SQL}
    FROM policy_item p JOIN ops_namespace n ON n.id = p.namespace_id
    WHERE p.status = 'pending_review' AND ($1::text IS NULL OR n.name = $1)
"""


# ── 규칙 설정(ops_system_config) ───────────────────────────────────────────────

async def get_config(conn) -> dict:
    raw = await conn.fetchval("SELECT value FROM ops_system_config WHERE key = $1", CONFIG_KEY)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if raw:
        try:
            stored = json.loads(raw)
            cfg["sample_rate"] = float(stored.get("sample_rate", cfg["sample_rate"]))
            for k, v in (stored.get("rules") or {}).items():
                cfg["rules"][k] = {**cfg["rules"].get(k, {}), **v}
        except (ValueError, TypeError, AttributeError):
            logger.warning("정책 자동 통과 설정 파싱 실패 — 기본값 사용", exc_info=True)
    return cfg


async def _save_config(conn, cfg: dict) -> None:
    await conn.execute(
        "INSERT INTO ops_system_config (key, value, updated_at) VALUES ($1, $2, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()",
        CONFIG_KEY, json.dumps(cfg, ensure_ascii=False))


def rule_active(cfg: dict, rule_key: str) -> bool:
    r = cfg["rules"].get(rule_key) or {}
    return bool(r.get("enabled")) and not r.get("paused_at")


def is_sample(item_id: int, rate: float) -> bool:
    """결정론적 표본 — 같은 항목은 몇 번 돌려도 같은 결과(재현·설명 가능)."""
    return int(hashlib.md5(str(item_id).encode()).hexdigest(), 16) % 1000 < int(rate * 1000)


# ── 이력 ───────────────────────────────────────────────────────────────────────

async def log(conn, rows: list[dict], action: str, *, actor_id: Optional[int] = None, run_id: Optional[str] = None,
              note: Optional[str] = None) -> None:
    """rows: {id, logical_id, namespace_id, rule_key, risk_level, risk_reasons} — 결정 시점 등급·근거 스냅샷."""
    if not rows:
        return
    await conn.executemany(
        "INSERT INTO policy_review_log (policy_item_id, logical_id, namespace_id, action, actor_user_id, rule_key, "
        "risk_level, risk_reasons, run_id, note) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)",
        [(r["id"], r["logical_id"], r["namespace_id"], action, actor_id, r.get("rule_key"), r.get("risk_level"),
          json.dumps(r.get("risk_reasons") or [], ensure_ascii=False), run_id, note) for r in rows])


def _snapshot(row, rk: risk.Risk) -> dict:
    return {"id": row["id"], "logical_id": row["logical_id"], "namespace_id": row["namespace_id"],
            "rule_key": rk.rule_key, "risk_level": rk.level, "risk_reasons": rk.reasons}


# ── 실행 ───────────────────────────────────────────────────────────────────────

async def run(namespace: Optional[str], *, actor_id: Optional[int], dry_run: bool, trigger: str = "manual") -> dict:
    """pending_review 중 낮음 → 표본은 사람 큐에 남기고 나머지 자동 통과. dry_run이면 건수만."""
    run_id = uuid.uuid4().hex
    async with get_conn() as conn:
        cfg = await get_config(conn)
        async with conn.transaction():
            # 사람 승인/반려와 같은 행을 동시에 바꾸지 않도록 잠그고, 이미 잠긴 행은 이번엔 건너뛴다
            rows = await conn.fetch(_PENDING_SQL + " FOR UPDATE OF p SKIP LOCKED", namespace)
            approve, sample, counts = [], [], {"high": 0, "medium": 0, "low": 0}
            for r in rows:
                rk = risk.classify_row(r)
                counts[rk.level] += 1
                if rk.level != "low" or r["review_sample"] or not rule_active(cfg, rk.rule_key):
                    continue
                (sample if is_sample(r["id"], cfg["sample_rate"]) else approve).append(_snapshot(r, rk))
            if not dry_run:
                if approve:
                    await conn.execute(
                        "UPDATE policy_item SET status = 'active', review_source = 'auto_rule', review_rule = $2, "
                        "reviewed_at = NOW(), reviewed_by = NULL, updated_at = NOW() WHERE id = ANY($1::int[])",
                        [a["id"] for a in approve], risk.LOW_RISK_RULE)
                    await log(conn, approve, "auto_approved", run_id=run_id, note=trigger)
                if sample:
                    await conn.execute(
                        "UPDATE policy_item SET review_sample = TRUE, review_rule = $2, updated_at = NOW() "
                        "WHERE id = ANY($1::int[])", [s["id"] for s in sample], risk.LOW_RISK_RULE)
                    await log(conn, sample, "sampled", run_id=run_id, note=trigger)
    queue_before = len(rows) - sum(1 for r in rows if risk.classify_row(r).level == "low"
                                   and not r["review_sample"] and rule_active(cfg, risk.LOW_RISK_RULE))
    out = {"run_id": None if dry_run else run_id, "dry_run": dry_run, "pending": len(rows), "by_level": counts,
           "auto_approved": len(approve), "sampled": len(sample),
           "human_queue_after": len(rows) - len(approve)}
    if approve or sample:
        logger.info("[policy auto-review] %s ns=%s trigger=%s approved=%d sampled=%d queue_before=%d",
                    "DRY" if dry_run else run_id, namespace or "*", trigger, len(approve), len(sample), queue_before)
    return out


async def run_after_import(namespace: str) -> Optional[dict]:
    """임포트 직후 그 파트에 자동 적용 — 재임포트가 새 버전을 pending으로 다시 채우는 구조를 막는다.
    best-effort: 실패해도 임포트는 성공으로 두되 로그는 남긴다(다음 수동 실행으로 회복 가능)."""
    try:
        async with get_conn() as conn:
            if not rule_active(await get_config(conn), risk.LOW_RISK_RULE):
                return None
        return await run(namespace, actor_id=None, dry_run=False, trigger="import")
    except Exception:
        logger.warning("임포트 후 정책 자동 통과 실패 — 수동 실행으로 회복 가능 (ns=%s)", namespace, exc_info=True)
        return None


# ── 요약(사람이 봐야 할 큐) ────────────────────────────────────────────────────

async def summary(namespace: Optional[str]) -> dict:
    async with get_conn() as conn:
        cfg = await get_config(conn)
        rows = await conn.fetch(_PENDING_SQL, namespace)
        auto = await conn.fetch(
            "SELECT p.review_rule, COUNT(*) AS n FROM policy_item p JOIN ops_namespace n ON n.id = p.namespace_id "
            "WHERE p.status = 'active' AND p.review_source = 'auto_rule' AND ($1::text IS NULL OR n.name = $1) "
            "GROUP BY p.review_rule", namespace)
    active = rule_active(cfg, risk.LOW_RISK_RULE)
    q = {"high": 0, "medium": 0, "sample": 0, "low_waiting": 0}
    for r in rows:
        level = risk.classify_row(r).level
        if level != "low":
            q[level] += 1
        elif r["review_sample"]:
            q["sample"] += 1
        else:
            q["low_waiting"] += 1  # 규칙이 켜져 있으면 다음 실행 때 자동 통과, 멈췄으면 사람 큐
    human_queue = q["high"] + q["medium"] + q["sample"] + (0 if active else q["low_waiting"])
    return {"pending": len(rows), "human_queue": human_queue, "queue": q,
            "auto_approved": {r["review_rule"]: r["n"] for r in auto},
            "sample_rate": cfg["sample_rate"], "rules": cfg["rules"], "rule_active": active}


# ── 표본 반려 → 규칙 정지 / 재개 ────────────────────────────────────────────────

async def pause_rule(conn, rule_key: str, reason: str, *, actor_id: Optional[int], item: Optional[dict] = None) -> None:
    cfg = await get_config(conn)
    r = cfg["rules"].setdefault(rule_key, {"enabled": True})
    if r.get("paused_at"):
        return
    r["paused_at"] = (await conn.fetchval("SELECT NOW()")).isoformat()
    r["paused_reason"] = reason
    await _save_config(conn, cfg)
    if item:
        await log(conn, [item], "rule_paused", actor_id=actor_id, note=reason)
    logger.warning("[policy auto-review] 규칙 정지 %s — %s", rule_key, reason)


async def resume_rule(rule_key: str, actor_id: int) -> dict:
    async with get_conn() as conn:
        async with conn.transaction():
            cfg = await get_config(conn)
            if rule_key not in cfg["rules"]:
                raise ValueError(f"알 수 없는 규칙입니다: {rule_key}")
            cfg["rules"][rule_key] |= {"enabled": True, "paused_at": None, "paused_reason": None}
            await _save_config(conn, cfg)
            await conn.execute(
                "INSERT INTO policy_review_log (action, actor_user_id, rule_key, note) VALUES ('rule_resumed', $1, $2, $3)",
                actor_id, rule_key, "관리자 재개")
    return cfg["rules"][rule_key]


async def on_human_reject(conn, row: dict, rk: risk.Risk, actor_id: int) -> bool:
    """사람이 반려한 게 자동 통과 표본이면 그 규칙을 멈춘다. 멈췄으면 True."""
    if not row.get("review_sample") or not row.get("review_rule"):
        return False
    await pause_rule(conn, row["review_rule"], f"표본 #{row['id']} 반려 — 같은 규칙의 자동 통과를 멈춤",
                     actor_id=actor_id, item=_snapshot(row, rk) | {"rule_key": row["review_rule"]})
    return True


# ── 되돌리기 ───────────────────────────────────────────────────────────────────

_REVERT_SQL = """
    UPDATE policy_item p SET status = 'pending_review', review_source = NULL, review_rule = NULL,
           reviewed_at = NULL, reviewed_by = NULL, updated_at = NOW()
    WHERE p.id = ANY($1::int[]) AND p.status = 'active' AND p.review_source = 'auto_rule'
    RETURNING p.id, p.logical_id, p.namespace_id
"""


async def revert_item(namespace: str, item_id: int, actor_id: int) -> None:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")
        async with conn.transaction():
            owned = await conn.fetchval("SELECT 1 FROM policy_item WHERE id = $1 AND namespace_id = $2", item_id, ns_id)
            if not owned:
                raise ValueError(f"정책 항목을 찾을 수 없습니다: item_id={item_id}")
            rows = await conn.fetch(_REVERT_SQL, [item_id])
            if not rows:
                raise ValueError("자동 통과된 항목만 되돌릴 수 있습니다.")
            await log(conn, [dict(r) for r in rows], "auto_reverted", actor_id=actor_id)


async def revert_bulk(*, run_id: Optional[str], rule_key: Optional[str], actor_id: int,
                      namespace: Optional[str] = None) -> int:
    """한 번의 실행(run_id) 또는 한 규칙으로 자동 통과된 것 되돌리기 — 지금도 자동 통과 상태인 것만.
    namespace를 주면 그 파트만(화면이 보여주는 건수와 실제 되돌리는 범위가 같아야 한다 — /code-review)."""
    if not run_id and not rule_key:
        raise ValueError("run_id 또는 rule_key가 필요합니다.")
    async with get_conn() as conn:
        async with conn.transaction():
            if run_id:
                ids = [r["policy_item_id"] for r in await conn.fetch(
                    "SELECT l.policy_item_id FROM policy_review_log l JOIN ops_namespace n ON n.id = l.namespace_id "
                    "WHERE l.run_id = $1 AND l.action = 'auto_approved' AND l.policy_item_id IS NOT NULL "
                    "AND ($2::text IS NULL OR n.name = $2)", run_id, namespace)]
            else:
                ids = [r["id"] for r in await conn.fetch(
                    "SELECT p.id FROM policy_item p JOIN ops_namespace n ON n.id = p.namespace_id "
                    "WHERE p.status = 'active' AND p.review_source = 'auto_rule' AND p.review_rule = $1 "
                    "AND ($2::text IS NULL OR n.name = $2)", rule_key, namespace)]
            rows = await conn.fetch(_REVERT_SQL, ids) if ids else []
            await log(conn, [dict(r) for r in rows], "auto_reverted", actor_id=actor_id,
                      note=f"일괄 되돌리기 ({'run ' + run_id if run_id else 'rule ' + rule_key}, {namespace or '전체 파트'})")
    return len(rows)
