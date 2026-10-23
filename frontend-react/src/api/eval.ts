import { apiFetch } from './client';

/** 평가 게이트 판정 기록(eval_gate_run, v2.168) — 쓰기는 scripts/eval_gate.py --save뿐, 화면은 읽기 + 기준선 지정만. 원문 없음(qid·숫자). */

export interface RetrievalSummary {
  n: number;
  hit: number;
  top1: number;
  recall: number | null;
  top1_rate: number | null;
  by_type: Record<string, { n: number; hit: number; top1: number }>;
}

export interface AnswerSummary {
  n: number;
  accuracy: number | null;
  lo: number | null;
  hi: number | null;
  labels: Record<string, number>;
  reps: number;
  by_type: Record<string, { n: number; accuracy: number }>;
  retrieval_ok_answer_wrong?: string[];      // 상세에만
  retrieval_ok_answer_wrong_n?: number | null; // 목록에만
}

export interface AnswerGroup {
  n: number;
  before: number;
  after: number;
  diff: number;
  lo: number | null;
  hi: number | null;
  significant: boolean;
  worse: boolean;
  iid_fallback: boolean;
  undecidable: boolean;
  up: number;
  down: number;
}

export interface JudgeMeta {
  judge_model?: string[];
  judged_on?: string[];
  rubrics?: string[];
  answer_model?: string[];
  reps?: number;
  same_day_rubric?: boolean;
}

export interface GateRunListItem {
  id: number;
  run_at: string;
  created_by: string;
  label: string;
  kind: 'gate' | 'baseline' | 'judge_check';
  golden_version: string;
  verdict: 'pass' | 'fail' | null;
  is_baseline: boolean;
  git_commit: string | null;
  notes: string | null;
  judge_meta: JudgeMeta;
  summary: { retrieval: RetrievalSummary | null; answers: AnswerSummary | null; golden_before?: string | null };
  has_retrieval_gate: boolean;
  retrieval_down: number;
  retrieval_up: number;
  answers_failed: string[] | null;
  answers_total: AnswerGroup | null;
}

export interface GateRunDetail extends Omit<GateRunListItem, 'has_retrieval_gate' | 'retrieval_down' | 'retrieval_up' | 'answers_failed' | 'answers_total'> {
  retrieval: {
    pass: boolean; n: number; before: number; after: number; up: string[]; down: string[]; p: number; significant: boolean; error?: string;
    by_type: Record<string, { n: number; before: number; after: number }>;
  } | null;
  answers: { pass: boolean; failed: string[]; groups: Record<string, AnswerGroup>; up_qids: string[]; down_qids: string[] } | null;
}

export async function listGateRuns(limit = 50): Promise<GateRunListItem[]> {
  return apiFetch<GateRunListItem[]>(`/eval/gate-runs?limit=${limit}`);
}

export async function getGateRun(id: number): Promise<GateRunDetail> {
  return apiFetch<GateRunDetail>(`/eval/gate-runs/${id}`);
}

/** 현재 기준선(없으면 null) — 목록 범위와 무관하게 */
export async function getBaselineRun(): Promise<GateRunDetail | null> {
  return apiFetch<GateRunDetail | null>('/eval/gate-runs/baseline');
}

export async function setGateBaseline(id: number): Promise<{ id: number; is_baseline: boolean }> {
  return apiFetch(`/eval/gate-runs/${id}/baseline`, { method: 'POST' });
}

/** 자가 개선 루프(#87) — 진단(원인별 qid) + 개선 후보 + 사람 검수. 만들기는 scripts/eval_improve.py --save */
export type CandidateStatus = 'proposed' | 'approved' | 'held' | 'rejected' | 'adopted';

export interface ReplayRow {
  min_score: number; min_param_rank: number; refused: number; fixed: number; broken: number; before: number; after: number;
}

export interface Hypothesis { name: string; hint: string; qids: string[]; cause?: string }

export interface Candidate {
  id: number;
  key: string;
  cause: string;
  technique: string;
  technique_name: string;
  title: string;
  action: string;
  measure: 'replay' | 'retrieval' | 'answers';
  n_target: number;
  qids: string[];
  recommend: 'try' | 'reject' | 'review';
  why: string;
  replay: { grid: ReplayRow[]; best: ReplayRow | null } | null;
  hypotheses: Hypothesis[];
  status: CandidateStatus;
  review_reason: string | null;
  reviewed_at: string | null;
  carried_from_id: number | null;
  adopted_gate_run_id: number | null;
  adopted_gate_label: string | null;
}

export interface Improvement {
  run: {
    id: number; created_at: string; gate_run_id: number; gate_label: string; gate_is_baseline: boolean; model: string;
    diagnosis: { n: number; n_failed: number; causes: Record<string, string[]> };
    hypotheses: Hypothesis[];
  };
  causes: { key: string; name: string; where: string }[];
  candidates: Candidate[];
}

export async function getLatestImprovement(): Promise<Improvement | null> {
  return apiFetch<Improvement | null>('/eval/improvements/latest');
}

export async function reviewCandidate(id: number, status: Exclude<CandidateStatus, 'adopted'>, reason = ''): Promise<{ id: number; status: string }> {
  return apiFetch(`/eval/candidates/${id}/review`, { method: 'POST', body: JSON.stringify({ status, reason }) });
}

export async function adoptCandidate(id: number, gateRunId: number): Promise<{ id: number; status: string }> {
  return apiFetch(`/eval/candidates/${id}/adopt`, { method: 'POST', body: JSON.stringify({ gate_run_id: gateRunId }) });
}

/** 답 기법 대장(읽기) — 기준값은 서버 운영값 */
export interface Technique {
  key: string; name: string; stage: string; does: string; where: string | null;
  values: { label: string; value: string; setting: string | null }[];
  evidence: { date: string; note: string }[];
}

export async function listTechniques(): Promise<Technique[]> {
  return apiFetch<Technique[]>('/eval/techniques');
}

/** AI 판단 원장 집계(관리자) — 종류별 사람 확정·변경·틀림 + 되묻기 고른 분포 + 개선 후보 검수 */
export interface DecisionKindSummary {
  total: number; confident: number; decided: number; changed: number; wrong: number; auto_wrong: number;
  context_changed: number; unknown: number; wrong_rate: number | null; reasons: Record<string, number>;
}

export interface DecisionsReport {
  days: number;
  kinds: Record<string, DecisionKindSummary>;
  clarify_picks: Record<string, number>;
  candidates: { total: number; by_status: Record<string, number>; agree: number; disagree: number };
}

export async function getDecisionsReport(days = 90): Promise<DecisionsReport> {
  return apiFetch<DecisionsReport>(`/eval/decisions?days=${days}`);
}
