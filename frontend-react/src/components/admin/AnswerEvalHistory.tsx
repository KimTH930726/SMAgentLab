import { useQuery } from '@tanstack/react-query';
import { Info, TrendingDown, TrendingUp, Minus, SearchCheck } from 'lucide-react';
import { getAnswerEvalHistory, type AnswerEvalRun } from '../../api/policy';

/**
 * 최종 답변 정확도(2026-10-07) — 골든셋 문항을 실제 채팅 경로로 답하게 하고 로컬 LLM이 정책 원문과 대조해 채점한 집계.
 * 검색 지표(골든셋 실행 탭의 hit@K)는 "정답 근거가 AI에게 갔나"까지만 보여서, "답이 맞았나"는 여기서 본다.
 * 실행은 화면에서 하지 않는다(문항당 사내 LLM 호출 — 수십 분): backend/scripts/eval_answers.py collect → judge → report --save.
 * 화면 구성(2026-10-07 사용자 요청 "표 말고 한눈에"): 최신 정답률 → 유형별 → 답변 단계 문제 → 추이.
 */

const LABELS = [
  { key: '정답', bar: 'bg-emerald-500 dark:bg-emerald-400', text: 'text-emerald-600 dark:text-emerald-400', hint: '원문과 맞는 답' },
  { key: '부분', bar: 'bg-amber-400 dark:bg-amber-300', text: 'text-amber-600 dark:text-amber-400', hint: '맞지만 빠진 내용이 있음' },
  { key: '오답', bar: 'bg-rose-500 dark:bg-rose-400', text: 'text-rose-600 dark:text-rose-400', hint: '원문과 다른 답' },
  { key: '거절', bar: 'bg-slate-500', text: 'text-slate-400', hint: '"관련 지식을 찾지 못했습니다"로 답함' },
] as const;

const TYPES: Record<string, { name: string; hint: string }> = {
  param: { name: '값 조회', hint: '"배송비 기준 금액이 얼마야?"처럼 정해진 값을 묻는 질문' },
  narrative: { name: '설명형', hint: '절차·규칙을 설명해 달라는 질문' },
  condition_filter: { name: '조건형', hint: '"3만 원 넘을 때만"처럼 조건이 붙은 질문' },
  navigation: { name: '목록형', hint: '"○○ 관련 정책 전부 보여줘"처럼 한 분류를 다 보여 달라는 질문' },
};
const TYPE_ORDER = ['param', 'narrative', 'condition_filter', 'navigation'];

const rate = (n: number, d: number) => (d ? Math.round((n / d) * 100) : 0);
const correct = (r: AnswerEvalRun) => r.counts['정답'] ?? 0;
const fmtDate = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

function StackBar({ counts, total, height = 'h-3' }: { counts: Record<string, number>; total: number; height?: string }) {
  return (
    <div className={`flex w-full ${height} rounded-full overflow-hidden bg-slate-700`}>
      {LABELS.map((l) => {
        const n = counts[l.key] ?? 0;
        if (!n || !total) return null;
        return <div key={l.key} className={l.bar} style={{ width: `${(n / total) * 100}%` }} title={`${l.key} ${n}`} />;
      })}
    </div>
  );
}

