"""관리 도메인 — namespace, stats, LLM 설정 라우터."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam

from core.database import get_conn
from core.dependencies import get_current_user, get_current_admin, check_namespace_ownership
from shared.embedding import embedding_service
from domain.admin.schemas import (
    NamespaceCreate, NamespaceInfo,
    NamespaceStats, StatsResponse, TermStat, NamespaceDetailStats,
    LLMConfigUpdate, LLMTestRequest, ThresholdUpdate,
)
from domain.admin import service
from domain.llm.factory import get_llm_provider, switch_provider, get_runtime_config
from domain.llm.ollama import OllamaProvider
from domain.llm.inhouse import InHouseLLMProvider
from domain.knowledge.retrieval import get_thresholds, set_thresholds

router = APIRouter(tags=["admin"])

_CONFIG_FIELDS = (
    "ollama_base_url", "ollama_model", "ollama_timeout",
    "inhouse_llm_url", "inhouse_llm_api_key", "inhouse_llm_model", "inhouse_llm_timeout",
)


def _extract_config(body) -> dict:
    cfg = {"provider": body.provider}
    for field in _CONFIG_FIELDS:
        val = getattr(body, field, None)
        if val is not None:
            cfg[field] = val
    return cfg


async def _insert_feedback_if_message_exists(conn, namespace: str, question: str, message_id: int | None) -> None:
    if not message_id:
        return
    exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops_message WHERE id = $1)", message_id)
    if exists:
        await conn.execute(
            "INSERT INTO ops_feedback (namespace, question, is_positive, message_id) VALUES ($1, $2, TRUE, $3)",
            namespace, question, message_id,
        )


# ── Namespace ────────────────────────────────────────────────────────────────

@router.get("/api/namespaces", response_model=list[str])
async def get_namespaces(user: dict = Depends(get_current_user)):
    return await service.list_namespaces()


@router.get("/api/namespaces/detail", response_model=list[NamespaceInfo])
async def get_namespaces_detail(user: dict = Depends(get_current_user)):
    return await service.list_namespaces_detail()


@router.post("/api/namespaces", response_model=dict)
async def create_namespace_endpoint(body: NamespaceCreate, user: dict = Depends(get_current_user)):
    return await service.create_namespace(
        body.name, body.description,
        owner_part=user["part"], created_by_user_id=user["id"],
    )


@router.delete("/api/namespaces/{name}", status_code=204)
async def delete_namespace_endpoint(name: str, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(name, user)
    success = await service.delete_namespace(name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Namespace '{name}' not found")


# ── Stats ────────────────────────────────────────────────────────────────────

@router.get("/api/stats", response_model=StatsResponse)
async def get_stats(user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        ns_rows = await conn.fetch(
            """
            WITH all_ns AS (
                SELECT DISTINCT name AS namespace FROM ops_namespace
                UNION SELECT DISTINCT namespace FROM ops_knowledge
                UNION SELECT DISTINCT namespace FROM ops_glossary
            ),
            q_agg AS (
                SELECT namespace, COUNT(*) AS total_queries,
                    COUNT(*) FILTER (WHERE status = 'resolved') AS resolved,
                    COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                    COUNT(*) FILTER (WHERE status = 'unresolved') AS unresolved
                FROM ops_query_log GROUP BY namespace
            ),
            fb_agg AS (
                SELECT namespace,
                    COUNT(*) FILTER (WHERE is_positive) AS positive_feedback,
                    COUNT(*) FILTER (WHERE NOT is_positive) AS negative_feedback
                FROM ops_feedback GROUP BY namespace
            ),
            k_agg AS (SELECT namespace, COUNT(*) AS cnt FROM ops_knowledge GROUP BY namespace),
            g_agg AS (SELECT namespace, COUNT(*) AS cnt FROM ops_glossary GROUP BY namespace)
            SELECT n.namespace,
                COALESCE(q.total_queries, 0) AS total_queries,
                COALESCE(q.resolved, 0) AS resolved,
                COALESCE(q.pending, 0) AS pending,
                COALESCE(q.unresolved, 0) AS unresolved,
                COALESCE(f.positive_feedback, 0) AS positive_feedback,
                COALESCE(f.negative_feedback, 0) AS negative_feedback,
                COALESCE(k.cnt, 0) AS knowledge_count,
                COALESCE(g.cnt, 0) AS glossary_count
            FROM all_ns n
            LEFT JOIN q_agg q ON n.namespace = q.namespace
            LEFT JOIN fb_agg f ON n.namespace = f.namespace
            LEFT JOIN k_agg k ON n.namespace = k.namespace
            LEFT JOIN g_agg g ON n.namespace = g.namespace
            ORDER BY total_queries DESC, n.namespace
            """
        )
        unresolved_rows = await conn.fetch(
            "SELECT namespace, question, created_at::text FROM ops_query_log WHERE status = 'unresolved' ORDER BY created_at DESC LIMIT 20"
        )

    return StatsResponse(
        namespaces=[NamespaceStats(**dict(r)) for r in ns_rows],
        unresolved_cases=[dict(r) for r in unresolved_rows],
    )


@router.get("/api/stats/namespace/{name}", response_model=NamespaceDetailStats)
async def get_namespace_stats(name: str, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        summary = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total_queries,
                COUNT(*) FILTER (WHERE status = 'resolved') AS resolved,
                COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                COUNT(*) FILTER (WHERE status = 'unresolved') AS unresolved
            FROM ops_query_log WHERE namespace = $1
            """, name,
        )
        term_rows = await conn.fetch(
            """
            SELECT COALESCE(mapped_term, '기타') AS term,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                COUNT(*) FILTER (WHERE status = 'unresolved') AS unresolved
            FROM ops_query_log WHERE namespace = $1
            GROUP BY mapped_term ORDER BY total DESC LIMIT 20
            """, name,
        )
        unresolved_rows = await conn.fetch(
            """
            SELECT id, question, mapped_term, created_at::text
            FROM ops_query_log WHERE namespace = $1 AND status = 'unresolved'
            ORDER BY created_at DESC LIMIT 30
            """, name,
        )

    return NamespaceDetailStats(
        namespace=name,
        total_queries=summary["total_queries"] or 0,
        resolved=summary["resolved"] or 0,
        pending=summary["pending"] or 0,
        unresolved=summary["unresolved"] or 0,
        term_distribution=[TermStat(**dict(r)) for r in term_rows],
        unresolved_cases=[dict(r) for r in unresolved_rows],
    )


