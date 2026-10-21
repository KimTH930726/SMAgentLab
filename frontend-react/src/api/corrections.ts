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
  /** 정정 요청의 [직접 수정] 기록(누가·언제) */
  edits?: { by: number | null; by_name?: string | null; at: string }[];
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
  /** 이 신고가 나온 질문·당시 답변(근거 대상 건도 맥락이 보이도록) */
  question_text: string | null;
  answer_text: string | null;
  ai_verdict: AiVerdict | null;
  namespace: string;
  message_id: number | null;
  target_type: CorrectionTargetType;
  target_id: number | null;
  target_sub_id: number | null;
  /** 정책 화면 직접 수정은 없음(null) */
  user_input: string | null;
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

/** 정책서 화면의 수정 제안(10/21) — 직접 수정(원문·비고 그대로) 또는 AI 제안(한 줄). 정정 요청에서 승인해야 반영된다.
 *  같은 정책에 검토 중 정정이 있으면 409(ApiError.detail.existing_id). */
export type PolicyProposalSource = 'policy_direct' | 'policy_ai';

export interface PolicyProposalCreated {
  id: number;
  source: PolicyProposalSource;
  proposed: Record<string, string | null> | null;
  /** AI 제안인데 AI가 초안을 못 만듦 — 제안은 남았고 정정 요청에서 이어 간다 */
  ai_failed: boolean;
}

/** 정정 출처 이름 — 정정 요청 카드와 정책 상세의 "정정 #N 검토 중 (…)"이 같은 이름을 쓴다(10/21 리뷰 C4 — 두 곳에 따로 있어 이름이 달랐다).
 *  kind는 평가 게이트 "이상해요"(search_noise)를 가르는 데만 쓴다. */
const CORRECTION_SOURCE_LABEL: Record<string, string> = {
  policy_direct: '정책 직접 수정',
  policy_ai: '정책 AI 제안',
  json_import: 'JSON 가져오기',
  chat_answer_auto: '답변 틀림 + 한 줄(AI 판정)',
  chat_answer_wrong: '답변 틀림(의견 없음)',
  review_flag_migrated: '답변 틀림(의견 없음)',
  eval_gate: '평가 게이트 이상해요',
};

export function correctionSourceLabel(source: string | null | undefined, kind?: string): string {
  if (kind === 'search_noise') return CORRECTION_SOURCE_LABEL.eval_gate;
  return (source && CORRECTION_SOURCE_LABEL[source]) || '근거 직접 지정';
}

export async function createPolicyProposal(
  namespace: string, itemId: number, source: PolicyProposalSource,
  body: { proposed?: { raw_body: string; remark: string }; user_input?: string },
): Promise<PolicyProposalCreated> {
  return apiFetch('/corrections', {
    method: 'POST',
    body: JSON.stringify({ namespace, target_type: 'policy_narrative', target_id: itemId, source, ...body }),
  });
}

/** 정정 요청 카드의 [직접 수정] — 저장된 수정안을 바꾼다(반영은 [승인]에서) */
export async function updateCorrectionProposed(id: number, proposed: Record<string, string | null>): Promise<{ id: number; proposed: Record<string, string | null> }> {
  return apiFetch(`/corrections/${id}/proposed`, { method: 'PATCH', body: JSON.stringify({ proposed }) });
}

export async function getCorrectionStatus(
  knowledgeIds: number[], policyItemIds: number[],
): Promise<{ knowledge: number[]; policy_item?: number[]; policy_param: number[]; policy_narrative: number[] }> {
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

/** proposed = 화면에 보인 수정안(필수, 10/21 리뷰 5c) — 서버가 저장된 안과 다르면 409(보지 못한 안이 반영되지 않게) */
export async function approveCorrection(id: number, proposed: Record<string, string | null>): Promise<unknown> {
  return apiFetch(`/corrections/${id}/approve`, { method: 'POST', body: JSON.stringify({ proposed }) });
}

export async function rejectCorrection(id: number, reason: string): Promise<unknown> {
  return apiFetch(`/corrections/${id}/reject`, { method: 'POST', body: JSON.stringify({ reason }) });
}

/** 담당자 요청 한 줄 → AI가 수정안을 다시 씀(직접 고치려면 updateCorrectionProposed) */
export async function redraftCorrection(id: number, feedback: string): Promise<{ id: number; proposed: Record<string, string>; marked: boolean }> {
  return apiFetch(`/corrections/${id}/redraft`, { method: 'POST', body: JSON.stringify({ feedback }) });
}

/** 의견 없는 "답변 틀림"을 AI로 원인 추정(이관된 옛 신호 등 분석을 못 받은 건) */
export async function analyzeCorrection(id: number): Promise<{ analyzed: boolean }> {
  return apiFetch(`/corrections/${id}/analyze`, { method: 'POST' });
}

/** 담당자가 AI 판정 대상을 바꿈(같은 대상이면 초안만 다시 생성) */
/** 대상 변경·초안 다시 만들기 — proposed가 null이면 초안 없음(의견 없음·답변 오류 대상·AI 응답 실패) */
export async function retargetCorrection(id: number, key: string): Promise<{ id: number; target_type: string; proposed: Record<string, string> | null }> {
  return apiFetch(`/corrections/${id}/retarget`, { method: 'POST', body: JSON.stringify({ key }) });
}
