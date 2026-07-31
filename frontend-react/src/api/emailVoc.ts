import { apiFetch } from './client';

// ─── §9 폴링 설정 ───────────────────────────────────────────────────────────

export interface EmailCollectionSettings {
  email_collection_enabled: boolean;
  email_polling_interval_minutes: number;
  email_lookback_days: number;
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
  is_active?: boolean;
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

// ─── 1단계 수동 1회성 실행 (§9, §5 Phase 1) ────────────────────────────────

export interface MailboxCollectionResult {
  mailbox_upn: string;
  part: string;
  ok: boolean;
  error: string | null;
  fetched: number;
  analyzed: number;
  skipped_duplicate: number;
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

export async function getEmailHistory(
  namespace: string, limit = 50, offset = 0,
): Promise<EmailAnalysisHistoryItem[]> {
  return apiFetch<EmailAnalysisHistoryItem[]>(
    `/email-voc/history?namespace=${encodeURIComponent(namespace)}&limit=${limit}&offset=${offset}`,
  );
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