function Delta({ now, prev, unit = '문항' }: { now: number; prev?: number; unit?: string }) {
  if (prev === undefined) return null;
  const d = now - prev;
  const Icon = d > 0 ? TrendingUp : d < 0 ? TrendingDown : Minus;
  const cls = d > 0 ? 'text-emerald-600 dark:text-emerald-400' : d < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-500';
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${cls}`} title="직전 운영 측정 대비">
      <Icon className="w-3.5 h-3.5" />
      {d > 0 ? `+${d}` : d}{unit}
    </span>
  );
}

function TrendChart({ runs }: { runs: AnswerEvalRun[] }) {
  // 오래된 → 최신. 정답률(%) 한 줄 + 점마다 값
  const W = 640, H = 170, L = 36, R = 16, T = 18, B = 26;
  const pts = runs.map((r, i) => ({
    x: runs.length === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (runs.length - 1),
    y: T + (1 - rate(correct(r), r.total_n) / 100) * (H - T - B),
    v: rate(correct(r), r.total_n),
    r,
  }));
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join(' ');
  const area = `${path} L${pts[pts.length - 1].x},${H - B} L${pts[0].x},${H - B} Z`;
  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[420px] h-auto text-slate-500" role="img" aria-label="정답률 추이">
        {[0, 25, 50, 75, 100].map((g) => {
          const y = T + (1 - g / 100) * (H - T - B);
          return (
            <g key={g}>
              <line x1={L} x2={W - R} y1={y} y2={y} stroke="currentColor" strokeOpacity={g === 0 ? 0.5 : 0.15} />
              <text x={L - 6} y={y + 3} textAnchor="end" fontSize="10" fill="currentColor">{g}%</text>
            </g>
          );
        })}
        <path d={area} className="fill-emerald-500/10 dark:fill-emerald-400/10" />
        <path d={path} fill="none" strokeWidth={2} className="stroke-emerald-500 dark:stroke-emerald-400" />
        {pts.map((p, i) => (
          <g key={p.r.id}>
            <circle cx={p.x} cy={p.y} r={i === pts.length - 1 ? 5 : 3.5} className="fill-emerald-500 dark:fill-emerald-400" />
            <text x={p.x} y={p.y - 9} textAnchor="middle" fontSize="11" fontWeight={600} className="fill-slate-200">{p.v}%</text>
            <text x={p.x} y={H - 8} textAnchor="middle" fontSize="10" fill="currentColor">#{i + 1}</text>
            <title>{`${p.r.label} — ${correct(p.r)}/${p.r.total_n}`}</title>
          </g>
        ))}
      </svg>
    </div>
  );
}

export function AnswerEvalHistory() {
  const { data: runs = [], isLoading, error } = useQuery({
    queryKey: ['answer-eval-history'],
    queryFn: () => getAnswerEvalHistory(30),
    staleTime: 30_000,
  });

  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">이력을 불러오지 못했습니다: {(error as Error).message}</p>;
  if (!runs.length) {
    return (
      <p className="text-sm text-slate-500">
        아직 측정 이력이 없습니다 — <code className="text-xs">backend/scripts/eval_answers.py</code>로 실행하면 여기에 쌓입니다.
      </p>
    );
  }

  // 최신 정답률·추이는 운영과 같은 조건(variant=after)만 — 비교 실험(before·no_defs 등)이 섞이면 헤드라인이 왜곡된다(리뷰 2026-10-07)
  const all = [...runs].sort((a, b) => a.run_at.localeCompare(b.run_at));
  const ops = all.filter((r) => r.variant === 'after');
  const experiments = all.filter((r) => r.variant !== 'after');
  const chrono = ops.length ? ops : all;
  const latest = chrono[chrono.length - 1];
  const prev = chrono.length > 1 ? chrono[chrono.length - 2] : undefined;
  const latestRate = rate(correct(latest), latest.total_n);
  const types = [...TYPE_ORDER.filter((t) => latest.by_type[t]), ...Object.keys(latest.by_type).filter((t) => !TYPE_ORDER.includes(t))];

  return (
    <div className="space-y-5">
      {/* 최신 측정 */}
      <section className="rounded-xl border border-slate-700 bg-slate-800 p-5">
        <div className="flex flex-wrap items-end gap-x-8 gap-y-4">
          <div>
            <p className="text-xs text-slate-500" title={latest.notes ?? ''}>최신 측정 · {fmtDate(latest.run_at)} · {latest.label}</p>
            <div className="flex items-baseline gap-3 mt-1">
              <span className="text-5xl font-bold tabular-nums text-slate-100">{latestRate}<span className="text-2xl text-slate-400">%</span></span>
              <Delta now={latestRate} prev={prev ? rate(correct(prev), prev.total_n) : undefined} unit="%p" />
            </div>
            <p className="text-sm text-slate-400 mt-1 tabular-nums">{latest.total_n}문항 중 <b className="text-slate-200">{correct(latest)}</b>문항 정답</p>
          </div>
          <div className="flex-1 min-w-[260px] space-y-2.5">
            <StackBar counts={latest.counts} total={latest.total_n} height="h-4" />
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {LABELS.map((l) => (
                <div key={l.key} className="flex items-center gap-2" title={l.hint}>
                  <span className={`w-2.5 h-2.5 rounded-sm ${l.bar}`} />
                  <span className="text-xs text-slate-400">{l.key}</span>
                  <span className={`text-sm font-semibold tabular-nums ${l.text}`}>{latest.counts[l.key] ?? 0}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* 유형별 */}
      <section className="space-y-2">
        <h4 className="text-sm font-semibold text-slate-300">질문 유형별 정답률</h4>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {types.map((t) => {
            const c = latest.by_type[t] ?? {};
            const tot = Object.values(c).reduce((a, b) => a + b, 0);
            const r = rate(c['정답'] ?? 0, tot);
            const pc = prev?.by_type[t]?.['정답'];
            const weak = r < 60;
            return (
              <div key={t} className="rounded-xl border border-slate-700 bg-slate-800 p-4 space-y-3" title={TYPES[t]?.hint ?? t}>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-200">{TYPES[t]?.name ?? t}</span>
                  {weak && <span className="text-[11px] px-1.5 py-0.5 rounded bg-rose-100 text-rose-700 dark:bg-rose-950/50 dark:text-rose-300">개선 필요</span>}
                </div>
                <div className="flex items-baseline gap-2">
                  <span className={`text-3xl font-bold tabular-nums ${weak ? 'text-rose-600 dark:text-rose-400' : 'text-slate-100'}`}>{r}%</span>
                  <span className="text-xs text-slate-500 tabular-nums">{c['정답'] ?? 0}/{tot}</span>
                  <span className="ml-auto"><Delta now={r} prev={pc === undefined || !prev ? undefined
                    : rate(pc, Object.values(prev.by_type[t] ?? {}).reduce((a, b) => a + b, 0))} unit="%p" /></span>
                </div>
                <StackBar counts={c} total={tot} height="h-2" />
                <p className="text-[11px] text-slate-500 tabular-nums">
                  {LABELS.filter((l) => c[l.key]).map((l) => `${l.key} ${c[l.key]}`).join(' · ')}
                </p>
              </div>
            );
          })}
        </div>
      </section>

      {/* 답변 단계 문제 */}
      <section className="flex items-start gap-3 rounded-xl border border-amber-300 dark:border-amber-700/60 bg-amber-50 dark:bg-amber-950/20 px-4 py-3">
        <SearchCheck className="w-5 h-5 mt-0.5 flex-shrink-0 text-amber-600 dark:text-amber-400" />
        <div className="text-sm">
          <p className="text-amber-800 dark:text-amber-200 font-medium tabular-nums">
            검색은 맞았는데 답이 틀린 문항 {latest.retrieval_ok_but_wrong}개
            <span className="font-normal text-amber-700/80 dark:text-amber-300/70"> (정답 근거가 AI에게 전달된 {latest.retrieval_ok}문항 중)</span>
          </p>
          <p className="text-xs text-amber-700/80 dark:text-amber-300/70 mt-0.5">검색이 아니라 답을 만드는 단계(프롬프트·답 형식)를 고쳐야 하는 문항입니다.</p>
        </div>
      </section>

      {/* 추이 */}
      {chrono.length > 1 && (
        <section className="rounded-xl border border-slate-700 bg-slate-800 p-5 space-y-4">
          <h4 className="text-sm font-semibold text-slate-300">정답률 추이</h4>
          <TrendChart runs={chrono} />
          <ol className="divide-y divide-slate-700/70">
            {[...chrono].reverse().map((r) => {
              const i = chrono.indexOf(r);
              const p = i > 0 ? chrono[i - 1] : undefined;
              return (
                <li key={r.id} className="py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5">
                  <span className="text-xs text-slate-500 tabular-nums w-6">#{i + 1}</span>
                  <div className="min-w-[180px] flex-1" title={r.notes ?? ''}>
                    <p className="text-sm text-slate-200">{r.label}</p>
                    <p className="text-[11px] text-slate-500">{fmtDate(r.run_at)} · {r.total_n}문항</p>
                  </div>
                  <div className="w-40 sm:w-56"><StackBar counts={r.counts} total={r.total_n} height="h-2" /></div>
                  <span className="text-sm font-semibold tabular-nums text-slate-100 w-12 text-right">{rate(correct(r), r.total_n)}%</span>
                  <span className="w-16 text-right"><Delta now={rate(correct(r), r.total_n)} prev={p ? rate(correct(p), p.total_n) : undefined} unit="%p" /></span>
                </li>
              );
            })}
          </ol>
        </section>
      )}

      {experiments.length > 0 && (
        <section className="rounded-xl border border-slate-700 p-4 space-y-2">
          <h4 className="text-sm font-semibold text-slate-300" title="운영과 다른 조건으로 돌린 비교 측정 — 위 추이에는 넣지 않음">비교 실험</h4>
          <ul className="divide-y divide-slate-700/70">
            {[...experiments].reverse().map((r) => (
              <li key={r.id} className="py-2 flex flex-wrap items-center gap-x-4 gap-y-1">
                <div className="min-w-[180px] flex-1" title={r.notes ?? ''}>
                  <p className="text-sm text-slate-300">{r.label} <span className="text-[11px] text-slate-500">({r.variant})</span></p>
                  <p className="text-[11px] text-slate-500">{fmtDate(r.run_at)} · {r.total_n}문항</p>
                </div>
                <div className="w-40 sm:w-56"><StackBar counts={r.counts} total={r.total_n} height="h-2" /></div>
                <span className="text-sm font-semibold tabular-nums text-slate-200 w-12 text-right">{rate(correct(r), r.total_n)}%</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <p className="flex items-center gap-1.5 text-[11px] text-slate-500"
        title="실행: backend/scripts/eval_answers.py collect → judge → report --save (문항당 사내 LLM 호출이라 화면에서 실행하지 않음)">
        <Info className="w-3.5 h-3.5" />
        채점은 로컬 LLM — 같은 설정으로도 몇 문항씩 흔들릴 수 있습니다(±2~3문항은 차이로 보지 않음).
      </p>
    </div>
  );
}
