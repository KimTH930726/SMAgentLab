"""사내 LLM Provider (DevX MCP API) — per-user api_key 지원."""
import json
import logging
from typing import AsyncIterator, Callable, Optional

import httpx

from core.config import settings
from service.llm.base import LLMProvider, _FALLBACK_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _build_query(
    context: str, question: str, history: list[dict] | None = None,
    *, system_prompt: str | None = None,
) -> str:
    """시스템 프롬프트 + 컨텍스트 + 대화 이력 + 질문을 단일 query 문자열로 합친다.
    system_prompt를 지정하면 기본 시스템 프롬프트 대신 사용한다.
    """
    sp = system_prompt if system_prompt is not None else _FALLBACK_SYSTEM_PROMPT
    parts = [sp]
    if context:
        parts.append(f"\n[참고 문서]\n{context}")
    if history:
        for msg in history:
            role = "사용자" if msg["role"] == "user" else "어시스턴트"
            parts.append(f"\n[{role}] {msg['content']}")
    parts.append(f"\n[사용자] {question}")
    return "\n".join(parts)


def _extract_answer(data: dict) -> str:
    """응답 JSON에서 answer를 추출한다. SDK extractAnswer() 우선순위 준수."""
    # 1) external_response.dify_response.answer
    ext = data.get("external_response")
    if isinstance(ext, dict):
        dify = ext.get("dify_response")
        if isinstance(dify, dict) and dify.get("answer"):
            return dify["answer"]
    # 2) message
    if data.get("message"):
        return data["message"]
    # 3) answer
    if data.get("answer"):
        return data["answer"]
    # 4) fallback
    return json.dumps(data, ensure_ascii=False)


def _extract_session(data: dict) -> tuple[Optional[str], Optional[str]]:
    """응답 JSON에서 (conversation_id, project_id)를 추출한다. 루트에 위치."""
    return data.get("conversation_id") or None, data.get("project_id") or None


