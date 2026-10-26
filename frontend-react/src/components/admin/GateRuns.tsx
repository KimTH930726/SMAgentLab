import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { getBaselineRun, getLayerQuestions, type GateRunDetail } from '../../api/eval';
import { layerOf, lockedAccuracy, mergeLayers } from '../../utils/evalLayers';
import { useAuthStore } from '../../store/useAuthStore';
import { QuestionDetailModal } from './QuestionDetailModal';

/**
 * 평가 게이트 [검색] [답] (v2.169 → v2.172 카드·층별 막대, 사용자 10/26 "표가 투박해 눈에 안 들어온다" — 목업 검수). 판정 이력 탭은 v2.172에서 뺐다
 * (기준선은 [운영에 반영했어요] 때 자동으로 옮겨진다). 층 줄을 누르면 그 층의 놓친·틀린 문항(관리자, /gate-runs/{id}/layers).
 * 숫자는 판정 기록(eval_gate_run)을 읽기만 한다 — 계산은 scripts/eval_gate.py 한 곳(화면 재계산 없음).
 * 문항은 번호(qid)만 — 질문·답 원문은 DB에 두지 않는다(원문은 eval_out/ 결과 파일).
 */

const LABELS = [
  { key: '정답', cls: 'text-emerald-600 dark:text-emerald-400' },
  { key: '부분', cls: 'text-amber-600 dark:text-amber-400' },
  { key: '오답', cls: 'text-rose-600 dark:text-rose-400' },
  { key: '거절', cls: 'text-slate-400' },
] as const;

const pct = (x: number | null | undefined, d = 1) => (x == null ? '-' : `${(x * 100).toFixed(d)}%`);
const fmtDate = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

/** 기준선은 목록(최신 50)에서 찾지 않고 따로 받는다 — 기준선 뒤 판정이 50건 넘거나 기준선이 아직 없을 때 "불러오는 중"에 멈췄다(리뷰 M1) */
function useBaseline() {
  return useQuery({ queryKey: ['eval-gate-baseline'], queryFn: getBaselineRun, staleTime: 30_000 });
}

function NoBaseline() {
  return (
    <p className="text-sm text-slate-500" title="통과한 측정을 eval_gate.py --save로 기록하거나, 리포트에서 [운영에 반영했어요]를 누르면 그 측정이 지금 점수가 됩니다">
      지금 점수가 없습니다 ⓘ
    </p>
  );
}

function Loading({ error }: { error: unknown }) {
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">기록을 불러오지 못했습니다: {(error as Error).message}</p>;
  return <p className="text-sm text-slate-500">불러오는 중…</p>;
}

function BaselineHeader({ run }: { run: GateRunDetail }) {
  const [n] = run.golden_version.split(':');
  return (
    <p className="text-xs text-slate-500">
      지금 점수 · 측정 #{run.id} "{run.label}" · {fmtDate(run.run_at)} · {n}문항
      <span title={`[운영에 반영했어요]를 누르면 그 측정이 지금 점수가 됩니다. 문항 집합 지문 ${run.golden_version}${run.git_commit ? ` · 커밋 ${run.git_commit}` : ''}`}> ⓘ</span>
    </p>
  );
}

/** 숫자 카드 — 누를 수 있으면(onClick) 그 문항들이 열린다 */
function Stat({ id, label, value, sub, hint, onClick, tone }: {
  id: string; label: string; value: string; sub?: string; hint?: string; onClick?: () => void; tone?: 'bad';
}) {
  const body = (
    <>
      <p className="text-xs text-slate-400" title={hint}>{label}{hint && ' ⓘ'}</p>
      <p className={`text-2xl font-semibold ${tone === 'bad' ? 'text-rose-600 dark:text-rose-400' : 'text-slate-100'}`}>
        {value}{sub && <span className="ml-1.5 text-sm font-normal text-slate-400">{sub}</span>}
      </p>
      {onClick && <p className="text-xs text-indigo-600 dark:text-indigo-400">문항 보기 →</p>}
    </>
  );
  const cls = 'rounded-lg border border-slate-700 bg-slate-800 p-4 text-left space-y-1 min-w-0';
  return onClick
    ? <button className={`${cls} hover:border-indigo-500 dark:hover:border-indigo-400`} onClick={onClick} data-testid={`stat-${id}`}>{body}</button>
    : <div className={cls} data-testid={`stat-${id}`}>{body}</div>;
}

