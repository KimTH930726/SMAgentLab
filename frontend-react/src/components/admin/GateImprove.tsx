import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ChevronDown, ChevronRight } from 'lucide-react';
import {
  adoptCandidate, getBaselineRun, listGateRuns, markHypothesis, reviewCandidate, startExperiment,
  type Candidate, type CandidateStatus, type GateAxis, type GateRunListItem, type Hypothesis,
} from '../../api/eval';
import { showError } from '../../store/useDialogStore';
import { FormModal } from '../ui/FormModal';
import { Modal } from '../ui/Modal';
import { QidList } from './GateRuns';
import { lockedMove } from '../../utils/evalLayers';

/**
 * 개선 후보 카드(v2.171 [개선] 탭 → v2.172 틀린 문항 리포트의 "고칠 방법"을 펼치면 보인다). 버튼 = 누르면 무슨 일(실험해 보기 / 나중에 / 안 하기 /
 * 운영에 반영했어요), 진행 단계 막대(제안 → 실험 → 측정 → 운영 반영). 진단·후보·숫자는 scripts/eval_improve.py 기록을 읽기만.
 * AI 묶음은 사람이 [맞아요]/[다시 묶기] — 맞아요 한 이름은 다음 진단이 그대로 쓴다(C1).
 */

type Action = Exclude<CandidateStatus, 'adopted'>;

const RECOMMEND: Record<Candidate['recommend'], { label: string; cls: string; hint: string }> = {
  try: { label: 'AI 추천: 해 볼 만함', cls: 'text-emerald-600 dark:text-emerald-400', hint: 'LLM 없이 다시 계산했더니 맞던 답을 깨지 않고 고치는 문항이 있음' },
  reject: { label: 'AI 추천: 안 하는 게 좋음', cls: 'text-rose-600 dark:text-rose-400', hint: 'LLM 없이 다시 계산했더니 고치는 문항이 없거나 맞던 답이 깨짐' },
  review: { label: 'AI 추천: 재 봐야 앎', cls: 'text-slate-400', hint: '측정해 봐야 아는 후보 — 실험할지 사람이 정함' },
};

const MEASURE: Record<Candidate['measure'], { label: string; pass: string; next: string }> = {
  replay: { label: '이미 계산함(LLM 없음)', pass: '고침 > 0 · 망가짐 0이면 해 볼 만함', next: '설정을 바꾼 뒤 검색·답을 다시 재서 판정 기록에 --candidate로 연결' },
  retrieval: { label: '검색만 다시 재기(몇 분)', pass: '검색 나빠짐 0이면 통과', next: 'eval_golden_v0.py --details … → eval_gate.py --save "…" --candidate <번호>' },
  answers: { label: '답을 다시 재기(로컬 LLM ~1시간)', pass: '답이 의미 있게 떨어지지 않으면 통과', next: 'eval_answers.py collect … → judge → eval_gate.py --save "…" --candidate <번호>' },
};

const STEPS = ['제안', '실험', '측정', '운영 반영'] as const;