class InHouseLLMProvider(LLMProvider):

    def __init__(self, runtime_cfg: dict | None = None):
        cfg = runtime_cfg or {}
        url = cfg.get("inhouse_llm_url", settings.inhouse_llm_url)
        if not url:
            raise ValueError(
                "LLM_PROVIDER=inhouse 이지만 INHOUSE_LLM_URL 이 설정되지 않았습니다."
            )
        self._url = url.rstrip("/")
        self._usecase_code = cfg.get("inhouse_llm_agent_code", settings.inhouse_llm_agent_code)
        self._usecase_id = cfg.get("inhouse_llm_usecase_id", settings.inhouse_llm_usecase_id) or None
        self._project_id = cfg.get("inhouse_llm_project_id", settings.inhouse_llm_project_id) or None
        self._model = cfg.get("inhouse_llm_model", settings.inhouse_llm_model) or None
        self._response_mode = cfg.get("inhouse_llm_response_mode", settings.inhouse_llm_response_mode)
        self._system_api_key = cfg.get("inhouse_llm_api_key", settings.inhouse_llm_api_key)
        self._timeout = cfg.get("inhouse_llm_timeout", settings.inhouse_llm_timeout)

    def _build_headers(self, api_key: Optional[str] = None) -> dict:
        """per-user api_key 우선 사용, 없으면 시스템 키 fallback (health_check 등)."""
        key = api_key or self._system_api_key
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _build_payload(
        self, query: str, response_mode: str,
        ext_conversation_id: Optional[str] = None,
    ) -> dict:
        payload: dict = {
            "usecase_code": self._usecase_code,
            "query": query,
            "response_mode": response_mode,
        }
        if self._model:
            payload["inputs"] = {"model": self._model}
        if self._usecase_id:
            payload["usecase_id"] = self._usecase_id
        if self._project_id:
            payload["project_id"] = self._project_id
        if ext_conversation_id:
            payload["conversation_id"] = ext_conversation_id
        return payload

    async def generate_once(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2000,
        api_key: Optional[str] = None,
    ) -> str:
        """파이프라인 스테이지용 단순 단일 응답."""
        query = f"{system}\n\n{prompt}" if system else prompt
        payload = self._build_payload(query, response_mode="blocking")
        headers = self._build_headers(api_key)
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            resp = await client.post(self._url, json=payload)
            resp.raise_for_status()
            return _extract_answer(resp.json())

    async def generate(
        self,
        context: str,
        question: str,
        history: list[dict] | None = None,
        *,
        api_key: Optional[str] = None,
        ext_conversation_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> tuple[str, Optional[str]]:
        query = _build_query(context, question, history, system_prompt=system_prompt)
        payload = self._build_payload(query, response_mode="blocking", ext_conversation_id=ext_conversation_id)
        headers = self._build_headers(api_key)
        logger.info(
            "generate(blocking) → POST %s (query=%d chars, conv_id=%s)",
            self._url, len(query), ext_conversation_id,
        )
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            resp = await client.post(self._url, json=payload)
            logger.info("generate ← status=%d, body=%s", resp.status_code, resp.text[:200])
            resp.raise_for_status()
            data = resp.json()
            answer = _extract_answer(data)
            new_conv_id, _ = _extract_session(data)
            return answer, new_conv_id

    async def generate_stream(
        self,
        context: str,
        question: str,
        history: list[dict] | None = None,
        *,
        api_key: Optional[str] = None,
        ext_conversation_id: Optional[str] = None,
        on_ext_conversation_id: Optional[Callable[[str], None]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[str]:
        query = _build_query(context, question, history, system_prompt=system_prompt)
        use_streaming = self._response_mode == "streaming"
        payload = self._build_payload(query, response_mode=self._response_mode, ext_conversation_id=ext_conversation_id)
        headers = self._build_headers(api_key)

        if not use_streaming:
            # blocking 모드: 전체 응답을 한번에 받아서 yield
            async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
                resp = await client.post(self._url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                new_ext_conv_id, _ = _extract_session(data)
                if new_ext_conv_id and on_ext_conversation_id:
                    on_ext_conversation_id(new_ext_conv_id)
                yield _extract_answer(data)
            return

        # streaming 모드: SSE 파싱
        # DevX MCP API 형식: event 필드가 별도 행이 아닌 data JSON 안에 포함
        # data: {"event":"message","answer":"토큰","conversation_id":"..."}
        # data: {"event":"message_end","conversation_id":"..."}
        logger.info(
            "generate_stream(streaming) → POST %s (query=%d chars, ext_conv_id=%s)",
            self._url, len(query), ext_conversation_id,
        )
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            async with client.stream("POST", self._url, json=payload) as resp:
                logger.info("generate_stream ← status=%d", resp.status_code)
                resp.raise_for_status()
                line_count = 0
                captured_ext_conv_id: Optional[str] = None
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    line_count += 1
                    try:
                        chunk = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        logger.warning("SSE parse error: %s", line[:100])
                        continue
                    event_type = chunk.get("event", "")
                    # conversation_id를 처음 수신했을 때 포착 (message 또는 message_end)
                    if not captured_ext_conv_id:
                        cid = chunk.get("conversation_id") or None
                        if cid:
                            captured_ext_conv_id = cid
                    if event_type == "message_end":
                        logger.info("SSE message_end (total data lines=%d, ext_conv_id=%s)", line_count, captured_ext_conv_id)
                        if captured_ext_conv_id and on_ext_conversation_id:
                            on_ext_conversation_id(captured_ext_conv_id)
                        break
                    if event_type == "message":
                        token = chunk.get("answer", "")
                        if token:
                            yield token

    async def health_check(self) -> bool:
        """서버 도달 가능 여부 확인. 인증 없이 401이 와도 서버는 살아있는 것."""
        try:
            headers = self._build_headers()
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                resp = await client.post(
                    self._url,
                    json=self._build_payload("health check", response_mode="blocking"),
                )
                logger.info("health_check ← status=%d", resp.status_code)
                # 200=정상, 401/403=서버 도달 가능(인증만 없음)
                return resp.status_code in (200, 401, 403)
        except Exception as e:
            logger.warning("health_check 실패: %s: %s", type(e).__name__, e)
            return False
