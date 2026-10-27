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
  answers_groups: Record<string, AnswerGroup> | null;
}

export interface GateRunDetail extends Omit<GateRunListItem, 'has_retrieval_gate' | 'retrieval_down' | 'retrieval_up' | 'answers_failed' | 'answers_total' | 'answers_groups'> {
  retrieval: {
    pass: boolean; n: number; before: number; after: number; up: string[]; down: string[]; p: number; significant: boolean; error?: string;
    by_type: Record<string, { n: number; before: number; after: number }>;
  } | null;
  answers: { pass: boolean; failed: string[]; groups: Record<string, AnswerGroup>; up_qids: string[]; down_qids: string[] } | null;
}

export async function listGateRuns(limit = 50): Promise<GateRunListItem[]> {
  return apiFetch<GateRunListItem[]>(`/eval/gate-runs?limit=${limit}`);
}

/** 현재 기준선(없으면 null) — 목록 범위와 무관하게 */
export async function getBaselineRun(): Promise<GateRunDetail | null> {
  return apiFetch<GateRunDetail | null>('/eval/gate-runs/baseline');
}

/** 자가 개선 루프(#87) — 진단(원인별 qid) + 개선 후보 + 사람 검수. 만들기는 scripts/eval_improve.py --save */
export type CandidateStatus = 'proposed' | 'approved' | 'held' | 'rejected' | 'adopted';

export interface ReplayRow {
  min_score: number; min_param_rank: number; refused: number; fixed: number; broken: number; before: number; after: number;
}

export interface Hypothesis { name: string; hint: string; qids: string[]; cause?: string; status?: 'confirmed' | 'rejected' | 'open' }

/** 후보를 잰 판정(#88) — 진행 단계의 "측정" */
export interface Measurement { id: number; label: string; verdict: 'pass' | 'fail' | null; run_at: string; diff: number | null }

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
  measurements: Measurement[];
}

export interface Improvement {
  run: {
    id: number; created_at: string; gate_run_id: number; gate_label: string; gate_is_baseline: boolean; model: string; accuracy: number | null;
    diagnosis: { n: number; n_failed: number; causes: Record<string, string[]>; locked_n?: number };
    hypotheses: Hypothesis[];
  };
  causes: { key: string; name: string; where: string }[];
  candidates: Candidate[];
}

export async function getLatestImprovement(runId?: number): Promise<Improvement | null> {
  return apiFetch<Improvement | null>(`/eval/improvements/latest${runId != null ? `?run_id=${runId}` : ''}`);
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
  evidence: { date: string; note: string; gate_run_id: number | null }[];
  candidates: { id: number; title: string; status: CandidateStatus; recommend: Candidate['recommend']; n_target: number }[];
}

export async function listTechniques(): Promise<Technique[]> {
  return apiFetch<Technique[]>('/eval/techniques');
}

/** 문항 상세(관리자) — 질문·답·채점 이유는 서버가 결과 파일에서 읽어 줌(DB 저장 없음) */
export interface QuestionAnswer { rep: number; answer: string; label: string | null; reason: string; abstained: boolean; error: string | null; clarified: boolean }

export interface QuestionDetail {
  qid: string; type: string | null; namespace: string | null; question: string; expected: string; gold_policies: string[];
  retrieval: { gold_in_context: boolean; mapped_term: string | null; hit?: boolean; top1?: boolean; coverage?: number | null; clarify?: boolean };
  answers: QuestionAnswer[];
  before: QuestionAnswer[] | null;
  moved: 'up' | 'down' | 'same' | null;
  cause: { key: string; name: string; where: string } | null;
  hypotheses: { name: string; hint: string; n: number }[];
  storage: { gold: ReportRow['gold']; tags: string[] } | null;
  candidates: {
    id: number; title: string; status: CandidateStatus; action: string; measure: Candidate['measure']; technique_name: string;
    measurements: { id: number; label: string; verdict: 'pass' | 'fail' | null; this_question: 'up' | 'down' | 'same' }[];
  }[];
}

export async function getQuestionDetail(runId: number, qid: string): Promise<QuestionDetail> {
  return apiFetch<QuestionDetail>(`/eval/gate-runs/${runId}/questions/${encodeURIComponent(qid)}`);
}

export async function markHypothesis(improvementId: number, cause: string, name: string, status: 'confirmed' | 'rejected' | 'open') {
  return apiFetch(`/eval/improvements/${improvementId}/hypotheses`, { method: 'POST', body: JSON.stringify({ cause, name, status }) });
}

/** 틀린 문항 리포트(관리자) — AI가 낸 답을 관리자가 리포트로 받고 개선을 승인하는 화면(10/26) */
export interface ReportTechnique {
  id: number; key: string; technique: string; title: string; status: CandidateStatus; recommend: Candidate['recommend'];
  technique_name: string; action: string; measure: Candidate['measure']; why: string; review_reason: string | null; reviewed_at: string | null;
  n_failed: number;
  effect: {
    gate_run_id: number; verdict: 'pass' | 'fail' | null; label: string; total_diff: number | null;
    locked: { n: number; before: number; after: number; down: boolean } | null; retrieval_up: number; retrieval_down: number;
  } | null;
}

