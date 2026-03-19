"""관리 도메인 — namespace, stats, LLM 설정 라우터."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from pydantic import BaseModel

from core.database import get_conn, resolve_namespace_id
from core.dependencies import get_current_user, get_current_admin, check_namespace_ownership
from shared.embedding import embedding_service
from domain.admin.schemas import (
    NamespaceCreate, NamespaceInfo,
    KnowledgeCategoryCreate, KnowledgeCategoryOut,
    NamespaceStats, StatsResponse, TermStat, NamespaceDetailStats,
    LLMConfigUpdate, LLMTestRequest, ThresholdUpdate, SearchDefaultsUpdate,
)
from domain.admin import service
from domain.llm.factory import get_llm_provider, switch_provider, get_runtime_config
from domain.llm.ollama import OllamaProvider
from domain.llm.inhouse import InHouseLLMProvider
from domain.knowledge.retrieval import get_thresholds, set_thresholds, get_search_defaults, set_search_defaults
from domain.prompt.loader import get_prompt as load_prompt
from shared import cache as sem_cache
from agents.base import AgentRegistry

router = APIRouter(tags=["admin"])


# ── Agent Directory ───────────────────────────────────────────────────────────

@router.get("/api/agents")
async def list_agents(_user: dict = Depends(get_current_user)):
    """등록된 에이전트 목록 반환."""
    return AgentRegistry.list_all()


@router.get("/api/agents/{agent_id}/health")
async def agent_health(agent_id: str, _user: dict = Depends(get_current_user)):
    try:
        agent = AgentRegistry.get(agent_id)
        ok = await agent.health_check()
        return {"agent_id": agent_id, "healthy": ok}
    except ValueError:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")

_CONFIG_FIELDS = (
    "ollama_base_url", "ollama_model", "ollama_timeout",
    "inhouse_llm_url", "inhouse_llm_api_key", "inhouse_llm_model",
    "inhouse_llm_agent_code", "inhouse_llm_response_mode", "inhouse_llm_timeout",
)


def _extract_config(body) -> dict:
    cfg = {"provider": body.provider}
    for field in _CONFIG_FIELDS:
        val = getattr(body, field, None)
        if val is not None:
            cfg[field] = val
    return cfg


async def _insert_feedback_if_message_exists(conn, namespace_id: int, question: str, message_id: int | None) -> None:
    if not message_id:
        return
    exists = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops_message WHERE id = $1)", message_id)
    if exists:
        await conn.execute(
            "INSERT INTO ops_feedback (namespace_id, question, is_positive, message_id) VALUES ($1, $2, TRUE, $3)",
            namespace_id, question, message_id,
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
    # admin이 생성한 네임스페이스는 공통(owner_part=NULL) — 모든 사용자가 CRUD 가능
    owner_part = None if user["role"] == "admin" else user["part"]
    return await service.create_namespace(
        body.name, body.description,
        owner_part=owner_part, created_by_user_id=user["id"],
    )


@router.patch("/api/namespaces/{name}", response_model=dict)
async def rename_namespace_endpoint(name: str, body: dict, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(name, user)
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="새 이름을 입력해주세요.")
    success = await service.rename_namespace(name, new_name)
    if not success:
        raise HTTPException(status_code=409, detail="이미 존재하는 파트 이름입니다.")
    return {"name": new_name}


@router.delete("/api/namespaces/{name}", status_code=204)
async def delete_namespace_endpoint(name: str, user: dict = Depends(get_current_user)):
    # 공통 파트(owner_part_id=NULL) 삭제는 admin 전용
    if user["role"] != "admin":
        async with get_conn() as conn:
            owner_part_id = await conn.fetchval(
                "SELECT owner_part_id FROM ops_namespace WHERE name = $1", name,
            )
        if owner_part_id is None:
            raise HTTPException(status_code=403, detail="공통 파트는 관리자만 삭제할 수 있습니다.")
    await check_namespace_ownership(name, user)
    success = await service.delete_namespace(name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Namespace '{name}' not found")


# ── Knowledge Categories ──────────────────────────────────────────────────────

@router.get("/api/namespaces/{name}/categories", response_model=list[KnowledgeCategoryOut])
async def get_namespace_categories(name: str, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, name)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT kc.id, n.name AS namespace, kc.name, kc.created_at::text
            FROM ops_knowledge_category kc
            JOIN ops_namespace n ON kc.namespace_id = n.id
            WHERE kc.namespace_id = $1
            ORDER BY kc.name
            """,
            ns_id,
        )
    return [dict(r) for r in rows]


