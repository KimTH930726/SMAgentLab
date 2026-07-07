import type { SSEEvent } from '../types';
import { useAuthStore } from '../store/useAuthStore';

const BASE_URL = '/api';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
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
      logout();
      return false;
    }
    const data = await resp.json();
    setAccessToken(data.access_token);
    return true;
  } catch {
    logout();
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
    return fetch(url, {
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
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      const detail = body.detail ?? body.message;
      if (Array.isArray(detail)) {
        message = detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join('; ');
      } else if (detail) {
        message = String(detail);
      }
    } catch {
      // ignore parse error
    }
    throw new ApiError(response.status, message);
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

// 바이너리 응답(파일 다운로드)용 — apiFetch와 동일한 401 갱신-재시도 + 에러 상세 파싱을 공유하되 blob을 반환한다.
export async function apiFetchBlob(path: string): Promise<Blob> {
  const url = `${BASE_URL}${path}`;
  const doFetch = (authHeaders: Record<string, string>) => fetch(url, { headers: authHeaders });

  let response = await doFetch(getAuthHeaders());

  if (response.status === 401) {
    const refreshed = await tryRefreshToken();
    if (refreshed) {
      response = await doFetch(getAuthHeaders());
    }
  }

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      const detail = body.detail ?? body.message;
      if (detail) message = String(detail);
    } catch {
      // ignore — binary/empty error body
    }
    throw new ApiError(response.status, message);
  }

  return response.blob();
}

export async function* streamSSE(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const url = `${BASE_URL}${path}`;

  const doFetch = (authHeaders: Record<string, string>) =>
    fetch(url, {
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
    let message = `HTTP ${response.status}`;
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
