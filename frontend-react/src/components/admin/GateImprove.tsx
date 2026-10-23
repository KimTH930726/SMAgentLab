import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Lightbulb } from 'lucide-react';
import {
  adoptCandidate, getLatestImprovement, listGateRuns, reviewCandidate,
  type Candidate, type CandidateStatus, type Improvement,
} from '../../api/eval';
import { useAuthStore } from '../../store/useAuthStore';
import { showError } from '../../store/useDialogStore';
import { FormModal } from '../ui/FormModal';
import { QidList } from './GateRuns';

/**
 * 평가 게이트 [개선] (v2.170, eval-gate-redesign §10 — 자가 개선 루프, 10/23 사용자 "평가 게이트의 핵심").
 * 정답률 → 왜 틀렸나(흐름의 첫 실패로 원인 칸) → 원인마다 개선 후보(볼 기법 · 바꿀 것 · 잴 방법) → 사람 검수 → 채택은 통과한 판정과 연결.
 * 진단·후보·리플레이 숫자는 scripts/eval_improve.py가 만든 기록을 읽기만 한다. 문항은 번호만, 가설은 로컬 LLM 묶음(사람 확인 전).
 */

const RECOMMEND: Record<Candidate['recommend'], { label: string; cls: string; hint: string }> = {
  try: { label: '시도 추천', cls: 'text-emerald-600 dark:text-emerald-400', hint: 'LLM 없이 다시 잰 결과 망가짐 0으로 고치는 문항이 있음' },
  reject: { label: '기각 추천', cls: 'text-rose-600 dark:text-rose-400', hint: 'LLM 없이 다시 잰 결과 고치는 문항이 없거나 맞던 답이 망가짐' },
  review: { label: '검토', cls: 'text-slate-400', hint: '측정해 봐야 아는 후보 — 시도할지 사람이 정함' },
};

const STATUS: Record<CandidateStatus, { label: string; cls: string }> = {
  proposed: { label: '제안', cls: 'text-slate-400' },
  approved: { label: '시도 승인', cls: 'text-indigo-600 dark:text-indigo-400' },
  held: { label: '보류', cls: 'text-amber-600 dark:text-amber-400' },
  rejected: { label: '기각', cls: 'text-rose-600 dark:text-rose-400' },
  adopted: { label: '채택', cls: 'text-emerald-600 dark:text-emerald-400' },
};

// 승인한 뒤 다음에 할 일 — 측정은 화면에서 돌리지 않는다(LLM 호출·수십 분). 명령은 판정 기록(--save)으로 이어진다
const NEXT_STEP: Record<Candidate['measure'], { label: string; cmd: string }> = {
  replay: { label: 'LLM 0 리플레이(이미 잼)', cmd: '설정 바꾼 뒤 검색·답 재측정 → python scripts/eval_gate.py ... --save "라벨"' },
  retrieval: { label: '검색 재측정(LLM 0, 수 분)', cmd: 'python scripts/eval_golden_v0.py --details eval_out/golden_v0/retr_<이름>.jsonl → eval_gate.py --save' },
  answers: { label: '답 재측정(로컬 LLM ~1시간)', cmd: 'eval_answers.py collect --golden v0 --llm local → judge → eval_gate.py --save' },
};

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const fmtDate = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

