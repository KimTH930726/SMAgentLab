import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { ChevronRight } from 'lucide-react';
import { getGateReport, getOverrides } from '../../api/eval';
import { useAuthStore } from '../../store/useAuthStore';

/**
 * 평가 게이트 맨 위 — 질문이 답이 되는 단계 흐름 요약(10/27 밤, 옛 RAG 파이프라인 메뉴를 흡수). 단계마다 "틀림 n"(시험지 — 틀린 문항의 원인을 단계로)과
 * "번복 n"(운영 — AI 감사 로그의 번복을 단계로). 누르면 아래 리포트가 그 단계로 걸러진다. 어디를 손볼지 고르는 지도.
 */

export const STAGES = [
  { key: '적재', en: 'INGESTION' }, { key: '검색', en: 'RETRIEVAL' }, { key: '근거 판정', en: 'GROUNDING' },
  { key: '답 모양', en: 'RESPONSE' }, { key: '생성', en: 'GENERATION' },
] as const;
export type Stage = typeof STAGES[number]['key'];

/** 틀린 문항 원인 → 단계(trustkit.eval.diagnose CAUSES) */
export const CAUSE_STAGE: Record<string, Stage> = {
  retrieval_miss: '검색', answered_no_answer: '근거 판정', list_incomplete: '답 모양',
  wrong_with_context: '생성', refused_with_context: '생성',
};
/** 번복 종류 → 단계 */
const KIND_STAGE: Record<string, Stage> = { knowledge_location: '적재', source_check: '적재', chat_clarify: '답 모양', collection_suggest: '검색' };

export function GateFlow({ stage, onStage }: { stage: Stage | null; onStage: (s: Stage | null) => void }) {
  // 리포트·번복은 관리자 전용 API — 일반 사용자에겐 흐름을 안 그린다(403을 "틀림 0"으로 보이지 않게, 리뷰 10/27 M3)
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  const rep = useQuery({ queryKey: ['eval-report'], queryFn: getGateReport, enabled: isAdmin, staleTime: 30_000 });
  const ov = useQuery({ queryKey: ['ai-overrides', 30], queryFn: () => getOverrides(30), enabled: isAdmin, staleTime: 30_000 });
  const failed: Partial<Record<Stage, number>> = {};
  for (const r of rep.data?.failed ?? []) {
    const s = r.cause ? CAUSE_STAGE[r.cause] : undefined;
    if (s) failed[s] = (failed[s] ?? 0) + 1;
  }
  // 적재는 시험지 원인이 아니라 적재 검수(어긋난 검사로 만든 후보)로 — 저장이 설계와 다른 곳
  const ingestBad = (rep.data?.techniques ?? []).filter((t) => t.cause === 'ingest').length;
  const over: Partial<Record<Stage, number>> = {};
  for (const k of ov.data?.kinds ?? []) {
    const s = KIND_STAGE[k.kind];
    if (s && k.overrides) over[s] = (over[s] ?? 0) + k.overrides;
  }
  if (!isAdmin) return null;
  if (rep.error || ov.error) return <p className="text-xs text-rose-600 dark:text-rose-400">단계 흐름을 불러오지 못했습니다: {((rep.error ?? ov.error) as Error).message}</p>;
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-stretch gap-1" role="group" aria-label="단계 흐름 — 누르면 리포트를 그 단계로">
        {STAGES.map((s, i) => {
          const active = stage === s.key;
          const f = s.key === '적재' ? ingestBad : failed[s.key] ?? 0;
          return (
            <div key={s.key} className="flex items-center gap-1 flex-1 min-w-[140px]">
              <button onClick={() => onStage(active ? null : s.key)} aria-pressed={active} data-testid={`flow-${s.key}`}
                className={clsx('flex-1 rounded-xl border px-3 py-2 text-left transition-colors',
                  active ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500 dark:border-indigo-400 dark:bg-indigo-500/10 dark:ring-indigo-400'
                    : 'border-slate-700 bg-slate-800 hover:border-slate-500')}>
                <span className="block text-[10px] tracking-wider text-slate-500">{s.en}</span>
                <span className="block text-sm font-semibold text-slate-100">{s.key}</span>
                <span className="block text-xs mt-0.5">
                  <span className={f ? 'text-rose-600 dark:text-rose-400' : 'text-slate-500'}
                    title={s.key === '적재' ? '적재 검수에서 어긋난 검사(설계 완료 기준과 다른 저장)' : '이 단계에서 처음 실패한 틀린 문항(시험지)'}>
                    {s.key === '적재' ? '어긋남' : '틀림'} {f}</span>
                  {(over[s.key] ?? 0) > 0 && <span className="text-amber-700 dark:text-amber-300" title="AI 감사 로그 — 사람이 AI 추천을 번복한 수(최근 30일, 진단은 90일)"> · 번복 {over[s.key]}</span>}
                </span>
              </button>
              {i < STAGES.length - 1 && <ChevronRight className="w-4 h-4 shrink-0 text-slate-600" aria-hidden />}
            </div>
          );
        })}
      </div>
      <p className="text-[11px] text-slate-500">단계를 누르면 아래 리포트가 그 단계로 걸러져요 · 틀림 = 시험지, 번복 = 운영(AI 감사 로그)</p>
    </div>
  );
}
