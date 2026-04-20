import { apiFetch } from './client';

// ─── Types ──────────────────────────────────────────────────────────────────

export interface TeamsAuthStatus {
  authenticated: boolean;
  chat_count: number;
  token_expired: boolean | null;
}

export interface TeamsChat {
  id: string;
  label: string;
  members: string;
  has_custom_title: boolean;
  is_one_on_one: boolean;
}

export interface TeamsMessageReplyInfo {
  type: 'reply' | 'forward';
  from: string;
  preview: string;
}

export interface TeamsMessage {
  id: string;
  from: string;
  content: string;
  time: string; // ISO 8601 (Teams composetime)
  date: string; // "YYYY-MM-DD HH:MM"
  reply_to?: TeamsMessageReplyInfo;
}

export interface TeamsMessagesResult {
  messages: TeamsMessage[];
  count: number;
  has_more: boolean;
}

export interface TeamsThread {
  title: string;
  messages: TeamsMessage[];
}

export interface TeamsImportResult {
  created: number;
  job_id: number | null;
  threads: number;
  chunks: number;
  source_name: string;
  source_type: string;
}

// ─── Auth ───────────────────────────────────────────────────────────────────
// 토큰 캡처는 사용자 PC에서 scripts/teams_desktop_login.py 실행으로 이뤄진다.
// 서버는 헬퍼가 POST한 토큰을 수신/저장만 하며, 여기서는 상태 조회/로그아웃만 제공한다.

export async function getTeamsAuthStatus(): Promise<TeamsAuthStatus> {
  return apiFetch<TeamsAuthStatus>('/teams-collect/auth/status');
}

export async function logoutTeams(): Promise<{ status: string }> {
  return apiFetch('/teams-collect/auth/logout', { method: 'POST' });
}

// ─── Helper ZIP 다운로드 ───────────────────────────────────────────────────
// apiFetch 는 JSON 응답용이므로 바이너리 다운로드는 fetch 로 직접 처리한다.
// 인증은 현재 JWT 를 Authorization 헤더로 전달.

export async function downloadHelperExe(accessToken: string | null): Promise<void> {
  const resp = await fetch('/api/teams-collect/helper/download', {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch { /* binary or no body */ }
    throw new Error(detail);
  }
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'OpsNavHelper.exe';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ─── Chats & Messages ───────────────────────────────────────────────────────

export async function listTeamsChats(): Promise<{ chats: TeamsChat[] }> {
  return apiFetch('/teams-collect/chats');
}

export async function fetchTeamsMessages(
  chatId: string,
  opts?: { pageSize?: number; before?: string },
): Promise<TeamsMessagesResult> {
  return apiFetch('/teams-collect/messages', {
    method: 'POST',
    body: JSON.stringify({
      chat_id: chatId,
      page_size: opts?.pageSize ?? 50,
      before: opts?.before ?? '',
    }),
  });
}

// ─── Knowledge Import ───────────────────────────────────────────────────────

export async function importTeamsThreads(
  namespace: string,
  docs: TeamsThread[],
  opts?: { chatId?: string; chatLabel?: string; chunkStrategy?: string; category?: string },
): Promise<TeamsImportResult> {
  return apiFetch('/knowledge/import/teams', {
    method: 'POST',
    body: JSON.stringify({
      namespace,
      chat_id: opts?.chatId ?? null,
      chat_label: opts?.chatLabel ?? null,
      docs,
      chunk_strategy: opts?.chunkStrategy ?? 'auto',
      category: opts?.category ?? null,
    }),
  });
}
