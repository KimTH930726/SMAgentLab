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
  failures?: string[];   // 답을 못 낸 문항(게이트웨이 거부·연결 실패·채점 실패) — 정답률에서 뺌(10/29)
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

/** 판정 기록 축(#96, 10/29) — 정책서·업무지식. 기준선은 축마다 한 줄 */
export type GateAxis = 'policy' | 'knowledge';

export interface GateRunListItem {
  id: number;
  axis: GateAxis;
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
  /** 단정 오답(채점 "오답") 전/후 — 운영 반영 조건(10/30 B안, 서버 improve.confident_wrong_error) */
  answers_confident_wrong?: { before: number | null; after: number | null; n: number; reps_before: number; reps_after: number } | null;
}

export interface GateRunDetail extends Omit<GateRunListItem, 'has_retrieval_gate' | 'retrieval_down' | 'retrieval_up' | 'answers_failed' | 'answers_total' | 'answers_groups' | 'answers_confident_wrong'> {
  retrieval: {
    pass: boolean; n: number; before: number; after: number; up: string[]; down: string[]; p: number; significant: boolean; error?: string;
    by_type: Record<string, { n: number; before: number; after: number }>;
  } | null;
  answers: { pass: boolean; failed: string[]; groups: Record<string, AnswerGroup>; up_qids: string[]; down_qids: string[] } | null;
}

export async function listGateRuns(limit = 50, axis?: GateAxis): Promise<GateRunListItem[]> {
  return apiFetch<GateRunListItem[]>(`/eval/gate-runs?limit=${limit}${axis ? `&axis=${axis}` : ''}`);
}

/** 그 축의 현재 기준선(없으면 null) — 목록 범위와 무관하게 */
export async function getBaselineRun(axis: GateAxis = 'policy'): Promise<GateRunDetail | null> {
  return apiFetch<GateRunDetail | null>(`/eval/gate-runs/baseline?axis=${axis}`);
}

/** 자가 개선 루프(#87) — 진단(원인별 qid) + 개선 후보 + 사람 검수. 만들기는 scripts/eval_improve.py --save */
export type CandidateStatus = 'proposed' | 'approved' | 'held' | 'rejected' | 'adopted' | 'failed';
/** 화면 단계(서버 phase_of) — 제안 · 실험 중 · 실험 오류 · 검토 대기 · 반영 · 실패 · 기각 · 보류 */
export type CandidatePhase = 'proposed' | 'running' | 'error' | 'review' | 'adopted' | 'failed' | 'rejected' | 'held';

export interface ReplayRow {
  min_score: number; min_param_rank: number; refused: number; fixed: number; broken: number; before: number; after: number;
}

export interface Hypothesis { name: string; hint: string; qids: string[]; cause?: string; status?: 'confirmed' | 'rejected' | 'open' }

/** 후보를 잰 판정(#88) — 진행 단계의 "측정" */
export interface Measurement {
  id: number; label: string; verdict: 'pass' | 'fail' | null; run_at: string; diff: number | null;
  // 실험 결과 칸(10/30) — 정답률 전/후(묶음별) · 단정 오답·거절 · 검색
  kind?: string; has_answers?: boolean; groups?: Record<string, AnswerGroup> | null;
  confident_wrong?: { before: number | null; after: number | null; refused_before?: number | null; refused_after?: number | null;
    n: number; reps_before: number; reps_after: number } | null;
  retrieval?: { n: number; before: number; after: number; down: number } | null;
}

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
  phase?: CandidatePhase;
  /** 이 후보를 만든 진단 — 개선 후보 목록은 여러 진단의 줄을 섞어 보이므로 모달은 줄마다 이 진단으로 연다 */
  run_id: number;
  review_reason: string | null;
  reviewed_at: string | null;
  carried_from_id: number | null;
  adopted_gate_run_id: number | null;
  adopted_gate_label: string | null;
  measurements: Measurement[];
  /** 가장 최근 측정을 운영에 반영할 수 있나 — 서버가 반영 버튼과 같은 규칙(adopt_error)으로 미리 판정 */
  adopt_check?: { gate_run_id: number; ok: boolean; reason: string | null } | null;
  /** 자동 실험 되나(10/30 실험 실행기) — 안 되면 이유(바꿀 것이 글뿐 등) */
  auto?: { ok: boolean; reason: string | null };
  /** 가장 최근 실험 작업 — 대기·진행·끝·실패 */
  experiment?: ExperimentJob | null;
}

export interface ExperimentJob {
  id: number; status: 'queued' | 'running' | 'done' | 'failed'; stage: string | null; done: number; total: number;
  error: string | null; gate_run_id: number | null; created_at: string; finished_at: string | null;
}

/** 평가 게이트 › 개선 후보(10/30) — 최근 진단 후보 + 그 전 진단에서 종결된 반영·실패·기각(줄마다 phase) */
export async function getCandidateBoard(axis: GateAxis): Promise<{ run_id: number | null; candidates: Candidate[] }> {
  return apiFetch(`/eval/improvements/board?axis=${axis}`);
}