// 카드 머리 배지 — 지금 결정 상태(10/27 가독성 목업 ①: 제목 옆에 한눈에)
export const STATUS_BADGE: Record<CandidateStatus, { label: string; cls: string }> = {
  proposed: { label: '결정 전', cls: 'bg-slate-700 text-slate-300' },
  approved: { label: '실험 중', cls: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300' },
  held: { label: '나중에', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300' },
  rejected: { label: '안 하기로 함', cls: 'bg-slate-700 text-slate-400' },
  adopted: { label: '운영 반영', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' },
};

function stepOf(c: Candidate): number {
  if (c.status === 'adopted') return 3;
  if (c.status === 'approved') return c.measurements.length ? 2 : 1;
  return 0;   // 제안·나중에·안 하기 — 예전 측정이 있어도 지금 실험 중은 아님(리뷰 L6, 측정은 막대 옆에 따로)
}

/** 서버 confident_wrong_error와 같은 조건 — 답을 잰 측정이면 단정 오답이 안 늘고 2회 이상 잰 것만(10/30 B안). 검색만 잰 측정은 해당 없음 */
function confidentWrongBlocked(r: { answers_groups: unknown; answers_confident_wrong?: GateRunListItem['answers_confident_wrong'] }): boolean {
  if (r.answers_groups == null) return false;
  const cw = r.answers_confident_wrong;
  if (!cw || cw.before == null || cw.after == null) return true;
  return Math.min(cw.reps_before, cw.reps_after) < 2 || cw.after > cw.before;
}

const pct = (x: number | null | undefined) => (x == null ? '-' : `${(x * 100).toFixed(1)}%`);
const pp = (x: number | null | undefined) => (x == null ? '' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(1)}%p`);
const fmtDate = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

function Stepper({ c }: { c: Candidate }) {
  const at = stepOf(c);
  const last = c.measurements[c.measurements.length - 1];
  const side = c.status === 'rejected' ? '안 하기로 함' : c.status === 'held' ? '나중에 보기로 함' : null;
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs" aria-label="진행 단계">
      {STEPS.map((s, i) => (
        <span key={s} className="flex items-center gap-2">
          <span className={i <= at && !side ? 'text-indigo-600 dark:text-indigo-400 font-medium' : 'text-slate-500'}>
            {i <= at && !side ? '●' : '○'} {s}
            {i === 2 && last && at >= 2 && (
              <span className={last.verdict === 'pass' ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}>
                {' '}#{last.id} {last.verdict === 'pass' ? '통과' : '실패'}{last.diff != null ? ` ${pp(last.diff)}` : ''}
              </span>
            )}
          </span>
          {i < STEPS.length - 1 && <span className="text-slate-600">─</span>}
        </span>
      ))}
      {side && <span className="ml-2 text-amber-600 dark:text-amber-400">· {side}</span>}
      {at < 2 && last && (
        <span className="ml-2 text-slate-500" title="이 후보를 잰 예전 판정 — 지금 실험 중이라는 뜻은 아님">
          · 예전 측정 #{last.id} {last.verdict === 'pass' ? '통과' : '실패'}
        </span>
      )}
    </div>
  );
}

/** 실험 상태 한 줄 — 도는 중(단계·진행) / 실패(이유 + 다시 돌리기) / 자동 실험 불가(이유 + 손으로 할 일). 운영은 안 바뀐다 */
function ExperimentStatus({ c, next, isAdmin, onRetry, retrying }: {
  c: Candidate; next: string; isAdmin: boolean; onRetry: () => void; retrying: boolean;
}) {
  const j = c.experiment;
  if (j && (j.status === 'queued' || j.status === 'running')) {
    return (
      <div className="rounded-lg border border-indigo-300 dark:border-indigo-500/40 bg-indigo-50/60 dark:bg-indigo-500/10 px-3 py-2 text-xs" data-testid="cand-exp-running">
        <p className="text-indigo-700 dark:text-indigo-300 font-medium">
          {j.status === 'queued' ? '실험 대기 중 — 앞 실험이 끝나면 시작' : `실험 돌리는 중 — ${j.stage ?? ''}`}
          {j.status === 'running' && j.total > 0 && <span className="tabular-nums"> {j.done}/{j.total}</span>}
        </p>
        <p className="text-slate-500 mt-0.5">지금 설정과 바꾼 설정을 같은 골든셋으로 재고 게이트로 판정해요 — 운영은 그대로. 끝나면 아래에 결과가 붙어요</p>
        {j.status === 'running' && j.total > 0 && (
          <span className="mt-1.5 block h-1.5 rounded-full bg-slate-700 overflow-hidden" aria-hidden>
            <span className="block h-full bg-indigo-500 dark:bg-indigo-400" style={{ width: `${(j.done / j.total) * 100}%` }} />
          </span>
        )}
      </div>
    );
  }
  if (j && j.status === 'failed' && c.status === 'approved') {
    return (
      <div className="rounded-lg border border-rose-300 dark:border-rose-500/40 bg-rose-50/60 dark:bg-rose-500/10 px-3 py-2 text-xs space-y-1" data-testid="cand-exp-failed">
        <p className="text-rose-700 dark:text-rose-300 font-medium">실험 실패 #{j.id}</p>
        <p className="text-slate-400 break-words">{j.error}</p>
        {isAdmin && c.auto?.ok && <button className="text-indigo-600 dark:text-indigo-400 hover:underline disabled:opacity-50" disabled={retrying} onClick={onRetry}>다시 실행</button>}
      </div>
    );
  }
  // 승인됐는데 작업이 없거나(옛 승인·이어받은 승인) 끝난 뒤 다시 재고 싶을 때 — 시작 버튼(리뷰 10/30 M2)
  if (c.status === 'approved' && c.auto?.ok && isAdmin && (!j || j.status === 'done')) {
    return (
      <p className="text-xs text-slate-400" data-testid="cand-exp-start">
        {j ? `실험 #${j.id} 완료` : '실험 대기 전'}
        <button className="ml-2 text-indigo-600 dark:text-indigo-400 hover:underline disabled:opacity-50" disabled={retrying} onClick={onRetry}>
          {j ? '다시 실행' : '실험 실행'}</button>
      </p>
    );
  }
  if (c.status === 'approved' && !c.measurements.length && c.auto && !c.auto.ok) {
    return <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="cand-exp-manual" title={next}>자동 실험 불가 — {c.auto.reason} · 손으로 할 일: {next}</p>;
  }
  return null;
}

