"""HTTP Tool 에이전트 — 외부 API 호출을 LLM 컨텍스트에 통합."""
import asyncio
import json
import logging
from typing import AsyncIterator, Optional

import httpx

from agents.base import AgentBase
from core.database import get_conn, resolve_namespace_id
from service.chat.helpers import (
    LLM_UNAVAILABLE_MSG,
    results_to_json, results_to_payload,
    update_assistant_message, create_query_log, post_save_tasks,
)
from agents.knowledge_rag.knowledge import retrieval
from service.llm.base import resolve_system_prompt
from service.llm.factory import get_llm_provider
from service.prompt.loader import get_prompt as load_prompt
from shared.embedding import embedding_service

logger = logging.getLogger(__name__)


# ── LLM 프롬프트: 도구 선택 + 파라미터 추출 ─────────────────────────────────

_TOOL_SELECT_SYSTEM = """\
HTTP API 도구 선택 AI. 사용자 질문을 분석해 도구를 선택하고 파라미터를 추출한다.

규칙:
1. 파라미터 값은 사용자 메시지에서 명시된 값만 추출. 언급 없으면 missing_params에 등록.
2. example 값은 입력 힌트일 뿐 — 사용자가 말하지 않은 경우 절대 기본값으로 채우지 말 것.
3. 도구 설명이 질문 의도와 명확히 맞을 때만 선택. 불확실하면 no_tool 반환.
4. 반드시 순수 JSON만 출력. 마크다운·설명 없이."""

_TOOL_SELECT_PROMPT = """\
## 사용 가능한 도구 목록
{tool_list}

## 규칙
1. 질문에 맞는 도구가 있으면 선택하고, 질문에서 파라미터 값을 추출하세요.
2. 필수(required=true) 파라미터 중 값을 알 수 없으면 missing_params에 넣으세요.
3. 도구가 필요 없으면 "no_tool"을 반환하세요.

반드시 아래 JSON만 출력하세요:
- 도구 선택 시: {{"tool_id": 숫자, "tool_name": "이름", "params": {{"key": "value"}}, "missing_params": ["누락된 필수 파라미터명"]}}
- 도구 불필요 시: {{"tool_id": null, "reason": "이유"}}

사용자 질문: {question}"""


async def _select_tool(question: str, tools: list[dict], *, api_key: str | None = None) -> dict:
    """LLM에게 도구 선택 + 파라미터 추출을 요청."""
    tool_descriptions = []
    for t in tools:
        params_desc = []
        for p in (t.get("param_schema") or []):
            req = "필수" if p.get("required") else "선택"
            ex = f' (예: {p["example"]})' if p.get("example") else ""
            params_desc.append(f'    - {p["name"]} ({req}, {p.get("type","string")}): {p.get("description","")}{ex}')
        tool_descriptions.append(
            f'- ID: {t["id"]}, 이름: {t["name"]}\n'
            f'  설명: {t["description"]}\n'
            f'  Method: {t["method"]} {t["url"]}\n'
            f'  파라미터:\n' + "\n".join(params_desc)
        )

    prompt = _TOOL_SELECT_PROMPT.format(
        tool_list="\n\n".join(tool_descriptions),
        question=question,
    )
    tool_select_prompt = await load_prompt("tool_select", _TOOL_SELECT_SYSTEM)
    answer, _ = await get_llm_provider().generate(
        context="", question=prompt, api_key=api_key,
        system_prompt=tool_select_prompt,
    )
    text = answer.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


async def _fetch_active_tools(namespace: str) -> list[dict]:
    """네임스페이스의 활성 도구 목록 조회."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT id, name, description, method, url, headers,
                   param_schema, response_example, timeout_sec, max_response_kb
            FROM ops_http_tool
            WHERE namespace_id = $1 AND is_active = true
            ORDER BY name
            """,
            ns_id,
        )
    tools = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("param_schema"), str):
            d["param_schema"] = json.loads(d["param_schema"])
        if isinstance(d.get("headers"), str):
            d["headers"] = json.loads(d["headers"])
        tools.append(d)
    return tools


def _coerce_params(params: dict, schema: list[dict]) -> dict:
    """param_schema 타입에 맞게 파라미터 값 변환 (string→number/boolean/array)."""
    schema_map = {p["name"]: p for p in schema}
    result = {}
    for key, value in params.items():
        param_type = schema_map.get(key, {}).get("type", "string")
        if param_type == "number":
            try:
                result[key] = int(value) if str(value).strip().lstrip("-").isdigit() else float(value)
            except (ValueError, TypeError):
                result[key] = value
        elif param_type == "boolean":
            result[key] = str(value).lower() in ("true", "1", "yes")
        elif param_type == "array":
            try:
                parsed = json.loads(value) if isinstance(value, str) else value
                result[key] = parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                result[key] = []
        else:
            result[key] = value
    return result


