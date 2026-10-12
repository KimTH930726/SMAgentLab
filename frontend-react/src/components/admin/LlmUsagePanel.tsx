import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getLlmUsage, type LlmUsageFeature } from '../../api/llm';

/**
 * 사내 LLM 사용량(2026-10-12) — 기능별 호출 수·토큰(게이트웨이가 주면 토큰, 아니면 글자)·평균 시간·실패, 읽기 전용.
 * 막힌 일: "토큰이 너무 많이 나간다"는데 어느 기능이 얼마나 쓰는지 숫자가 없어 무엇부터 줄일지 정할 수 없었다.
 */
const fmt = (n: number | null | undefined) => (n == null ? '-' : Math.round(n).toLocaleString());

function UsageBar({ value, max }: { value: number; max: number }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-slate-700 overflow-hidden">
      <div className="h-full bg-indigo-500 dark:bg-indigo-400" style={{ width: `${max ? Math.max(2, (value / max) * 100) : 0}%` }} />
    </div>
  );
}

export function LlmUsagePanel() {
  const [days, setDays] = useState(7);
  const { data, isLoading, error } = useQuery({
    queryKey: ['llm-usage', days],
    queryFn: () => getLlmUsage(days),
    staleTime: 30_000,
  });
  const rows: LlmUsageFeature[] = data?.features ?? [];
  const total = (r: LlmUsageFeature) => (r.input_tokens ?? 0) + (r.output_tokens ?? 0);
  const max = Math.max(0, ...rows.map(total));
  const sum = rows.reduce((a, r) => ({ calls: a.calls + r.calls, tokens: a.tokens + total(r), failed: a.failed + r.failed, aborted: a.aborted + (r.aborted ?? 0) }),
    { calls: 0, tokens: 0, failed: 0, aborted: 0 });

  return (
    <section data-testid="llm-usage" className="bg-slate-800 border border-slate-700 rounded-xl p-5 space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm font-semibold text-slate-200">사내 LLM 사용량</h3>
        <span className="text-[11px] text-slate-500 tabular-nums"
          title="호출 1건마다 기능·토큰(게이트웨이 metadata.usage)·시간을 기록합니다. 본문은 저장하지 않습니다(2026-10-12부터). 중단 = 사용자가 답변을 멈춘 호출(실패 아님).">
          최근 {days}일 · 호출 {fmt(sum.calls)} · 토큰 {fmt(sum.tokens)}{sum.failed ? ` · 실패 ${fmt(sum.failed)}` : ''}{sum.aborted ? ` · 중단 ${fmt(sum.aborted)}` : ''}
        </span>
        <select id="llm-usage-days" value={days} onChange={(e) => setDays(Number(e.target.value))}
          className="ml-auto bg-slate-900 border border-slate-600 rounded px-2 py-0.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500">
          <option value={1}>1일</option><option value={7}>7일</option><option value={30}>30일</option>
        </select>
      </div>
      {isLoading && <p className="text-xs text-slate-500">불러오는 중…</p>}
      {error && <p className="text-xs text-rose-600 dark:text-rose-400">사용량을 불러오지 못했습니다: {(error as Error).message}</p>}
      {!isLoading && !error && rows.length === 0 && <p className="text-xs text-slate-500">아직 기록이 없습니다.</p>}
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs tabular-nums">
            <thead>
              <tr className="text-slate-500 text-left border-b border-slate-700">
                <th className="py-1.5 pr-3 font-medium">기능</th>
                <th className="py-1.5 px-2 font-medium text-right">호출</th>
                <th className="py-1.5 px-2 font-medium text-right" title="게이트웨이가 알려준 입력 토큰">입력 토큰</th>
                <th className="py-1.5 px-2 font-medium text-right" title="게이트웨이가 알려준 출력 토큰">출력 토큰</th>
                <th className="py-1.5 px-2 font-medium text-right" title="호출 1건 평균 입력 토큰">건당 입력</th>
                <th className="py-1.5 px-2 font-medium text-right">평균 시간</th>
                <th className="py-1.5 px-2 font-medium text-right">실패</th>
                <th className="py-1.5 pl-2 font-medium w-28">비중</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.feature} className="border-b border-slate-800 text-slate-300">
                  <td className="py-1.5 pr-3 text-slate-200">{r.feature}</td>
                  <td className="py-1.5 px-2 text-right">{fmt(r.calls)}</td>
                  <td className="py-1.5 px-2 text-right" title={r.with_tokens < r.calls ? `토큰 정보 없는 호출 ${r.calls - r.with_tokens}건 — 입력 글자 ${fmt(r.input_chars)}` : ''}>
                    {fmt(r.input_tokens)}{r.with_tokens < r.calls ? '*' : ''}
                  </td>
                  <td className="py-1.5 px-2 text-right">{fmt(r.output_tokens)}</td>
                  <td className="py-1.5 px-2 text-right">{r.with_tokens ? fmt((r.input_tokens ?? 0) / r.with_tokens) : '-'}</td>
                  <td className="py-1.5 px-2 text-right">{r.avg_ms != null ? `${(r.avg_ms / 1000).toFixed(1)}초` : '-'}</td>
                  <td className={`py-1.5 px-2 text-right ${r.failed ? 'text-rose-600 dark:text-rose-400' : ''}`}>{fmt(r.failed)}</td>
                  <td className="py-1.5 pl-2"><UsageBar value={total(r)} max={max} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
