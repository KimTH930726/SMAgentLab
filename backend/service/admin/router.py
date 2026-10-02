"""관리 도메인 — namespace, stats, LLM 설정 라우터."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam
from pydantic import BaseModel

from core.database import get_conn, resolve_namespace_id
from core.dependencies import get_current_user, get_current_admin, check_namespace_ownership
from service.admin.schemas import (
    NamespaceCreate, NamespaceInfo,
    KnowledgeCategoryCreate, KnowledgeCategoryOut,
    TermStat, NamespaceDetailStats,
    LLMConfigUpdate, LLMTestRequest, ThresholdUpdate, SearchDefaultsUpdate,
)
from service.admin import service
from service.llm.factory import get_llm_provider, switch_provider, get_runtime_config
from service.llm.ollama import OllamaProvider
from service.llm.inhouse import InHouseLLMProvider
from agents.knowledge_rag.knowledge.retrieval import (
    get_thresholds, set_thresholds, get_search_defaults, set_search_defaults, persist_runtime_overrides,
)
from service.prompt.loader import get_prompt as load_prompt
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
    "inhouse_llm_base_url", "inhouse_llm_client_id", "inhouse_llm_client_secret",
    "inhouse_llm_agent_id", "inhouse_llm_agent_code", "inhouse_llm_conversation_id",
    "inhouse_llm_model", "inhouse_llm_response_mode", "inhouse_llm_timeout",
)


def _extract_config(body) -> dict:
    cfg = {"provider": body.provider}
    for field in _CONFIG_FIELDS:
        val = getattr(body, field, None)
        if val is not None:
            cfg[field] = val
    return cfg


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
            FROM rag_knowledge_category kc
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
            "SELECT id FROM rag_knowledge_category WHERE namespace_id = $1 AND name = $2",
            ns_id, body.name.strip(),
        )
        if existing:
            raise HTTPException(status_code=409, detail="이미 존재하는 업무구분입니다.")
        row = await conn.fetchrow(
            """
            INSERT INTO rag_knowledge_category (namespace_id, name) VALUES ($1, $2)
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
            "SELECT id FROM rag_knowledge_category WHERE namespace_id = $1 AND name = $2",
            ns_id, new_name,
        )
        if existing:
            raise HTTPException(status_code=409, detail="이미 존재하는 업무구분입니다.")
        await conn.execute(
            "UPDATE rag_knowledge SET category = $3 WHERE namespace_id = $1 AND category = $2",
            ns_id, cat_name, new_name,
        )
        row = await conn.fetchrow(
            """
            UPDATE rag_knowledge_category SET name = $3
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
            "UPDATE rag_knowledge SET category = NULL WHERE namespace_id = $1 AND category = $2",
            ns_id, cat_name,
        )
        result = await conn.execute(
            "DELETE FROM rag_knowledge_category WHERE namespace_id = $1 AND name = $2",
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

    suggested = await service.suggest_category_for_content(ns_id, content)
    return {"suggested_category": suggested}


# ── Stats ────────────────────────────────────────────────────────────────────

# 질의 상태는 "답변(pending)"과 "지식 공백(no_knowledge)" 둘뿐(2026-10-02, #61) — 좋아요/싫어요 기반 해결·미해결은
# 없앴고, 틀린 답은 정정 요청(개선 원장 pending)으로 센다. 공백을 지식 등록으로 메우면 상태는 그대로, 연결 지식만 채운다.
# {a} = 테이블 별칭 자리(빈 문자열이면 별칭 없음) — 같은 조건을 집계·목록·용어 분포에서 같이 쓴다
_OPEN_GAP = "{a}status = 'no_knowledge' AND {a}resolved_knowledge_id IS NULL"
_FILLED_GAP = "{a}status = 'no_knowledge' AND {a}resolved_knowledge_id IS NOT NULL"
# 목록 필터 — 화면의 카드 하나 = 필터 하나
_QUERY_FILTERS = {"pending": "q.status = 'pending'", "no_knowledge": _OPEN_GAP.format(a="q."),
                  "filled": _FILLED_GAP.format(a="q.")}


@router.get("/api/stats/namespace/{name}", response_model=NamespaceDetailStats)
async def get_namespace_stats(name: str, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, name)
        if ns_id is None:
            raise HTTPException(status_code=404, detail=f"Namespace '{name}' not found")
        summary = await conn.fetchrow(
            f"""
            SELECT COUNT(*) FILTER (WHERE status <> 'system_error') AS total_queries,
                COUNT(*) FILTER (WHERE status = 'pending') AS answered,
                COUNT(*) FILTER (WHERE status = 'system_error') AS system_errors,
                COUNT(*) FILTER (WHERE {_OPEN_GAP.format(a="")}) AS no_knowledge,
                COUNT(*) FILTER (WHERE {_FILLED_GAP.format(a="")}) AS filled
            FROM ops_query_log WHERE namespace_id = $1
            """, ns_id,
        )
        term_rows = await conn.fetch(
            f"""
            SELECT COALESCE(ql.mapped_term, '기타') AS term,
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE {_OPEN_GAP.format(a="ql.")}) AS no_knowledge,
                MAX(g.description) AS description
            FROM ops_query_log ql
            LEFT JOIN rag_glossary g ON g.namespace_id = ql.namespace_id AND g.term = ql.mapped_term
            WHERE ql.namespace_id = $1 AND ql.status <> 'system_error'
            GROUP BY ql.mapped_term ORDER BY total DESC LIMIT 20
            """, ns_id,
        )
        corrections_open = await conn.fetchval(
            "SELECT COUNT(*) FROM ops_improvement_item WHERE namespace_id = $1 AND status = 'pending'", ns_id,
        )

    return NamespaceDetailStats(
        namespace=name,
        total_queries=summary["total_queries"] or 0,
        answered=summary["answered"] or 0,
        no_knowledge=summary["no_knowledge"] or 0,
        filled=summary["filled"] or 0,
        system_errors=summary["system_errors"] or 0,
        corrections_open=corrections_open or 0,
        term_distribution=[TermStat(**dict(r)) for r in term_rows],
    )


@router.get("/api/stats/namespace/{name}/queries")
async def get_namespace_queries(
    name: str,
    status: Optional[str] = None,
    limit: int = QueryParam(default=100, le=500),
    user: dict = Depends(get_current_user),
):
    """status = pending(답변) / no_knowledge(열린 공백) / filled(메운 공백), 없으면 전체.
    메운 공백은 등록된 지식의 최신 내용을 answer로 보여준다(그 지식이 검토 대기거나 이후 반려·병합되면 원래 답변으로 폴백).
    resolved_knowledge_id는 원래 연결 값 그대로 — 집계(메움)와 화면 판단이 어긋나지 않게(검토 대기 지식으로 메운 경우)."""
    if status is not None and status not in _QUERY_FILTERS:
        raise HTTPException(status_code=400, detail="status는 pending / no_knowledge / filled 중 하나")
    # 전체 목록에도 시스템 오류(LLM 연결 실패)는 뺀다 — 질문에 대한 기록이 아니라 장애 기록
    where = "q.namespace_id = $1 AND " + (_QUERY_FILTERS[status] if status else "q.status <> 'system_error'")
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, name)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            f"""
            SELECT q.id, q.question, COALESCE(k.content, q.answer) AS answer,
                   q.mapped_term, q.status, q.created_at::text, q.resolved_at::text,
                   q.resolved_knowledge_id, (k.id IS NOT NULL) AS knowledge_active
            FROM ops_query_log q
            LEFT JOIN rag_knowledge k ON k.id = q.resolved_knowledge_id AND k.status = 'active'
            WHERE {where}
            ORDER BY COALESCE(q.resolved_at, q.created_at) DESC LIMIT $2
            """, ns_id, limit,
        )
    return [dict(r) for r in rows]


class FillGapRequest(BaseModel):
    knowledge_id: int


@router.patch("/api/stats/query-log/{log_id}/fill", status_code=200)
async def fill_knowledge_gap(log_id: int, body: FillGapRequest, user: dict = Depends(get_current_user)):
    """지식 공백 질의를 등록한 지식으로 메웠다고 기록 — 상태는 공백 그대로, 연결 지식·시각만 채운다(메움 실적).
    연결할 지식은 같은 파트의 살아 있는 지식만(없는 id면 FK 오류로 500, 남의 파트 지식이면 엉뚱한 내용이 "메움"으로 보였다)."""
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT n.name AS namespace, ql.namespace_id FROM ops_query_log ql JOIN ops_namespace n ON ql.namespace_id = n.id
            WHERE ql.id = $1 AND ql.status = 'no_knowledge'
            """, log_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="지식 공백 질의가 아닙니다.")
        await check_namespace_ownership(row["namespace"], user)
        same_ns = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM rag_knowledge WHERE id = $1 AND namespace_id = $2 "
            "AND status NOT IN ('deleted', 'rejected', 'deprecated'))", body.knowledge_id, row["namespace_id"])
        if not same_ns:
            raise HTTPException(status_code=400, detail="이 파트에 등록된 지식이 아닙니다.")
        await conn.execute(
            "UPDATE ops_query_log SET resolved_knowledge_id = $2, resolved_at = NOW() WHERE id = $1",
            log_id, body.knowledge_id,
        )
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
        # 삭제 대상의 네임스페이스별 소유 파트를 한 번에 조회 — 네임스페이스마다
        # check_namespace_ownership()을 호출하면 매번 DB 왕복이 생기므로 인라인 검증
        rows = await conn.fetch(
            """
            SELECT DISTINCT n.name AS namespace, n.owner_part_id
            FROM ops_query_log ql
            JOIN ops_namespace n ON ql.namespace_id = n.id
            WHERE ql.id = ANY($1::int[])
            """, ids,
        )
        if user["role"] != "admin":
            for r in rows:
                if r["owner_part_id"] is not None and r["owner_part_id"] != user["part_id"]:
                    raise HTTPException(status_code=403, detail="이 네임스페이스에 대한 권한이 없습니다.")
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
    # switch_provider()는 프로세스 전역 싱글턴을 바꾼다 — 모든 네임스페이스/사용자에게
    # 영향을 주는 시스템 설정이므로 thresholds/search-defaults/cache-config 등
    # 다른 전역 설정 엔드포인트와 동일하게 관리자 전용이어야 한다(기존엔 일반 사용자도
    # 호출 가능해, LLM 엔드포인트·자격증명을 임의로 바꿔치기할 수 있었음)
    if body.provider not in ("ollama", "inhouse"):
        raise HTTPException(status_code=400, detail="provider는 'ollama' 또는 'inhouse'여야 합니다.")
    try:
        new_provider = switch_provider(_extract_config(body))
        is_connected = await new_provider.health_check()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {**get_runtime_config(), "is_connected": is_connected}


