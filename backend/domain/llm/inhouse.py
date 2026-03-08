"""사내 LLM Provider (OpenAI 호환 API) — per-user api_key 지원."""
import json
from typing import AsyncIterator, Optional

import httpx

from core.config import settings
from domain.llm.base import LLMProvider, build_messages


class InHouseLLMProvider(LLMProvider):

    def __init__(self, runtime_cfg: dict | None = None):
        cfg = runtime_cfg or {}
        url = cfg.get("inhouse_llm_url", settings.inhouse_llm_url)
        if not url:
            raise ValueError(
                "LLM_PROVIDER=inhouse 이지만 INHOUSE_LLM_URL 이 설정되지 않았습니다."
            )
        self._url = url.rstrip("/")
        self._model = cfg.get("inhouse_llm_model", settings.inhouse_llm_model)
        self._system_api_key = cfg.get("inhouse_llm_api_key", settings.inhouse_llm_api_key)
        self._timeout = cfg.get("inhouse_llm_timeout", settings.inhouse_llm_timeout)

    def _build_headers(self, api_key: Optional[str] = None) -> dict:
        """per-user api_key가 있으면 우선 사용, 없으면 시스템 키 사용."""
        key = api_key or self._system_api_key
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def generate(self, context: str, question: str, history: list[dict] | None = None, *, api_key: Optional[str] = None) -> str:
        payload = {
            "model": self._model,
            "messages": build_messages(context, question, history),
            "stream": False,
        }
        headers = self._build_headers(api_key)
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            resp = await client.post(f"{self._url}/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def generate_stream(self, context: str, question: str, history: list[dict] | None = None, *, api_key: Optional[str] = None) -> AsyncIterator[str]:
        payload = {
            "model": self._model,
            "messages": build_messages(context, question, history),
            "stream": True,
        }
        headers = self._build_headers(api_key)
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            async with client.stream(
                "POST", f"{self._url}/chat/completions", json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        chunk_str = line[6:]
                        if chunk_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(chunk_str)
                            token = chunk["choices"][0]["delta"].get("content", "")
                            if token:
                                yield token
                        except (json.JSONDecodeError, KeyError):
                            continue

    async def health_check(self) -> bool:
        try:
            headers = self._build_headers()
            async with httpx.AsyncClient(timeout=5.0, headers=headers) as client:
                resp = await client.get(f"{self._url}/models")
                return resp.status_code == 200
        except Exception:
            return False