async def _execute_http_call(tool: dict, params: dict) -> tuple[str, str | None]:
    """실제 HTTP 호출 실행. 반환: (response_body, error_message | None)"""
    method = tool["method"].upper()
    url = tool["url"]
    headers = tool.get("headers") or {}
    timeout = tool.get("timeout_sec", 10)
    max_kb = tool.get("max_response_kb", 50)

    # param_schema 타입에 맞게 변환
    schema = tool.get("param_schema") or []
    params = _coerce_params(params, schema)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                resp = await client.get(url, params=params, headers=headers)
            else:
                resp = await client.request(method, url, json=params, headers=headers)
        logger.warning("[HTTP_TOOL] %s %s | params=%s | headers_keys=%s | status=%s", method, resp.url, params, list(headers.keys()), resp.status_code)
        logger.warning("[HTTP_TOOL] response body: %s", resp.text[:300])
        resp.raise_for_status()

        body = resp.text
        max_bytes = max_kb * 1024
        if len(body.encode("utf-8")) > max_bytes:
            body = body.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
            body += "\n... (응답이 잘렸습니다)"
        return body, None

    except httpx.TimeoutException:
        return "", f"HTTP 호출 타임아웃 ({tool['timeout_sec']}초 초과)"
    except httpx.HTTPStatusError as e:
        return "", f"HTTP 오류 {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:
        return "", f"HTTP 호출 실패: {e}"


async def _build_rag_context(
    namespace: str, query: str,
    w_vector: float, w_keyword: float, top_k: int,
    category: str | None,
) -> tuple[str, list, str | None]:
    """벡터DB 검색. 반환: (context_str, results, mapped_term). 실패 시 ("", [], None)."""
    try:
        query_vec = await embedding_service.embed(query)
        glossary_match, results = await asyncio.gather(
            retrieval.map_glossary_term(namespace, query_vec),
            retrieval.search_knowledge(namespace, query_vec, query, w_vector, w_keyword, top_k, category),
        )
        mapped_term = glossary_match.term if glossary_match else None
        return retrieval.build_context(results), results, mapped_term
    except Exception as e:
        logger.warning("RAG 검색 실패 (무시됨): %s", e)
        return "", [], None


# ── LLM 프롬프트: HTTP 응답 기반 최종 답변 ───────────────────────────────────

_ANSWER_SYSTEM = """\
실시간 API 데이터와 내부 지식베이스를 통합하여 사용자 질문에 답변하는 AI.

답변 원칙:
- API 데이터: 현재 상태·실시간 값의 1차 근거. 빈 배열·null은 "조회 결과 없음"으로 해석.
- 내부 지식베이스: 코드 정의·업무 규칙·배경 지식. API 응답에 코드값(예: "W", "40", "01")이 있으면 지식베이스에서 해당 정의를 찾아 함께 설명.
- 두 소스를 통합해 완성도 높게 답변. API가 비어있어도 지식베이스로 답변 가능하면 답변.
- 어느 소스에도 없는 내용은 생성하지 마세요.
- Markdown 형식, 한국어 답변."""

_ANSWER_WITH_DATA_PROMPT = """\
## 실시간 API 데이터
- 도구: {tool_name} ({method} {url})
- 파라미터: {params}
- 응답:
{response_data}
{rag_section}
## 사용자 질문
{question}"""


def _build_rag_section(rag_context: str) -> str:
    if not rag_context.strip():
        return ""
    return f"\n## 내부 지식베이스\n{rag_context}\n"


