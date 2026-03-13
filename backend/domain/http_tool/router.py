"""HTTP 도구 CRUD + LLM 자동완성 라우터."""
import json
import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException

from core.database import get_conn, resolve_namespace_id
from core.dependencies import get_current_user, check_namespace_ownership
from core.security import get_user_api_key
from domain.http_tool.schemas import (
    HttpToolCreate, HttpToolUpdate, HttpToolOut, HttpToolToggle, AutoCompleteRequest,
    HttpToolTestRequest,
)
from domain.llm.factory import get_llm_provider
from domain.prompt.loader import get_prompt as load_prompt

logger = logging.getLogger(__name__)
router = APIRouter(tags=["http-tools"])


# ── helpers ──────────────────────────────────────────────────────────────────

def _row_to_out(row) -> dict:
    """DB row → HttpToolOut 호환 dict. JSONB 컬럼이 문자열로 올 수 있으므로 파싱."""
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
    return d


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("/api/http-tools", response_model=list[HttpToolOut])
async def list_http_tools(namespace: str, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT ht.*, n.name AS namespace
            FROM ops_http_tool ht
            JOIN ops_namespace n ON ht.namespace_id = n.id
            WHERE ht.namespace_id = $1
            ORDER BY ht.created_at DESC
            """,
            ns_id,
        )
    return [_row_to_out(r) for r in rows]


@router.post("/api/http-tools", response_model=HttpToolOut, status_code=201)
async def create_http_tool(body: HttpToolCreate, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(body.namespace, user)
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, body.namespace)
        if ns_id is None:
            raise HTTPException(status_code=404, detail="네임스페이스를 찾을 수 없습니다.")
        row = await conn.fetchrow(
            """
            INSERT INTO ops_http_tool
                (namespace_id, name, description, method, url, headers,
                 param_schema, response_example, timeout_sec, max_response_kb, created_by_user_id)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9,$10,$11)
            RETURNING *, $12::text AS namespace
            """,
            ns_id, body.name, body.description, body.method, body.url,
            json.dumps(body.headers), json.dumps([p.model_dump() for p in body.param_schema]),
            json.dumps(body.response_example) if body.response_example else None,
            body.timeout_sec, body.max_response_kb, user["id"], body.namespace,
        )
    return _row_to_out(row)


@router.patch("/api/http-tools/{tool_id}", response_model=HttpToolOut)
async def update_http_tool(tool_id: int, body: HttpToolUpdate, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT ht.*, n.name AS namespace FROM ops_http_tool ht JOIN ops_namespace n ON ht.namespace_id = n.id WHERE ht.id = $1",
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
            UPDATE ops_http_tool SET {set_clauses}
            WHERE id = $1
            RETURNING *, (SELECT name FROM ops_namespace WHERE id = namespace_id) AS namespace
            """,
            tool_id, *vals,
        )
    return _row_to_out(row)


@router.patch("/api/http-tools/{tool_id}/toggle", response_model=HttpToolOut)
async def toggle_http_tool(tool_id: int, body: HttpToolToggle, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT n.name AS namespace FROM ops_http_tool ht JOIN ops_namespace n ON ht.namespace_id = n.id WHERE ht.id = $1",
            tool_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="도구를 찾을 수 없습니다.")
        await check_namespace_ownership(existing["namespace"], user)

        row = await conn.fetchrow(
            """
            UPDATE ops_http_tool SET is_active = $2, updated_at = now()
            WHERE id = $1
            RETURNING *, (SELECT name FROM ops_namespace WHERE id = namespace_id) AS namespace
            """,
            tool_id, body.is_active,
        )
    return _row_to_out(row)


@router.delete("/api/http-tools/{tool_id}", status_code=204)
async def delete_http_tool(tool_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT n.name AS namespace FROM ops_http_tool ht JOIN ops_namespace n ON ht.namespace_id = n.id WHERE ht.id = $1",
            tool_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="도구를 찾을 수 없습니다.")
        await check_namespace_ownership(existing["namespace"], user)
        await conn.execute("DELETE FROM ops_http_tool WHERE id = $1", tool_id)


# ── 테스트 호출 ──────────────────────────────────────────────────────────────

@router.post("/api/http-tools/{tool_id}/test")
async def test_http_tool(tool_id: int, body: HttpToolTestRequest, user: dict = Depends(get_current_user)):
    """HTTP 도구를 실제로 호출하고 요청/응답 상세를 반환."""
    async with get_conn() as conn:
        row = await conn.fetchrow("SELECT * FROM ops_http_tool WHERE id = $1", tool_id)
    if not row:
        raise HTTPException(status_code=404, detail="도구를 찾을 수 없습니다.")

    tool = dict(row)
    method = tool["method"].upper()
    url = tool["url"]
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
  "url": "API URL",
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


@router.post("/api/http-tools/autocomplete")
async def autocomplete_http_tool(body: AutoCompleteRequest, user: dict = Depends(get_current_user)):
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
