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

// ─── 정책서 화면(v0, 2026-10-21 재구축 2단계) — 읽기 전용. 내용 변경은 정정 원장의 제안(api/corrections)으로만 ───

/** 지금 쓰이는 정책 1건 = 1줄(문서 순서) */
export interface PolicyRow {
  item_id: number;
  logical_id: number;
  version: number;
  category_path: string[];
  policy_name: string;
  has_remark: boolean;
  /** 이 정책을 겨눈 검토 중 정정(출처 무관) — 있으면 새 제안을 못 만든다 */
  open_correction_id: number | null;
}

export interface PolicyParamRow {
  id: number;
  name: string;
  condition: string | null;
  value: string | null;
  unit: string | null;
  external_source: string | null;
}

export interface PolicyTerm { id: number; term: string; description: string }

export interface PolicyVersion {
  item_id: number;
  version: number;
  status: string;
  created_at: string | null;
  /** '적재' | '정정 #N' */
  origin: string;
}

export interface PolicyDetail {
  item_id: number;
  chunk_id: number | null;
  logical_id: number;
  version: number;
  status: string;
  category_path: string[];
  policy_name: string;
  raw_body: string;
  remark: string | null;
  source_file: string | null;
  params: PolicyParamRow[];
  terms: PolicyTerm[];
  versions: PolicyVersion[];
  open_correction: { id: number; source: string } | null;
}

export async function listPolicies(namespace: string, q?: string): Promise<PolicyRow[]> {
  const params = new URLSearchParams({ namespace });
  if (q?.trim()) params.set('q', q.trim());
  return apiFetch<PolicyRow[]>(`/policy/policies?${params.toString()}`);
}

export async function getPolicy(namespace: string, logicalId: number): Promise<PolicyDetail> {
  return apiFetch<PolicyDetail>(`/policy/policies/${logicalId}?namespace=${encodeURIComponent(namespace)}`);
}

export interface RefTableRow {
  id: number;
  logical_id: number;
  version: number;
  title: string;
  category_path: string[];
  n_columns: number;
  n_rows: number;
}

export interface RefTableColumn { name: string; role?: string; decisive?: boolean }

export interface RefTableDetail {
  id: number;
  logical_id: number;
  version: number;
  title: string;
  category_path: string[];
  columns: RefTableColumn[];
  rows: Record<string, string | number | null>[];
}

export async function listRefTables(namespace: string): Promise<RefTableRow[]> {
  return apiFetch<RefTableRow[]>(`/policy/ref-tables?namespace=${encodeURIComponent(namespace)}`);
}

export async function getRefTable(namespace: string, id: number): Promise<RefTableDetail> {
  return apiFetch<RefTableDetail>(`/policy/ref-tables/${id}?namespace=${encodeURIComponent(namespace)}`);
}

export interface PolicyOverview { id: number; title: string; body: string; source_key: string | null; source_file: string | null }

export async function listOverviews(namespace: string): Promise<PolicyOverview[]> {
  return apiFetch<PolicyOverview[]>(`/policy/overviews?namespace=${encodeURIComponent(namespace)}`);
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

/** 최종 답변 정확도 실행 이력(집계만) — scripts/eval_answers.py report --save */
export interface AnswerEvalRun {
  id: number;
  run_at: string;
  label: string;
  variant: string;
  total_n: number;
  counts: Record<string, number>;
  by_type: Record<string, Record<string, number>>;
  retrieval_ok_but_wrong: number;
  retrieval_ok: number;
  judge_model: string | null;
  notes: string | null;
}

export async function getAnswerEvalHistory(limit = 30): Promise<AnswerEvalRun[]> {
  return apiFetch<AnswerEvalRun[]>(`/policy/answer-eval/history?limit=${limit}`);
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
