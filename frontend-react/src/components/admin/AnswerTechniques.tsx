import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronRight } from 'lucide-react';
import { listTechniques, type Technique } from '../../api/eval';
import { Modal } from '../ui/Modal';
import { TechniqueCard } from './TechniqueCard';

/**
 * 품질 › 답 기법 (v2.171 → 10/27 다시 올림·단순화, 사용자 "가시성과 단순성이 핵심") — 질문이 답이 되는 흐름(적재 → 검색 → 근거 판정 → 답 모양 → 생성)
 * 한 줄 위에 기법 수, 아래는 기법 한 줄씩(이름 · 지금 값 · 켜짐/꺼짐), 누르면 창에 하는 일·왜 있나(측정 번호)·바꾸려면.
 * 대장 하나가 기준(backend/service/eval/techniques.py) — 값은 여기서 바꾸지 않는다(리포트의 고칠 방법에서 실험·측정 후).
 */

const STAGES = ['적재', '검색', '근거 판정', '답 모양', '생성'] as const;
const STAGE_HINT: Record<string, string> = {
  적재: '정책·지식을 넣을 때 — 어디에, 어떤 모양으로 저장하나',
  검색: '질문에 맞는 정책·지식을 찾아 AI에게 넘길 근거를 만든다',
  '근거 판정': '넘길 근거가 너무 약하면 LLM을 부르지 않고 "찾지 못했습니다"',
  '답 모양': '목록이 크면 되묻고, 목록 답이면 근거를 거른다',
  생성: 'AI에게 주는 답 지시(프롬프트)',
};

// 꺼짐·안 함이 값에 적힌 기법만 표시 — 나머지는 '쓰는 중'이라 단정하지 않는다(실험만 한 기법을 쓰는 중으로 보이던 것, 10/27)
const isOff = (t: Technique) => t.values.some((v) => v.value === '꺼짐' || v.value.includes('기본 꺼짐') || v.value.startsWith('안 함'));

export function AnswerTechniques() {
  const { data = [], isLoading, error } = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, staleTime: 60_000 });
  const [stage, setStage] = useState<string | null>(null);
  const [open, setOpen] = useState<Technique | null>(null);
  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(error as Error).message}</p>;
  const order = (t: Technique) => STAGES.indexOf(t.stage as typeof STAGES[number]);
  const shown = (stage ? data.filter((t) => t.stage === stage) : [...data]).sort((a, b) => order(a) - order(b));
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1" role="group" aria-label="흐름 단계">
        {STAGES.map((s, i) => {
          const n = data.filter((t) => t.stage === s).length;
          return (
            <span key={s} className="flex items-center gap-1">
              <button onClick={() => setStage(stage === s ? null : s)} aria-pressed={stage === s} title={STAGE_HINT[s]}
                className={`rounded-lg border px-3 py-2 text-sm ${stage === s ? 'border-indigo-500 text-indigo-600 dark:text-indigo-400' : 'border-slate-700 text-slate-200 hover:border-slate-500'}`}>
                {s} <span className="text-xs text-slate-500 tabular-nums">{n}</span>
              </button>
              {i < STAGES.length - 1 && <ChevronRight className="w-4 h-4 text-slate-600" aria-hidden />}
            </span>
          );
        })}
        {stage && <button className="ml-2 text-xs text-slate-400 hover:text-slate-200" onClick={() => setStage(null)}>전부 보기</button>}
      </div>
      {stage && <p className="text-xs text-slate-500 -mt-2">{STAGE_HINT[stage]}</p>}
      <div className="rounded-xl border border-slate-700 divide-y divide-slate-700">
        {shown.map((t) => (
          <button key={t.key} onClick={() => setOpen(t)} data-testid={`technique-row-${t.key}`}
            className="w-full flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-left hover:bg-slate-800/60">
            <span className="text-xs text-slate-500 w-16 shrink-0">{t.stage}</span>
            <span className="text-sm text-slate-100 flex-1 min-w-0">{t.name}</span>
            <span className="text-xs text-slate-300">
              {t.values.slice(0, 2).map((v) => <span key={v.label} className="ml-3">{v.label} <b>{v.value}</b></span>)}
            </span>
            <span className="text-xs w-12 text-right text-slate-500">{isOff(t) ? '꺼짐' : ''}</span>
            <span className="text-xs text-slate-500 w-16 text-right" title="도입·보류 근거로 남긴 측정 수">근거 {t.evidence.length}</span>
          </button>
        ))}
      </div>
      <Modal isOpen={open !== null} onClose={() => setOpen(null)} title={open?.name ?? ''} maxWidth="max-w-2xl">
        {open && <TechniqueCard t={open} />}
      </Modal>
    </div>
  );
}