/** 층별 막대 한 줄 — 가장 약한 층은 강조, 문항이 있으면 눌러서 연다 */
function LayerBar({ name, hint, n, bars, weakest, qids, onPick, what }: {
  name: string; hint: string; n: number; bars: { label?: string; v: number; strong?: boolean }[];
  weakest: boolean; qids: string[] | null; onPick?: (qids: string[], i: number) => void; what: string;
}) {
  const can = !!onPick && !!qids && qids.length > 0;   // qids null = 층별 문항을 못 받음(비관리자·오류·불러오는 중) — 0으로 보이지 않게
  const row = (
    <>
      <span className="w-20 shrink-0 text-sm text-slate-200" title={hint}>{name}</span>
      <span className="w-10 shrink-0 text-right text-xs text-slate-500">{n}</span>
      <span className="flex-1 min-w-0 space-y-1">
        {bars.map((b, k) => (
          <span key={k} className="flex items-center gap-2">
            <span className={`flex-1 rounded-full bg-slate-700 overflow-hidden ${b.strong ? 'h-2.5' : 'h-1.5'}`}>
              <span className={`block h-full ${weakest && b.strong ? 'bg-amber-500 dark:bg-amber-400' : b.strong ? 'bg-indigo-500 dark:bg-indigo-400' : 'bg-slate-500'}`}
                style={{ width: `${Math.max(0, Math.min(1, b.v)) * 100}%` }} />
            </span>
            <span className={`w-24 shrink-0 text-right ${b.strong ? 'text-sm text-slate-100' : 'text-xs text-slate-400'}`}>
              {b.label && <span className="text-slate-500">{b.label} </span>}{pct(b.v)}
            </span>
          </span>
        ))}
      </span>
      <span className="w-28 shrink-0 text-right text-xs">
        {weakest && <span className="text-amber-600 dark:text-amber-400">가장 약함 </span>}
        {can ? <span className="text-indigo-600 dark:text-indigo-400">{what} {qids!.length} →</span>
          : qids?.length === 0 ? <span className="text-slate-500">{what} 0</span> : null}
      </span>
    </>
  );
  const cls = 'w-full flex items-center gap-3 px-3 py-2.5';
  return can
    ? <button className={`${cls} text-left hover:bg-slate-800/60`} onClick={() => onPick!(qids!, 0)} data-testid={`layer-${name}`}>{row}</button>
    : <div className={cls} data-testid={`layer-${name}`}>{row}</div>;
}

/** 층 이름별 문항 — 서버는 type(list·navigation 따로)으로 주니 화면 층(목록)으로 합친다 */
function byLayer(m: Record<string, string[]> | undefined): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [t, qs] of Object.entries(m ?? {})) {
    const k = layerOf(t)?.name ?? t;
    out[k] = [...(out[k] ?? []), ...qs].sort();
  }
  return out;
}

/** 결과 파일이 서버에 없으면 층별 놓침·틀림이 0으로 보인다 — 숨기지 않고 알린다(리뷰 L1) */
function FilesMissing({ names, error }: { names?: string[]; error?: unknown }) {
  if (error) return <p className="text-xs text-rose-600 dark:text-rose-400">층별 문항을 못 불러왔어요: {(error as Error).message}</p>;
  if (!names?.length) return null;
  return <p className="text-xs text-rose-600 dark:text-rose-400">결과 파일이 서버에 없어 층별 문항을 못 열어요: {names.join(', ')} (eval_out/)</p>;
}

function useLayerQids(runId: number | undefined) {
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  return useQuery({ queryKey: ['eval-gate-layers', runId], queryFn: () => getLayerQuestions(runId!), enabled: isAdmin && runId != null, staleTime: 60_000 });
}

