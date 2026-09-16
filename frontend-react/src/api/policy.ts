import { apiFetch } from './client';

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

export async function getPolicyItems(namespace: string, category?: string, q?: string): Promise<PolicyItem[]> {
  const params = new URLSearchParams({ namespace });
  if (category) params.set('category', category);
  if (q) params.set('q', q);
  return apiFetch<PolicyItem[]>(`/policy/items?${params.toString()}`);
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

// ─── 파이프라인 모니터(실험실 게이트 작업3) ────────────────────────────────────

export interface PipelineStatsTrendPoint {
  day: string;
  count: number;
}

export interface PipelineStats {
  policy_item: number;
  policy_param: number;
  policy_chunk: number;
  ref_db_column: number;
  ref_common_code: number;
  trend: PipelineStatsTrendPoint[];
}

export async function getPipelineStats(namespace: string): Promise<PipelineStats> {
  const params = new URLSearchParams({ namespace });
  return apiFetch<PipelineStats>(`/policy/pipeline-stats?${params.toString()}`);
}
