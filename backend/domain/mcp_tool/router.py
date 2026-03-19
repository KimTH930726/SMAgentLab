"""MCP 도구 CRUD + LLM 자동완성 라우터."""
import json
import logging
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException

from core.database import get_conn, resolve_namespace_id
from core.dependencies import get_current_user, check_namespace_ownership
from core.security import get_user_api_key
from domain.mcp_tool.schemas import (
    McpToolCreate, McpToolUpdate, McpToolOut, McpToolToggle, AutoCompleteRequest,
    McpToolTestRequest,
)
from domain.llm.factory import get_llm_provider
from domain.prompt.loader import get_prompt as load_prompt

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mcp-tools"])


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_dt(dt_str: str | None):
    """ISO 8601 문자열 → datetime (timezone aware). asyncpg TIMESTAMPTZ 파라미터용."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def _row_to_out(row) -> dict:
    """DB row → McpToolOut 호환 dict. JSONB 컬럼이 문자열로 올 수 있으므로 파싱."""
    d = dict(row)
    for key in ("param_schema", "headers", "response_example"):
        val = d.get(key)
        if isinstance(val, str):
            try:
                d[key] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    d["param_schema"] = d.get("param_schema") or []
    d["headers"] = d.get("headers") or {}
    d["created_at"] = str(d["created_at"])
    hub_base_url = d.get("hub_base_url") or ""
    tool_path = d.get("tool_path") or ""
    d["url"] = hub_base_url + tool_path
    return d


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("/api/mcp-tools", response_model=list[McpToolOut])
async def list_mcp_tools(namespace: str, agent_type: str = "knowledge_rag", user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT ht.*, n.name AS namespace
            FROM ops_mcp_tool ht
            JOIN ops_namespace n ON ht.namespace_id = n.id
            WHERE ht.namespace_id = $1 AND ht.agent_type = $2
            ORDER BY ht.created_at DESC
            """,
            ns_id, agent_type,
        )
    return [_row_to_out(r) for r in rows]


@router.post("/api/mcp-tools", response_model=McpToolOut, status_code=201)
async def create_mcp_tool(body: McpToolCreate, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(body.namespace, user)
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, body.namespace)
        if ns_id is None:
            raise HTTPException(status_code=404, detail="네임스페이스를 찾을 수 없습니다.")
        row = await conn.fetchrow(
            """
            INSERT INTO ops_mcp_tool
                (namespace_id, name, description, method, hub_base_url, tool_path, headers,
                 param_schema, response_example, timeout_sec, max_response_kb, created_by_user_id, agent_type)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10,$11,$12,$13)
            RETURNING *, $14::text AS namespace
            """,
            ns_id, body.name, body.description, body.method, body.hub_base_url, body.tool_path,
            json.dumps(body.headers), json.dumps([p.model_dump() for p in body.param_schema]),
            json.dumps(body.response_example) if body.response_example else None,
            body.timeout_sec, body.max_response_kb, user["id"], body.agent_type, body.namespace,
        )
    return _row_to_out(row)


@router.patch("/api/mcp-tools/{tool_id}", response_model=McpToolOut)
async def update_mcp_tool(tool_id: int, body: McpToolUpdate, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT ht.*, n.name AS namespace FROM ops_mcp_tool ht JOIN ops_namespace n ON ht.namespace_id = n.id WHERE ht.id = $1",
            tool_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="도구를 찾을 수 없습니다.")
        await check_namespace_ownership(existing["namespace"], user)

        updates = body.model_dump(exclude_none=True)
        if not updates:
            return _row_to_out(existing)

        # param_schema, headers → JSON 직렬화
        if "param_schema" in updates:
            updates["param_schema"] = json.dumps([p.model_dump() if hasattr(p, "model_dump") else p for p in updates["param_schema"]])
        if "headers" in updates:
            updates["headers"] = json.dumps(updates["headers"])
        if "response_example" in updates:
            updates["response_example"] = json.dumps(updates["response_example"])

        set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates.keys()))
        set_clauses += f", updated_at = now()"
        vals = list(updates.values())

        row = await conn.fetchrow(
            f"""
            UPDATE ops_mcp_tool SET {set_clauses}
            WHERE id = $1
            RETURNING *, (SELECT name FROM ops_namespace WHERE id = namespace_id) AS namespace
            """,
            tool_id, *vals,
        )
    return _row_to_out(row)


@router.patch("/api/mcp-tools/{tool_id}/toggle", response_model=McpToolOut)
async def toggle_mcp_tool(tool_id: int, body: McpToolToggle, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT n.name AS namespace FROM ops_mcp_tool ht JOIN ops_namespace n ON ht.namespace_id = n.id WHERE ht.id = $1",
            tool_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="도구를 찾을 수 없습니다.")
        await check_namespace_ownership(existing["namespace"], user)

        row = await conn.fetchrow(
            """
            UPDATE ops_mcp_tool SET is_active = $2, updated_at = now()
            WHERE id = $1
            RETURNING *, (SELECT name FROM ops_namespace WHERE id = namespace_id) AS namespace
            """,
            tool_id, body.is_active,
        )
    return _row_to_out(row)