@router.post("/api/namespaces/{name}/categories", response_model=KnowledgeCategoryOut, status_code=201)
async def create_namespace_category(name: str, body: KnowledgeCategoryCreate, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(name, user)
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, name)
        if ns_id is None:
            raise HTTPException(status_code=404, detail="네임스페이스를 찾을 수 없습니다.")
        existing = await conn.fetchval(
            "SELECT id FROM ops_knowledge_category WHERE namespace_id = $1 AND name = $2",
            ns_id, body.name.strip(),
        )
        if existing:
            raise HTTPException(status_code=409, detail="이미 존재하는 업무구분입니다.")
        row = await conn.fetchrow(
            """
            INSERT INTO ops_knowledge_category (namespace_id, name) VALUES ($1, $2)
            RETURNING id, $3::text AS namespace, name, created_at::text
            """,
            ns_id, body.name.strip(), name,
        )
    return dict(row)


@router.patch("/api/namespaces/{name}/categories/{cat_name}", response_model=KnowledgeCategoryOut)
async def rename_namespace_category(name: str, cat_name: str, body: KnowledgeCategoryCreate, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(name, user)
    new_name = body.name.strip()
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, name)
        if ns_id is None:
            raise HTTPException(status_code=404, detail="네임스페이스를 찾을 수 없습니다.")
        existing = await conn.fetchval(
            "SELECT id FROM ops_knowledge_category WHERE namespace_id = $1 AND name = $2",
            ns_id, new_name,
        )
        if existing:
            raise HTTPException(status_code=409, detail="이미 존재하는 업무구분입니다.")
        await conn.execute(
            "UPDATE ops_knowledge SET category = $3 WHERE namespace_id = $1 AND category = $2",
            ns_id, cat_name, new_name,
        )
        row = await conn.fetchrow(
            """
            UPDATE ops_knowledge_category SET name = $3
            WHERE namespace_id = $1 AND name = $2
            RETURNING id, $4::text AS namespace, name, created_at::text
            """,
            ns_id, cat_name, new_name, name,
        )
    if not row:
        raise HTTPException(status_code=404, detail="업무구분을 찾을 수 없습니다.")
    return dict(row)


@router.delete("/api/namespaces/{name}/categories/{cat_name}", status_code=204)
async def delete_namespace_category(name: str, cat_name: str, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(name, user)
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, name)
        if ns_id is None:
            raise HTTPException(status_code=404, detail="네임스페이스를 찾을 수 없습니다.")
        # 해당 카테고리를 사용하는 지식 항목의 category를 NULL로 초기화
        await conn.execute(
            "UPDATE ops_knowledge SET category = NULL WHERE namespace_id = $1 AND category = $2",
            ns_id, cat_name,
        )
        result = await conn.execute(
            "DELETE FROM ops_knowledge_category WHERE namespace_id = $1 AND name = $2",
            ns_id, cat_name,
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="업무구분을 찾을 수 없습니다.")


@router.post("/api/namespaces/{name}/categories/suggest")
async def suggest_category(name: str, body: dict, user: dict = Depends(get_current_user)):
    """지식 내용을 분석해 가장 적합한 업무구분을 LLM으로 추천."""
    content: str = body.get("content", "").strip()
    if not content:
        return {"suggested_category": None}

    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, name)
        if ns_id is None:
            return {"suggested_category": None}
        rows = await conn.fetch(
            "SELECT name FROM ops_knowledge_category WHERE namespace_id = $1 ORDER BY name", ns_id
        )
    categories = [r["name"] for r in rows]
    if not categories:
        return {"suggested_category": None}

    categories_str = ", ".join(f'"{c}"' for c in categories)
    _CATEGORY_SUGGEST_FALLBACK = (
        "다음 지식 내용을 읽고, 제시된 업무구분 중 가장 적합한 하나를 골라주세요. "
        "반드시 제시된 업무구분 중 하나의 이름만 답하고, 다른 설명은 절대 하지 마세요.\n\n"
        "업무구분 목록: {categories}\n\n"
        "지식 내용:\n{content}\n\n"
        "가장 적합한 업무구분 이름:"
    )
    template = await load_prompt("category_suggest", _CATEGORY_SUGGEST_FALLBACK)
    try:
        prompt = template.format(categories=categories_str, content=content[:600])
    except KeyError:
        prompt = _CATEGORY_SUGGEST_FALLBACK.format(categories=categories_str, content=content[:600])
    try:
        answer, _ = await get_llm_provider().generate(context="", question=prompt)
        suggested = answer.strip().strip('"').strip("'").strip()
        if suggested not in categories:
            # 부분 일치 시도
            matched = next((c for c in categories if c in suggested or suggested in c), None)
            suggested = matched
    except Exception:
        suggested = None
    return {"suggested_category": suggested}


