"""Ollama LLM Provider — /api/chat 엔드포인트 (api_key, ext_conversation_id 무시)."""
import json
from typing import AsyncIterator, Callable, Optional

import httpx

from core.config import settings
from service.llm.base import LLMProvider, build_messages


def _ollama_timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=10.0,
        read=float(settings.ollama_timeout),
        write=30.0,
        pool=10.0,
    )


class OllamaProvider(LLMProvider):

    def __init__(self, runtime_cfg: dict | None = None):
        cfg = runtime_cfg or {}
        self._base_url = cfg.get("ollama_base_url", settings.ollama_base_url)
        self._model = cfg.get("ollama_model", settings.ollama_model)

    async def generate_once(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2000,
        api_key: str | None = None,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        async with httpx.AsyncClient(timeout=_ollama_timeout()) as client:
            resp = await client.post(
                f"{self._base_url}/api/chat",
                json={"model": self._model, "messages": messages, "stream": False,
                      "options": {"num_predict": max_tokens, "temperature": 0, "num_ctx": 8192}},
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]

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
        messages = build_messages(context, question, history, system_prompt=system_prompt)
        async with httpx.AsyncClient(timeout=_ollama_timeout()) as client:
            resp = await client.post(
                f"{self._base_url}/api/chat",
                json={"model": self._model, "messages": messages, "stream": False},
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"], None

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
        messages = build_messages(context, question, history, system_prompt=system_prompt)
        async with httpx.AsyncClient(timeout=_ollama_timeout()) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json={"model": self._model, "messages": messages, "stream": True},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line:
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                yield token
                            if chunk.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False