export function RetrievalTab() {
  const base = useBaseline();
  const { picker, modal } = useQuestionPicker();
  const layers = useLayerQids(base.data?.id);
  if (base.isLoading || base.error) return <Loading error={base.error} />;
  if (!base.data) return <NoBaseline />;
  const s = base.data.summary.retrieval;
  if (!s) return <p className="text-sm text-slate-500">지금 점수에 검색 측정이 없습니다.</p>;
  const rows = mergeLayers(s.by_type, (a, b) => ({ n: a.n + b.n, hit: a.hit + b.hit, top1: a.top1 + b.top1 }));
  const weakest = rows.length > 1 ? rows.reduce((m, r) => (m.v.hit / m.v.n <= r.v.hit / r.v.n ? m : r)).name : null;
  const missed = byLayer(layers.data?.missed);
  const allMissed = Object.values(missed).flat().sort();
  const pick = picker(base.data.id);
  return (
    <div className="space-y-4">
      <BaselineHeader run={base.data} />
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <Stat id="recall" label="정답을 AI에게 넘김" value={pct(s.recall)} sub={`${s.hit}/${s.n}`} hint="정답 정책이 AI에게 넘기는 문맥(상위 10)에 들어간 비율 — Recall@10. 답 없는 질문은 찾을 정답이 없어 뺌" />
        <Stat id="top1" label="첫째로 찾음" value={pct(s.top1_rate)} sub={`${s.top1}/${s.n}`} hint="정답 정책이 첫째로 나온 비율 — Top-1" />
        <Stat id="missed" label="놓친 문항" value={String(s.n - s.hit)} sub="문항" hint="정답 정책이 문맥에 못 들어간 문항 — 답도 틀릴 가능성이 큼"
          tone={s.n - s.hit > 0 ? 'bad' : undefined} onClick={pick && allMissed.length ? () => pick(allMissed, 0) : undefined} />
      </div>
      <FilesMissing names={layers.data?.files_missing} error={layers.error} />
      <div className="rounded-lg border border-slate-700 divide-y divide-slate-700">
        <p className="px-3 py-2 text-xs text-slate-500">층별 — 굵은 막대 = 넘김, 얇은 막대 = 첫째로 찾음</p>
        {rows.map(({ name, hint, v }) => (
          <LayerBar key={name} name={name} hint={hint} n={v.n} weakest={weakest === name} what="놓침"
            bars={[{ v: v.hit / v.n, strong: true }, { label: '첫째', v: v.top1 / v.n }]}
            qids={layers.data ? missed[name] ?? [] : null} onPick={pick} />
        ))}
      </div>
      {modal}
    </div>
  );
}

/** 문항 번호 목록 — onPick이 있으면 번호를 누르면 문항 상세(질문·답·이유)가 열린다(관리자, 10/26) */
export function QidList({ title, qids, hint, onPick }: {
  title: string; qids: string[]; hint: string; onPick?: (qids: string[], index: number) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!qids.length) return <p className="text-xs text-slate-500">{title}: 0문항</p>;
  return (
    <div className="text-xs">
      <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 text-slate-300 hover:text-slate-100" title={hint}>
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        {title}: {qids.length}문항
      </button>
      {open && (onPick ? (
        <div className="mt-1 pl-5 flex flex-wrap gap-1.5">
          {qids.map((q, i) => (
            <button key={q} onClick={() => onPick(qids, i)} title="질문·답·채점 이유 보기"
              className="px-1.5 py-0.5 rounded border border-slate-600 text-slate-300 hover:border-indigo-500 dark:hover:border-indigo-400">
              {q}
            </button>
          ))}
        </div>
      ) : <p className="mt-1 pl-5 text-slate-400 leading-relaxed break-all">{qids.join(' · ')}</p>)}
    </div>
  );
}

/** 문항 상세 창 열기 상태 — 판정 하나(runId)의 결과 파일에서 읽는다 */
export function useQuestionPicker() {
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  const [pick, setPick] = useState<{ runId: number; qids: string[]; index: number } | null>(null);
  const picker = (runId: number) => (isAdmin ? (qids: string[], index: number) => setPick({ runId, qids, index }) : undefined);
  const modal = pick && <QuestionDetailModal runId={pick.runId} qids={pick.qids} start={pick.index} onClose={() => setPick(null)} />;
  return { picker, modal };
}

const LABEL_BAR: Record<string, string> = {
  정답: 'bg-emerald-500 dark:bg-emerald-400', 부분: 'bg-amber-500 dark:bg-amber-400', 오답: 'bg-rose-500 dark:bg-rose-400', 거절: 'bg-slate-500',
};

