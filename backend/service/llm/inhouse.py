"""사내 LLM Provider (DevX Gateway) — OAuth2 Client Credentials + 하이브리드 자격증명.

인증 흐름:
  1) POST {base_url}/api/v1/auth/token  (form: grant_type=client_credentials)
  2) POST {base_url}/api/v1/agent/chat   (Bearer access_token)

자격증명 정책 (하이브리드):
  - 호출 시 user_credentials 전달되면 사용자 자격증명으로 토큰 발급
  - 없으면 시스템 .env 자격증명으로 토큰 발급
  - 토큰은 client_id 단위로 분리 캐싱 (TokenEntry per client_id)
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Optional

import httpx

from core.config import settings
from service.llm.base import LLMProvider, _FALLBACK_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _build_query(
    context: str, question: str, history: list[dict] | None = None,
    *, system_prompt: str | None = None,
) -> str:
    """시스템 프롬프트 + 컨텍스트 + 대화 이력 + 질문을 단일 query 문자열로 합친다."""
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
    """응답 JSON에서 answer를 추출. 게이트웨이는 `answer` 또는 `message` 필드로 반환."""
    return data.get("answer") or data.get("message") or json.dumps(data, ensure_ascii=False)


@dataclass
class _TokenEntry:
    """client_id 단위 토큰 캐시 엔트리."""
    token: Optional[str] = None
    expires_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class InHouseLLMProvider(LLMProvider):
    """DevX Gateway 기반 사내 LLM Provider — 시스템/사용자 자격증명 하이브리드."""

    def __init__(self, runtime_cfg: dict | None = None):
        cfg = runtime_cfg or {}
        base_url = cfg.get("inhouse_llm_base_url", settings.inhouse_llm_base_url)
        if not base_url:
            raise ValueError(
                "LLM_PROVIDER=inhouse 이지만 INHOUSE_LLM_BASE_URL 이 설정되지 않았습니다."
            )
        self._base_url = base_url.rstrip("/")
        self._token_url = f"{self._base_url}/api/v1/auth/token"
        self._chat_url = f"{self._base_url}/api/v1/agent/chat"

        # 시스템 .env 자격증명 (fallback용)
        self._sys_client_id = cfg.get("inhouse_llm_client_id", settings.inhouse_llm_client_id)
        self._sys_client_secret = cfg.get("inhouse_llm_client_secret", settings.inhouse_llm_client_secret)

        self._agent_code = cfg.get("inhouse_llm_agent_code", settings.inhouse_llm_agent_code)
        self._agent_id = cfg.get("inhouse_llm_agent_id", settings.inhouse_llm_agent_id) or None
        self._model = cfg.get("inhouse_llm_model", settings.inhouse_llm_model) or None
        self._fixed_conversation_id = (
            cfg.get("inhouse_llm_conversation_id", settings.inhouse_llm_conversation_id) or ""
        )
        self._response_mode = cfg.get("inhouse_llm_response_mode", settings.inhouse_llm_response_mode)
        self._timeout = cfg.get("inhouse_llm_timeout", settings.inhouse_llm_timeout)
        self._token_refresh_buffer = cfg.get(
            "inhouse_llm_token_refresh_buffer", settings.inhouse_llm_token_refresh_buffer,
        )

        # client_id 단위 토큰 캐시
        self._token_cache: dict[str, _TokenEntry] = {}
        # 캐시 자체에 대한 락 (엔트리 생성 시 race condition 방지)
        self._cache_lock = asyncio.Lock()

    # ── 자격증명 해석 ─────────────────────────────────────────────

    def _resolve_credentials(self, user_credentials: Optional[dict]) -> tuple[str, str, Optional[str]]:
        """user_credentials 있으면 그것 사용, 없으면 시스템 자격증명.

        Returns: (client_id, client_secret, user_id_for_payload)
        user_id_for_payload는 user_credentials의 user_id (없으면 None — 시스템 호출 시 user_identifier로 fallback)
        """
        if user_credentials:
            cid = user_credentials.get("client_id")
            csec = user_credentials.get("client_secret")
            uid = user_credentials.get("user_id")
            if cid and csec:
                return cid, csec, uid
        return self._sys_client_id, self._sys_client_secret, None

    # ── OAuth 토큰 ────────────────────────────────────────────────

    async def _fetch_access_token(self, client_id: str, client_secret: str) -> str:
        """OAuth 토큰 신규 발급. 발급 후 expires_at 갱신."""
        if not client_id or not client_secret:
            raise ValueError(
                "INHOUSE_LLM_CLIENT_ID / INHOUSE_LLM_CLIENT_SECRET 이 설정되지 않았습니다."
            )
        logger.info("OAuth 토큰 발급 요청 → %s (client_id=%s)", self._token_url, client_id[:12])
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self._token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        token = data.get("access_token")
        if not token:
            raise ValueError(f"토큰 응답에 access_token 없음: {data}")
        expires_in = int(data.get("expires_in", 3600))
        entry = await self._get_or_create_entry(client_id)
        entry.token = token
        entry.expires_at = time.time() + max(expires_in - self._token_refresh_buffer, 30)
        logger.info("OAuth 토큰 발급 완료 (client_id=%s, expires_in=%ds)", client_id[:12], expires_in)
        return token

    async def _get_or_create_entry(self, client_id: str) -> _TokenEntry:
        """client_id 캐시 엔트리를 반환. 없으면 생성."""
        if client_id not in self._token_cache:
            async with self._cache_lock:
                if client_id not in self._token_cache:
                    self._token_cache[client_id] = _TokenEntry()
        return self._token_cache[client_id]

    async def _get_access_token(self, client_id: str, client_secret: str) -> str:
        """캐시된 토큰을 반환. 만료(또는 임박) 시 자동 재발급. 동시 호출 안전."""
        entry = await self._get_or_create_entry(client_id)
        if entry.token and time.time() < entry.expires_at:
            return entry.token
        async with entry.lock:
            # double-check 락 안에서 재확인
            if entry.token and time.time() < entry.expires_at:
                return entry.token
            return await self._fetch_access_token(client_id, client_secret)

    async def _build_headers(
        self,
        *,
        client_id: str,
        client_secret: str,
        accept_sse: bool = False,
    ) -> dict:
        token = await self._get_access_token(client_id, client_secret)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if accept_sse:
            headers["Accept"] = "text/event-stream"
        return headers

    # ── Payload ───────────────────────────────────────────────────

    def _build_payload(
        self,
        query: str,
        *,
        response_mode: str,
        payload_user: str,
        ext_conversation_id: Optional[str],
    ) -> dict:
        # DevX dify는 사전 등록된 conversation_id만 허용 → 우리 시스템의 conv_id를
        # 직접 전달하지 않고 시스템 공통 고정 ID(있다면)를 사용.
        conv_id = ext_conversation_id or self._fixed_conversation_id
        payload: dict = {
            "user": payload_user,
            "query": query,
            "agent_code": self._agent_code,
            "knowledge_ids": [],
            "response_mode": response_mode,
        }
        if self._agent_id:
            payload["agent_id"] = self._agent_id
        if conv_id:
            payload["conversation_id"] = conv_id
        if self._model:
            payload["inputs"] = {"model": self._model}
        return payload

    # ── 공개 API ──────────────────────────────────────────────────

    async def generate_once(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2000,
        user_credentials: Optional[dict] = None,
    ) -> str:
        """파이프라인 스테이지용 단순 단일 응답 (blocking)."""
        query = f"{system}\n\n{prompt}" if system else prompt
        cid, csec, uid = self._resolve_credentials(user_credentials)
        payload = self._build_payload(
            query, response_mode="blocking",
            payload_user=uid or "system", ext_conversation_id=None,
        )
        headers = await self._build_headers(client_id=cid, client_secret=csec)
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            resp = await client.post(self._chat_url, json=payload)
            resp.raise_for_status()
            return _extract_answer(resp.json())

    async def generate(
        self,
        context: str,
        question: str,
        history: list[dict] | None = None,
        *,
        ext_conversation_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
        user_credentials: Optional[dict] = None,
    ) -> tuple[str, Optional[str]]:
        query = _build_query(context, question, history, system_prompt=system_prompt)
        cid, csec, uid = self._resolve_credentials(user_credentials)
        payload = self._build_payload(
            query, response_mode="blocking",
            payload_user=uid or "system", ext_conversation_id=ext_conversation_id,
        )
        headers = await self._build_headers(client_id=cid, client_secret=csec)
        logger.info(
            "generate(blocking) → POST %s (query=%d chars, conv_id=%s, user=%s, system_creds=%s)",
            self._chat_url, len(query), ext_conversation_id, uid or "system",
            user_credentials is None,
        )
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            resp = await client.post(self._chat_url, json=payload)
            logger.info("generate ← status=%d, body=%s", resp.status_code, resp.text[:200])
            resp.raise_for_status()
            data = resp.json()
            answer = _extract_answer(data)
            new_conv_id = data.get("conversation_id")
            return answer, new_conv_id

    async def generate_stream(
        self,
        context: str,
        question: str,
        history: list[dict] | None = None,
        *,
        ext_conversation_id: Optional[str] = None,
        on_ext_conversation_id: Optional[Callable[[str], None]] = None,
        system_prompt: Optional[str] = None,
        user_credentials: Optional[dict] = None,
    ) -> AsyncIterator[str]:
        query = _build_query(context, question, history, system_prompt=system_prompt)
        use_streaming = self._response_mode == "streaming"
        cid, csec, uid = self._resolve_credentials(user_credentials)
        payload = self._build_payload(
            query, response_mode=self._response_mode,
            payload_user=uid or "system", ext_conversation_id=ext_conversation_id,
        )

        if not use_streaming:
            # blocking 모드: 전체 응답을 한번에 받아서 yield
            headers = await self._build_headers(client_id=cid, client_secret=csec)
            async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
                resp = await client.post(self._chat_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                new_ext_conv_id = data.get("conversation_id")
                if new_ext_conv_id and on_ext_conversation_id:
                    on_ext_conversation_id(new_ext_conv_id)
                yield _extract_answer(data)
            return

        # streaming 모드: SSE 파싱
        headers = await self._build_headers(client_id=cid, client_secret=csec, accept_sse=True)
        logger.info(
            "generate_stream(streaming) → POST %s (query=%d chars, ext_conv_id=%s, user=%s, system_creds=%s)",
            self._chat_url, len(query), ext_conversation_id, uid or "system",
            user_credentials is None,
        )
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            async with client.stream("POST", self._chat_url, json=payload) as resp:
                logger.info("generate_stream ← status=%d", resp.status_code)
                resp.raise_for_status()
                line_count = 0
                captured_ext_conv_id: Optional[str] = None
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    line_count += 1
                    raw = line[5:].strip()
                    if raw in ("[DONE]", ""):
                        continue
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("SSE parse error: %s", raw[:100])
                        continue
                    if not captured_ext_conv_id:
                        cidc = chunk.get("conversation_id")
                        if cidc:
                            captured_ext_conv_id = cidc
                    event_type = chunk.get("event", "")
                    if event_type == "message_end":
                        logger.info(
                            "SSE message_end (data_lines=%d, ext_conv_id=%s)",
                            line_count, captured_ext_conv_id,
                        )
                        if captured_ext_conv_id and on_ext_conversation_id:
                            on_ext_conversation_id(captured_ext_conv_id)
                        break
                    if event_type == "message":
                        token = chunk.get("answer", "")
                        if token:
                            yield token

    async def health_check(self) -> bool:
        """시스템 자격증명으로 토큰 발급 시도. 4xx 응답도 게이트웨이 도달 가능 의미로 간주."""
        try:
            await self._fetch_access_token(self._sys_client_id, self._sys_client_secret)
            return True
        except httpx.HTTPStatusError as e:
            logger.warning("health_check 토큰 발급 4xx: %s", e.response.status_code)
            return e.response.status_code in (400, 401, 403)
        except Exception as e:
            logger.warning("health_check 실패: %s: %s", type(e).__name__, e)
            return False
