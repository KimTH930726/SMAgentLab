import { apiFetch } from './client';

// ─── 정책 단건 검색 (즉석 질의 — 실험실, 2026-09-18) ───────────────────────────

export interface ParamHit {
  item_id: number;
  logical_id: number;
  policy_name: string;
  category_path: string[];
  status: string;
  param_name: string;
  condition: string | null;
  value: string | null;
  unit: string | null;
  raw_body: string;
  score: number;
}

export interface NarrativeHit {
  item_id: number;
  logical_id: number;
  policy_name: string;
  category_path: string[];
  status: string;
  chunk_text: string;
  score: number;
  raw_body: string;
}

export interface PolicySearchResult {
  params: ParamHit[];
  narratives: NarrativeHit[];
}

export async function searchPolicy(
  namespace: string, q: string, opts?: { category?: string; topK?: number },
): Promise<PolicySearchResult> {
  const params = new URLSearchParams({ namespace, q });
  if (opts?.category) params.set('category', opts.category);
  if (opts?.topK) params.set('top_k', String(opts.topK));
  return apiFetch<PolicySearchResult>(`/policy/search?${params.toString()}`);
}

// ─── unresolved 팀별 집계 리포트 (docs/policy-doc-pipeline-plan.md §2-3) ────────

export interface UnresolvedSegment {
  text: string;
  reason: string | null;
}

export interface UnresolvedItem {
  item_id: number;
  logical_id: number;
  policy_name: string;
  category_path: string[];
  segments: UnresolvedSegment[];
}

export interface SystemUnresolvedGroup {
  system_key: string;
  item_count: number;
  segment_count: number;
  items: UnresolvedItem[];
}

export interface UnresolvedSummary {
  total_items: number;
  total_segments: number;
  by_system: SystemUnresolvedGroup[];
}

export async function getUnresolvedSummary(namespace: string, systemKey?: string): Promise<UnresolvedSummary> {
  const params = new URLSearchParams({ namespace });
  if (systemKey) params.set('system_key', systemKey);
  return apiFetch<UnresolvedSummary>(`/policy/unresolved-summary?${params.toString()}`);
}

// unresolved segment를 서술(policy_chunk)로 수동 편입(2026-09-16) — "조회만 있고 액션이
// 없다"는 지적으로 추가된 유일한 쓰기 액션. 정밀 재분류가 아니라 최소한 검색은 되게
// 만드는 원클릭 액션이다.
export interface PromoteSegmentResult {
  remaining_segments: number;
}

export async function promoteUnresolvedSegment(
  itemId: number, segmentIndex: number, namespace: string,
): Promise<PromoteSegmentResult> {
  return apiFetch<PromoteSegmentResult>(`/policy/unresolved/${itemId}/promote`, {
    method: 'POST',
    body: JSON.stringify({ namespace, segment_index: segmentIndex }),
  });
}

// unresolved segment를 파라미터(policy_param)로 수동 편입(2026-09-23) — "서술로 편입밖에
// 없으면 반쪽짜리 아니냐"는 지적으로 추가. 서술과 달리 원문 그대로 못 쓰고(구조화된 필드가
// 필요 — LLM이 애초에 못 뽑아낸 부분) 사람이 폼으로 입력한 값을 그대로 저장한다.
export interface PromoteParamFields {
  name: string;
  condition?: string | null;
  value?: string | null;
  unit?: string | null;
}

export async function promoteUnresolvedSegmentToParam(
  itemId: number, segmentIndex: number, namespace: string, fields: PromoteParamFields,
): Promise<PromoteSegmentResult> {
  return apiFetch<PromoteSegmentResult>(`/policy/unresolved/${itemId}/promote-param`, {
    method: 'POST',
    body: JSON.stringify({ namespace, segment_index: segmentIndex, ...fields }),
  });
}

// ─── 정책 항목 브라우저 (item 단위, param/narrative 자식 포함) ─────────────────

export interface PolicyParam {
  id: number;
  name: string;
  condition: string | null;
  value: string | null;
  unit: string | null;
}

export interface PolicyChunk {
  id: number;
  chunk_text: string;
  chunk_idx: number;
}

export interface PolicyItem {
  item_id: number;
  logical_id: number;
  version: number;
  policy_name: string;
  category_path: string[];
  raw_body: string;
  status: string;
  parse_status: string;
  system_key: string | null;
  params: PolicyParam[];
  narratives: PolicyChunk[];
  matched_via: string[];
}

export async function getPolicyItems(
  namespace: string, category?: string, q?: string, status?: string,
): Promise<PolicyItem[]> {
  const params = new URLSearchParams({ namespace });
  if (category) params.set('category', category);
  if (q) params.set('q', q);
  if (status) params.set('status', status);
  return apiFetch<PolicyItem[]>(`/policy/items?${params.toString()}`);
}

// 검토 대기(pending_review) 정책 항목 승인/반려(2026-09-23) — "pending 필터링이 없다"는
// 지적의 근본 원인은 필터 부재가 아니라 상태를 바꾸는 액션 자체가 없었던 것이라, 필터보다
// 이 액션을 먼저 추가한다.
export async function approvePolicyItem(itemId: number, namespace: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/policy/items/${itemId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ namespace }),
  });
}

