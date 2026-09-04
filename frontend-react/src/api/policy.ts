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
}

export interface Track2Result {
  total_n: number;
  a_hit_rate: number;
  b_hit_rate: number;
  by_type: Track2TypeResult[];
  golden_set_file: string;
  top_k: number;
  duration_seconds: number;
}

export async function runTrack2(topK = 10): Promise<Track2Result> {
  return apiFetch<Track2Result>(`/policy/track2/run?top_k=${topK}`, { method: 'POST' });
}