/** 실험 결과(10/30 사용자 "측정 결과를 사람이 보고 운영 반영할지 정해야") — 가장 최근 측정의 전/후 한 표 + 운영 반영 가능 여부(서버 판정).
 * 업계 실험 비교 화면처럼 정답률·단정 오답·거절·잠근 문항·검색을 나란히. 측정 전이면 무엇을 기다리는지 한 줄 */
function ExperimentResult({ c }: { c: Candidate }) {
  const last = c.measurements[c.measurements.length - 1];
  if (!last) return null;
  const t = last.groups?.['전체'];
  const cw = last.confident_wrong;
  const lockedRows = ['human', 'real'].map((k) => [k, last.groups?.[k]] as const).filter(([, g]) => g);
  const row = (name: string, b: number | null | undefined, a: number | null | undefined, goodWhenUp: boolean) => {
    if (b == null || a == null) return null;
    const d = a - b;
    const tone = Math.abs(d) < 0.0005 ? 'text-slate-400' : (d > 0) === goodWhenUp ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400';
    return (
      <tr key={name} className="border-t border-slate-700/60">
        <td className="py-1 pr-4 text-slate-400">{name}</td>
        <td className="py-1 pr-3 text-right tabular-nums text-slate-300">{pct(b)}</td>
        <td className="py-1 pr-3 text-slate-500">→</td>
        <td className="py-1 pr-4 text-right tabular-nums text-slate-100 font-medium">{pct(a)}</td>
        <td className={`py-1 text-right tabular-nums ${tone}`}>{pp(d) || '±0'}</td>
      </tr>
    );
  };
  const chk = c.adopt_check;
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/40 p-3 space-y-2" data-testid="cand-result">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="text-xs font-semibold text-slate-200">실험 결과</p>
        <span className="text-xs text-slate-500" title={last.label}>측정 #{last.id} · {fmtDate(last.run_at)}{cw ? ` · 답 ${cw.reps_after}회` : ''}</span>
        <span className={`ml-auto text-xs font-medium ${last.verdict === 'pass' ? 'text-emerald-600 dark:text-emerald-400' : last.verdict === 'fail' ? 'text-rose-600 dark:text-rose-400' : 'text-slate-400'}`}>
          {last.verdict === 'pass' ? '게이트 통과' : last.verdict === 'fail' ? '게이트 실패' : '판정 없음'}</span>
      </div>
      <table className="text-xs w-full max-w-md">
        <tbody>
          {row('정답률', t?.before, t?.after, true)}
          {row('단정 오답', cw?.before, cw?.after, false)}
          {row('거절', cw?.refused_before, cw?.refused_after, false)}
          {lockedRows.map(([k, g]) => row(k === 'human' ? '사람이 쓴 질문' : '실사용 질문', g!.before, g!.after, true))}
          {last.retrieval && row('검색(정답 정책을 문맥에)', last.retrieval.before / Math.max(1, last.retrieval.n), last.retrieval.after / Math.max(1, last.retrieval.n), true)}
        </tbody>
      </table>
      {!last.has_answers && <p className="text-[11px] text-slate-500">검색만 잰 측정 — 답은 안 쟀어요</p>}
      {c.status === 'approved' && chk && (
        <p className={`text-xs ${chk.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-300'}`} data-testid="cand-adopt-check">
          {chk.ok ? '→ 운영 반영 가능 — 운영 설정을 바꾼 뒤 [운영에 반영했어요]' : `→ 운영 반영 불가: ${chk.reason}`}</p>
      )}
    </div>
  );
}

