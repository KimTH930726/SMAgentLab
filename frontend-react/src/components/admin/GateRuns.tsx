import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Star } from 'lucide-react';
import { getBaselineRun, getGateRun, listGateRuns, setGateBaseline, type AnswerGroup, type GateRunDetail, type GateRunListItem } from '../../api/eval';
import { groupOrder, layerOf, lockedAccuracy, lockedMove, mergeLayers } from '../../utils/evalLayers';
import { useAuthStore } from '../../store/useAuthStore';
import { showConfirm, showError } from '../../store/useDialogStore';
import { QuestionDetailModal } from './QuestionDetailModal';

/**
 * 평가 게이트 [검색] [답] [판정 이력] (v2.169, eval-gate-redesign §4-1 — 10/23 목업 검수).
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
const pp = (x: number | null | undefined) => (x == null ? '-' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(1)}`);
const fmtDate = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

function useRuns() {
  return useQuery({ queryKey: ['eval-gate-runs'], queryFn: () => listGateRuns(50), staleTime: 30_000 });
}

/** 기준선은 목록(최신 50)에서 찾지 않고 따로 받는다 — 기준선 뒤 판정이 50건 넘거나 기준선이 아직 없을 때 "불러오는 중"에 멈췄다(리뷰 M1) */
function useBaseline() {
  return useQuery({ queryKey: ['eval-gate-baseline'], queryFn: getBaselineRun, staleTime: 30_000 });
}

function NoBaseline() {
  return (
    <p className="text-sm text-slate-500" title="통과한 판정을 --save로 기록하면 기준선이 됩니다(채택 안 한 실험은 --keep-baseline). 판정 이력 탭에서 관리자가 지정할 수도 있습니다">
      기준선이 없습니다 ⓘ
    </p>
  );
}

function Empty() {
  return (
    <p className="text-sm text-slate-500">
      아직 판정 기록이 없습니다 — <code className="text-xs">backend/scripts/eval_gate.py --save "무엇을 바꿨나"</code>로 판정하면 여기에 쌓입니다.
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
    <p className="text-xs text-slate-400">
      <Star className="inline w-3.5 h-3.5 mr-1 text-amber-500 dark:text-amber-400" />
      기준선 · {fmtDate(run.run_at)} "{run.label}" · 골든셋 {n}문항
      <span className="text-slate-500" title={`문항 집합 지문 ${run.golden_version} — 세트가 바뀌면 값이 달라져 숫자를 나란히 놓으면 안 된다`}> ⓘ</span>
      {run.git_commit && <> · 커밋 {run.git_commit}</>}
    </p>
  );
}

export function RetrievalTab() {
  const base = useBaseline();
  if (base.isLoading || base.error) return <Loading error={base.error} />;
  if (!base.data) return <NoBaseline />;
  const s = base.data.summary.retrieval;
  if (!s) return <p className="text-sm text-slate-500">기준선에 검색 측정이 없습니다.</p>;
  const rows = mergeLayers(s.by_type, (a, b) => ({ n: a.n + b.n, hit: a.hit + b.hit, top1: a.top1 + b.top1 }));
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-2">
        <BaselineHeader run={base.data} />
        <div className="flex flex-wrap gap-8">
          <div>
            <p className="text-xs text-slate-400" title="정답 정책이 AI에게 넘기는 문맥(상위 10)에 들어간 비율">Recall@10 ⓘ</p>
            <p className="text-2xl font-semibold text-slate-100">{pct(s.recall)} <span className="text-sm font-normal text-slate-400">({s.hit}/{s.n})</span></p>
          </div>
          <div>
            <p className="text-xs text-slate-400" title="정답 정책이 첫째로 나온 비율">Top-1 ⓘ</p>
            <p className="text-2xl font-semibold text-slate-100">{pct(s.top1_rate)} <span className="text-sm font-normal text-slate-400">({s.top1}/{s.n})</span></p>
          </div>
        </div>
        <p className="text-xs text-slate-500" title="정책서에 답이 없는 질문은 찾을 정답이 없어 검색 분모에서 뺀다 — [답] 탭에서 거절을 맞혔는지 본다">답 없는 질문은 제외 ⓘ</p>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-slate-400 border-b border-slate-700">
            <th className="text-left py-2 font-medium">층</th><th className="text-right font-medium">문항</th>
            <th className="text-right font-medium">Recall</th><th className="text-right font-medium">Top-1</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ name, hint, v }) => (
            <tr key={name} className="border-b border-slate-800 text-slate-200">
              <td className="py-2" title={hint}>{name}</td>
              <td className="text-right text-slate-400">{v.n}</td>
              <td className="text-right">{pct(v.hit / v.n)} <span className="text-xs text-slate-500">({v.hit})</span></td>
              <td className="text-right">{pct(v.top1 / v.n)} <span className="text-xs text-slate-500">({v.top1})</span></td>
            </tr>
          ))}
        </tbody>
      </table>
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