export async function startExperiment(id: number): Promise<{ id: number; status: string }> {
  return apiFetch(`/eval/candidates/${id}/experiment`, { method: 'POST' });
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

export async function getLatestImprovement(runId?: number, axis: GateAxis = 'policy'): Promise<Improvement | null> {
  return apiFetch<Improvement | null>(`/eval/improvements/latest?axis=${axis}${runId != null ? `&run_id=${runId}` : ''}`);
}

export async function reviewCandidate(id: number, status: Exclude<CandidateStatus, 'adopted'>, reason = ''): Promise<{ id: number; status: string; experiment?: { id: number; status: string } | null; experiment_note?: string }> {
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
  /** 설명 화면용(있는 것만) — 가장 짧은 예시 한 장면(가상 문장) + 규칙 3줄(기준값은 지금 값으로 채워 옴) */
  example?: { where: string; lines: [string, string][]; chips: string[] } | null;
  rules?: string[];
  candidates: { id: number; title: string; status: CandidateStatus; recommend: Candidate['recommend']; n_target: number }[];
  /** 흐름도(11/03) — 어느 흐름·단계에서 쓰이나, 필수/적용 중/미적용, 실행 중 켜고 끌 수 있나 */
  flow: 'chat' | 'ingest' | 'eval';
  step: string;
  state: 'required' | 'on' | 'off';
  switchable: boolean;
  off_reason: string | null;
  changed: { by: string | null; at: string | null } | null;
  /** 검증된 최적 값(스위치만) — 지금 값이 이것과 다르면 "최적에서 벗어남" */
  baseline_on: boolean | null;
  baseline_warn: string | null;
}

export interface TechniqueBaseline { optimal: boolean; off: string[]; evidence: string }
export const getTechniqueBaseline = () => apiFetch<TechniqueBaseline>('/eval/techniques/baseline');
export const restoreTechniqueBaseline = () => apiFetch<{ restored: string[]; optimal: boolean }>('/eval/techniques/baseline/restore', { method: 'POST' });

export async function listTechniques(): Promise<Technique[]> {
  return apiFetch<Technique[]>('/eval/techniques');
}

/** 기법 켜고 끄기(관리자) — 바로 적용, 답변 캐시 비움 */
export async function switchTechnique(key: string, on: boolean): Promise<{ key: string; on: boolean; by: string | null; at: string }> {
  return apiFetch(`/eval/techniques/${key}/switch`, { method: 'PUT', body: JSON.stringify({ on }) });
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
  n_failed: number; cause: string;
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
  axis?: GateAxis;
  gate_run_id: number; gate_label: string; improvement_id: number; diagnosed_at: string;
  score: { total: number | null; locked: number | null; locked_n: number; retrieval: number | null; n: number };
  failed: ReportRow[]; techniques: ReportTechnique[];
  unmeasured: { store: string; n: number; total: number; ref: string; qids: string[] }[];
  diagnosed_gate_run_id: number; files_missing: string[];
}

/** 축(정책서·업무지식)마다 같은 모양 — 평가 게이트 한 템플릿(10/29) */
export async function getGateReport(axis: GateAxis = 'policy'): Promise<GateReport | null> {
  return apiFetch<GateReport | null>(`/eval/report?axis=${axis}`);
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

/** 운영 번복 요약(10/27, 감사 로그 위 요약·자가 개선 입력) — 종류별 판단·번복·이유·패턴 + 위치 판정 리플레이 + 번복으로 만든 후보 */
/** 사람 검토 결과(HITL, 11/04) — 수용 · 번복 · 자동 적용(사람 손 없이) · 규칙 적용(사람이 정한 규칙, AI 판단 미사용) · 검토 대기 */
export type ReviewStatus = 'accept' | 'override' | 'auto' | 'rule' | 'pending';
export interface OverrideKind {
  /** decided = 사람 검토 수(수용 + 번복) — 번복률 분모 */
  kind: string; kind_name: string; decided: number; overrides: number; rate: number | null;
  review: Record<ReviewStatus, number>;
  /** 표본 감사(spot check) — 다시 본 수 · 그중 번복 */
  spot: { n: number; overridden: number; auto_n: number; auto_overridden: number };
  /** 관리자가 검토하는 종류(등록 위치·원문 확인·중복 의심) — 요약 번복률·검토 대기는 이것만(되묻기·제안은 사용자 선택) */
  admin_reviewed: boolean;
  reasons: Record<string, number>; patterns: { pattern: string; n: number }[]; rubber_stamp: boolean;
}
export interface OverrideReport {
  days: number; kinds: OverrideKind[];
  location_replay: { rules: { rule: string; hit: number; n: number; auto_wrong: number; fixed: number[]; broken: number[] }[]; best: { rule: string } | null };
  candidates: { id: number; title: string; status: CandidateStatus; qids: string[] }[];
  /** 날짜별(최신 먼저) — 그날 AI 판단 수 · 그중 번복(11/03 날짜 카드) */
  daily: { day: string; n: number; overrides: number }[];
}

/** 평가 게이트 › 적용 이력(10/27) — 기법 대장 근거(측정 기록) + 개선 후보 사람 결정, 날짜 내림차순 */
export interface HistoryItem {
  date: string; stage: string; technique: string; technique_name: string; what: string;
  gate_run_id: number | null; gate: { id: number; label: string; verdict: 'pass' | 'fail' | null } | null;
  result: '반영' | '기각' | '보류' | '실험 중' | '실패' | null; now?: '안 함' | '꺼짐' | null;   // 서버 history.RESULT_NAME(10/30)
  reason?: string | null; candidate_id?: number; source: 'registry' | 'decision';
}

export async function getHistory(limit = 200): Promise<HistoryItem[]> {
  return apiFetch<HistoryItem[]>(`/eval/history?limit=${limit}`);
}

export async function getOverrides(days = 30): Promise<OverrideReport> {
  return apiFetch<OverrideReport>(`/eval/overrides?days=${days}`);
}

/** AI 감사 로그 한 줄(10/27) — 원장 시간순. ai/human = 서버가 만든 한 줄 설명(원문 없음), open = 아직 사람·사용자 확정 전 */
export interface DecisionLogItem {
  id: number; kind: string; kind_name: string; at: string; decided_at: string | null; confident: boolean; changed: boolean; open: boolean;
  ai: string; ai_reason: string | null; human: string | null; asked_by: string | null; changed_by: string | null; rule: string | null;
  verdict: ChangedDecision['verdict']; verdict_reason: string | null;
  override_reason: 'wrong' | 'both_ok' | 'context_changed' | null;   // 번복 순간에 고른 이유(지식 등록, 선택)
  candidate: { id: number; title: string; status: CandidateStatus } | null;   // 이 번복으로 만든 개선 후보
  review: ReviewStatus;
  spot: 'accepted' | 'overridden' | null; spot_reason: 'wrong' | 'both_ok' | 'context_changed' | null; spot_by: string | null; spot_at: string | null;
}

export async function getDecisionLog(q: { days: number; kind?: string; changedOnly?: boolean; before?: number | null; limit?: number; day?: string | null; review?: ReviewStatus | null }):
  Promise<{ items: DecisionLogItem[]; next_before: number | null }> {
  const p = new URLSearchParams({ days: String(q.days), limit: String(q.limit ?? 50) });
  if (q.kind) p.set('kind', q.kind);
  if (q.changedOnly) p.set('changed_only', 'true');
  if (q.before) p.set('before', String(q.before));
  if (q.day) p.set('day', q.day);
  if (q.review) p.set('review', q.review);
  return apiFetch(`/eval/decisions/log?${p.toString()}`);
}

/** 표본 감사(spot check, 11/04) — 아직 안 본 자동 적용·수용 판단 무작위 n건, left = 남은 대상 수 */
export async function getSpotCheck(q: { days: number; kind?: string; n?: number }): Promise<{ items: DecisionLogItem[]; left: number }> {
  const p = new URLSearchParams({ days: String(q.days), n: String(q.n ?? 10) });
  if (q.kind) p.set('kind', q.kind);
  return apiFetch(`/eval/decisions/spot-check?${p.toString()}`);
}

export async function markSpotCheck(id: number, result: 'accepted' | 'overridden', reason?: 'wrong' | 'both_ok' | 'context_changed' | null) {
  return apiFetch<{ id: number; spot: string }>(`/eval/decisions/${id}/spot-check`, { method: 'POST', body: JSON.stringify({ result, reason: reason ?? null }) });
}

/** AI가 본 근거(10/28, 옛 검색 미리보기) — 채팅과 같은 검색(LLM 0, 로그인 사용자). 정책 줄의 logical_id로 [시험지에 넣기](관리자) */
export interface PreviewItem {
  kind: 'policy' | 'knowledge' | 'refdata' | 'ref'; how: string; rank?: number; name: string; path: string; score: number | null;
  label: string | null; logical_id?: number; id?: number; adopted?: boolean;
}
export interface PreviewResult {
  namespace: string; question: string;
  verdict: { key: 'clarify' | 'abstain' | 'list' | 'empty' | 'answer'; name: string; detail: string | null; options: string[] };
  terms: string[]; mapped_term: string | null; signals: Record<string, number | boolean>; context_chars: number; items: PreviewItem[];
}

export async function previewSearch(namespace: string, question: string): Promise<PreviewResult> {
  return apiFetch<PreviewResult>('/eval/preview', { method: 'POST', body: JSON.stringify({ namespace, question }) });
}

export async function addPreviewQuestion(namespace: string, question: string, logicalId: number): Promise<{ id: number; qid: string; created: boolean }> {
  return apiFetch('/eval/preview/questions', { method: 'POST', body: JSON.stringify({ namespace, question, logical_id: logicalId }) });
}
