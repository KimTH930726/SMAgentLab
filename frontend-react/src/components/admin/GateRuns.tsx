import { Fragment, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Star } from 'lucide-react';
import { getBaselineRun, getGateRun, listGateRuns, setGateBaseline, type AnswerGroup, type GateRunDetail, type GateRunListItem } from '../../api/eval';
import { groupOrder, layerOf, mergeLayers } from '../../utils/evalLayers';
import { useAuthStore } from '../../store/useAuthStore';
import { showError } from '../../store/useDialogStore';

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

function QidList({ title, qids, hint }: { title: string; qids: string[]; hint: string }) {
  const [open, setOpen] = useState(false);
  if (!qids.length) return <p className="text-xs text-slate-500">{title}: 0문항</p>;
  return (
    <div className="text-xs">
      <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 text-slate-300 hover:text-slate-100" title={hint}>
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        {title}: {qids.length}문항
      </button>
      {open && <p className="mt-1 pl-5 text-slate-400 leading-relaxed break-all">{qids.join(' · ')}</p>}
    </div>
  );
}

export function AnswerTab() {
  const base = useBaseline();
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
        <p className="text-2xl font-semibold text-slate-100">
          정답률 {pct(s.accuracy)}
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
          hint="정답 정책이 문맥에 들어갔는데 회차 과반이 틀린 문항 — 답 생성·문맥 다듬기 쪽을 볼 후보(원문은 eval_out/ 결과 파일)" />
      </div>
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

function Verdict({ run }: { run: GateRunListItem }) {
  if (run.verdict === 'pass') return <span className="text-emerald-600 dark:text-emerald-400">통과</span>;
  if (run.verdict === 'fail') return <span className="text-rose-600 dark:text-rose-400">실패</span>;
  return <span className="text-slate-500" title="판정 없이 단독 측정">—</span>;
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
          <th className="text-right font-medium" title="같은 문항끼리 차이 · 95% 구간">차이 [구간] ⓘ</th>
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

function RunDetail({ id }: { id: number }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['eval-gate-run', id], queryFn: () => getGateRun(id), staleTime: 30_000 });
  if (isLoading || error || !data) return <Loading error={error} />;
  const r = data.retrieval;
  const a = data.answers;
  const jm = data.judge_meta;
  return (
    <div className="space-y-3 py-2 pl-6">
      {r && (
        <div className="text-xs text-slate-300 space-y-1">
          {r.error && <p className="text-rose-600 dark:text-rose-400">검색 판정 실패 — {r.error}(qid·mode 확인)</p>}
          <p>검색 Recall {r.before}/{r.n} → {r.after}/{r.n} · McNemar p={r.p.toFixed(3)}{r.significant ? ' (의미 있게 좋아짐)' : ''}</p>
          {Object.keys(r.by_type).length > 0 && (
            <p className="text-slate-400">
              {mergeLayers(r.by_type, (a, b) => ({ n: a.n + b.n, before: a.before + b.before, after: a.after + b.after }))
                .map(({ name, v }) => `${name} ${v.before}/${v.n} → ${v.after}/${v.n}`).join(' · ')}
            </p>
          )}
          <QidList title="검색 나빠짐" qids={r.down} hint="전엔 정답 정책이 문맥에 들어갔는데 후엔 빠진 문항 — 1건이라도 있으면 실패" />
          <QidList title="검색 좋아짐" qids={r.up} hint="전엔 빠졌는데 후엔 들어간 문항" />
        </div>
      )}
      {a && (
        <div className="space-y-1">
          <GroupTable groups={a.groups} />
          <QidList title="답 나빠짐" qids={a.down_qids} hint="문항별 회차 평균 정답이 내려간 문항(원문은 eval_out/ 결과 파일)" />
          <QidList title="답 좋아짐" qids={a.up_qids} hint="문항별 회차 평균 정답이 올라간 문항" />
        </div>
      )}
      {!r && !a && data.kind === 'baseline' && <p className="text-xs text-slate-400">단독 측정 — 정답률 {pct(data.summary.answers?.accuracy)} · Recall {pct(data.summary.retrieval?.recall)}</p>}
      <p className="text-xs text-slate-500">
        채점 {jm.judged_on?.join(', ') || '-'} · 기준 {jm.rubrics?.join(', ') || '-'}
        {jm.same_day_rubric != null && <> · 전/후 같은 날·같은 기준: {jm.same_day_rubric ? '예' : '아니오'}</>}
        {data.notes && <> · {data.notes}</>}
      </p>
    </div>
  );
}