export function AnswerTab() {
  const base = useBaseline();
  const { picker, modal } = useQuestionPicker();
  if (base.isLoading || base.error) return <Loading error={base.error} />;
  if (!base.data) return <NoBaseline />;
  const s = base.data.summary.answers;
  if (!s) return <p className="text-sm text-slate-500">기준선에 답 측정이 없습니다.</p>;
  const jm = base.data.judge_meta;
  const rows = mergeLayers(s.by_type, (a, b) => ({ n: a.n + b.n, accuracy: (a.accuracy * a.n + b.accuracy * b.n) / (a.n + b.n) }));
  const weakest = rows.reduce((m, r) => (m && m.v.accuracy <= r.v.accuracy ? m : r), rows[0]);
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-2">
        <BaselineHeader run={base.data} />
        {(() => {
          const lk = lockedAccuracy(s.by_type);
          return lk && (
            <p className="text-2xl font-semibold text-slate-100" data-testid="locked-accuracy">
              사람이 쓴 질문 {pct(lk.accuracy)}
              <span className="ml-2 text-sm font-normal text-slate-400"
                title="잠근 시험지 — 팀원이 직접 쓴 질문·실사용 질문. 고칠 때(진단·후보)는 안 보고 판정 때만 봐서 '시험지에만 강해지는' 착시를 막는다. 목표 숫자">
                {lk.n}문항 · 잠근 시험지 ⓘ
              </span>
            </p>
          );
        })()}
        <p className="text-lg font-medium text-slate-300">
          전체 정답률 {pct(s.accuracy)}
          {s.lo != null && <span className="ml-2 text-sm font-normal text-slate-400" title="95% 구간 — 같은 원문 묶음 문항은 서로 독립이 아니라 묶어서 계산">[{pct(s.lo)} – {pct(s.hi)}] ⓘ</span>}
          <span className="ml-2 text-sm font-normal text-slate-400">{s.n}문항 × {s.reps}회</span>
        </p>
        <p className="text-xs">
          {LABELS.map((l) => <span key={l.key} className={`mr-3 ${l.cls}`}>{l.key} {s.labels[l.key] ?? 0}</span>)}
        </p>
        <p className="text-xs text-slate-500">
          채점 {jm.judge_model?.join(', ') || '-'} · {jm.judged_on?.join(', ') || '-'}
          <span title={`채점 기준: ${jm.rubrics?.join(', ') || '-'}`}> · 기준 {jm.rubrics?.length ?? 0}종 ⓘ</span>
          {!!jm.answer_model?.length && <> · 답 생성 {jm.answer_model.join(', ')}</>}
        </p>
        <QidList title="검색은 맞았는데 답이 틀림" qids={s.retrieval_ok_answer_wrong ?? []}
          hint="정답 정책이 문맥에 들어갔는데 회차 과반이 틀린 문항 — 눌러서 질문·답·채점 이유 보기" onPick={picker(base.data.id)} />
      </div>
      {modal}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-slate-400 border-b border-slate-700">
            <th className="text-left py-2 font-medium">층</th><th className="text-right font-medium">문항</th><th className="text-right font-medium">정답률</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ name, hint, v }) => (
            <tr key={name} className="border-b border-slate-800 text-slate-200">
              <td className="py-2" title={hint}>{name}</td>
              <td className="text-right text-slate-400">{v.n}</td>
              <td className="text-right">
                {pct(v.accuracy)}
                {weakest?.name === name && rows.length > 1 && <span className="ml-2 text-xs text-amber-600 dark:text-amber-400">가장 약함</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 판정 한 줄을 문장으로 — 무엇이 얼마나 바뀌었나(사용자 10/26 "직관적으로") */
function headline(run: GateRunListItem): { text: string; tone: 'good' | 'bad' | 'plain' } {
  const t = run.answers_total;
  if (run.kind === 'baseline') return { text: `정답률 ${pct(run.summary.answers?.accuracy)} 측정`, tone: 'plain' };
  if (run.verdict === 'fail') {
    const failed = Object.entries(run.answers_groups ?? {}).filter(([g]) => (run.answers_failed ?? []).includes(g));
    if (failed.length) {
      const [g, x] = failed[0];
      return { text: `${g === '전체' ? '전체' : `${layerOf(g)?.name ?? g} 질문`} ${pct(x.before)} → ${pct(x.after)} (${pp(x.diff)}%p, 의미 있게 하락)`, tone: 'bad' };
    }
    if (run.retrieval_down) return { text: `검색 나빠짐 ${run.retrieval_down}문항 — 정답 정책을 못 넘기게 된 문항`, tone: 'bad' };
    return { text: '실패', tone: 'bad' };
  }
  const lk = lockedMove(run.answers_groups);
  if (t && lk?.down) {   // 잠근 시험지에서 내려가면 통과여도 그게 먼저 — 운영 반영이 막힌다(adopt_error)
    return { text: `사람이 쓴 질문 ${pct(lk.before)} → ${pct(lk.after)} (내려감 · 전체 ${pp(t.diff)}%p)`, tone: 'bad' };
  }
  if (t) {
    const sig = t.significant && t.diff > 0 ? '의미 있게 좋아짐' : '오차 안';
    return { text: `정답률 ${pct(t.before)} → ${pct(t.after)} (${pp(t.diff)}%p, ${sig})`, tone: t.significant && t.diff > 0 ? 'good' : 'plain' };
  }
  return { text: run.has_retrieval_gate ? `검색 좋아짐 ${run.retrieval_up} · 나빠짐 ${run.retrieval_down}` : '-', tone: 'plain' };
}

function Warnings({ run }: { run: GateRunListItem }) {
  const w: string[] = [];
  if (run.judge_meta.same_day_rubric === false) w.push('전/후 채점일·채점 기준이 다름 — 같은 날 다시 채점해서 비교해야 함');
  if (run.summary.golden_before) w.push(`전/후 문항 세트가 다름(${run.summary.golden_before} → ${run.golden_version}) — 짝지은 문항만 비교됨`);
  if (!w.length) return null;
  return <span className="ml-1 text-amber-600 dark:text-amber-400" title={w.join('\n')}>⚠</span>;
}

function GroupTable({ groups }: { groups: Record<string, AnswerGroup> }) {
  const names = (g: string) => (g === '전체' ? '전체' : layerOf(g)?.name ?? g);
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-slate-400 border-b border-slate-700">
          <th className="text-left py-1 font-medium">층</th><th className="text-right font-medium">문항</th>
          <th className="text-right font-medium">전</th><th className="text-right font-medium">후</th>
          <th className="text-right font-medium" title="같은 문항끼리 차이 · 95% 구간 — 구간이 0을 넘지 않으면 '의미 있게' 바뀐 것">차이 [구간] ⓘ</th>
          <th className="text-right font-medium">좋아짐</th><th className="text-right font-medium">나빠짐</th>
        </tr>
      </thead>
      <tbody>
        {groupOrder(groups).map(([g, x]) => (
          <tr key={g} className="border-b border-slate-800 text-slate-300">
            <td className="py-1">{names(g)}{x.iid_fallback && <span className="text-slate-500" title="묶음이 1개라 문항 독립 가정으로 구간을 계산(덜 보수적)"> ※</span>}</td>
            <td className="text-right text-slate-500">{x.n}</td>
            <td className="text-right">{pct(x.before)}</td>
            <td className="text-right">{pct(x.after)}</td>
            <td className={`text-right ${x.worse ? 'text-rose-600 dark:text-rose-400' : x.significant && x.diff > 0 ? 'text-emerald-600 dark:text-emerald-400' : ''}`}>
              {pp(x.diff)}%p {x.lo != null ? `[${pp(x.lo)}, ${pp(x.hi)}]` : '[계산불가]'}
            </td>
            <td className="text-right">{x.up}</td>
            <td className="text-right">{x.down}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RunDetail({ id, onPick }: { id: number; onPick?: (qids: string[], index: number) => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['eval-gate-run', id], queryFn: () => getGateRun(id), staleTime: 30_000 });
  if (isLoading || error || !data) return <Loading error={error} />;
  const r = data.retrieval;
  const a = data.answers;
  const jm = data.judge_meta;
  return (
    <div className="space-y-3">
      {a && (
        <div className="space-y-1">
          <QidList title="나빠진 문항" qids={a.down_qids} hint="바꾼 뒤 정답이 줄어든 문항 — 눌러서 질문·답·이유 보기" onPick={onPick} />
          <QidList title="좋아진 문항" qids={a.up_qids} hint="바꾼 뒤 정답이 늘어난 문항" onPick={onPick} />
          <GroupTable groups={a.groups} />
        </div>
      )}
      {r && (
        <div className="text-xs text-slate-300 space-y-1">
          {r.error && <p className="text-rose-600 dark:text-rose-400">검색 판정 실패 — {r.error}(qid·mode 확인)</p>}
          <p title="정답 정책이 AI에게 넘어간 문항 수">검색 {r.before}/{r.n} → {r.after}/{r.n}{r.significant ? ' (의미 있게 좋아짐)' : ''}
            {Object.keys(r.by_type).length > 0 && <span className="text-slate-500"> · {mergeLayers(r.by_type, (x, y) => ({ n: x.n + y.n, before: x.before + y.before, after: x.after + y.after }))
              .map(({ name, v }) => `${name} ${v.before}→${v.after}`).join(' · ')}</span>}
          </p>
          <QidList title="검색 나빠짐" qids={r.down} hint="전엔 정답 정책을 넘겼는데 후엔 못 넘긴 문항 — 1건이라도 있으면 실패" onPick={onPick} />
        </div>
      )}
      {!r && !a && data.kind === 'baseline' && (
        <p className="text-xs text-slate-400">이 측정 자체의 숫자 — 정답률 {pct(data.summary.answers?.accuracy)} · 검색 {pct(data.summary.retrieval?.recall)}</p>
      )}
      <p className="text-xs text-slate-500">
        채점 {jm.judged_on?.join(', ') || '-'}
        {jm.same_day_rubric != null && <> · 전/후 같은 날 채점: {jm.same_day_rubric ? '예' : '아니오(비교 주의)'}</>}
        {data.git_commit && <> · 코드 {data.git_commit}</>}
        {data.notes && <> · {data.notes}</>}
      </p>
    </div>
  );
}

const TONE: Record<string, string> = {
  good: 'text-emerald-600 dark:text-emerald-400',
  bad: 'text-rose-600 dark:text-rose-400',
  plain: 'text-slate-200',
};

export function HistoryTab() {
  const { data: runs, isLoading, error } = useRuns();
  const base = useBaseline();
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  const qc = useQueryClient();
  const [open, setOpen] = useState<number | null>(null);
  const { picker, modal } = useQuestionPicker();
  const setBase = useMutation({
    mutationFn: (id: number) => setGateBaseline(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-gate-runs'] });
      qc.invalidateQueries({ queryKey: ['eval-gate-baseline'] });
    },
    onError: (err) => showError(err, '비교 기준을 바꾸지 못했습니다'),
  });
  const askBase = async (run: GateRunListItem) => {
    const acc = run.answers_total?.after ?? run.summary.answers?.accuracy;
    const ok = await showConfirm(
      `앞으로 바꾼 결과를 이 측정(${run.label}${acc != null ? ` · 정답률 ${pct(acc)}` : ''})과 비교합니다.` +
      (base.data ? ` 지금 비교 기준(${base.data.label})은 이력으로 남습니다.` : ''),
      { title: '비교 기준 바꾸기', confirmLabel: '이걸 비교 기준으로' });
    if (ok) setBase.mutate(run.id);
  };
  if (isLoading || error) return <Loading error={error} />;
  if (!runs?.length) return <Empty />;
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-3 text-sm">
        {base.data ? (
          <p className="text-slate-200">
            <Star className="inline w-3.5 h-3.5 mr-1 text-amber-500 dark:text-amber-400" />
            지금 비교 기준: <b>{base.data.label}</b> · 정답률 {pct(base.data.summary.answers?.accuracy)}
            <span className="ml-2 text-xs text-slate-400" title="무언가를 바꾸면 그 결과를 이 측정과 같은 문항끼리 비교해 통과·실패를 정합니다. 통과한 판정을 기준으로 바꿀 수 있어요">다음 변경은 이 숫자와 비교해요 ⓘ</span>
          </p>
        ) : <p className="text-slate-400">비교 기준이 아직 없습니다.</p>}
      </div>
      <ul className="space-y-2">
        {runs.map((run) => {
          const h = headline(run);
          const expanded = open === run.id;
          const t = run.answers_total;
          return (
            <li key={run.id} className="rounded-lg border border-slate-700" data-testid={`gate-run-${run.id}`}>
              <button className="w-full text-left px-4 py-3 flex flex-wrap items-center gap-x-3 gap-y-1 hover:bg-slate-800/60" onClick={() => setOpen(expanded ? null : run.id)}
                aria-expanded={expanded}>
                {expanded ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
                <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${run.verdict === 'pass' ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                  : run.verdict === 'fail' ? 'bg-rose-500/15 text-rose-600 dark:text-rose-400' : 'bg-slate-700 text-slate-300'}`}>
                  {run.verdict === 'pass' ? '✔ 통과' : run.verdict === 'fail' ? '✖ 실패' : '측정'}
                </span>
                <span className="text-xs text-slate-500 whitespace-nowrap">{fmtDate(run.run_at)}</span>
                <span className="text-sm text-slate-100 min-w-0"><span>{run.label}</span>
                  {run.is_baseline && <span className="ml-2 text-xs text-amber-600 dark:text-amber-400"><Star className="inline w-3 h-3 mr-0.5" />비교 기준</span>}
                  <Warnings run={run} />
                </span>
                <span className={`text-sm ml-auto ${TONE[h.tone]}`}>{h.text}</span>
                {t && <span className="text-xs text-slate-400 whitespace-nowrap">좋아짐 {t.up} · 나빠짐 {t.down}</span>}
              </button>
              {expanded && (
                <div className="border-t border-slate-800 px-4 py-3 space-y-3">
                  <RunDetail id={run.id} onPick={picker(run.id)} />
                  {isAdmin && !run.is_baseline && run.verdict !== 'fail' && (
                    <div className="flex justify-end">
                      <button className="text-xs text-indigo-500 dark:text-indigo-400 hover:underline disabled:opacity-50" disabled={setBase.isPending}
                        title="앞으로 바꾼 결과를 이 측정과 비교합니다" onClick={() => askBase(run)}>
                        이걸 비교 기준으로
                      </button>
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {modal}
    </div>
  );
}