function Hypotheses({ c, improvementId, isAdmin, onPick }: {
  c: Candidate; improvementId: number; isAdmin: boolean; onPick?: (qids: string[], i: number) => void;
}) {
  const qc = useQueryClient();
  const mark = useMutation({
    mutationFn: ({ h, status }: { h: Hypothesis; status: 'confirmed' | 'rejected' | 'open' }) => markHypothesis(improvementId, c.cause, h.name, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['eval-improvement'] }),
    onError: (e) => showError(e, '묶음 확인을 저장하지 못했습니다'),
  });
  if (!c.hypotheses.length) return null;
  return (
    <div className="text-xs">
      <p className="text-slate-400 mb-1" title="틀린 문항의 채점 이유를 로컬 LLM이 패턴별로 묶은 것 — 맞으면 [맞아요](다음 진단도 이 이름으로), 아니면 [다시 묶기]">
        왜 틀렸나 — AI가 묶은 패턴 ⓘ
      </p>
      <div className="rounded-lg border border-slate-700 divide-y divide-slate-700/70">
      {c.hypotheses.map((h) => (
        <div key={h.name} className="px-3 py-1.5 flex flex-wrap items-start gap-2">
          <div className="min-w-0 flex-1"><QidList title={h.name} qids={h.qids} hint={h.hint || h.name} onPick={onPick} /></div>
          {h.status === 'confirmed' && <span className="text-emerald-600 dark:text-emerald-400"><Check className="inline w-3 h-3" /> 맞음</span>}
          {h.status === 'rejected' && <span className="text-amber-600 dark:text-amber-400">다음 진단에서 다시 묶음</span>}
          {isAdmin && (
            <span className="flex gap-2">
              {h.status !== 'confirmed' && (
                <button className="text-emerald-600 dark:text-emerald-400 hover:underline" title="이 묶음이 맞아요 — 다음 진단도 이 이름으로 묶습니다"
                  disabled={mark.isPending} onClick={() => mark.mutate({ h, status: 'confirmed' })}>맞아요</button>
              )}
              {h.status !== 'rejected' && (
                <button className="text-amber-600 dark:text-amber-400 hover:underline" title="묶음이 이상해요 — 다음 진단에서 이 칸을 새로 묶습니다"
                  disabled={mark.isPending} onClick={() => mark.mutate({ h, status: 'rejected' })}>다시 묶기</button>
              )}
              {h.status && h.status !== 'open' && (
                <button className="text-slate-400 hover:underline" disabled={mark.isPending} onClick={() => mark.mutate({ h, status: 'open' })}>취소</button>
              )}
            </span>
          )}
        </div>
      ))}
      </div>
    </div>
  );
}