export interface ReportRow {
  qid: string; type: string | null; locked: boolean; question: string; labels: (string | null)[];
  cause: string | null; cause_name: string | null; storage: string[]; real_status: 'proposed' | 'added' | 'dismissed' | null;
  gold: { kind: string; name: string | null; path?: string; body_len?: number; params?: number; chunks?: number; table?: boolean; remark?: boolean; rows?: number | null; missing?: boolean }[];
  techniques: { id: number; title: string; status: CandidateStatus; technique_name: string }[];
}

export interface GateReport {
  gate_run_id: number; gate_label: string; improvement_id: number; diagnosed_at: string;
  score: { total: number | null; locked: number | null; locked_n: number; retrieval: number | null; n: number };
  failed: ReportRow[]; techniques: ReportTechnique[];
  unmeasured: { store: string; n: number; total: number; ref: string; qids: string[] }[];
  diagnosed_gate_run_id: number; files_missing: string[];
}

export async function getGateReport(): Promise<GateReport | null> {
  return apiFetch<GateReport | null>('/eval/report');
}

/** 실사용 문항을 시험지에서 빼기·다시 넣기(관리자) — 다음 측정부터 반영. 정정 승인 때 자동으로 들어간다(10/26) */
export async function setRealQuestion(id: number, status: 'added' | 'dismissed') {
  return apiFetch(`/eval/question-candidates/${id}`, { method: 'POST', body: JSON.stringify({ status }) });
}

/** 판정 하나의 층별 검색 놓침·답 틀림 문항 번호(관리자) — [검색]·[답] 층 줄을 누르면 연다 */
export async function getLayerQuestions(runId: number): Promise<{ missed: Record<string, string[]>; wrong: Record<string, string[]>; files_missing: string[] }> {
  return apiFetch(`/eval/gate-runs/${runId}/layers`);
}

/** AI 판단 원장 집계(관리자, 품질 › AI 판단) — 종류별 사람 확정·변경·틀림 + 되묻기(정책 분류·참고표) + 원문 확인 결론 + 다른 컬렉션 제안 + 개선 후보 */
export interface DecisionKindSummary {
  total: number; confident: number; decided: number; changed: number; wrong: number; auto_wrong: number;
  context_changed: number; unknown: number; wrong_rate: number | null; reasons: Record<string, number>;
}

/** 사람이 AI 값을 바꾼 판단 한 건 — AI 이유·사람 선택·"AI가 틀렸나요?" 답 */
export interface ChangedDecision {
  id: number; kind: string; at: string; changed_by: string | null; target: string | null;
  ai: { collection: string | null; doc_type: string | null; reason: string | null };
  human: { collection: string | null; choice: string | null };
  verdict: 'wrong' | 'context_changed' | 'unknown' | null; verdict_reason: string | null;
}

export interface DecisionsReport {
  days: number;
  kinds: Record<string, DecisionKindSummary>;
  clarify: { category: Record<string, number>; ref: Record<string, number>; open: number };
  source_checks: { by_reason: Record<string, number>; by_source: Record<string, { resolved: number; no_issue: number }> };
  collection_suggest: { total: number; picked: number; enabled: boolean };
  candidates: { total: number; by_status: Record<string, number>; agree: number; disagree: number };
  changed: ChangedDecision[];
}

export async function getDecisionsReport(days = 90): Promise<DecisionsReport> {
  return apiFetch<DecisionsReport>(`/eval/decisions?days=${days}`);
}

/** AI 감사 로그 한 줄(10/27) — 원장 시간순. ai/human = 서버가 만든 한 줄 설명(원문 없음), open = 아직 사람·사용자 확정 전 */
export interface DecisionLogItem {
  id: number; kind: string; kind_name: string; at: string; decided_at: string | null; confident: boolean; changed: boolean; open: boolean;
  ai: string; ai_reason: string | null; human: string | null; asked_by: string | null; changed_by: string | null; rule: string | null;
  verdict: ChangedDecision['verdict']; verdict_reason: string | null;
}

export async function getDecisionLog(q: { days: number; kind?: string; changedOnly?: boolean; before?: number | null; limit?: number }):
  Promise<{ items: DecisionLogItem[]; next_before: number | null }> {
  const p = new URLSearchParams({ days: String(q.days), limit: String(q.limit ?? 50) });
  if (q.kind) p.set('kind', q.kind);
  if (q.changedOnly) p.set('changed_only', 'true');
  if (q.before) p.set('before', String(q.before));
  return apiFetch(`/eval/decisions/log?${p.toString()}`);
}

/** "AI가 틀렸나요?" 답 — 바꾼 사람만(서버가 확인) */
export async function setDecisionVerdict(id: number, verdict: string, reason = ''): Promise<{ id: number; verdict: string }> {
  return apiFetch(`/eval/decisions/${id}/verdict`, { method: 'POST', body: JSON.stringify({ verdict, reason }) });
}

