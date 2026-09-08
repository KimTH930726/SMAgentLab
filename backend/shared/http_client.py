"""아웃바운드 HTTP 호출 — 프로세스 전역 커넥션 재사용 + 응답 크기/시간 제한.

특정 에이전트나 서비스에 속한 기능이 아니라 "외부 시스템에 HTTP로 뭔가를 보낸다"는
순수 인프라 관심사라 core/shared에 둔다. VOC의 Teams 웹훅 발송이 첫 소비자이고,
앞으로 비슷한 아웃바운드 연동이 생기면 여기 재사용하면 된다(구 MCP 도구 에이전트의
_execute_http_call을 그 기능만 떼어내 이관한 것 — 도구 레지스트리·파라미터 스키마
같은 MCP 전용 개념은 가져오지 않았다).
"""
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def call_http(
    method: str,
    url: str,
    *,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: float = 10.0,
    max_response_kb: int = 50,
) -> tuple[str, Optional[str], Optional[int], float, int]:
    """범용 아웃바운드 HTTP 호출.

    반환: (response_body, error_message, status_code, response_kb, duration_ms)
    """
    method = method.upper()
    headers = headers or {}
    start = time.time()
    try:
        client = _get_client()
        if method == "GET":
            resp = await client.get(url, params=params, headers=headers, timeout=timeout)
        else:
            resp = await client.request(method, url, json=json_body, headers=headers, timeout=timeout)
        duration_ms = round((time.time() - start) * 1000)
        logger.debug("[HTTP] %s %s | status=%s", method, resp.url, resp.status_code)
        resp.raise_for_status()
        # raise_for_status()는 4xx/5xx만 걸러낸다 — AsyncClient가 follow_redirects=False라서
        # 3xx(로그인 페이지로 리다이렉트 등)가 예외 없이 여기까지 내려와 "성공"으로 오인될 수 있다.
        if resp.status_code >= 300:
            return "", f"성공 범위(2xx)가 아닌 응답입니다 (HTTP {resp.status_code})", resp.status_code, 0.0, duration_ms

        body = resp.text
        max_bytes = max_response_kb * 1024
        if len(body.encode("utf-8")) > max_bytes:
            body = body.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
            body += "\n... (응답이 잘렸습니다)"
        response_kb = len(resp.content) / 1024
        return body, None, resp.status_code, response_kb, duration_ms

    except httpx.TimeoutException:
        duration_ms = round((time.time() - start) * 1000)
        return "", f"HTTP 호출 타임아웃 ({timeout}초 초과)", None, 0.0, duration_ms
    except httpx.HTTPStatusError as e:
        duration_ms = round((time.time() - start) * 1000)
        return "", f"HTTP 오류 {e.response.status_code}: {e.response.text[:200]}", e.response.status_code, 0.0, duration_ms
    except Exception as e:
        duration_ms = round((time.time() - start) * 1000)
        return "", f"HTTP 호출 실패: {e}", None, 0.0, duration_ms
