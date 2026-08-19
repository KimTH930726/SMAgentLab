import { apiFetch } from './client';

// ─── §9 폴링 설정 ───────────────────────────────────────────────────────────

export interface EmailCollectionSettings {
  email_collection_enabled: boolean;
  email_polling_interval_minutes: number;
  email_lookback_days: number;
  email_relevance_min_score: number;
}

export async function getEmailCollectionSettings(): Promise<EmailCollectionSettings> {
  return apiFetch<EmailCollectionSettings>('/email-voc/settings');
}

export async function updateEmailCollectionSettings(
  patch: Partial<EmailCollectionSettings>,
): Promise<EmailCollectionSettings> {
  return apiFetch<EmailCollectionSettings>('/email-voc/settings', {
    method: 'PUT',
    body: JSON.stringify(patch),
  });
}

// ─── §10 담당자 라우팅 매핑 ─────────────────────────────────────────────────

export interface VocRouting {
  id: number;
  namespace: string;
  part: string;
  mailbox_upn: string;
  teams_webhook_url: string | null;
  oncall_contact_name: string | null;
  oncall_contact_phone: string | null;
  mail_folder_id: string | null;
  mail_folder_name: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface VocRoutingPayload {
  part: string;
  mailbox_upn: string;
  teams_webhook_url?: string | null;
  oncall_contact_name?: string | null;
  oncall_contact_phone?: string | null;
  mail_folder_id?: string | null;
  mail_folder_name?: string | null;
  is_active?: boolean;
}

export interface MailFolder {
  id: string;
  display_name: string;
  unread_count: number;
  total_count: number;
}

// mailbox_upn의 실제 Outlook 폴더 목록 조회 — 라우팅 폼의 "메일 폴더" 드롭다운용.
// 비워두면(mail_folder_id 없음) 기존과 동일하게 메일함 전체를 조회한다.
export async function getMailFolders(mailboxUpn: string): Promise<MailFolder[]> {
  return apiFetch<MailFolder[]>(`/email-voc/mail-folders?mailbox_upn=${encodeURIComponent(mailboxUpn)}`);
}

export async function listVocRouting(namespace: string): Promise<VocRouting[]> {
  return apiFetch<VocRouting[]>(`/email-voc/routing?namespace=${encodeURIComponent(namespace)}`);
}

export async function createVocRouting(namespace: string, payload: VocRoutingPayload): Promise<VocRouting> {
  return apiFetch<VocRouting>('/email-voc/routing', {
    method: 'POST',
    body: JSON.stringify({ namespace, ...payload }),
  });
}

export async function updateVocRouting(
  id: number, namespace: string, payload: Partial<VocRoutingPayload>,
): Promise<VocRouting> {
  return apiFetch<VocRouting>(`/email-voc/routing/${id}?namespace=${encodeURIComponent(namespace)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function deleteVocRouting(id: number, namespace: string): Promise<void> {
  await apiFetch<void>(`/email-voc/routing/${id}?namespace=${encodeURIComponent(namespace)}`, {
    method: 'DELETE',
  });
}

// ─── 테스트 진입점 (§11 Track A #2, #3) ───────────────────────────────────

export interface EmailAnalysisResult {
  category: string;
  severity: string;
  mismatch_flagged: boolean;
  knowledge_ref_ids: number[];
  resolution_draft: string | null;
  reasoning: string | null;
  mapped_term: string | null;
}

export async function testAnalyzeEmail(
  namespace: string, subject: string, body: string, part?: string,
): Promise<EmailAnalysisResult> {
  return apiFetch<EmailAnalysisResult>('/email-voc/test-analyze', {
    method: 'POST',
    body: JSON.stringify({ namespace, subject, body, part }),
  });
}

export interface TeamsTestNotifyPayload {
  webhook_url: string;
  subject: string;
  sender?: string;
  part?: string;
  category: string;
  severity: string;
  mismatch_flagged?: boolean;
  resolution_draft?: string | null;
  oncall_contact_name?: string | null;
}

export async function testNotifyTeams(payload: TeamsTestNotifyPayload): Promise<{ sent: boolean }> {
  return apiFetch<{ sent: boolean }>('/email-voc/test-notify', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ─── Graph API 자격증명 (§7 Q10 승인 후 관리자가 수기 입력) ───────────────

export interface GraphCredentialsStatus {
  configured: boolean;
  tenant_id: string | null;
  client_id: string | null;
}

export async function getGraphCredentials(): Promise<GraphCredentialsStatus> {
  return apiFetch<GraphCredentialsStatus>('/email-voc/graph-credentials');
}

export async function updateGraphCredentials(payload: {
  tenant_id: string; client_id: string; client_secret: string;
}): Promise<GraphCredentialsStatus> {
  return apiFetch<GraphCredentialsStatus>('/email-voc/graph-credentials', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

// ─── Delegated 권한 로그인 (Application 권한/Track B 승인 전 임시 경로) ───────

export interface DelegatedAuthStatus {
  configured: boolean;
  logged_in: boolean;
  account: string | null;
  pending: boolean;
  login_error: string | null;
  redirect_uri: string | null;
  client_secret_configured: boolean;
}

export interface DelegatedAuthStartResult {
  auth_url: string;
}

export async function getDelegatedAuthStatus(): Promise<DelegatedAuthStatus> {
  return apiFetch<DelegatedAuthStatus>('/email-voc/delegated-auth/status');
}

// client_secret은 선택 입력 — 비우면 Public Client(PKCE만), 채우면 Confidential
// Client로 동작(백엔드 delegated_auth.py 참고). 값은 저장 후 절대 다시 조회되지
// 않으므로(GraphCredentials와 동일 원칙) 폼에도 매번 빈 값으로만 표시해야 한다.
export async function updateDelegatedAuthConfig(payload: {
  tenant_id: string; client_id: string; redirect_uri: string; client_secret?: string;
}): Promise<DelegatedAuthStatus> {
  return apiFetch<DelegatedAuthStatus>('/email-voc/delegated-auth/config', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function startDelegatedAuth(): Promise<DelegatedAuthStartResult> {
  return apiFetch<DelegatedAuthStartResult>('/email-voc/delegated-auth/start', {
    method: 'POST',
  });
}

// ─── 1단계 수동 1회성 실행 (§9, §5 Phase 1) ────────────────────────────────

export interface MailboxCollectionResult {
  mailbox_upn: string;
  part: string;
  ok: boolean;
  error: string | null;
  fetched: number;
  analyzed: number;
  skipped_duplicate: number;
  skipped_low_relevance: number;
  notified: number;
  notify_failed: number;
}

export interface ManualCollectionResult {
  date_from: string;
  date_to: string;
  mailboxes: MailboxCollectionResult[];
}

export async function runManualCollection(
  namespace: string, dateFrom: string, dateTo: string,
): Promise<ManualCollectionResult> {
  return apiFetch<ManualCollectionResult>('/email-voc/collect/run', {
    method: 'POST',
    body: JSON.stringify({ namespace, date_from: dateFrom, date_to: dateTo }),
  });
}

// ─── 이력 조회 ──────────────────────────────────────────────────────────────

export interface EmailAnalysisHistoryItem {
  id: number;
  mailbox_upn: string;
  part: string | null;
  subject: string;
  sender: string;
  received_at: string | null;
  category: string | null;
  severity: string | null;
  mismatch_flagged: boolean;
  knowledge_ref_ids: number[];
  resolution_draft: string | null;
  reasoning: string | null;
  status: string;
  teams_sent_at: string | null;
  notify_error: string | null;
  created_at: string;
}

export interface EmailHistoryFilters {
  severity?: string;
  status?: string;
  mismatchOnly?: boolean;
  keyword?: string;
}

export async function getEmailHistory(
  namespace: string, limit = 50, offset = 0, filters: EmailHistoryFilters = {},
): Promise<EmailAnalysisHistoryItem[]> {
  const params = new URLSearchParams({ namespace, limit: String(limit), offset: String(offset) });
  if (filters.severity) params.set('severity', filters.severity);
  if (filters.status) params.set('status', filters.status);
  if (filters.mismatchOnly) params.set('mismatch_only', 'true');
  if (filters.keyword) params.set('keyword', filters.keyword);
  return apiFetch<EmailAnalysisHistoryItem[]>(`/email-voc/history?${params.toString()}`);
}

// ─── 폴링 실시간 상태 + 사이클 이력 ─────────────────────────────────────────

export interface PollCycleItem {
  id: number;
  started_at: string;
  finished_at: string | null;
  namespaces_processed: number;
  mailboxes_ok: number;
  mailboxes_failed: number;
  total_fetched: number;
  total_analyzed: number;
  total_notified: number;
  total_notify_failed: number;
  total_skipped_duplicate: number;
  total_skipped_low_relevance: number;
  error_summary: string | null;
}

export interface SchedulerStatus {
  enabled: boolean;
  is_running_now: boolean;
  polling_interval_minutes: number;
  last_cycle: PollCycleItem | null;
  next_estimated_at: string | null;
}

export async function getSchedulerStatus(): Promise<SchedulerStatus> {
  return apiFetch<SchedulerStatus>('/email-voc/scheduler-status');
}

export async function getPollCycles(limit = 20, offset = 0): Promise<PollCycleItem[]> {
  return apiFetch<PollCycleItem[]>(`/email-voc/poll-cycles?limit=${limit}&offset=${offset}`);
}