@router.get("/api/stats/namespace/{name}/queries")
async def get_namespace_queries(
    name: str,
    status: Optional[str] = None,
    limit: int = QueryParam(default=100, le=500),
    user: dict = Depends(get_current_user),
):
    async with get_conn() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT id, question, answer, mapped_term, status, created_at::text FROM ops_query_log WHERE namespace = $1 AND status = $2 ORDER BY created_at DESC LIMIT $3",
                name, status, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, question, answer, mapped_term, status, created_at::text FROM ops_query_log WHERE namespace = $1 ORDER BY created_at DESC LIMIT $2",
                name, limit,
            )
    return [dict(r) for r in rows]


@router.patch("/api/stats/query-log/{log_id}/resolve", status_code=200)
async def resolve_query_log(log_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT namespace, question, answer, mapped_term, message_id FROM ops_query_log WHERE id = $1 AND status = 'pending'", log_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Query log not found or not pending")
        await check_namespace_ownership(row["namespace"], user)
        if not row["answer"]:
            raise HTTPException(status_code=400, detail="답변이 없어 지식으로 등록할 수 없습니다.")

        vec = await embedding_service.embed(row["answer"])
        await conn.execute(
            """INSERT INTO ops_knowledge
               (namespace, container_name, content, embedding, created_by_part, created_by_user_id)
               VALUES ($1, $2, $3, $4::vector, $5, $6)""",
            row["namespace"], row["mapped_term"] or "미분류", row["answer"], str(vec),
            user["part"], user["id"],
        )
        await conn.execute("UPDATE ops_query_log SET status = 'resolved' WHERE id = $1", log_id)
        await _insert_feedback_if_message_exists(conn, row["namespace"], row["question"], row["message_id"])
    return {"status": "ok"}


