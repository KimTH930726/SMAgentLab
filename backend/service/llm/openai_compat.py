"""OpenAI 호환 LLM Provider — /v1/chat/completions (vLLM·로컬 Qwen 등). 2026-10-20.

용도: dev에서 사내 게이트웨이 대신 폐쇄망 로컬 LLM으로 E2E·확인을 돌린다(게이트웨이 토큰 0, 컨플루언스 원문이 밖으로 안 나감).
운영 LLM 전환용이 아니다 — OPENAI_COMPAT_BASE_URL이 설정된 환경에서만 고를 수 있다(admin/router.py). 사용량 기록(usage)은
사내 게이트웨이 비용 화면용이라 남기지 않는다(Ollama와 같음).
Qwen3 계열은 생각 과정(<think>…</think>)을 내보낼 수 있어 끄고(enable_thinking=False), 혹시 섞여 나오면 걸러낸다.
"""
from __future__ import annotations

import json
import logging
import re
from typing import AsyncIterator, Callable, Optional

import httpx

from core.config import settings
from service.llm.base import LLMProvider, build_messages

logger = logging.getLogger(__name__)
# 닫힌 생각 구간 + 잘려서(max_tokens) 안 닫힌 생각 구간(끝까지) — 리뷰 10/20 L2
_THINK_RE = re.compile(r"<think>.*?(?:</think>\s*|$)", re.S)
_OPEN, _CLOSE = "<think>", "</think>"


class ThinkFilter:
    """스트림 토큰에서 생각 구간을 걸러낸다 — 태그가 토큰 경계에 걸치거나("<thi"+"nk>") 한 토큰에 열고 닫는 경우도(리뷰 L1).
    태그의 앞부분일 수 있는 꼬리는 다음 토큰까지 들고 있다가 판단한다."""

    def __init__(self) -> None:
        self.buf, self.inside = "", False

    def feed(self, token: str) -> str:
        self.buf += token
        out = []
        while True:
            tag = _CLOSE if self.inside else _OPEN
            i = self.buf.find(tag)
            if i >= 0:
                if not self.inside:
                    out.append(self.buf[:i])
                self.buf, self.inside = self.buf[i + len(tag):], not self.inside
                continue
            keep = next((k for k in range(min(len(tag) - 1, len(self.buf)), 0, -1) if tag.startswith(self.buf[-k:])), 0)
            if not self.inside:
                out.append(self.buf[:len(self.buf) - keep])
            self.buf = self.buf[len(self.buf) - keep:] if keep else ""
            return "".join(out)

    def flush(self) -> str:
        rest, self.buf = ("" if self.inside else self.buf), ""
        return rest


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=10.0, read=float(settings.openai_compat_timeout), write=30.0, pool=10.0)


class OpenAICompatProvider(LLMProvider):

    def __init__(self, runtime_cfg: dict | None = None):
        cfg = runtime_cfg or {}
        self._base_url = (cfg.get("openai_compat_base_url") or settings.openai_compat_base_url or "").rstrip("/")
        if not self._base_url:
            raise ValueError("OPENAI_COMPAT_BASE_URL이 설정되지 않았습니다.")
        self._model = cfg.get("openai_compat_model") or settings.openai_compat_model or ""

    async def _model_id(self, client: httpx.AsyncClient) -> str:
        if not self._model:   # 비우면 서버가 올린 첫 모델(vLLM은 보통 하나)
            r = await client.get(f"{self._base_url}/models")
            r.raise_for_status()
            self._model = r.json()["data"][0]["id"]
        return self._model

    def _body(self, model: str, messages: list[dict], max_tokens: Optional[int], stream: bool) -> dict:
        body = {"model": model, "messages": messages, "temperature": 0, "stream": stream,
                "chat_template_kwargs": {"enable_thinking": False}}
        if max_tokens:
            body["max_tokens"] = max_tokens
        return body

    async def _complete(self, messages: list[dict], max_tokens: Optional[int]) -> str:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            r = await client.post(f"{self._base_url}/chat/completions",
                                  json=self._body(await self._model_id(client), messages, max_tokens, False))
            r.raise_for_status()
            return _THINK_RE.sub("", r.json()["choices"][0]["message"]["content"] or "").strip()

    async def generate_once(self, prompt: str, system: str = "", max_tokens: int = 2000,
                            user_credentials: Optional[dict] = None) -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        return await self._complete(messages, max_tokens)

    async def generate(self, context: str, question: str, history: list[dict] | None = None, *,
                       ext_conversation_id: Optional[str] = None, system_prompt: Optional[str] = None,
                       user_credentials: Optional[dict] = None) -> tuple[str, Optional[str]]:
        return await self._complete(build_messages(context, question, history, system_prompt=system_prompt), None), None

    async def generate_stream(self, context: str, question: str, history: list[dict] | None = None, *,
                              ext_conversation_id: Optional[str] = None,
                              on_ext_conversation_id: Optional[Callable[[str], None]] = None,
                              system_prompt: Optional[str] = None,
                              user_credentials: Optional[dict] = None) -> AsyncIterator[str]:
        messages = build_messages(context, question, history, system_prompt=system_prompt)
        think = ThinkFilter()
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            body = self._body(await self._model_id(client), messages, None, True)
            async with client.stream("POST", f"{self._base_url}/chat/completions", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        logger.warning("로컬 LLM 스트림: JSON 아닌 줄 무시 — %s", data[:200])
                        continue
                    if "error" in payload:   # vLLM은 스트림 중간 오류를 data: {"error": …}로 보낸다 — 삼키지 말고 실패로(리뷰 L3)
                        raise RuntimeError(f"로컬 LLM 오류: {str(payload['error'])[:300]}")
                    choices = payload.get("choices") or []
                    token = ((choices[0].get("delta") or {}).get("content") or "") if choices else ""
                    token = think.feed(token)
                    if token:
                        yield token
        tail = think.flush()
        if tail:
            yield tail

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                return (await client.get(f"{self._base_url}/models")).status_code == 200
        except Exception:
            return False
