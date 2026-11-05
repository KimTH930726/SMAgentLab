import { apiFetch } from './client';

/** 근거 정정·지식 누락 신호(개선 원장, 2026-10-01). 사용자는 신호만 주고, 반영은 관리자 승인 후에만. */
export type CorrectionTargetType = 'knowledge' | 'policy_param' | 'policy_narrative' | 'missing' | 'answer' | 'auto'
  | PolicyChangeTarget;
/** 정책 추가·폐기·분류 옮기기 제안(C3 2단계, 10/27) — 내용 정정이 아니라 수정안 칸·AI 초안 없이 승인/반려만 */
export type PolicyChangeTarget = 'policy_add' | 'policy_retire' | 'policy_move';
export const POLICY_CHANGE_TARGETS: PolicyChangeTarget[] = ['policy_add', 'policy_retire', 'policy_move'];
/** 신고 시 대상 — auto는 "답변 틀림"(대상 안 고름, 서버가 그 답변의 근거 중 틀린 것을 판정) */
export type CorrectionRequestTarget = Exclude<CorrectionTargetType, 'answer' | PolicyChangeTarget>;

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
  /** 정책 컬렉션 "빠진 내용"을 넘긴 정책 추가 제안 번호(11/03) */
  forwarded_to?: number;
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
  user_reason?: UserReason | null;
}

/** [답변 틀림] 사유 칩(11/04, 규격 피드백) — 지식 문제(낡음·단계 빠짐) / 검색 문제(다른 시스템·근거 못 찾음) */
export type UserReason = 'stale' | 'other_system' | 'missing_step' | 'not_found';
/** example = 칩을 고르면 한 줄 칸 placeholder(11/05 태훈 — 사유마다 무엇을 쓰면 되는지 예시) */
export const USER_REASONS: { key: UserReason; label: string; hint: string; example: string }[] = [
  { key: 'stale', label: '내용이 낡음', hint: '예전 기준·옛 화면 — 지식을 고쳐야 해요', example: '예) 90일이 아니라 60일로 바뀌었어요' },
  { key: 'other_system', label: '다른 시스템 얘기', hint: '묻는 시스템이 아닌 다른 곳 지식으로 답했어요 — 검색 범위 문제', example: '예) 주문 시스템이 아니라 정산 시스템 기준으로 답했어요' },
  { key: 'missing_step', label: '단계·조건 빠짐', hint: '답이 일부만 맞아요 — 빠진 단계나 조건이 있어요', example: '예) 재기동 전에 배치 중지 단계가 빠졌어요' },
  { key: 'not_found', label: '근거를 못 찾음', hint: '맞는 지식이 있는데 못 찾았거나, 지식 자체가 없어요', example: '예) 운영 지식 "배송비 정책" 문서에 답이 있어요' },
];

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
  /** 처리할 컬렉션의 구분 — 정책 컬렉션의 "빠진 내용"은 운영 지식이 아니라 정책 추가 제안으로 넘긴다(11/03) */
  namespace_area?: 'policy' | 'system';
  message_id: number | null;
  target_type: CorrectionTargetType;
  target_id: number | null;
  target_sub_id: number | null;
  /** 정책 화면 직접 수정은 없음(null) */
  user_input: string | null;
  user_reason?: UserReason | null;
  /** 원문 확인 카드에서 올린 정책 정정이면 그 카드 번호 · 정책 정정의 대상 버전(11/05 카드 머리) */
  source_check_id?: number | null;
  policy_version?: number | null;
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
  sourceCheckId?: number | null,
): Promise<PolicyProposalCreated> {
  return apiFetch('/corrections', {
    method: 'POST',
    // 원문 확인 카드에서 연 정책이면 그 카드 번호 — 제안이 승인되면 카드도 해결(C3)
    body: JSON.stringify({ namespace, target_type: 'policy_narrative', target_id: itemId, source, ...body, source_check_id: sourceCheckId ?? null }),
  });
}

export type PolicyChange =
  | { change: 'add'; policy_name: string; category_path: string[]; raw_body: string; remark?: string; reason?: string; query_log_id?: number }
  | { change: 'retire'; target_id: number; reason: string }
  | { change: 'move'; target_id: number; category_path: string[]; reason?: string };

/** 정책 추가·폐기·분류 옮기기 제안 — 정정 요청에 올라가고 승인해야 반영(C3 2단계) */
export async function createPolicyChange(namespace: string, body: PolicyChange): Promise<{ id: number; target_type: PolicyChangeTarget }> {
  return apiFetch('/corrections/policy-change', { method: 'POST', body: JSON.stringify({ namespace, ...body }) });
}

/** 정책 컬렉션의 "빠진 내용" 신고 → 정책 추가 제안(11/03). 원래 신고는 처리됨(넘긴 제안 번호가 ai_verdict.forwarded_to) */
export async function forwardMissingToPolicyAdd(itemId: number, namespace: string,
  body: { policy_name: string; category_path: string[]; raw_body: string; remark?: string }): Promise<{ id: number; forwarded_to: number }> {
  return apiFetch(`/corrections/${itemId}/to-policy-add`, { method: 'POST', body: JSON.stringify({ namespace, change: 'add', ...body }) });
}

/** 폐기 제안 창 경고 — 이 정책을 정답으로 쓰는 평가 문항(checked=false면 서버에 평가 문항 파일이 없어 못 셈) */
export async function getPolicyChangeUses(namespace: string, itemId: number): Promise<{ n: number; qids: string[]; checked: boolean }> {
  return apiFetch(`/corrections/policy-change/uses?namespace=${encodeURIComponent(namespace)}&target_id=${itemId}`);
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