class HttpToolAgent(AgentBase):

    @property
    def agent_id(self) -> str:
        return "http_tool"

    @property
    def metadata(self) -> dict:
        return {
            "display_name": "HTTP 도구",
            "description": "외부 API 연동으로 실시간 데이터 조회",
            "icon": "Globe",
            "color": "emerald",
            "output_type": "text",
            "welcome_message": "외부 API 도구를 활용하여 질문에 답변합니다.",
            "supports_debug": False,
        }

    async def stream_chat(
        self,
        query: str,
        user: dict,
        conversation_id: int,
        context: dict,
    ) -> AsyncIterator[dict]:
        namespace: str = context["namespace"]
        msg_id: int = context["msg_id"]
        api_key: str | None = context.get("api_key")
        approved_tool: Optional[dict] = context.get("approved_tool")
        selected_tool_id: Optional[int] = context.get("selected_tool_id")
        w_vector: float = context.get("w_vector", 0.7)
        w_keyword: float = context.get("w_keyword", 0.3)
        top_k: int = context.get("top_k", 5)
        category: str | None = context.get("category")

        full_answer = ""

        try:
            # ── Case 1: 승인된 도구 → HTTP 호출 실행 ──────────────────────────
            if approved_tool:
                yield {"type": "status", "step": "tool_load", "message": "도구 정보 로드 중..."}

                tool_id = approved_tool["tool_id"]
                params = approved_tool["params"]

                async with get_conn() as conn:
                    tool_row = await conn.fetchrow(
                        "SELECT * FROM ops_http_tool WHERE id = $1 AND is_active = true",
                        tool_id,
                    )
                if not tool_row:
                    yield {"type": "tool_error", "message": "도구가 비활성화되었거나 존재하지 않습니다."}
                    await update_assistant_message(msg_id, "[도구를 찾을 수 없습니다.]", "completed")
                    return

                tool = dict(tool_row)
                if isinstance(tool.get("param_schema"), str):
                    tool["param_schema"] = json.loads(tool["param_schema"])
                if isinstance(tool.get("headers"), str):
                    tool["headers"] = json.loads(tool["headers"])

                # HTTP 호출 + RAG 검색 병렬 실행
                (response_data, http_error), (rag_context, rag_results, mapped_term) = await asyncio.gather(
                    _execute_http_call(tool, params),
                    _build_rag_context(namespace, query, w_vector, w_keyword, top_k, category),
                )

                # RAG 결과를 메시지 DB에 저장 + meta SSE 이벤트 (검색 결과 UI 표시)
                async with get_conn() as conn:
                    await conn.execute(
                        "UPDATE ops_message SET mapped_term = $1, results = $2::jsonb WHERE id = $3",
                        mapped_term, results_to_json(rag_results), msg_id,
                    )
                yield {
                    "type": "meta", "conversation_id": conversation_id, "message_id": msg_id,
                    "mapped_term": mapped_term, "results": results_to_payload(rag_results),
                }

                if http_error:
                    yield {"type": "tool_error", "message": f"HTTP 도구 실행 실패: {http_error}"}
                    yield {"type": "status", "step": "llm", "message": "지식베이스 기반으로 답변 중..."}
                    answer_sys = await resolve_system_prompt()  # HTTP 실패 → RAG 전용 프롬프트
                    try:
                        async for token in get_llm_provider().generate_stream(
                            rag_context, query, api_key=api_key,
                            system_prompt=answer_sys,
                        ):
                            full_answer += token
                            yield {"type": "token", "data": token}
                    except Exception as e:
                        logger.warning("LLM 스트리밍 실패: %s", e)
                        full_answer = LLM_UNAVAILABLE_MSG
                        yield {"type": "token", "data": LLM_UNAVAILABLE_MSG}
                    final_answer = full_answer or LLM_UNAVAILABLE_MSG
                    await update_assistant_message(msg_id, final_answer, "completed")
                    await create_query_log(namespace, query, final_answer, False, mapped_term, msg_id)
                    yield {"type": "done", "message_id": msg_id}
                    return

                yield {"type": "status", "step": "http_response", "message": f"HTTP 응답 수신 완료 — {tool['name']}"}
                yield {"type": "tool_result", "data": response_data[:500]}
                yield {"type": "status", "step": "llm", "message": "AI 답변 생성 중..."}

                llm_prompt = _ANSWER_WITH_DATA_PROMPT.format(
                    tool_name=tool["name"],
                    method=tool["method"],
                    url=tool["url"],
                    params=json.dumps(params, ensure_ascii=False),
                    response_data=response_data,
                    rag_section=_build_rag_section(rag_context),
                    question=query,
                )

                answer_sys = await load_prompt("tool_answer", _ANSWER_SYSTEM)

                try:
                    async for token in get_llm_provider().generate_stream(
                        llm_prompt, query, api_key=api_key,
                        system_prompt=answer_sys,
                    ):
                        full_answer += token
                        yield {"type": "token", "data": token}
                except Exception as e:
                    logger.warning("LLM 스트리밍 실패: %s", e)
                    full_answer = LLM_UNAVAILABLE_MSG
                    yield {"type": "token", "data": LLM_UNAVAILABLE_MSG}

                final_answer = full_answer or LLM_UNAVAILABLE_MSG
                await update_assistant_message(msg_id, final_answer, "completed")
                await create_query_log(namespace, query, final_answer, True, mapped_term, msg_id)
                yield {"type": "done", "message_id": msg_id}
                return

            # ── Case 2: 사용자가 도구 직접 선택 → 항상 파라미터 입력 폼 표시 ──────
            if selected_tool_id:
                yield {"type": "status", "step": "tool_load", "message": "선택된 도구 로드 중..."}

                all_tools = await _fetch_active_tools(namespace)
                selected_tool = next((t for t in all_tools if t["id"] == selected_tool_id), None)

                if not selected_tool:
                    yield {"type": "tool_error", "message": "선택된 도구를 찾을 수 없습니다."}
                    await update_assistant_message(msg_id, "[선택된 도구를 찾을 수 없습니다.]", "completed")
                    return

                # 사용자가 직접 선택한 경우 — LLM 추출 없이 항상 필수 파라미터 입력 폼 표시
                param_schema = selected_tool.get("param_schema") or []
                required_params = [p["name"] for p in param_schema if p.get("required")]
                tool_summary = [{"id": t["id"], "name": t["name"], "description": t["description"]} for t in all_tools]

                if required_params:
                    yield {
                        "type": "tool_request",
                        "action": "missing_params",
                        "tool_id": selected_tool_id,
                        "tool_name": selected_tool["name"],
                        "tool_url": f'{selected_tool["method"]} {selected_tool["url"]}',
                        "params": {},
                        "missing_params": required_params,
                        "param_schema": param_schema,
                        "tools": tool_summary,
                    }
                    await update_assistant_message(msg_id, "[추가 정보 입력 대기 중]", "completed")
                else:
                    # 필수 파라미터 없으면 바로 확인 카드
                    yield {
                        "type": "tool_request",
                        "action": "confirm",
                        "tool_id": selected_tool_id,
                        "tool_name": selected_tool["name"],
                        "tool_url": f'{selected_tool["method"]} {selected_tool["url"]}',
                        "params": {},
                        "param_schema": param_schema,
                        "tools": tool_summary,
                    }
                    await update_assistant_message(msg_id, "[도구 실행 승인 대기 중]", "completed")
                return

            # ── Case 3: 첫 진입 → LLM 도구 선택 ─────────────────────────────
            yield {"type": "status", "step": "tool_fetch", "message": "활성 도구 목록 조회 중..."}

            tools = await _fetch_active_tools(namespace)
            if not tools:
                yield {"type": "tool_request", "action": "no_tools", "message": "등록된 활성 도구가 없습니다."}
                await update_assistant_message(msg_id, "[등록된 활성 도구가 없습니다.]", "completed")
                return

            yield {"type": "status", "step": "tool_select", "message": f"LLM 도구 선택 중... ({len(tools)}개 도구 분석)"}

            try:
                selection = await _select_tool(query, tools, api_key=api_key)
            except Exception as e:
                logger.warning("도구 선택 LLM 실패: %s", e)
                yield {"type": "tool_error", "message": "도구 선택에 실패했습니다. 다시 시도해주세요."}
                await update_assistant_message(msg_id, "[도구 선택 실패]", "completed")
                return

            yield {"type": "status", "step": "tool_params", "message": f"파라미터 검증 중... → {selection.get('tool_name', '도구')}"}

            if selection.get("tool_id") is None:
                reason = selection.get("reason", "이 질문에는 HTTP 도구가 필요하지 않습니다.")
                yield {
                    "type": "tool_request",
                    "action": "no_tool_needed",
                    "message": reason,
                    "tools": [
                        {"id": t["id"], "name": t["name"], "description": t["description"]}
                        for t in tools
                    ],
                }
                await update_assistant_message(msg_id, "[도구 선택 대기 중]", "completed")
                return

            missing = selection.get("missing_params", [])
            tool_summary = [{"id": t["id"], "name": t["name"], "description": t["description"]} for t in tools]
            selected_tool = next((t for t in tools if t["id"] == selection["tool_id"]), None)

            if not selected_tool:
                yield {"type": "tool_error", "message": "선택된 도구를 찾을 수 없습니다."}
                await update_assistant_message(msg_id, "[선택된 도구를 찾을 수 없습니다.]", "completed")
                return

            if missing:
                yield {
                    "type": "tool_request",
                    "action": "missing_params",
                    "tool_id": selection["tool_id"],
                    "tool_name": selection["tool_name"],
                    "tool_url": f'{selected_tool["method"]} {selected_tool["url"]}',
                    "params": selection.get("params", {}),
                    "missing_params": missing,
                    "param_schema": selected_tool.get("param_schema", []),
                    "tools": tool_summary,
                }
                await update_assistant_message(msg_id, "[추가 정보 입력 대기 중]", "completed")
            else:
                yield {
                    "type": "tool_request",
                    "action": "confirm",
                    "tool_id": selection["tool_id"],
                    "tool_name": selection["tool_name"],
                    "tool_url": f'{selected_tool["method"]} {selected_tool["url"]}',
                    "params": selection.get("params", {}),
                    "param_schema": selected_tool.get("param_schema", []),
                    "tools": tool_summary,
                }
                await update_assistant_message(msg_id, "[도구 실행 승인 대기 중]", "completed")

        except Exception as e:
            logger.error("HttpToolAgent 에러: %s", e, exc_info=True)
            if not full_answer:
                full_answer = LLM_UNAVAILABLE_MSG
            await update_assistant_message(msg_id, full_answer, "completed")