function ReplayTable({ c }: { c: Candidate }) {
  const [open, setOpen] = useState(false);
  if (!c.replay) return null;
  return (
    <div className="text-xs">
      <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 text-slate-300 hover:text-slate-100"
        title="저장된 검색 신호로 실제 판정 함수를 다시 돌린 결과 — 다른 답은 그대로라는 가정">
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        다시 계산한 표 ({c.replay.grid.length}가지)
      </button>
      {open && (
        <div className="overflow-x-auto">
          <table className="mt-1 ml-5 text-slate-300">
            <thead>
              <tr className="text-slate-500">
                <th className="text-left pr-4 font-medium">하한</th><th className="text-right pr-4 font-medium">거절</th>
                <th className="text-right pr-4 font-medium">고침</th><th className="text-right pr-4 font-medium">깨짐</th>
                <th className="text-right font-medium">정답률</th>
              </tr>
            </thead>
            <tbody>
              {c.replay.grid.map((r) => (
                <tr key={`${r.min_score}-${r.min_param_rank}`}>
                  <td className="pr-4">{r.min_score.toFixed(2)} / {r.min_param_rank.toFixed(2)}</td>
                  <td className="text-right pr-4">{r.refused}</td>
                  <td className="text-right pr-4">{r.fixed}</td>
                  <td className={`text-right pr-4 ${r.broken ? 'text-rose-600 dark:text-rose-400' : ''}`}>{r.broken}</td>
                  <td className="text-right">{pct(r.before)} → {pct(r.after)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function CandidateCard({ c, improvementId, isAdmin, onPick, hideQids = false, axis = 'policy', hideTitle = false }: {
  c: Candidate; improvementId: number; isAdmin: boolean; onPick?: (qids: string[], i: number) => void;
  hideTitle?: boolean;  // 모달 제목에 이미 후보 이름이 있을 때
  axis?: GateAxis;     // 후보를 진단한 축 — 운영 반영도 그 축 측정·기준선만(서버도 막음, 10/29 업무지식도 같은 루프)
  hideQids?: boolean;   // 리포트는 옆에 겨냥 문항을 페이지로 따로 보여 준다(10/27)
}) {
  const qc = useQueryClient();
  const [modal, setModal] = useState<null | 'rejected' | 'held' | 'adopt'>(null);
  const [reason, setReason] = useState('');
  const [gateId, setGateId] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['eval-improvement'] });
    qc.invalidateQueries({ queryKey: ['eval-techniques'] });   // 답 기법 화면의 "걸린 후보" 상태도(10/26 리뷰 L5)
    qc.invalidateQueries({ queryKey: ['eval-report'] });       // 틀린 문항 리포트의 기법 상태·지금 점수(반영하면 기준선이 옮겨짐)
    qc.invalidateQueries({ queryKey: ['eval-gate-baseline'] });
    qc.invalidateQueries({ queryKey: ['eval-question'] });     // 문항 상세의 고칠 방법 상태 칩(재리뷰 10/30 L2)
  };
  const review = useMutation({
    mutationFn: ({ status, why }: { status: Action; why?: string }) => reviewCandidate(c.id, status, why),
    onSuccess: () => { setModal(null); setReason(''); refresh(); },
    onError: (e) => (modal ? setErr((e as Error).message) : showError(e, '결정을 저장하지 못했습니다')),
  });
  const retry = useMutation({
    mutationFn: () => startExperiment(c.id),
    onSuccess: () => refresh(),
    onError: (e) => showError(e, '실험을 다시 돌리지 못했습니다'),
  });
  const adopt = useMutation({
    mutationFn: (id: number) => adoptCandidate(c.id, id),
    onSuccess: () => { setModal(null); refresh(); },
    onError: (e) => setErr((e as Error).message),
  });
  // 운영 반영에 연결할 측정 — 실험 시작(승인) 뒤에 잰 통과 판정만(서버 adopt_error와 같은 조건)
  const runs = useQuery({ queryKey: ['eval-gate-runs', axis], queryFn: () => listGateRuns(50, axis), enabled: modal === 'adopt' });
  const base = useQuery({ queryKey: ['eval-gate-baseline', axis], queryFn: () => getBaselineRun(axis), enabled: modal === 'adopt' });
  // 서버 adopt_error와 같은 조건 — 이 후보를 잰 측정 · 통과 · 실험 시작 뒤 · 지금 점수보다 예전 아님 · 잠근 시험지에서 내려가지 않음
  // (반영하면 그 측정이 지금 점수가 된다 — 남의 측정·옛 측정으로 되돌아가지 않게, v2.172 리뷰 M3)
  const measured = new Set(c.measurements.map((m) => m.id));
  const baseAt = base.data && base.data.id ? new Date(base.data.run_at) : null;
  const eligible = (runs.data ?? []).filter((r) => measured.has(r.id) && r.kind === 'gate' && r.verdict === 'pass' && !!c.reviewed_at
    && new Date(r.run_at) >= new Date(c.reviewed_at) && (!baseAt || r.is_baseline || new Date(r.run_at) >= baseAt)
    && !lockedMove(r.answers_groups)?.down && !confidentWrongBlocked(r));
  const rec = RECOMMEND[c.recommend];
  const m = MEASURE[c.measure];
  const open = (x: 'rejected' | 'held' | 'adopt') => { setErr(null); setReason(''); setGateId(null); setModal(x); };
  const btn = 'text-xs hover:underline disabled:opacity-50';

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-3" data-testid={`candidate-${c.key}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${STATUS_BADGE[c.status].cls}`} data-testid="cand-status">{STATUS_BADGE[c.status].label}</span>
        {!hideTitle && <p className="text-sm font-semibold text-slate-100 min-w-0">{c.title}</p>}
        <span className="text-xs text-slate-500" title="평가 게이트 › 적용 이력의 항목">{c.technique_name}</span>
      </div>
      {/* 항목 줄 — 무엇을 바꿔(맨 위·크게) · 왜 · 어떻게 재고 · AI는 어떻게 보나(10/30 사용자: 같은 크기 세 칸은 무엇을 실험하라는지 안 들어옴) */}
      <dl className="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-4 gap-y-2.5 border-y border-slate-700 py-3" data-testid="cand-rows">
        <dt className="text-xs text-slate-500 pt-0.5">무엇을 바꾸나</dt>
        <dd className="text-base font-semibold text-slate-100 break-words">{c.action}</dd>
        <dt className="text-xs text-slate-500 pt-0.5">왜</dt>
        <dd className="text-sm text-slate-200 break-words">
          틀린 {c.n_target}문항{c.why ? <> — {c.why}</> : null}
          {!hideQids && <div className="mt-1"><QidList title="문항 보기" qids={c.qids} hint="이 후보가 겨냥한 틀린 문항 — 눌러서 질문·답·이유 보기" onPick={onPick} /></div>}
        </dd>
        <dt className="text-xs text-slate-500 pt-0.5">어떻게 재나</dt>
        <dd className="text-sm text-slate-200">{m.label} <span className="text-slate-400">→ {m.pass}</span></dd>
        <dt className="text-xs text-slate-500 pt-0.5">AI 추천</dt>
        <dd className="text-sm"><span className={`font-medium ${rec.cls}`}>{rec.label.replace('AI 추천: ', '')}</span> <span className="text-xs text-slate-500">{rec.hint}</span></dd>
      </dl>
      <Hypotheses c={c} improvementId={improvementId} isAdmin={isAdmin} onPick={onPick} />
      <ReplayTable c={c} />
      <ExperimentStatus c={c} next={m.next} isAdmin={isAdmin} onRetry={() => retry.mutate()} retrying={retry.isPending} />
      <ExperimentResult c={c} />
      {c.status === 'adopted' && <p className="text-xs text-emerald-600 dark:text-emerald-400">판정 #{c.adopted_gate_run_id} "{c.adopted_gate_label}"로 운영 반영 완료</p>}
      {c.review_reason && c.status !== 'adopted' && (
        <div className="rounded-lg border border-slate-700 bg-slate-900/40 px-3 py-2 text-xs" data-testid="cand-reason">
          <p className="text-[11px] text-slate-500">결정 사유{c.reviewed_at ? ` · ${fmtDate(c.reviewed_at)}` : ''}{c.carried_from_id ? ' · 이전 진단의 결정 이어받음' : ''}</p>
          <p className="text-slate-200 break-words">{c.review_reason}</p>
        </div>
      )}

      <Stepper c={c} />
      {isAdmin && c.status !== 'adopted' && (
        <div className="flex flex-wrap gap-4">
          {(c.status === 'proposed' || c.status === 'held') && (
            <button className={`${btn} text-indigo-600 dark:text-indigo-400 font-medium`} disabled={review.isPending}
              title="지금 설정과 바꾼 설정을 골든셋으로 재고 게이트로 판정합니다(자동 실험이 되는 후보) — 운영은 안 바뀝니다"
              onClick={() => review.mutate({ status: 'approved' })}>실험해 보기</button>
          )}
          {c.status === 'approved' && (
            <button className={`${btn} text-emerald-600 dark:text-emerald-400 font-medium`}
              title="설정·프롬프트를 운영에 실제로 바꾼 뒤, 통과한 측정과 연결해 '반영 완료'로 남깁니다"
              onClick={() => open('adopt')}>운영에 반영했어요…</button>
          )}
          {c.status !== 'held' && c.status !== 'rejected' && (
            <button className={`${btn} text-amber-600 dark:text-amber-400`} title="목록에 두되 지금은 안 합니다 — 다음 진단에도 '나중에'로 이어집니다"
              onClick={() => open('held')}>나중에…</button>
          )}
          {c.status !== 'rejected' && (
            <button className={`${btn} text-rose-600 dark:text-rose-400`} title="하지 않습니다(이유 필수) — 다음 진단이 같은 후보를 다시 묻지 않습니다"
              onClick={() => open('rejected')}>안 하기…</button>
          )}
          {c.status !== 'proposed' && (
            <button className={`${btn} text-slate-400`} disabled={review.isPending} title="결정을 지우고 처음(제안) 상태로"
              onClick={() => review.mutate({ status: 'proposed' })}>결정 취소</button>
          )}
        </div>
      )}

      <FormModal zClass="z-[60]"
        isOpen={modal === 'rejected' || modal === 'held'}
        title={modal === 'rejected' ? '안 하기' : '나중에'}
        onClose={() => setModal(null)}
        onSubmit={() => review.mutate({ status: modal as Action, why: reason })}
        submitLabel={modal === 'rejected' ? '안 하기' : '나중에'}
        submitting={review.isPending}
        submitDisabled={modal === 'rejected' && !reason.trim()}
        danger={modal === 'rejected'}
        error={err}
      >
        <label htmlFor={`cand-${c.id}-reason`} className="block text-xs text-slate-400">
          {modal === 'rejected' ? '안 하는 이유 (다음 진단에서 같은 후보가 나오면 이 이유가 보입니다)' : '나중으로 미루는 이유 (선택)'}
        </label>
        <textarea id={`cand-${c.id}-reason`} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus
          className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
      </FormModal>

      <FormModal zClass="z-[60]"
        isOpen={modal === 'adopt'}
        title="운영에 반영했어요 — 통과한 측정 연결"
        onClose={() => setModal(null)}
        onSubmit={() => gateId != null && adopt.mutate(gateId)}
        submitLabel="반영 완료로 기록"
        submitting={adopt.isPending}
        submitDisabled={gateId == null}
        error={err}
      >
        <p className="text-xs text-slate-400">이 후보를 실험 시작 뒤에 잰 측정 중 통과한 것만 고를 수 있어요(답을 쟀으면 2회 이상 · 단정 오답이 안 늘어난 것). 고른 측정이 <b>지금 점수</b>가 됩니다.
          운영 설정·프롬프트는 이 버튼이 바꾸지 않습니다 — 바꾼 뒤 눌러 주세요.</p>
        {runs.isLoading || base.isLoading ? <p className="text-xs text-slate-500">불러오는 중…</p> : eligible.length === 0 ? (
          <p className="text-xs text-slate-500">연결할 측정이 없습니다 — 먼저 재고 <code>eval_gate.py --save … --candidate {c.id}</code>.</p>
        ) : (
          <div className="space-y-1">
            {eligible.map((r) => (
              <label key={r.id} className="flex items-center gap-2 text-sm text-slate-200">
                <input type="radio" name={`adopt-${c.id}`} checked={gateId === r.id} onChange={() => setGateId(r.id)} />
                #{r.id} {r.label} <span className="text-xs text-slate-500">{fmtDate(r.run_at)}</span>
              </label>
            ))}
          </div>
        )}
      </FormModal>
    </div>
  );
}

/** 후보 모달 — 후보를 여는 곳(② 진행 중인 개선 · ① 원인 안 개선 · 문항 상세 고칠 방법)이 모두 이 한 모양(10/30 사용자: 모달·포맷 통일).
 * 제목 = 후보 이름, 본문 = 후보 카드(항목 줄 → 단계 → 결정). zClass는 연 자리 모달보다 한 칸 위로 */
export function CandidateModal({ isOpen, onClose, c, improvementId, axis, isAdmin, onPick, zClass = 'z-40' }: {
  isOpen: boolean; onClose: () => void; c: Candidate; improvementId: number; axis: GateAxis; isAdmin: boolean;
  onPick?: (qids: string[], i: number) => void; zClass?: string;
}) {
  return (
    <Modal isOpen={isOpen} onClose={onClose} title={c.title} maxWidth="max-w-3xl" zClass={zClass}>
      <CandidateCard c={c} improvementId={improvementId} isAdmin={isAdmin} axis={axis} onPick={onPick} hideTitle />
    </Modal>
  );
}
