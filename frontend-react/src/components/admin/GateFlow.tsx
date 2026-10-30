import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { ChevronRight } from 'lucide-react';
import { getOverrides, type GateAxis, type GateReport } from '../../api/eval';
import { useAuthStore } from '../../store/useAuthStore';

/**
 * 평가 게이트 단계 줄(10/27 밤 → 10/29 한 템플릿) — 적재 → 검색 → 근거 판정 → 답 모양 → 생성. 칸 숫자 = 그 단계에서 처음 틀린 문항(원인 → 단계),
 * 아래 [개선할 곳] 원인 줄과 같은 수(합 = 진단한 틀린 문항). 적재만 기준이 다르다 — 적재 검사(저장이 설계와 다름)에 걸린 틀린 문항이라
 * 다른 칸과 겹칠 수 있어 "겹침"으로 표시. 누르면 [개선할 곳]이 그 단계 원인만 보인다(다시 누르면 전체).
 */

export const STAGES = [
  { key: '적재', en: 'INGESTION' }, { key: '검색', en: 'RETRIEVAL' }, { key: '근거 판정', en: 'GROUNDING' },
  { key: '답 모양', en: 'RESPONSE' }, { key: '생성', en: 'GENERATION' },
] as const;
export type Stage = typeof STAGES[number]['key'];

/** 틀린 문항 원인 → 단계(trustkit.eval.diagnose CAUSES) · 진단 원인이 아닌 후보 묶음(적재 검사·운영 번복)은 적재 */
export const CAUSE_STAGE: Record<string, Stage> = {
  retrieval_miss: '검색', answered_no_answer: '근거 판정', list_incomplete: '답 모양',
  wrong_with_context: '생성', refused_with_context: '생성', ingest: '적재', override_location: '적재',
};
/** 번복 종류 → 단계 */
const KIND_STAGE: Record<string, Stage> = { knowledge_location: '적재', source_check: '적재', chat_clarify: '답 모양', collection_suggest: '검색' };

/** 적재 검사 후보가 걸린 틀린 문항 — 단계 줄과 [개선할 곳]이 같은 규칙 */
export function ingestQids(rep: GateReport): Set<string> {
  const ids = new Set(rep.techniques.filter((t) => t.cause === 'ingest').map((t) => t.id));
  return new Set(rep.failed.filter((r) => r.techniques.some((t) => ids.has(t.id))).map((r) => r.qid));
}

// 위치 판정 번복은 업무지식 축의 적재 몫(번복 후보도 업무지식 진단에서 만든다) — 정책서 축 적재 칸엔 안 붙인다
const KIND_AXIS: Record<string, GateAxis> = { knowledge_location: 'knowledge' };

export function GateFlow({ report, stage, onStage, axis }: {
  report: GateReport | null | undefined; stage: Stage | null; onStage: (s: Stage | null) => void; axis: GateAxis;
}) {
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  const ov = useQuery({ queryKey: ['ai-overrides', 30], queryFn: () => getOverrides(30), enabled: isAdmin, staleTime: 30_000 });
  const failed: Partial<Record<Stage, number>> = {};
  for (const r of report?.failed ?? []) {
    const s = r.cause ? CAUSE_STAGE[r.cause] : undefined;
    if (s) failed[s] = (failed[s] ?? 0) + 1;
  }
  const ingestN = report ? ingestQids(report).size : 0;
  const over: Partial<Record<Stage, number>> = {};
  for (const k of ov.data?.kinds ?? []) {
    const s = KIND_STAGE[k.kind];
    if (s && k.overrides && (KIND_AXIS[k.kind] ?? axis) === axis) over[s] = (over[s] ?? 0) + k.overrides;
  }
  return (
    <div className="flex flex-wrap items-stretch gap-1" role="group" aria-label="단계 — 누르면 개선할 곳을 그 단계로" data-testid="gate-flow">
      {STAGES.map((s, i) => {
        const active = stage === s.key;
        const f = s.key === '적재' ? ingestN : failed[s.key] ?? 0;
        return (
          <div key={s.key} className="flex items-center gap-1 flex-1 min-w-[120px]">
            <button onClick={() => onStage(active ? null : s.key)} aria-pressed={active} data-testid={`flow-${s.key}`}
              className={clsx('flex-1 rounded-lg border px-3 py-1.5 text-left transition-colors',
                active ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500 dark:border-indigo-400 dark:bg-indigo-500/10 dark:ring-indigo-400'
                  : 'border-slate-700 bg-slate-800 hover:border-slate-500')}
              title={s.key === '적재' ? '적재 검사(저장이 설계와 다름)에 걸린 틀린 문항 — 다른 단계 칸과 겹칠 수 있음' : '이 단계에서 처음 틀린 문항 — 개선할 곳의 원인 줄과 같은 수'}>
              <span className="block text-[10px] tracking-wider text-slate-500">{s.en}</span>
              <span className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-semibold text-slate-100">{s.key}</span>
                <span className={clsx('text-sm font-semibold tabular-nums', f ? 'text-rose-600 dark:text-rose-400' : 'text-slate-500')}>{f}</span>
              </span>
              <span className="block text-[11px] text-slate-500">
                {s.key === '적재' ? '검사 걸림 · 겹침' : '틀림'}
                {(over[s.key] ?? 0) > 0 && <span className="text-amber-700 dark:text-amber-300" title="AI 감사 로그 — 사람이 AI 추천을 번복한 수(최근 30일)"> · 번복 {over[s.key]}</span>}
              </span>
            </button>
            {i < STAGES.length - 1 && <ChevronRight className="w-4 h-4 shrink-0 text-slate-600" aria-hidden />}
          </div>
        );
      })}
    </div>
  );
}