@router.patch("/api/stats/query-log/{log_id}/mark-resolved", status_code=200)
async def mark_query_log_resolved(log_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT namespace, question, message_id FROM ops_query_log WHERE id = $1", log_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Query log not found")
        await conn.execute("UPDATE ops_query_log SET status = 'resolved' WHERE id = $1", log_id)
        await _insert_feedback_if_message_exists(conn, row["namespace"], row["question"], row["message_id"])
    return {"status": "ok"}


@router.delete("/api/stats/query-log/{log_id}", status_code=204)
async def delete_query_log(log_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        row = await conn.fetchrow("SELECT namespace FROM ops_query_log WHERE id = $1", log_id)
        if not row:
            raise HTTPException(status_code=404, detail="Query log not found")
        await check_namespace_ownership(row["namespace"], user)
        await conn.execute("DELETE FROM ops_query_log WHERE id = $1", log_id)


@router.post("/api/stats/query-logs/bulk-delete", status_code=200)
async def bulk_delete_query_logs(body: dict, user: dict = Depends(get_current_user)):
    ids: list[int] = body.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="ids is required")
    async with get_conn() as conn:
        # 삭제 대상의 네임스페이스를 확인하여 권한 체크
        rows = await conn.fetch("SELECT DISTINCT namespace FROM ops_query_log WHERE id = ANY($1::int[])", ids)
        for r in rows:
            await check_namespace_ownership(r["namespace"], user)
        result = await conn.execute("DELETE FROM ops_query_log WHERE id = ANY($1::int[])", ids)
    deleted = int(result.split()[-1]) if result else 0
    return {"deleted": deleted}


# ── LLM Settings ─────────────────────────────────────────────────────────────

@router.get("/api/llm/config")
async def get_llm_config(user: dict = Depends(get_current_user)):
    config = get_runtime_config()
    is_connected = await get_llm_provider().health_check()
    return {**config, "is_connected": is_connected}


@router.put("/api/llm/config")
async def update_llm_config(body: LLMConfigUpdate, admin: dict = Depends(get_current_admin)):
    if body.provider not in ("ollama", "inhouse"):
        raise HTTPException(status_code=400, detail="provider는 'ollama' 또는 'inhouse'여야 합니다.")
    try:
        new_provider = switch_provider(_extract_config(body))
        is_connected = await new_provider.health_check()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {**get_runtime_config(), "is_connected": is_connected}


@router.post("/api/llm/test")
async def test_llm_connection(body: LLMTestRequest, user: dict = Depends(get_current_user)):
    cfg = _extract_config(body)
    try:
        provider = InHouseLLMProvider(cfg) if body.provider == "inhouse" else OllamaProvider(cfg)
        is_connected = await provider.health_check()
    except ValueError as e:
        return {"is_connected": False, "error": str(e)}
    return {"is_connected": is_connected, "provider": body.provider}


@router.get("/api/llm/thresholds")
async def get_threshold_config(user: dict = Depends(get_current_user)):
    return get_thresholds()


@router.put("/api/llm/thresholds")
async def update_threshold_config(body: ThresholdUpdate, admin: dict = Depends(get_current_admin)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    for k, v in updates.items():
        if not 0.0 <= v <= 1.0:
            raise HTTPException(status_code=400, detail=f"{k}는 0~1 범위여야 합니다.")
    return set_thresholds(updates)
