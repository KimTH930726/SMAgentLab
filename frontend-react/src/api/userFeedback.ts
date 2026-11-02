import { apiFetch } from './client';

/** 사용자 피드백(10/30) — 오류 신고·기능 제안. 답 내용 오류는 정정 요청(api/feedback)으로 */
export type FeedbackKind = 'bug' | 'idea';
export type FeedbackStatus = 'new' | 'planned' | 'done' | 'duplicate' | 'declined';

export interface MyFeedback {
  id: number; kind: FeedbackKind; body: string; status: FeedbackStatus; admin_note: string | null;
  created_at: string; decided_at: string | null; unseen: boolean;
}
export interface FeedbackItem {
  id: number; kind: FeedbackKind; body: string; page: string | null; conversation_id: number | null; status: FeedbackStatus;
  admin_note: string | null; duplicate_of: number | null; created_at: string; decided_at: string | null;
  reporter: string | null; reporter_part: string | null; decided_by: string | null;
}
export interface AutoSignals {
  days: number; total_questions: number; failures: number; by_kind: Record<string, number>;
  by_day: { date: string; kind: string; n: number }[];
}

const BASE = '/user-feedback';
export const sendFeedback = (b: { kind: FeedbackKind; body: string; page?: string; conversation_id?: number | null }) =>
  apiFetch<{ id: number; status: FeedbackStatus }>(BASE, { method: 'POST', body: JSON.stringify(b) });
export const myFeedback = () => apiFetch<MyFeedback[]>(`${BASE}/mine`);
export const myUnseen = () => apiFetch<{ count: number }>(`${BASE}/mine/unseen`);
export const listFeedback = (status?: FeedbackStatus) =>
  apiFetch<{ counts: Record<FeedbackStatus, number>; items: FeedbackItem[] }>(`${BASE}${status ? `?status=${status}` : ''}`);
export const triageFeedback = (id: number, b: { status: FeedbackStatus; note?: string; duplicate_of?: number | null }) =>
  apiFetch<{ id: number; status: FeedbackStatus }>(`${BASE}/${id}`, { method: 'PATCH', body: JSON.stringify(b) });
export const getAutoSignals = (days = 14) => apiFetch<AutoSignals>(`${BASE}/auto-signals?days=${days}`);