@router.post("/api/llm/test")
async def test_llm_connection(body: LLMTestRequest, admin: dict = Depends(get_current_admin)):
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
    await persist_runtime_overrides(updates)  # 저장 성공 후에만 메모리 반영(재시작해도 유지)
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
    await persist_runtime_overrides(updates)  # 저장 성공 후에만 메모리 반영(재시작해도 유지)
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
    source: str = QueryParam(default="questions", pattern="^(questions|knowledge)$"),
    limit: int = QueryParam(default=50, ge=5, le=200),
    admin: dict = Depends(get_current_admin),
):
    """미매핑 질문 또는 등록된 지식을 분석해 업무 용어를 LLM으로 추천 (이미 등록된 용어는 제외)."""
    import json
    import re

    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise HTTPException(status_code=404, detail="네임스페이스를 찾을 수 없습니다.")

        existing_terms = [
            r["term"] for r in await conn.fetch(
                "SELECT term FROM rag_glossary WHERE namespace_id = $1", ns_id,
            )
        ]

        if source == "knowledge":
            rows = await conn.fetch(
                # active만 — 반려·폐기·수집 중(staging, 취소되면 사라질) 행에서 용어를 추천하지 않게
                "SELECT content FROM rag_knowledge WHERE namespace_id = $1 AND status = 'active' ORDER BY created_at DESC LIMIT $2",
                ns_id, limit,
            )
            items = [r["content"] for r in rows]
            item_label, per_item_chars = "지식", 300
        else:
            rows = await conn.fetch(
                "SELECT question FROM ops_query_log WHERE namespace_id = $1 AND mapped_term IS NULL ORDER BY created_at DESC LIMIT $2",
                ns_id, limit,
            )
            items = [r["question"] for r in rows]
            item_label, per_item_chars = "질문", 80

    if len(items) < 3:
        return {"suggestions": [], "message": f"분석할 {item_label}이(가) 부족합니다 (최소 3건 필요)"}

    # 보안 필터 회피: 8자리+ 숫자 마스킹 + 항목당 길이 제한 (LLM 입력 크기 제한)
    sanitized = [re.sub(r"\d{8,}", "[ID]", it)[:per_item_chars] for it in items]
    existing_text = ", ".join(existing_terms[:50]) if existing_terms else "(없음)"

    _GLOSSARY_SUGGEST_SYS_FALLBACK = "당신은 업무 용어를 추출하는 전문가입니다. 답변은 반드시 JSON 형식으로만 출력하세요."
    system_prompt = await load_prompt("glossary_suggest", _GLOSSARY_SUGGEST_SYS_FALLBACK)
    question = (
        f"다음 {item_label} 목록에서 자주 등장하거나 중요한 업무 용어를 최대 10개 추출해주세요.\n\n"
        f"[이미 등록된 용어 (중복 제외)]\n{existing_text}\n\n"
        f"{item_label} 목록:\n"
        + "\n".join(f"- {s}" for s in sanitized)
        + '\n\n다음 JSON 형식으로만 답변하세요 (다른 텍스트 없이):\n'
        + '[{"term": "용어명", "description": "이 용어의 업무적 의미와 설명 (2-3문장)"}]'
    )

    from core.security import get_user_llm_credentials
    user_credentials = get_user_llm_credentials(admin)

    try:
        answer, _ = await get_llm_provider().generate(
            context="",
            question=question,
            system_prompt=system_prompt,
            user_credentials=user_credentials,
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

    # LLM이 프롬프트 지시를 무시하고 기존 용어를 다시 낸 경우를 대비한 후처리 필터
    existing_lower = {t.lower() for t in existing_terms}
    suggestions = [
        s for s in suggestions
        if isinstance(s, dict) and s.get("term", "").strip().lower() not in existing_lower
    ]

    return {"suggestions": suggestions, "message": f"{len(items)}건의 {item_label}에서 용어 추출 완료"}


@router.post("/api/admin/glossary/suggest/apply")
async def apply_glossary_suggestion(
    body: GlossarySuggestApplyRequest,
    admin: dict = Depends(get_current_admin),
):
    """AI 추천 용어를 용어집에 등록."""
    from agents.knowledge_rag.knowledge import service as knowledge_service

    try:
        result = await knowledge_service.create_glossary(
            body.namespace, body.term, body.description,
            created_by_part=admin.get("part"),
            created_by_user_id=admin.get("id"),
        )
    except ValueError as e:
        status = 409 if "이미 등록된" in str(e) else 404
        raise HTTPException(status_code=status, detail=str(e))
    return result