export async function rejectPolicyItem(itemId: number, namespace: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/policy/items/${itemId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ namespace }),
  });
}

// 반려된 항목의 파라미터/서술 수정(2026-09-23) — "반려하면 그냥 데이터를 버리는데?"라는
// 지적으로 추가. 저장하면 서버가 자동으로 다시 검토대기(pending_review)로 되돌린다 —
// 반환되는 status를 그대로 쓰면 화면을 즉시 갱신할 수 있다.
export async function updatePolicyParam(
  paramId: number, namespace: string, fields: PromoteParamFields,
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/policy/params/${paramId}`, {
    method: 'PATCH',
    body: JSON.stringify({ namespace, ...fields }),
  });
}

export async function updatePolicyNarrative(
  chunkId: number, namespace: string, chunkText: string,
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/policy/narratives/${chunkId}`, {
    method: 'PATCH',
    body: JSON.stringify({ namespace, chunk_text: chunkText }),
  });
}

// 미분류 segment의 파라미터 필드 LLM 1차 추측(2026-09-23) — "파라미터로 편입" 폼을 열 때
// 자동 호출해 프리필하는 용도. 실패해도 에러 없이 전부 null로 옴(사람이 그냥 수동 입력).
// name까지 null일 수 있어(백엔드 SuggestParamOut) PromoteParamFields(name 필수)와는 별도 타입.
export interface ParamSuggestion {
  name: string | null;
  condition: string | null;
  value: string | null;
  unit: string | null;
}

export async function suggestParamFields(
  itemId: number, segmentIndex: number, namespace: string,
): Promise<ParamSuggestion> {
  return apiFetch<ParamSuggestion>(`/policy/unresolved/${itemId}/suggest-param`, {
    method: 'POST',
    body: JSON.stringify({ namespace, segment_index: segmentIndex }),
  });
}

// ─── Track 2 저장소 전략 실험실 ─────────────────────────────────────────────

export interface Track2TypeResult {
  type: string;
  n: number;
  a_hit_rate: number;
  b_hit_rate: number;
  a_precision: number;
  b_precision: number;
  b_hit_rdb_only: number;
  b_hit_vector_only: number;
  b_hit_both: number;
  // Top-1 Accuracy(v2.74) — B는 RDB/벡터 두 채널로 나뉘어 "진짜 하나의 순위"가 원래
  // 없어서(아키텍처 자체의 특징) 채널별로 따로 잰다. reranker_enabled=True면 채널별
  // 재정렬 결과의 1위가 자연히 반영된다.
  a_top1_accuracy: number;
  b_top1_param_accuracy: number;
  b_top1_narrative_accuracy: number;
}

export interface Track2Result {
  total_n: number;
  a_hit_rate: number;
  b_hit_rate: number;
  a_precision: number;
  b_precision: number;
  b_hit_rdb_only: number;
  b_hit_vector_only: number;
  b_hit_both: number;
  by_type: Track2TypeResult[];
  golden_set_file: string;
  top_k: number;
  duration_seconds: number;
  a_top1_accuracy: number;
  b_top1_param_accuracy: number;
  b_top1_narrative_accuracy: number;
}

// 실행 이력 스냅샷(v2.74) — POST /track2/run 호출마다 자동 저장됨. 모니터링 뷰(실험실
// 게이트 작업3)의 추이 차트 재료.
export interface Track2RunHistory extends Track2Result {
  id: number;
  run_at: string;
  triggered_by: number | null;
}

export async function runTrack2(topK = 10, axis = 'policy'): Promise<Track2Result> {
  return apiFetch<Track2Result>(`/policy/track2/run?top_k=${topK}&axis=${encodeURIComponent(axis)}`, { method: 'POST' });
}

export async function getTrack2History(limit = 50): Promise<Track2RunHistory[]> {
  return apiFetch<Track2RunHistory[]>(`/policy/track2/history?limit=${limit}`);
}

// 비교 가능한 데이터 축 목록(2026-09-16, 엔진 파라미터화) — 지금은 "정책서" 하나뿐이지만
// 새 축(CMDB 등)이 백엔드 track2.AXIS_REGISTRY에 등록되면 이 목록도 자동으로 늘어난다.
export interface Track2Axis {
  key: string;
  label: string;
}

export async function getTrack2Axes(): Promise<Track2Axis[]> {
  return apiFetch<Track2Axis[]>('/policy/track2/axes');
}

// 파이프라인 모니터(실험실 게이트 작업3)의 프론트 소비부(PolicyPipelineMonitor.tsx)는
// 2026-09-16 화면 재설계로 제거됨(커밋 001aa4c) — 백엔드 GET /policy/pipeline-stats는
// "재사용 가능하니 유지"로 의도적으로 남겼지만, 그 화면만 쓰던 이 프론트 래퍼는 정리
// 대상에서 빠진 채 고아로 남아있었다(2026-09-24 정리). 다시 필요해지면
// docs/architecture.md v2.77 항목과 git 이력(001aa4c) 참고해 복원.