@router.get("/api/mcp-tools/logs/stats")
async def get_mcp_tool_log_stats(
    namespace: str,
    from_dt: str | None = None,
    to_dt: str | None = None,
    user: dict = Depends(get_current_user),
):
    """도구별 호출 통계 집계 (메인 로그 화면용)."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []

        conditions = ["l.namespace_id = $1"]
        params: list = [ns_id]
        from_dt_val = _parse_dt(from_dt)
        to_dt_val = _parse_dt(to_dt)
        if from_dt_val:
            params.append(from_dt_val)
            conditions.append(f"l.called_at >= ${len(params)}")
        if to_dt_val:
            params.append(to_dt_val)
            conditions.append(f"l.called_at <= ${len(params)}")
        where = " AND ".join(conditions)

        agg_rows = await conn.fetch(
            f"""
            SELECT tool_id, tool_name,
                   COUNT(*)                                                       AS total_calls,
                   COUNT(*) FILTER (WHERE response_status BETWEEN 200 AND 299)   AS success_calls,
                   ROUND(AVG(duration_ms))::int                                  AS avg_duration_ms,
                   MAX(called_at)                                                 AS last_called_at
            FROM ops_mcp_tool_log l
            WHERE {where}
            GROUP BY tool_id, tool_name
            ORDER BY total_calls DESC
            """,
            *params,
        )
        dist_rows = await conn.fetch(
            f"""
            SELECT tool_id,
                   COALESCE(response_status::text, 'error') AS status_key,
                   COUNT(*)                                  AS cnt
            FROM ops_mcp_tool_log l
            WHERE {where}
            GROUP BY tool_id, status_key
            """,
            *params,
        )

    dist_map: dict = {}
    for r in dist_rows:
        dist_map.setdefault(r["tool_id"], {})[r["status_key"]] = r["cnt"]

    return [
        {
            "tool_id": r["tool_id"],
            "tool_name": r["tool_name"],
            "total_calls": r["total_calls"],
            "success_calls": r["success_calls"],
            "avg_duration_ms": r["avg_duration_ms"],
            "last_called_at": str(r["last_called_at"]) if r["last_called_at"] else None,
            "status_dist": dist_map.get(r["tool_id"], {}),
        }
        for r in agg_rows
    ]


@router.get("/api/mcp-tools/logs")
async def list_mcp_tool_logs(
    namespace: str,
    tool_id: int | None = None,
    from_dt: str | None = None,
    to_dt: str | None = None,
    page: int = 1,
    page_size: int = 20,
    user: dict = Depends(get_current_user),
):
    """MCP 도구 호출 감사 로그 페이징 조회."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}

        conditions = ["l.namespace_id = $1"]
        params: list = [ns_id]
        if tool_id is not None:
            params.append(tool_id)
            conditions.append(f"l.tool_id = ${len(params)}")
        from_dt_val = _parse_dt(from_dt)
        to_dt_val = _parse_dt(to_dt)
        if from_dt_val:
            params.append(from_dt_val)
            conditions.append(f"l.called_at >= ${len(params)}")
        if to_dt_val:
            params.append(to_dt_val)
            conditions.append(f"l.called_at <= ${len(params)}")
        where = " AND ".join(conditions)

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM ops_mcp_tool_log l WHERE {where}", *params
        )
        offset = (max(page, 1) - 1) * page_size
        params_pg = params + [page_size, offset]
        rows = await conn.fetch(
            f"""
            SELECT l.*, u.username
            FROM ops_mcp_tool_log l
            LEFT JOIN ops_user u ON l.user_id = u.id
            WHERE {where}
            ORDER BY l.called_at DESC
            LIMIT ${len(params_pg) - 1} OFFSET ${len(params_pg)}
            """,
            *params_pg,
        )

    return {
        "items": [
            {
                "id": r["id"],
                "tool_id": r["tool_id"],
                "tool_name": r["tool_name"],
                "username": r["username"],
                "user_id": r["user_id"],
                "namespace_id": r["namespace_id"],
                "conversation_id": r["conversation_id"],
                "params": (json.loads(r["params"]) if isinstance(r["params"], str) else r["params"]) or {},
                "response_status": r["response_status"],
                "response_kb": r["response_kb"],
                "duration_ms": r["duration_ms"],
                "error": r["error"],
                "called_at": str(r["called_at"]),
                "request_url": r["request_url"],
                "http_method": r["http_method"],
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.delete("/api/mcp-tools/{tool_id}", status_code=204)
async def delete_mcp_tool(tool_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT n.name AS namespace FROM ops_mcp_tool ht JOIN ops_namespace n ON ht.namespace_id = n.id WHERE ht.id = $1",
            tool_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="도구를 찾을 수 없습니다.")
        await check_namespace_ownership(existing["namespace"], user)
        await conn.execute("DELETE FROM ops_mcp_tool WHERE id = $1", tool_id)


# ── 테스트 호출 ──────────────────────────────────────────────────────────────

@router.post("/api/mcp-tools/{tool_id}/test")
async def test_mcp_tool(tool_id: int, body: McpToolTestRequest, user: dict = Depends(get_current_user)):
    """MCP 도구를 실제로 호출하고 요청/응답 상세를 반환."""
    async with get_conn() as conn:
        row = await conn.fetchrow("SELECT * FROM ops_mcp_tool WHERE id = $1", tool_id)
    if not row:
        raise HTTPException(status_code=404, detail="도구를 찾을 수 없습니다.")

    tool = dict(row)
    method = tool["method"].upper()
    hub_base_url = tool.get("hub_base_url") or ""
    tool_path = tool.get("tool_path") or ""
    url = hub_base_url + tool_path
    headers = tool.get("headers") or {}
    if isinstance(headers, str):
        try:
            headers = json.loads(headers)
        except (json.JSONDecodeError, TypeError):
            headers = {}
    timeout = tool.get("timeout_sec", 10)
    max_kb = tool.get("max_response_kb", 50)
    params = body.params

    # 요청 정보 기록
    request_info = {
        "method": method,
        "url": url,
        "headers": headers,
        "params": params,
    }

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                resp = await client.get(url, params=params, headers=headers)
            else:
                resp = await client.request(method, url, json=params, headers=headers)
        elapsed_ms = round((time.time() - start) * 1000)

        resp_body = resp.text
        max_bytes = max_kb * 1024
        truncated = False
        if len(resp_body.encode("utf-8")) > max_bytes:
            resp_body = resp_body.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
            truncated = True

        return {
            "status": "ok",
            "request": request_info,
            "response": {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body": resp_body,
                "truncated": truncated,
                "elapsed_ms": elapsed_ms,
                "size_bytes": len(resp.content),
            },
        }
    except httpx.TimeoutException:
        elapsed_ms = round((time.time() - start) * 1000)
        return {
            "status": "error",
            "request": request_info,
            "error": f"타임아웃 ({timeout}초 초과)",
            "elapsed_ms": elapsed_ms,
        }
    except Exception as e:
        elapsed_ms = round((time.time() - start) * 1000)
        return {
            "status": "error",
            "request": request_info,
            "error": str(e),
            "elapsed_ms": elapsed_ms,
        }


# ── LLM 자동완성 ─────────────────────────────────────────────────────────────

_AUTOCOMPLETE_SYSTEM = """\
당신은 JSON 변환 전문가입니다. 사용자가 자연어로 설명하는 HTTP API 정보를 구조화된 JSON으로 변환합니다.
반드시 JSON만 출력하세요. 설명, 인사말, 마크다운 코드 블록 없이 순수 JSON만 반환합니다."""

_AUTOCOMPLETE_PROMPT = """\
아래 자연어 설명을 읽고 다음 JSON 형식으로 변환해주세요:
{{
  "name": "도구 이름 (한글 가능, 간결하게)",
  "description": "이 도구가 하는 일 (1~2문장)",
  "method": "GET 또는 POST",
  "hub_base_url": "MCP 허브 베이스 URL (예: https://mcp-hub.internal)",
  "tool_path": "도구 경로 (예: /tools/my-tool)",
  "headers": {{"키": "값"}},
  "param_schema": [
    {{
      "name": "파라미터명",
      "type": "string | number | boolean",
      "required": true 또는 false,
      "description": "설명",
      "example": "예시값"
    }}
  ],
  "response_example": {{ "응답 예시 JSON" }}
}}

사용자 입력:
{raw_text}"""


@router.post("/api/mcp-tools/autocomplete")
async def autocomplete_mcp_tool(body: AutoCompleteRequest, user: dict = Depends(get_current_user)):
    prompt = _AUTOCOMPLETE_PROMPT.format(raw_text=body.raw_text)
    api_key = get_user_api_key(user)
    autocomplete_sys = await load_prompt("autocomplete", _AUTOCOMPLETE_SYSTEM)
    try:
        answer, _ = await get_llm_provider().generate(
            context="", question=prompt, api_key=api_key,
            system_prompt=autocomplete_sys,
        )
        # JSON 블록 추출
        text = answer.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        parsed = json.loads(text)
        return {"status": "ok", "tool": parsed}
    except json.JSONDecodeError:
        return {"status": "error", "message": "LLM 응답을 파싱할 수 없습니다. 입력을 더 구체적으로 작성해주세요.", "raw": answer}
    except Exception as e:
        logger.exception("autocomplete error")
        raise HTTPException(status_code=500, detail=f"자동완성 실패: {e}")
