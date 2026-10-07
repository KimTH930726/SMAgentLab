import { useQuery } from '@tanstack/react-query';
import { getAnswerEvalHistory, type AnswerEvalRun } from '../../api/policy';

/**
 * 최종 답변 정확도 이력(2026-10-07) — 골든셋 문항을 실제 채팅 경로로 답하게 하고 로컬 LLM이 정책 원문과 대조해 채점한 집계.
 * 검색 지표(골든셋 실행 탭의 hit@K)는 "정답 근거가 AI에게 갔나"까지만 보여서, "답이 맞았나"는 여기서 본다.
 * 실행은 화면에서 하지 않는다(문항당 사내 LLM 호출 — 수십 분): backend/scripts/eval_answers.py collect → judge → report --save.
 */
const LABELS: { key: string; label: string; cls: string }[] = [
  { key: '정답', label: '정답', cls: 'text-emerald-600 dark:text-emerald-400' },
  { key: '부분', label: '부분', cls: 'text-amber-600 dark:text-amber-400' },
  { key: '오답', label: '오답', cls: 'text-rose-600 dark:text-rose-400' },
  { key: '거절', label: '거절', cls: 'text-slate-400' },
];

const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : '-');

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
        아직 실행 이력이 없습니다 — <code className="text-xs">backend/scripts/eval_answers.py</code>로 실행하면 여기에 쌓입니다.
      </p>
    );
  }
  const types = Array.from(new Set(runs.flatMap((r) => Object.keys(r.by_type)))).sort();

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-xl border border-slate-700">
        <table className="w-full text-xs tabular-nums">
          <thead>
            <tr className="text-slate-500 text-left border-b border-slate-700">
              <th className="py-2 px-3 font-medium">실행</th>
              <th className="py-2 px-2 font-medium" title="before = 예전 방식, after = 지금 운영, no_defs = 지금 운영에서 용어 설명만 뺌">버전</th>
              {LABELS.map((l) => <th key={l.key} className="py-2 px-2 font-medium text-right">{l.label}</th>)}
              <th className="py-2 px-2 font-medium text-right" title="정답 근거가 AI에게 전달됐는데도 정답이 아닌 문항 — 검색이 아니라 답변 단계 문제">근거 있는데 틀림</th>
              {types.map((t) => <th key={t} className="py-2 px-2 font-medium text-right" title={`${t} 유형 정답률`}>{t}</th>)}
            </tr>
          </thead>
          <tbody>
            {runs.map((r: AnswerEvalRun) => (
              <tr key={r.id} className="border-b border-slate-800 text-slate-300 align-top">
                <td className="py-2 px-3 whitespace-nowrap" title={r.notes ?? ''}>
                  <span className="text-slate-200">{r.label}</span>
                  <span className="block text-[11px] text-slate-500">{new Date(r.run_at).toLocaleString('ko-KR')} · {r.total_n}문항</span>
                </td>
                <td className="py-2 px-2">{r.variant}</td>
                {LABELS.map((l) => (
                  <td key={l.key} className={`py-2 px-2 text-right ${l.cls}`}>
                    {r.counts[l.key] ?? 0} <span className="text-slate-500">({pct(r.counts[l.key] ?? 0, r.total_n)})</span>
                  </td>
                ))}
                <td className="py-2 px-2 text-right">{r.retrieval_ok_but_wrong}/{r.retrieval_ok}</td>
                {types.map((t) => {
                  const c = r.by_type[t] ?? {};
                  const tot = Object.values(c).reduce((a, b) => a + b, 0);
                  return <td key={t} className="py-2 px-2 text-right">{pct(c['정답'] ?? 0, tot)}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-slate-500">채점은 로컬 LLM — 실행마다 사람 확인용 표본 5개가 함께 나옵니다(채점기가 맞는지 확인).</p>
    </div>
  );
}
