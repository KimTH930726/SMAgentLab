import { apiFetch } from './client';

/** 근거 정정·지식 누락 신호(개선 원장, 2026-10-01). 사용자는 신호만 주고, 반영은 관리자 승인 후에만. */
export type CorrectionTargetType = 'knowledge' | 'policy_param' | 'policy_narrative' | 'missing' | 'answer' | 'auto';
/** 신고 시 대상 — auto는 "답변 틀림"(대상 안 고름, 서버가 그 답변의 근거 중 틀린 것을 판정) */
export type CorrectionRequestTarget = Exclude<CorrectionTargetType, 'answer'>;

/** AI 판정 — 어느 근거를 왜 골랐고 뭐가 틀렸는지(사용자·담당자 화면 공용) */
export interface AiVerdict {
  verdict: 'evidence' | 'missing' | 'answer_error' | null;
  /** llm_no_opinion = 사용자 의견 없이 질문·답변·근거의 어긋남만 보고 한 추정 */
  method: 'llm' | 'similarity' | 'no_evidence' | 'llm_no_opinion';
  user_claim?: string | null;
  reason?: string | null;
  wrong_part?: string | null;
  fix_summary?: string | null;
  key?: string;
  label?: string;
  preview?: string;
  retargeted?: { from: string; to: string; by: number | null };
}

export interface CorrectionCandidate {
  key: string;
  target_type: CorrectionTargetType;
  target_id: number;
  target_sub_id: number | null;
  label: string;
  preview: string;
}

export interface CorrectionCreatePayload {
  namespace: string;
  message_id?: number | null;
  target_type: CorrectionRequestTarget;
  target_id?: number | null;
  target_sub_id?: number | null;
  user_input: string;
}

export interface CorrectionItem {
  id: number;
  kind: 'correction' | 'missing_knowledge' | 'answer_quality' | 'answer_signal' | 'search_noise';
  source: string;
  candidates: CorrectionCandidate[] | null;
  ai_verdict: AiVerdict | null;
  namespace: string;
  message_id: number | null;
  target_type: CorrectionTargetType;
  target_id: number | null;
  target_sub_id: number | null;
  user_input: string;
  original: Record<string, unknown> | null;
  proposed: Record<string, string | null> | null;
  status: 'pending' | 'approved' | 'rejected';
  reject_reason: string | null;
  applied_target_id: number | null;
  created_at: string;
  decided_at: string | null;
  reporter: string | null;
  decided_by: string | null;
}

export interface CorrectionCreated {
  id: number;
  status: 'pending';
  kind: string;
  target_type: CorrectionTargetType;
  ai_verdict: AiVerdict | null;
  original: Record<string, unknown>;
  proposed: Record<string, string | null> | null;
}

export async function createCorrection(payload: CorrectionCreatePayload): Promise<CorrectionCreated> {
  return apiFetch('/corrections', { method: 'POST', body: JSON.stringify(payload) });
}

export async function getCorrectionStatus(
  knowledgeIds: number[], policyItemIds: number[],
): Promise<{ knowledge: number[]; policy_param: number[]; policy_narrative: number[] }> {
  const q = new URLSearchParams();
  if (knowledgeIds.length) q.set('knowledge_ids', knowledgeIds.join(','));
  if (policyItemIds.length) q.set('policy_item_ids', policyItemIds.join(','));
  return apiFetch(`/corrections/status?${q.toString()}`);
}

export async function getMyCorrections(unseen = true): Promise<CorrectionItem[]> {
  return apiFetch(`/corrections/mine?unseen=${unseen}`);
}

export async function markCorrectionsSeen(ids: number[]): Promise<{ updated: number }> {
  return apiFetch('/corrections/mine/seen', { method: 'POST', body: JSON.stringify({ ids }) });
}

/** namespace 없으면 전체(관리자 전용), 있으면 그 파트(파트 담당자도 가능) */
export async function getCorrectionPendingCount(namespace?: string): Promise<{ count: number; by_namespace: Record<string, number> }> {
  return apiFetch(`/corrections/pending-count${namespace ? `?namespace=${encodeURIComponent(namespace)}` : ''}`);
}

export async function listCorrections(namespace: string | null, status: string | null): Promise<CorrectionItem[]> {
  const q = new URLSearchParams();
  if (namespace) q.set('namespace', namespace);
  if (status) q.set('status', status);
  return apiFetch(`/corrections?${q.toString()}`);
}

export async function approveCorrection(id: number, proposed?: Record<string, string | null>): Promise<unknown> {
  return apiFetch(`/corrections/${id}/approve`, { method: 'POST', body: JSON.stringify({ proposed: proposed ?? null }) });
}

export async function rejectCorrection(id: number, reason: string): Promise<unknown> {
  return apiFetch(`/corrections/${id}/reject`, { method: 'POST', body: JSON.stringify({ reason }) });
}

/** 담당자가 AI 판정 대상을 바꿈(같은 대상이면 초안만 다시 생성) */
export async function retargetCorrection(id: number, key: string): Promise<unknown> {
  return apiFetch(`/corrections/${id}/retarget`, { method: 'POST', body: JSON.stringify({ key }) });
}