# ── Stats ────────────────────────────────────────────────────────────────────

@router.get("/api/stats", response_model=StatsResponse)
async def get_stats(user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        ns_rows = await conn.fetch(
            """
            WITH q_agg AS (
                SELECT namespace_id, COUNT(*) AS total_queries,
                    COUNT(*) FILTER (WHERE status = 'resolved') AS resolved,
                    COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                    COUNT(*) FILTER (WHERE status = 'unresolved') AS unresolved
                FROM ops_query_log GROUP BY namespace_id
            ),
            fb_agg AS (
                SELECT namespace_id,
                    COUNT(*) FILTER (WHERE is_positive) AS positive_feedback,
                    COUNT(*) FILTER (WHERE NOT is_positive) AS negative_feedback
                FROM ops_feedback GROUP BY namespace_id
            ),
            k_agg AS (SELECT namespace_id, COUNT(*) AS cnt FROM ops_knowledge GROUP BY namespace_id),
            g_agg AS (SELECT namespace_id, COUNT(*) AS cnt FROM ops_glossary GROUP BY namespace_id)
            SELECT n.name AS namespace,
                COALESCE(q.total_queries, 0) AS total_queries,
                COALESCE(q.resolved, 0) AS resolved,
                COALESCE(q.pending, 0) AS pending,
                COALESCE(q.unresolved, 0) AS unresolved,
                COALESCE(f.positive_feedback, 0) AS positive_feedback,
                COALESCE(f.negative_feedback, 0) AS negative_feedback,
                COALESCE(k.cnt, 0) AS knowledge_count,
                COALESCE(g.cnt, 0) AS glossary_count
            FROM ops_namespace n
            LEFT JOIN q_agg q ON n.id = q.namespace_id
            LEFT JOIN fb_agg f ON n.id = f.namespace_id
            LEFT JOIN k_agg k ON n.id = k.namespace_id
            LEFT JOIN g_agg g ON n.id = g.namespace_id
            ORDER BY total_queries DESC, n.name
            """
        )
        unresolved_rows = await conn.fetch(
            """
            SELECT n.name AS namespace, ql.question, ql.created_at::text
            FROM ops_query_log ql
            JOIN ops_namespace n ON ql.namespace_id = n.id
            WHERE ql.status = 'unresolved'
            ORDER BY ql.created_at DESC LIMIT 20
            """
        )

    return StatsResponse(
        namespaces=[NamespaceStats(**dict(r)) for r in ns_rows],
        unresolved_cases=[dict(r) for r in unresolved_rows],
    )


@router.get("/api/stats/namespace/{name}", response_model=NamespaceDetailStats)
async def get_namespace_stats(name: str, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, name)
        if ns_id is None:
            raise HTTPException(status_code=404, detail=f"Namespace '{name}' not found")
        summary = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total_queries,
                COUNT(*) FILTER (WHERE status = 'resolved') AS resolved,
                COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                COUNT(*) FILTER (WHERE status = 'unresolved') AS unresolved
            FROM ops_query_log WHERE namespace_id = $1
            """, ns_id,
        )
        term_rows = await conn.fetch(
            """
            SELECT COALESCE(mapped_term, '기타') AS term,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                COUNT(*) FILTER (WHERE status = 'unresolved') AS unresolved
            FROM ops_query_log WHERE namespace_id = $1
            GROUP BY mapped_term ORDER BY total DESC LIMIT 20
            """, ns_id,
        )
        unresolved_rows = await conn.fetch(
            """
            SELECT id, question, mapped_term, created_at::text
            FROM ops_query_log WHERE namespace_id = $1 AND status = 'unresolved'
            ORDER BY created_at DESC LIMIT 30
            """, ns_id,
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
        ns_id = await resolve_namespace_id(conn, name)
        if ns_id is None:
            return []
        if status:
            rows = await conn.fetch(
                "SELECT id, question, answer, mapped_term, status, created_at::text FROM ops_query_log WHERE namespace_id = $1 AND status = $2 ORDER BY created_at DESC LIMIT $3",
                ns_id, status, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, question, answer, mapped_term, status, created_at::text FROM ops_query_log WHERE namespace_id = $1 ORDER BY created_at DESC LIMIT $2",
                ns_id, limit,
            )
    return [dict(r) for r in rows]


@router.patch("/api/stats/query-log/{log_id}/resolve", status_code=200)
async def resolve_query_log(log_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT ql.namespace_id, n.name AS namespace, ql.question, ql.answer, ql.mapped_term, ql.message_id
            FROM ops_query_log ql
            JOIN ops_namespace n ON ql.namespace_id = n.id
            WHERE ql.id = $1 AND ql.status = 'pending'
            """, log_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Query log not found or not pending")
        await check_namespace_ownership(row["namespace"], user)
        if not row["answer"]:
            raise HTTPException(status_code=400, detail="답변이 없어 지식으로 등록할 수 없습니다.")

        vec = await embedding_service.embed(row["answer"])
        await conn.execute(
            """INSERT INTO ops_knowledge
               (namespace_id, container_name, content, embedding, created_by_part, created_by_user_id)
               VALUES ($1, $2, $3, $4::vector, $5, $6)""",
            row["namespace_id"], row["mapped_term"] or "미분류", row["answer"], str(vec),
            user["part"], user["id"],
        )
        await conn.execute("UPDATE ops_query_log SET status = 'resolved' WHERE id = $1", log_id)
        await _insert_feedback_if_message_exists(conn, row["namespace_id"], row["question"], row["message_id"])
    return {"status": "ok"}


@router.patch("/api/stats/query-log/{log_id}/mark-resolved", status_code=200)
async def mark_query_log_resolved(log_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT ql.namespace_id, n.name AS namespace, ql.question, ql.message_id
            FROM ops_query_log ql
            JOIN ops_namespace n ON ql.namespace_id = n.id
            WHERE ql.id = $1
            """, log_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Query log not found")
        await conn.execute("UPDATE ops_query_log SET status = 'resolved' WHERE id = $1", log_id)
        await _insert_feedback_if_message_exists(conn, row["namespace_id"], row["question"], row["message_id"])
    return {"status": "ok"}


@router.delete("/api/stats/query-log/{log_id}", status_code=204)
async def delete_query_log(log_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT n.name AS namespace
            FROM ops_query_log ql
            JOIN ops_namespace n ON ql.namespace_id = n.id
            WHERE ql.id = $1
            """, log_id,
        )
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
        rows = await conn.fetch(
            """
            SELECT DISTINCT n.name AS namespace
            FROM ops_query_log ql
            JOIN ops_namespace n ON ql.namespace_id = n.id
            WHERE ql.id = ANY($1::int[])
            """, ids,
        )
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
async def update_llm_config(body: LLMConfigUpdate, user: dict = Depends(get_current_user)):
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


@router.get("/api/llm/search-defaults")
async def get_search_defaults_config(user: dict = Depends(get_current_user)):
    return get_search_defaults()


@router.put("/api/llm/search-defaults")
async def update_search_defaults_config(body: SearchDefaultsUpdate, admin: dict = Depends(get_current_admin)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if "default_top_k" in updates and not 1 <= updates["default_top_k"] <= 20:
        raise HTTPException(status_code=400, detail="default_top_k는 1~20 범위여야 합니다.")
    for k in ("default_w_vector", "default_w_keyword"):
        if k in updates and not 0.0 <= updates[k] <= 1.0:
            raise HTTPException(status_code=400, detail=f"{k}는 0~1 범위여야 합니다.")
    return set_search_defaults(updates)


# ── Semantic Cache ────────────────────────────────────────────────────────────

class CacheDeleteEntryRequest(BaseModel):
    key: str


class CacheConfigRequest(BaseModel):
    enabled: bool | None = None
    similarity_threshold: float | None = None
    cache_ttl: int | None = None


@router.get("/api/admin/cache/config")
async def get_cache_config(admin: dict = Depends(get_current_admin)):
    return {
        "enabled": sem_cache.is_cache_enabled(),
        "similarity_threshold": sem_cache.get_similarity_threshold(),
        "cache_ttl": sem_cache.get_cache_ttl(),
    }


@router.put("/api/admin/cache/config")
async def update_cache_config(body: CacheConfigRequest, admin: dict = Depends(get_current_admin)):
    if body.enabled is not None:
        sem_cache.set_cache_enabled(body.enabled)
    if body.similarity_threshold is not None:
        sem_cache.set_similarity_threshold(body.similarity_threshold)
    if body.cache_ttl is not None:
        sem_cache.set_cache_ttl(body.cache_ttl)
    # DB 영속화
    async with get_conn() as conn:
        await sem_cache.save_config_to_db(
            conn,
            enabled=body.enabled,
            similarity_threshold=sem_cache.get_similarity_threshold() if body.similarity_threshold is not None else None,
            cache_ttl=sem_cache.get_cache_ttl() if body.cache_ttl is not None else None,
        )
    return {
        "enabled": sem_cache.is_cache_enabled(),
        "similarity_threshold": sem_cache.get_similarity_threshold(),
        "cache_ttl": sem_cache.get_cache_ttl(),
    }


@router.get("/api/admin/cache/stats")
async def get_cache_stats(
    namespace: str = QueryParam(...),
    admin: dict = Depends(get_current_admin),
):
    return await sem_cache.get_stats(namespace)


@router.get("/api/admin/cache/entries")
async def get_cache_entries(
    namespace: str = QueryParam(...),
    admin: dict = Depends(get_current_admin),
):
    return await sem_cache.get_entries(namespace)


@router.delete("/api/admin/cache")
async def invalidate_cache(
    namespace: str = QueryParam(...),
    admin: dict = Depends(get_current_admin),
):
    deleted = await sem_cache.invalidate_namespace(namespace)
    return {"deleted": deleted}


@router.delete("/api/admin/cache/entry")
async def delete_cache_entry(
    body: CacheDeleteEntryRequest,
    admin: dict = Depends(get_current_admin),
):
    ok = await sem_cache.delete_entry(body.key)
    return {"deleted": ok}


# ── Glossary AI 용어 추천 ──────────────────────────────────────────────

class GlossarySuggestApplyRequest(BaseModel):
    namespace: str
    term: str
    description: str


@router.post("/api/admin/glossary/suggest")
async def suggest_glossary_terms(
    namespace: str = QueryParam(...),
    limit: int = QueryParam(default=50, ge=5, le=200),
    admin: dict = Depends(get_current_admin),
):
    """미매핑 질문을 분석해 업무 용어를 LLM으로 추천."""
    import json
    import re

    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise HTTPException(status_code=404, detail="네임스페이스를 찾을 수 없습니다.")
        rows = await conn.fetch(
            "SELECT question FROM ops_query_log WHERE namespace_id = $1 AND mapped_term IS NULL ORDER BY created_at DESC LIMIT $2",
            ns_id, limit,
        )

    questions = [r["question"] for r in rows]
    if len(questions) < 3:
        return {"suggestions": [], "message": "분석할 미매핑 질문이 부족합니다 (최소 3건 필요)"}

    # 보안 필터 회피: 8자리+ 숫자 마스킹 + 질문당 80자 초과 자르기 (LLM 입력 크기 제한)
    sanitized = [re.sub(r"\d{8,}", "[ID]", q)[:80] for q in questions]

    _GLOSSARY_SUGGEST_SYS_FALLBACK = "당신은 업무 용어를 추출하는 전문가입니다. 답변은 반드시 JSON 형식으로만 출력하세요."
    system_prompt = await load_prompt("glossary_suggest", _GLOSSARY_SUGGEST_SYS_FALLBACK)
    question = (
        f"다음 질문 목록에서 자주 등장하거나 중요한 업무 용어를 최대 10개 추출해주세요.\n\n"
        f"질문 목록:\n"
        + "\n".join(f"- {q}" for q in sanitized)
        + '\n\n다음 JSON 형식으로만 답변하세요 (다른 텍스트 없이):\n'
        + '[{"term": "용어명", "description": "이 용어의 업무적 의미와 설명 (2-3문장)"}]'
    )

    from core.security import get_user_api_key
    api_key = get_user_api_key(admin)

    try:
        answer, _ = await get_llm_provider().generate(
            context="",
            question=question,
            system_prompt=system_prompt,
            api_key=api_key,
        )
        # 마크다운 코드 블록 제거
        cleaned = re.sub(r"```(?:json)?\s*", "", answer).replace("```", "").strip()
        if not cleaned:
            suggestions = []
        else:
            suggestions = json.loads(cleaned)
            if not isinstance(suggestions, list):
                suggestions = []
    except Exception:
        suggestions = []

    return {"suggestions": suggestions, "message": f"{len(questions)}건의 질문에서 용어 추출 완료"}


@router.post("/api/admin/glossary/suggest/apply")
async def apply_glossary_suggestion(
    body: GlossarySuggestApplyRequest,
    admin: dict = Depends(get_current_admin),
):
    """AI 추천 용어를 용어집에 등록."""
    from domain.knowledge import service as knowledge_service

    try:
        result = await knowledge_service.create_glossary(
            body.namespace, body.term, body.description,
            created_by_part=admin.get("part"),
            created_by_user_id=admin.get("id"),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result
