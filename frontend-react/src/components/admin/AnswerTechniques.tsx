import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { ChevronRight } from 'lucide-react';
import { getBaselineRun, listTechniques, type Technique } from '../../api/eval';
import { Modal } from '../ui/Modal';
import { TechniqueCard } from './TechniqueCard';

/**
 * 품질 › RAG 파이프라인 (v2.171 "답 기법" → 10/27 이름·화면 바꿈, 사용자 "답이 만들어지는 과정 — AI 업계 용어로", "flow를 가시적으로, 누르면 세부로").
 * 위: 단계 박스를 화살표로 잇고(Ingestion → Retrieval → Grounding → Response → Generation — LangSmith·Phoenix 트레이스의 단계 줄을 참고),
 * 박스마다 대표 숫자 하나(검색 = 정답 근거 넘김, 생성 = 답 정답률 — 평가 게이트 기준선). 단계를 누르면 아래에 그 단계 항목 카드, 카드를 누르면 설명 창.
 * 대장 하나가 기준(backend/service/eval/techniques.py) — 값은 여기서 바꾸지 않는다(리포트의 고칠 방법에서 실험·측정 후).
 */

const STAGES = [
  { key: '적재', en: 'Ingestion', hint: '정책·지식을 넣을 때 — 어디에, 어떤 모양으로 저장하나' },
  { key: '검색', en: 'Retrieval', hint: '질문에 맞는 정책·지식을 찾아 AI에게 넘길 근거를 만든다' },
  { key: '근거 판정', en: 'Grounding', hint: '넘길 근거가 너무 약하면 LLM을 부르지 않고 "찾지 못했습니다"' },
  { key: '답 모양', en: 'Response', hint: '목록이 크면 되묻고, 목록 답이면 근거를 거른다' },
  { key: '생성', en: 'Generation', hint: 'AI에게 주는 답 지시(프롬프트)' },
] as const;
type StageKey = typeof STAGES[number]['key'];

// 꺼짐·안 함이 값에 적힌 항목만 표시 — 나머지는 '쓰는 중'이라 단정하지 않는다(실험만 한 항목을 쓰는 중으로 보이던 것, 10/27)
const isOff = (t: Technique) => t.values.some((v) => v.value === '꺼짐' || v.value.includes('기본 꺼짐') || v.value.startsWith('안 함'));
const pct = (x: number | null | undefined) => (x == null ? null : `${(x * 100).toFixed(1)}%`);

export function AnswerTechniques() {
  const { data = [], isLoading, error } = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, staleTime: 60_000 });
  const base = useQuery({ queryKey: ['eval-gate-baseline'], queryFn: getBaselineRun, staleTime: 60_000 });
  const [stage, setStage] = useState<StageKey>('검색');
  const [open, setOpen] = useState<Technique | null>(null);
  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(error as Error).message}</p>;

  const sum = base.data?.summary;
  // 박스의 대표 숫자 — 잰 값이 있는 단계만(기준선 판정 기록에서). 없으면 항목 수만
  const metric: Partial<Record<StageKey, { label: string; value: string }>> = {};
  if (pct(sum?.retrieval?.recall)) metric['검색'] = { label: '정답 근거 넘김', value: pct(sum?.retrieval?.recall)! };
  if (pct(sum?.answers?.accuracy)) metric['생성'] = { label: '답 정답률', value: pct(sum?.answers?.accuracy)! };
  const of = (s: StageKey) => data.filter((t) => t.stage === s);
  const cur = STAGES.find((s) => s.key === stage)!;
  const items = of(stage);

  return (
    <div className="space-y-4">
      {base.error && <p className="text-xs text-amber-700 dark:text-amber-300">기준선을 불러오지 못해 단계 숫자 대신 항목 수를 보여요: {(base.error as Error).message}</p>}
      <div className="flex flex-wrap items-stretch gap-1" role="group" aria-label="RAG 파이프라인 단계">
        {STAGES.map((s, i) => {
          const n = of(s.key).length;
          const off = of(s.key).filter(isOff).length;
          const m = metric[s.key];
          const active = stage === s.key;
          return (
            <div key={s.key} className="flex items-center gap-1 flex-1 min-w-[150px]">
              <button aria-pressed={active} onClick={() => setStage(s.key)} title={s.hint} data-testid={`stage-${s.key}`}
                className={clsx('flex-1 rounded-xl border px-3 py-3 text-left space-y-1 transition-colors',
                  active ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500 dark:border-indigo-400 dark:bg-indigo-500/10 dark:ring-indigo-400'
                    : 'border-slate-700 bg-slate-800 hover:border-slate-500')}>
                <span className="block text-[11px] uppercase tracking-wider text-slate-500">{s.en}</span>
                <span className="block text-sm font-semibold text-slate-100">{s.key}</span>
                {m
                  ? <span className="block text-lg font-semibold tabular-nums text-slate-100">{m.value}<span className="ml-1 text-[11px] font-normal text-slate-500">{m.label}</span></span>
                  : <span className="block text-lg font-semibold tabular-nums text-slate-300">{n}<span className="ml-1 text-[11px] font-normal text-slate-500">항목</span></span>}
                <span className="block text-[11px] text-slate-500">{[m ? `항목 ${n}` : null, off > 0 ? `꺼짐 ${off}` : null].filter(Boolean).join(' · ') || ' '}</span>
              </button>
              {i < STAGES.length - 1 && <ChevronRight className="w-4 h-4 shrink-0 text-slate-600" aria-hidden />}
            </div>
          );
        })}
      </div>

      <section className="rounded-xl border border-slate-700 bg-slate-900/40 p-4 space-y-3" aria-label={`${cur.key} 항목`} aria-live="polite">
        <div className="flex flex-wrap items-baseline gap-x-3">
          <h3 className="text-sm font-semibold text-slate-100">{cur.key} <span className="text-xs font-normal text-slate-500">{cur.en}</span></h3>
          <p className="text-xs text-slate-400">{cur.hint}</p>
        </div>
        {items.length === 0 ? <p className="text-sm text-slate-500">이 단계엔 아직 등록된 항목이 없어요.</p> : (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2">
            {items.map((t) => (
              <button key={t.key} onClick={() => setOpen(t)} data-testid={`technique-row-${t.key}`}
                className="rounded-lg border border-slate-700 bg-slate-800 p-3 text-left space-y-1.5 hover:border-indigo-500 dark:hover:border-indigo-400 min-w-0">
                <span className="flex items-start justify-between gap-2">
                  <span className="text-sm text-slate-100 min-w-0">{t.name}</span>
                  {isOff(t) && <span className="shrink-0 rounded px-1.5 py-0.5 text-[11px] bg-slate-700 text-slate-400">꺼짐</span>}
                </span>
                <span className="block text-xs text-slate-400 line-clamp-2">{t.does}</span>
                <span className="flex flex-wrap gap-x-3 text-xs text-slate-300">
                  {t.values.slice(0, 2).map((v) => <span key={v.label} className="truncate max-w-full">{v.label} <b>{v.value}</b></span>)}
                </span>
                <span className="block text-[11px] text-slate-500" title="도입·보류 근거로 남긴 측정 수">근거 {t.evidence.length}</span>
              </button>
            ))}
          </div>
        )}
      </section>

      <Modal isOpen={open !== null} onClose={() => setOpen(null)} title={open?.name ?? ''} maxWidth="max-w-2xl">
        {open && <TechniqueCard t={open} />}
      </Modal>
    </div>
  );
}
