import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ChevronDown, ChevronRight } from 'lucide-react';
import {
  adoptCandidate, getBaselineRun, listGateRuns, markHypothesis, reviewCandidate,
  type Candidate, type CandidateStatus, type Hypothesis,
} from '../../api/eval';
import { showError } from '../../store/useDialogStore';
import { FormModal } from '../ui/FormModal';
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

const MEASURE: Record<Candidate['measure'], { label: string; next: string }> = {
  replay: { label: '이미 계산함(LLM 없음)', next: '설정을 바꾼 뒤 검색·답을 다시 재서 판정 기록에 --candidate로 연결' },
  retrieval: { label: '검색만 다시 재면 됨(몇 분)', next: 'eval_golden_v0.py --details … → eval_gate.py --save "…" --candidate <번호>' },
  answers: { label: '답을 다시 재야 함(로컬 LLM ~1시간)', next: 'eval_answers.py collect … → judge → eval_gate.py --save "…" --candidate <번호>' },
};

const STEPS = ['제안', '실험', '측정', '운영 반영'] as const;

// 카드 머리 배지 — 지금 결정 상태(10/27 가독성 목업 ①: 제목 옆에 한눈에)
const STATUS_BADGE: Record<CandidateStatus, { label: string; cls: string }> = {
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

export function CandidateCard({ c, improvementId, isAdmin, onPick, hideQids = false }: {
  c: Candidate; improvementId: number; isAdmin: boolean; onPick?: (qids: string[], i: number) => void;
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
  };
  const review = useMutation({
    mutationFn: ({ status, why }: { status: Action; why?: string }) => reviewCandidate(c.id, status, why),
    onSuccess: () => { setModal(null); setReason(''); refresh(); },
    onError: (e) => (modal ? setErr((e as Error).message) : showError(e, '결정을 저장하지 못했습니다')),
  });
  const adopt = useMutation({
    mutationFn: (id: number) => adoptCandidate(c.id, id),
    onSuccess: () => { setModal(null); refresh(); },
    onError: (e) => setErr((e as Error).message),
  });
  // 운영 반영에 연결할 측정 — 실험 시작(승인) 뒤에 잰 통과 판정만(서버 adopt_error와 같은 조건)
  // 개선 후보는 정책서 문항으로 진단한 것 — 운영 반영도 정책서 축 측정·기준선만(#96, 서버도 막음)
  const runs = useQuery({ queryKey: ['eval-gate-runs', 'policy'], queryFn: () => listGateRuns(50, 'policy'), enabled: modal === 'adopt' });
  const base = useQuery({ queryKey: ['eval-gate-baseline', 'policy'], queryFn: () => getBaselineRun('policy'), enabled: modal === 'adopt' });
  // 서버 adopt_error와 같은 조건 — 이 후보를 잰 측정 · 통과 · 실험 시작 뒤 · 지금 점수보다 예전 아님 · 잠근 시험지에서 내려가지 않음
  // (반영하면 그 측정이 지금 점수가 된다 — 남의 측정·옛 측정으로 되돌아가지 않게, v2.172 리뷰 M3)
  const measured = new Set(c.measurements.map((m) => m.id));
  const baseAt = base.data && base.data.id ? new Date(base.data.run_at) : null;
  const eligible = (runs.data ?? []).filter((r) => measured.has(r.id) && r.kind === 'gate' && r.verdict === 'pass' && !!c.reviewed_at
    && new Date(r.run_at) >= new Date(c.reviewed_at) && (!baseAt || r.is_baseline || new Date(r.run_at) >= baseAt)
    && !lockedMove(r.answers_groups)?.down);
  const rec = RECOMMEND[c.recommend];
  const m = MEASURE[c.measure];
  const open = (x: 'rejected' | 'held' | 'adopt') => { setErr(null); setReason(''); setGateId(null); setModal(x); };
  const btn = 'text-xs hover:underline disabled:opacity-50';

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-3" data-testid={`candidate-${c.key}`}>
      <div className="space-y-0.5">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-semibold text-slate-100 min-w-0">{c.title}</p>
          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${STATUS_BADGE[c.status].cls}`} data-testid="cand-status">{STATUS_BADGE[c.status].label}</span>
        </div>
        <p className="text-xs text-slate-500" title="평가 게이트 › 적용 이력 › 항목 전체의 항목">{c.technique_name}</p>
      </div>
      <Stepper c={c} />
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
        <div className="rounded-lg border border-slate-700 bg-slate-900/40 px-3 py-2 min-w-0">
          <p className="text-[11px] text-slate-500">AI 추천</p>
          <p className={`font-medium ${rec.cls}`} title={rec.hint}>{rec.label.replace('AI 추천: ', '')}</p>
          {c.why && <p className="text-slate-400 break-words">{c.why}</p>}
        </div>
        <div className="rounded-lg border border-slate-700 bg-slate-900/40 px-3 py-2 min-w-0">
          <p className="text-[11px] text-slate-500">바꿀 것</p>
          <p className="text-slate-200 break-words">{c.action}</p>
        </div>
        <div className="rounded-lg border border-slate-700 bg-slate-900/40 px-3 py-2 min-w-0">
          <p className="text-[11px] text-slate-500">재는 법</p>
          <p className="text-slate-200">{m.label}</p>
        </div>
      </div>
      {!hideQids && <QidList title="고치려는 문항" qids={c.qids} hint="이 후보가 겨냥한 틀린 문항 — 눌러서 질문·답·이유 보기" onPick={onPick} />}
      <Hypotheses c={c} improvementId={improvementId} isAdmin={isAdmin} onPick={onPick} />
      <ReplayTable c={c} />
      {c.status === 'approved' && (
        <p className="text-xs text-indigo-600 dark:text-indigo-400" title={m.next}>다음 할 일: {m.next}</p>
      )}
      {c.status === 'adopted' && <p className="text-xs text-emerald-600 dark:text-emerald-400">판정 #{c.adopted_gate_run_id} "{c.adopted_gate_label}"로 운영 반영 완료</p>}
      {c.review_reason && c.status !== 'adopted' && (
        <div className="rounded-lg border border-slate-700 bg-slate-900/40 px-3 py-2 text-xs" data-testid="cand-reason">
          <p className="text-[11px] text-slate-500">결정 사유{c.reviewed_at ? ` · ${fmtDate(c.reviewed_at)}` : ''}{c.carried_from_id ? ' · 이전 진단의 결정 이어받음' : ''}</p>
          <p className="text-slate-200 break-words">{c.review_reason}</p>
        </div>
      )}

      {isAdmin && c.status !== 'adopted' && (
        <div className="flex flex-wrap gap-4 pt-2 border-t border-slate-700">
          {(c.status === 'proposed' || c.status === 'held') && (
            <button className={`${btn} text-indigo-600 dark:text-indigo-400 font-medium`} disabled={review.isPending}
              title="이 후보를 측정해 볼 대상으로 올립니다 — 운영은 아직 안 바뀝니다. 다음에 돌릴 명령이 보여요"
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

      <FormModal
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

      <FormModal
        isOpen={modal === 'adopt'}
        title="운영에 반영했어요 — 통과한 측정 연결"
        onClose={() => setModal(null)}
        onSubmit={() => gateId != null && adopt.mutate(gateId)}
        submitLabel="반영 완료로 기록"
        submitting={adopt.isPending}
        submitDisabled={gateId == null}
        error={err}
      >
        <p className="text-xs text-slate-400">이 후보를 실험 시작 뒤에 잰 측정 중 통과한 것만 고를 수 있어요. 고른 측정이 <b>지금 점수</b>가 됩니다.
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