export function AnswerTab() {
  const base = useBaseline();
  const { picker, modal } = useQuestionPicker();
  const layers = useLayerQids(base.data?.id);
  if (base.isLoading || base.error) return <Loading error={base.error} />;
  if (!base.data) return <NoBaseline />;
  const s = base.data.summary.answers;
  if (!s) return <p className="text-sm text-slate-500">지금 점수에 답 측정이 없습니다.</p>;
  const jm = base.data.judge_meta;
  const rows = mergeLayers(s.by_type, (a, b) => ({ n: a.n + b.n, accuracy: (a.accuracy * a.n + b.accuracy * b.n) / (a.n + b.n) }))
    .sort((a, b) => a.v.accuracy - b.v.accuracy);   // 낮은 순 — 어디부터 볼지
  const weakest = rows.length > 1 ? rows[0].name : null;
  const wrong = byLayer(layers.data?.wrong);
  const lk = lockedAccuracy(s.by_type);
  const totalLabels = LABELS.reduce((t, l) => t + (s.labels[l.key] ?? 0), 0);
  const okWrong = s.retrieval_ok_answer_wrong ?? [];
  const pick = picker(base.data.id);
  return (
    <div className="space-y-4">
      <BaselineHeader run={base.data} />
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {lk && (
          <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-1">
            <p className="text-xs text-slate-400"
              title="잠근 시험지 — 팀원이 직접 쓴 질문·실사용 질문. 고칠 때(진단·후보)는 안 보고 결과를 잴 때만 봐서 '시험지에만 강해지는' 착시를 막는다. 목표 숫자">
              사람이 쓴 질문 ⓘ</p>
            <p className="text-3xl font-semibold text-slate-100" data-testid="locked-accuracy">
              {pct(lk.accuracy)}<span className="ml-1.5 text-sm font-normal text-slate-400">{lk.n}문항</span>
            </p>
          </div>
        )}
        <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-1">
          <p className="text-xs text-slate-400">전체 정답률</p>
          <p className="text-3xl font-semibold text-slate-100">
            {pct(s.accuracy)}
            {s.lo != null && <span className="ml-1.5 text-sm font-normal text-slate-400" title="95% 구간 — 같은 원문 묶음 문항은 서로 독립이 아니라 묶어서 계산">[{pct(s.lo)} – {pct(s.hi)}] ⓘ</span>}
          </p>
          <p className="text-xs text-slate-500">{s.n}문항 × {s.reps}회</p>
        </div>
      </div>
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-2">
        <p className="text-xs text-slate-400">답 분포 <span className="text-slate-500">(회차까지 센 채점 {totalLabels}건)</span></p>
        <div className="flex h-3 rounded-full overflow-hidden bg-slate-700" aria-label="답 분포">
          {LABELS.map((l) => (s.labels[l.key] ? (
            <span key={l.key} className={LABEL_BAR[l.key]} style={{ width: `${(s.labels[l.key] / Math.max(1, totalLabels)) * 100}%` }} title={`${l.key} ${s.labels[l.key]}`} />
          ) : null))}
        </div>
        <p className="text-xs">{LABELS.map((l) => <span key={l.key} className={`mr-3 ${l.cls}`}>{l.key} {s.labels[l.key] ?? 0}</span>)}</p>
        <p className="text-xs text-slate-500">
          채점 {jm.judge_model?.join(', ') || '-'} · {jm.judged_on?.join(', ') || '-'}
          <span title={`채점 기준: ${jm.rubrics?.join(', ') || '-'}`}> · 기준 {jm.rubrics?.length ?? 0}종 ⓘ</span>
          {!!jm.answer_model?.length && <> · 답 생성 {jm.answer_model.join(', ')}</>}
        </p>
      </div>
      {okWrong.length > 0 && (
        <button className="w-full rounded-lg border border-amber-300 dark:border-amber-500/40 bg-amber-50 dark:bg-amber-500/10 p-3 text-left disabled:cursor-default"
          disabled={!pick} onClick={() => pick?.(okWrong, 0)} title="정답 정책이 문맥에 들어갔는데 회차 과반이 틀린 문항 — 답 생성·문맥 다듬기 쪽 문제">
          <span className="text-sm text-amber-800 dark:text-amber-300">검색은 맞았는데 답이 틀림 <b>{okWrong.length}</b>문항</span>
          {pick && <span className="ml-2 text-xs text-indigo-600 dark:text-indigo-400">문항 보기 →</span>}
        </button>
      )}
      <FilesMissing names={layers.data?.files_missing} error={layers.error} />
      <div className="rounded-lg border border-slate-700 divide-y divide-slate-700">
        <p className="px-3 py-2 text-xs text-slate-500">층별 정답률 — 낮은 순</p>
        {rows.map(({ name, hint, v }) => (
          <LayerBar key={name} name={name} hint={hint} n={v.n} weakest={weakest === name} what="틀림"
            bars={[{ v: v.accuracy, strong: true }]} qids={layers.data ? wrong[name] ?? [] : null} onPick={pick} />
        ))}
      </div>
      {modal}
    </div>
  );
}