export function HistoryTab() {
  const { data: runs, isLoading, error } = useRuns();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const qc = useQueryClient();
  const [open, setOpen] = useState<number | null>(null);
  const baseline = useMutation({
    mutationFn: (id: number) => setGateBaseline(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-gate-runs'] });
      qc.invalidateQueries({ queryKey: ['eval-gate-baseline'] });
    },
    onError: (err) => showError(err, '기준선을 바꾸지 못했습니다'),
  });
  if (isLoading || error) return <Loading error={error} />;
  if (!runs?.length) return <Empty />;
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-xs text-slate-400 border-b border-slate-700">
          <th className="text-left py-2 font-medium">날짜</th><th className="text-left font-medium">무엇을 바꿨나</th>
          <th className="text-left font-medium">결과</th>
          <th className="text-right font-medium" title="정답 정책이 문맥에 들어간 문항 — 좋아짐 / 나빠짐(1건이라도 나빠지면 실패)">검색 ⓘ</th>
          <th className="text-right font-medium" title="전체 정답률 차이 · 95% 구간(같은 문항끼리)">답 차이 [구간] ⓘ</th>
          <th className="text-left pl-4 font-medium">커밋</th><th />
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => {
          const t = run.answers_total;
          const expanded = open === run.id;
          return (
            <Fragment key={run.id}>
              <tr className="border-b border-slate-800 text-slate-200 cursor-pointer hover:bg-slate-800/60" onClick={() => setOpen(expanded ? null : run.id)}>
                <td className="py-2 whitespace-nowrap text-slate-400">
                  {expanded ? <ChevronDown className="inline w-3.5 h-3.5 mr-1" /> : <ChevronRight className="inline w-3.5 h-3.5 mr-1" />}
                  {fmtDate(run.run_at)}
                </td>
                <td>
                  <span>{run.label}</span>
                  {run.is_baseline && <span className="ml-2 text-xs text-amber-600 dark:text-amber-400"><Star className="inline w-3 h-3 mr-0.5" />기준선</span>}
                  <Warnings run={run} />
                </td>
                <td><Verdict run={run} /></td>
                <td className="text-right">
                  {run.kind === 'baseline' ? '—'
                    : !run.has_retrieval_gate ? <span className="text-slate-500" title="이 판정엔 검색 전/후 비교가 없음">-</span>
                    : `${run.retrieval_up} / ${run.retrieval_down}`}
                </td>
                <td className="text-right whitespace-nowrap">
                  {t ? `${pp(t.diff)}%p ${t.lo != null ? `[${pp(t.lo)}, ${pp(t.hi)}]` : '[계산불가]'}`
                    : run.kind === 'baseline' ? <span className="text-slate-500">단독 {pct(run.summary.answers?.accuracy)}</span> : '-'}
                </td>
                <td className="pl-4 text-xs text-slate-500">{run.git_commit ?? '-'}</td>
                <td className="text-right">
                  {isAdmin && !run.is_baseline && run.verdict !== 'fail' && (
                    <button
                      className="text-xs text-indigo-500 dark:text-indigo-400 hover:underline disabled:opacity-50"
                      disabled={baseline.isPending}
                      title="이 판정의 후(after) 측정을 다음 게이트의 기준으로"
                      onClick={(e) => { e.stopPropagation(); baseline.mutate(run.id); }}
                    >
                      기준선으로
                    </button>
                  )}
                </td>
              </tr>
              {expanded && (
                <tr className="border-b border-slate-800 bg-slate-900/40">
                  <td colSpan={7}><RunDetail id={run.id} /></td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
