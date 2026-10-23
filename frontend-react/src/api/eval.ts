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
