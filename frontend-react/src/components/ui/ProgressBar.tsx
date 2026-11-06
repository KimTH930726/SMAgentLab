import { useEffect, useState } from 'react';

/** 오래 걸리는 작업 진행 막대(11/05 태훈 "멈춘 것처럼 보인다 — 진행률 보여 주는 UI는 있어야지").
 *  done/total이 있으면 막대 + 경과 + 남은 시간 예상(지금까지 속도로), 없으면(total 0·단계만) 움직이는 막대 + 경과.
 *  서버가 진행을 기록하고 화면이 몇 초마다 읽는 방식에 붙인다 — 상태는 서버에(새로고침해도 이어 보임). */
export function ProgressBar({ label, done = 0, total = 0, startedAt, unit = '', detail }: {
  label: string; done?: number; total?: number; startedAt?: string | null; unit?: string; detail?: string;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const started = startedAt ? new Date(startedAt).getTime() : NaN;
  const elapsed = Number.isFinite(started) ? Math.max(0, (now - started) / 1000) : null;
  const known = total > 0;
  const pct = known ? Math.min(100, (100 * done) / total) : 0;
  const eta = known && elapsed != null && done > 0 && done < total ? (elapsed / done) * (total - done) : null;
  return (
    <div className="space-y-1.5 rounded-lg border border-indigo-300 bg-indigo-50 p-3 dark:border-indigo-500/40 dark:bg-indigo-500/10" role="status" aria-live="polite"
      data-testid="progress">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span className="font-semibold text-slate-100">{label}{known && <span className="ml-2 tabular-nums">{done.toLocaleString()} / {total.toLocaleString()}{unit}</span>}</span>
        <span className="text-xs tabular-nums text-slate-400">
          {elapsed != null && `경과 ${fmt(elapsed)}`}{eta != null && ` · 남은 약 ${fmt(eta)}`}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-700">
        {known ? <div className="h-full bg-indigo-500 transition-[width] duration-500 dark:bg-indigo-400" style={{ width: `${pct}%` }} />
          : <div className="h-full w-1/3 animate-pulse rounded-full bg-indigo-500 dark:bg-indigo-400" />}
      </div>
      {detail && <div className="text-xs text-slate-400">{detail}</div>}
    </div>
  );
}

function fmt(sec: number): string {
  const s = Math.round(sec);
  return s < 60 ? `${s}초` : s < 3600 ? `${Math.floor(s / 60)}분 ${s % 60}초` : `${Math.floor(s / 3600)}시간 ${Math.floor((s % 3600) / 60)}분`;
}
