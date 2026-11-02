import { apiFetch, apiFetchBlob } from './client';

/** 사용자 피드백(10/30) — 오류 신고·기능 제안. 답 내용 오류는 정정 요청(api/feedback)으로.
 *  11/02: 첨부 이미지(최대 3장) · 오류 창 문맥(context) · 진행 중 상태 */
export type FeedbackKind = 'bug' | 'idea';
export type FeedbackStatus = 'new' | 'planned' | 'in_progress' | 'done' | 'duplicate' | 'declined';
export interface FeedbackContext { message?: string; status?: number; path?: string; source?: 'dialog' | 'chat' }

export interface MyFeedback {
  id: number; kind: FeedbackKind; body: string; status: FeedbackStatus; admin_note: string | null;
  created_at: string; decided_at: string | null; unseen: boolean; files: number[];
}
export interface FeedbackItem {
  id: number; kind: FeedbackKind; body: string; page: string | null; conversation_id: number | null; status: FeedbackStatus;
  admin_note: string | null; duplicate_of: number | null; created_at: string; decided_at: string | null;
  reporter: string | null; reporter_part: string | null; decided_by: string | null;
  context: FeedbackContext | null; files: number[];
}
export interface AutoSignals {
  days: number; total_questions: number; failures: number; by_kind: Record<string, number>;
  by_day: { date: string; kind: string; n: number }[];
}

export const MAX_FILES = 3;
export const MAX_FILE_BYTES = 5 * 1024 * 1024;

const BASE = '/user-feedback';
export function sendFeedback(b: { kind: FeedbackKind; body: string; page?: string; conversation_id?: number | null;
                                  context?: FeedbackContext | null; files?: File[] }) {
  const fd = new FormData();
  fd.append('kind', b.kind);
  fd.append('body', b.body);
  if (b.page) fd.append('page', b.page);
  if (b.conversation_id != null) fd.append('conversation_id', String(b.conversation_id));
  if (b.context) fd.append('context', JSON.stringify(b.context));
  for (const f of b.files ?? []) fd.append('files', f, f.name || 'capture.png');
  return apiFetch<{ id: number; status: FeedbackStatus; files: number }>(BASE, { method: 'POST', body: fd });
}
export const myFeedback = () => apiFetch<MyFeedback[]>(`${BASE}/mine`);
export const myUnseen = () => apiFetch<{ count: number }>(`${BASE}/mine/unseen`);
export const listFeedback = (status?: FeedbackStatus) =>
  apiFetch<{ counts: Record<FeedbackStatus, number>; items: FeedbackItem[] }>(`${BASE}${status ? `?status=${status}` : ''}`);
export const triageFeedback = (id: number, b: { status: FeedbackStatus; note?: string; duplicate_of?: number | null }) =>
  apiFetch<{ id: number; status: FeedbackStatus }>(`${BASE}/${id}`, { method: 'PATCH', body: JSON.stringify(b) });
export const getAutoSignals = (days = 14) => apiFetch<AutoSignals>(`${BASE}/auto-signals?days=${days}`);
export const getFeedbackFile = (fid: number, fileId: number) => apiFetchBlob(`${BASE}/${fid}/files/${fileId}`);
