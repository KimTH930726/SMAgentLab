"""OpenAI 호환 LLM Provider — /v1/chat/completions (vLLM·로컬 Qwen 등). 2026-10-20.

용도: dev에서 사내 게이트웨이 대신 폐쇄망 로컬 LLM으로 E2E·확인을 돌린다(게이트웨이 토큰 0, 컨플루언스 원문이 밖으로 안 나감).
운영 LLM 전환용이 아니다 — OPENAI_COMPAT_BASE_URL이 설정된 환경에서만 고를 수 있다(admin/router.py). 사용량(usage)도 남긴다(11/09) —
처음엔 게이트웨이 비용 화면용이라 뺐지만, 운영 LLM(Bedrock) 기준으로 기능별 사용량을 재려면 개발 중 로컬 호출도 같은 표에 있어야 한다(provider로 구분).
Qwen3 계열은 생각 과정(<think>…</think>)을 내보낼 수 있어 끄고(enable_thinking=False), 혹시 섞여 나오면 걸러낸다.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from typing import AsyncIterator, Callable, Optional

import httpx

from core.config import settings
from service.llm import usage
from service.llm.base import CHAT_MAX_OUTPUT_TOKENS, LLMProvider, build_messages

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


def _log_usage(*, method: str, module: str, messages: list[dict], output_chars: int, u: Optional[dict], t0: float,
               ok: Optional[bool]) -> None:
    """사용량 기록 — 어떤 이유로도 LLM 호출을 깨뜨리지 않는다(inhouse와 같음). 토큰은 서버 응답 usage(OpenAI 규격)가 있을 때만."""
    try:
        u = u or {}
        it, ot = u.get("prompt_tokens"), u.get("completion_tokens")
        usage.record(provider="openai_compat", method=method, module=module,
                     input_chars=sum(len(m.get("content") or "") for m in messages), output_chars=output_chars,
                     input_tokens=it if isinstance(it, int) else None, output_tokens=ot if isinstance(ot, int) else None,
                     duration_ms=int((time.monotonic() - t0) * 1000), ok=ok)
    except Exception:
        logger.warning("LLM 사용량 기록 실패(호출에는 영향 없음)", exc_info=True)


def _caller() -> str:
    try:
        name = usage.caller_module()
        return name if isinstance(name, str) else ""
    except Exception:
        return ""


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

    async def _complete(self, messages: list[dict], max_tokens: Optional[int], method: str, *, cap_fallback: bool = False) -> str:
        module, t0, data, answer, ok = _caller(), time.monotonic(), None, "", False
        try:
            async with httpx.AsyncClient(timeout=_timeout()) as client:
                model = await self._model_id(client)
                r = await client.post(f"{self._base_url}/chat/completions", json=self._body(model, messages, max_tokens, False))
                if r.status_code == 400 and cap_fallback:
                    # 채팅 출력 상한(11/09)이 서버 문맥 한도와 겹치면(입력 + 상한 > max_model_len) vLLM이 400 — 상한 없이 한 번 더(리뷰 L3)
                    logger.warning("로컬 LLM 400 — 채팅 출력 상한 없이 다시 요청(입력이 서버 문맥 한도에 가까움)")
                    r = await client.post(f"{self._base_url}/chat/completions", json=self._body(model, messages, None, False))
                r.raise_for_status()
                data = r.json()
                answer = _THINK_RE.sub("", data["choices"][0]["message"]["content"] or "").strip()
                ok = True
                return answer
        except asyncio.CancelledError:
            ok = None   # 사용자 중단 — 실패로 세지 않는다
            raise
        finally:
            _log_usage(method=method, module=module, messages=messages, output_chars=len(answer),
                       u=data.get("usage") if isinstance(data, dict) else None, t0=t0, ok=ok)

    async def generate_once(self, prompt: str, system: str = "", max_tokens: int = 2000,
                            user_credentials: Optional[dict] = None) -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        return await self._complete(messages, max_tokens, "once")

    async def generate(self, context: str, question: str, history: list[dict] | None = None, *,
                       ext_conversation_id: Optional[str] = None, system_prompt: Optional[str] = None,
                       user_credentials: Optional[dict] = None) -> tuple[str, Optional[str]]:
        return await self._complete(build_messages(context, question, history, system_prompt=system_prompt),
                                    CHAT_MAX_OUTPUT_TOKENS, "generate", cap_fallback=True), None

    async def generate_stream(self, context: str, question: str, history: list[dict] | None = None, *,
                              ext_conversation_id: Optional[str] = None,
                              on_ext_conversation_id: Optional[Callable[[str], None]] = None,
                              system_prompt: Optional[str] = None,
                              user_credentials: Optional[dict] = None) -> AsyncIterator[str]:
        messages = build_messages(context, question, history, system_prompt=system_prompt)
        think = ThinkFilter()
        module, t0, out_chars, end_usage, ok = _caller(), time.monotonic(), 0, None, False
        try:
            async with httpx.AsyncClient(timeout=_timeout()) as client:
                model = await self._model_id(client)
                body = self._body(model, messages, CHAT_MAX_OUTPUT_TOKENS, True)
                body["stream_options"] = {"include_usage": True}   # 마지막 조각에 토큰 수(OpenAI 규격, vLLM 지원)
                async with contextlib.AsyncExitStack() as stack:
                    resp = await stack.enter_async_context(client.stream("POST", f"{self._base_url}/chat/completions", json=body))
                    if resp.status_code == 400:
                        # 보조 칸(출력 상한·usage 요청)을 모르는 서버이거나 입력 + 상한이 문맥 한도를 넘음 — 그 칸 없이 한 번 더(리뷰 L3·L4).
                        # 사용량 기록 같은 보조 기능 때문에 본 답이 깨지지 않게. 토큰 수는 못 받을 수 있다(글자 수만 기록)
                        logger.warning("로컬 LLM 스트림 400 — 출력 상한·usage 요청 없이 다시 요청")
                        await resp.aclose()
                        resp = await stack.enter_async_context(
                            client.stream("POST", f"{self._base_url}/chat/completions", json=self._body(model, messages, None, True)))
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
                        if payload.get("usage"):
                            end_usage = payload["usage"]
                        choices = payload.get("choices") or []
                        token = ((choices[0].get("delta") or {}).get("content") or "") if choices else ""
                        token = think.feed(token)
                        if token:
                            out_chars += len(token)
                            yield token
            tail = think.flush()
            if tail:
                out_chars += len(tail)
                yield tail
            ok = True
        except (GeneratorExit, asyncio.CancelledError):
            ok = None   # 소비자가 닫음(사용자 중단·연결 끊김) — 실패로 세지 않는다
            raise
        finally:
            _log_usage(method="stream", module=module, messages=messages, output_chars=out_chars, u=end_usage, t0=t0, ok=ok)

    @property
    def model(self) -> str:
        """지금 쓰는 모델(비워 두면 연결 확인·첫 호출 때 서버의 첫 모델로 채워짐) — 설정 화면 표시용."""
        return self._model

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self._base_url}/models")
                if r.status_code != 200:
                    return False
                if not self._model:   # 같은 응답으로 모델을 미리 알아 둔다(화면에 실제 모델이 보이게, 요청 추가 없음)
                    data = r.json().get("data") or []
                    self._model = data[0]["id"] if data else ""
                return True
        except Exception as e:   # noqa: BLE001 — 연결 확인은 실패를 False로(원인은 로그)
            logger.warning("[로컬 LLM] 연결 확인 실패 %s: %s", self._base_url, type(e).__name__)
            return False