function CauseBars({ data }: { data: Improvement }) {
  const { diagnosis } = data.run;
  const max = Math.max(1, ...Object.values(diagnosis.causes).map((v) => v.length));
  return (
    <div className="space-y-2">
      {data.causes.filter((c) => (diagnosis.causes[c.key] ?? []).length > 0).map((c) => {
        const qs = diagnosis.causes[c.key] ?? [];
        return (
          <div key={c.key} className="text-sm">
            <div className="flex items-center gap-3">
              <span className="w-36 shrink-0 text-slate-200" title={c.where}>{c.name}</span>
              <div className="flex-1 h-2.5 rounded-full bg-slate-700 overflow-hidden">
                <div className="h-full bg-indigo-500 dark:bg-indigo-400" style={{ width: `${(qs.length / max) * 100}%` }} />
              </div>
              <span className="w-10 text-right text-slate-300">{qs.length}</span>
            </div>
            <div className="pl-[9.75rem]"><QidList title="문항 번호" qids={qs} hint={c.where} /></div>
          </div>
        );
      })}
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
        리플레이 표 ({c.replay.grid.length}가지)
      </button>
      {open && (
        <table className="mt-1 ml-5 text-slate-300">
          <thead>
            <tr className="text-slate-500">
              <th className="text-left pr-4 font-medium">서술 하한</th><th className="text-left pr-4 font-medium">파라미터 하한</th>
              <th className="text-right pr-4 font-medium">거절</th><th className="text-right pr-4 font-medium">고침</th>
              <th className="text-right pr-4 font-medium">망가짐</th><th className="text-right font-medium">정답률</th>
            </tr>
          </thead>
          <tbody>
            {c.replay.grid.map((r) => (
              <tr key={`${r.min_score}-${r.min_param_rank}`}>
                <td className="pr-4">{r.min_score.toFixed(2)}</td><td className="pr-4">{r.min_param_rank.toFixed(2)}</td>
                <td className="text-right pr-4">{r.refused}</td>
                <td className="text-right pr-4">{r.fixed}</td>
                <td className={`text-right pr-4 ${r.broken ? 'text-rose-600 dark:text-rose-400' : ''}`}>{r.broken}</td>
                <td className="text-right">{pct(r.before)} → {pct(r.after)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function CandidateCard({ c, isAdmin }: { c: Candidate; isAdmin: boolean }) {
  const qc = useQueryClient();
  const [modal, setModal] = useState<null | 'reject' | 'hold' | 'adopt'>(null);
  const [reason, setReason] = useState('');
  const [gateId, setGateId] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ['eval-improvement'] });
  const review = useMutation({
    mutationFn: ({ status, why }: { status: Exclude<CandidateStatus, 'adopted'>; why?: string }) => reviewCandidate(c.id, status, why),
    onSuccess: () => { setModal(null); setReason(''); refresh(); },
    onError: (e) => (modal ? setErr((e as Error).message) : showError(e, '검수 상태를 바꾸지 못했습니다')),
  });
  const adopt = useMutation({
    mutationFn: (id: number) => adoptCandidate(c.id, id),
    onSuccess: () => { setModal(null); refresh(); },
    onError: (e) => setErr((e as Error).message),
  });
  // 채택에 연결할 판정 — 시도 승인 뒤에 잰 통과 게이트만(서버 adopt_error와 같은 조건)
  const runs = useQuery({ queryKey: ['eval-gate-runs'], queryFn: () => listGateRuns(50), enabled: modal === 'adopt' });
  const eligible = (runs.data ?? []).filter((r) => r.kind === 'gate' && r.verdict === 'pass' && !!c.reviewed_at && new Date(r.run_at) >= new Date(c.reviewed_at));
  const rec = RECOMMEND[c.recommend];
  const st = STATUS[c.status];
  const next = NEXT_STEP[c.measure];
  const open = (m: 'reject' | 'hold' | 'adopt') => { setErr(null); setReason(''); setGateId(null); setModal(m); };

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-2" data-testid={`candidate-${c.key}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm font-medium text-slate-100">{c.title}</p>
          <p className="text-xs text-slate-400">
            <span className={rec.cls} title={rec.hint}>{rec.label}</span>
            {' · '}<span title="품질 › 답 기법 대장의 항목">{c.technique_name}</span>
            {' · '}{next.label}
          </p>
        </div>
        <span className={`text-xs font-medium ${st.cls}`} title={c.carried_from_id ? `이전 진단의 결정을 이어받음 — ${c.review_reason ?? ''}` : c.review_reason ?? ''}>
          {st.label}{c.carried_from_id ? ' (이어받음)' : ''}
        </span>
      </div>
      {c.why && <p className="text-xs text-slate-300">{c.why}</p>}
      <p className="text-xs text-slate-500" title="바꿀 것">{c.action}</p>
      <QidList title="대상" qids={c.qids} hint="이 후보가 고치려는 틀린 문항(원문은 eval_out/ 결과 파일)" />
      <ReplayTable c={c} />
      {c.hypotheses.length > 0 && (
        <div className="text-xs space-y-1">
          <p className="text-slate-400" title="채점 이유를 로컬 LLM이 묶은 것 — 묶음이 맞는지 사람이 확인해야 후보 근거로 쓴다">가설(로컬 LLM 묶음 · 확인 필요) ⓘ</p>
          {c.hypotheses.map((h) => (
            <div key={h.name} className="pl-3">
              <QidList title={h.name} qids={h.qids} hint={h.hint || h.name} />
            </div>
          ))}
        </div>
      )}
      {c.status === 'approved' && <p className="text-xs text-indigo-600 dark:text-indigo-400" title={next.cmd}>다음: {next.cmd}</p>}
      {c.status === 'adopted' && <p className="text-xs text-emerald-600 dark:text-emerald-400">판정 #{c.adopted_gate_run_id} "{c.adopted_gate_label}"로 채택</p>}
      {c.review_reason && c.status !== 'adopted' && <p className="text-xs text-slate-500">사유: {c.review_reason}</p>}

      {isAdmin && c.status !== 'adopted' && (
        <div className="flex flex-wrap gap-3 pt-1 text-xs">
          {c.status !== 'approved' && (
            <button className="text-indigo-500 dark:text-indigo-400 hover:underline disabled:opacity-50" disabled={review.isPending}
              title="측정해 볼 후보로 — 다음 측정 명령을 보여 줍니다" onClick={() => review.mutate({ status: 'approved' })}>시도 승인</button>
          )}
          {c.status === 'approved' && (
            <button className="text-emerald-600 dark:text-emerald-400 hover:underline" title="이 후보를 적용하고 잰 통과 판정과 연결" onClick={() => open('adopt')}>채택…</button>
          )}
          {c.status !== 'held' && <button className="text-amber-600 dark:text-amber-400 hover:underline" onClick={() => open('hold')}>보류…</button>}
          {c.status !== 'rejected' && <button className="text-rose-600 dark:text-rose-400 hover:underline" onClick={() => open('reject')}>기각…</button>}
          {c.status !== 'proposed' && (
            <button className="text-slate-400 hover:underline disabled:opacity-50" disabled={review.isPending} onClick={() => review.mutate({ status: 'proposed' })}>되돌리기</button>
          )}
        </div>
      )}

      <FormModal
        isOpen={modal === 'reject' || modal === 'hold'}
        title={modal === 'reject' ? '기각' : '보류'}
        onClose={() => setModal(null)}
        onSubmit={() => review.mutate({ status: modal === 'reject' ? 'rejected' : 'held', why: reason })}
        submitLabel={modal === 'reject' ? '기각' : '보류'}
        submitting={review.isPending}
        submitDisabled={modal === 'reject' && !reason.trim()}
        danger={modal === 'reject'}
        error={err}
      >
        <label htmlFor={`cand-${c.id}-reason`} className="block text-xs text-slate-400">
          {modal === 'reject' ? '기각 이유 (다음 진단이 같은 후보를 이어받을 때 보입니다)' : '보류 이유 (선택)'}
        </label>
        <textarea id={`cand-${c.id}-reason`} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus
          className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
      </FormModal>

      <FormModal
        isOpen={modal === 'adopt'}
        title="채택 — 통과한 판정 연결"
        onClose={() => setModal(null)}
        onSubmit={() => gateId != null && adopt.mutate(gateId)}
        submitLabel="채택"
        submitting={adopt.isPending}
        submitDisabled={gateId == null}
        error={err}
      >
        <p className="text-xs text-slate-400">이 후보를 적용하고 잰 판정 중 통과한 것만 고를 수 있어요(시도 승인 뒤에 잰 것).</p>
        {runs.isLoading ? <p className="text-xs text-slate-500">불러오는 중…</p> : eligible.length === 0 ? (
          <p className="text-xs text-slate-500">연결할 판정이 없습니다 — 먼저 측정 후 <code>eval_gate.py --save</code>.</p>
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

export function ImprovementTab() {
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const { data, isLoading, error } = useQuery({ queryKey: ['eval-improvement'], queryFn: getLatestImprovement, staleTime: 30_000 });
  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">진단을 불러오지 못했습니다: {(error as Error).message}</p>;
  if (!data) {
    return (
      <p className="text-sm text-slate-500">
        아직 진단이 없습니다 — <code className="text-xs">backend/scripts/eval_improve.py --reasons --save</code>로 기준선을 진단하면 여기에 나옵니다.
      </p>
    );
  }
  const { run } = data;
  const d = run.diagnosis;
  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-3">
        <p className="text-xs text-slate-400">
          <Lightbulb className="inline w-3.5 h-3.5 mr-1 text-amber-500 dark:text-amber-400" />
          판정 #{run.gate_run_id} "{run.gate_label}"{run.gate_is_baseline ? ' (기준선)' : ''} 진단 · {fmtDate(run.created_at)}
          {run.model && <span title="가설 묶음을 만든 로컬 LLM"> · 묶음 {run.model}</span>}
        </p>
        <p className="text-lg font-semibold text-slate-100">
          틀린 문항 {d.n_failed} / {d.n}
          <span className="ml-2 text-sm font-normal text-slate-400" title="흐름에서 첫 실패 한 칸에 넣었다 — 앞 단계가 실패하면 뒤 단계는 보지 않는다(오류 분석의 축 코딩)">
            원인별(흐름의 첫 실패) ⓘ
          </span>
        </p>
        <CauseBars data={data} />
      </div>
      <div className="space-y-3">
        <p className="text-sm font-medium text-slate-200">개선 후보 {data.candidates.length}개
          <span className="ml-2 text-xs font-normal text-slate-500" title="사람이 시도 승인 → 측정 → 통과한 판정과 연결해야 채택. 설정·프롬프트는 자동으로 바뀌지 않습니다">사람 검수 후에만 반영 ⓘ</span>
        </p>
        {data.candidates.map((c) => <CandidateCard key={c.id} c={c} isAdmin={isAdmin} />)}
      </div>
    </div>
  );
}
