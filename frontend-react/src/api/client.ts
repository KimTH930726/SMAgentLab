import type { SSEEvent } from '../types';
import { useAuthStore } from '../store/useAuthStore';

const BASE_URL = '/api';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** 서버가 구조화된 detail(예: 정책서 업로드의 {message, problems})을 줬을 때 원본 그대로 — 화면이 표로 보여 줌 */
    public detail?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** 서버에 아예 닿지 못함(서버 재시작·네트워크 끊김) — 브라우저 원문 "Failed to fetch" 대신 한국어 안내(10/21, 사용자가 본 그대로 노출됐다) */
export const NETWORK_ERROR_MSG = '서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.';

async function fetchOrNetworkError(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;   // 사용자가 멈춘 것은 그대로
    console.warn('네트워크 오류', url, err);
    throw new ApiError(0, NETWORK_ERROR_MSG);
  }
}

/** 요청 본문이 nginx 업로드 한도(client_max_body_size)를 넘음 — nginx가 HTML 413을 줘 화면에 "HTTP 413"만 보이던 것(10/21 리뷰) */
export const TOO_LARGE_MSG = '파일이 너무 커요 — 더 작은 파일로 나눠서 다시 시도해 주세요.';

/** 오류 본문이 JSON이 아닐 때의 기본 문구 — 서버 재시작 중엔 연결 실패가 아니라 nginx가 HTML 502·504를 준다(10/21 리뷰).
 *  서버 자신의 502·503은 JSON detail이 있어 그 문구가 이 기본값을 덮어쓴다. */
function gatewayDownMessage(status: number): string {
  if (status === 413) return TOO_LARGE_MSG;
  return status === 502 || status === 503 || status === 504 ? NETWORK_ERROR_MSG : `HTTP ${status}`;
}

function getAuthHeaders(): Record<string, string> {
  const token = useAuthStore.getState().accessToken;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function tryRefreshToken(): Promise<boolean> {
  const { refreshToken, setAccessToken, logout } = useAuthStore.getState();
  if (!refreshToken) return false;

  try {
    const resp = await fetch(`${BASE_URL}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!resp.ok) {
      // 갱신 토큰이 거절된 것(401/403)만 로그인 만료 — 서버 재시작 중 nginx 502·504는 로그인을 유지(10/21 리뷰)
      if (resp.status === 401 || resp.status === 403) logout();
      return false;
    }
    const data = await resp.json();
    setAccessToken(data.access_token);
    return true;
  } catch (err) {
    // 네트워크가 잠깐 끊긴 것(서버 재시작 등)은 로그인 만료가 아니다 — 로그아웃하지 않고 다음 요청에서 다시 갱신(10/21 리뷰)
    console.warn('토큰 갱신 요청 실패(네트워크) — 로그인은 유지', err);
    return false;
  }
}

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const url = `${BASE_URL}${path}`;

  const doFetch = async (authHeaders: Record<string, string>) => {
    const { headers: optHeaders, ...restOptions } = options ?? {};
    // FormData 전송 시 Content-Type을 생략 (브라우저가 boundary 자동 설정)
    const isFormData = restOptions.body instanceof FormData;
    const baseHeaders: Record<string, string> = isFormData ? {} : { 'Content-Type': 'application/json' };
    return fetchOrNetworkError(url, {
      ...restOptions,
      headers: {
        ...baseHeaders,
        ...authHeaders,
        ...(optHeaders as Record<string, string>),
      },
    });
  };

  let response = await doFetch(getAuthHeaders());

  // 401 → try refresh once
  if (response.status === 401 && !path.startsWith('/auth/login') && !path.startsWith('/auth/register')) {
    const refreshed = await tryRefreshToken();
    if (refreshed) {
      response = await doFetch(getAuthHeaders());
    }
  }

  if (!response.ok) {
    let message = gatewayDownMessage(response.status);
    let rawDetail: unknown;
    try {
      const body = await response.json();
      const detail = body.detail ?? body.message;
      rawDetail = detail;
      if (Array.isArray(detail)) {
        message = detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join('; ');
      } else if (detail && typeof detail === 'object' && 'message' in detail) {
        message = String((detail as { message: unknown }).message);
      } else if (detail) {
        message = String(detail);
      }
    } catch {
      // ignore parse error
    }
    throw new ApiError(response.status, message, rawDetail);
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export async function* streamSSE(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const url = `${BASE_URL}${path}`;

  const doFetch = (authHeaders: Record<string, string>) =>
    fetchOrNetworkError(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders },
      body: JSON.stringify(body),
      signal,
    });

  let response = await doFetch(getAuthHeaders());

  // 401 → try refresh once (apiFetch와 동일)
  if (response.status === 401) {
    const refreshed = await tryRefreshToken();
    if (refreshed) {
      response = await doFetch(getAuthHeaders());
    }
  }

  if (!response.ok) {
    let message = gatewayDownMessage(response.status);
    try {
      const errBody = await response.json();
      const detail = errBody.detail ?? errBody.message;
      if (Array.isArray(detail)) {
        message = detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join('; ');
      } else if (detail) {
        message = String(detail);
      }
    } catch {
      // ignore
    }
    throw new ApiError(response.status, message);
  }

  if (!response.body) {
    throw new Error('Response body is null');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith('data:')) continue;
        const jsonStr = trimmed.slice(5).trim();
        if (!jsonStr || jsonStr === '[DONE]') continue;
        try {
          const event = JSON.parse(jsonStr) as SSEEvent;
          yield event;
        } catch {
          // skip malformed events
        }
      }
    }

    // Process any remaining buffer
    if (buffer.trim()) {
      const trimmed = buffer.trim();
      if (trimmed.startsWith('data:')) {
        const jsonStr = trimmed.slice(5).trim();
        if (jsonStr && jsonStr !== '[DONE]') {
          try {
            const event = JSON.parse(jsonStr) as SSEEvent;
            yield event;
          } catch {
            // skip
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

export async function healthCheck(): Promise<boolean> {
  try {
    const resp = await fetch('/health', { signal: AbortSignal.timeout(3000) });
    return resp.ok;
  } catch {
    return false;
  }
}
