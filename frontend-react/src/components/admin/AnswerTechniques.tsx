import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listTechniques, type Technique } from '../../api/eval';

/**
 * 품질 › 답 기법 (v2.171, 사용자 10/26 "왜 있나·효과가 있었나가 보여야") — 질문이 답이 되는 흐름 위에 기법을 놓고,
 * 기법마다 하는 일 · 지금 값 · 왜 있나(도입·보류 근거 숫자, 판정 기록 번호) · 지금 걸린 개선 후보 · 바꾸려면.
 * 대장 하나가 기준(backend/service/eval/techniques.py) — 값은 여기서 바꾸지 않는다(평가 게이트 [개선]에서 실험·측정 후).
 */

const STAGES = ['검색', '근거 판정', '답 모양', '생성'] as const;
const STAGE_HINT: Record<string, string> = {
  검색: '질문에 맞는 정책·지식을 찾아 AI에게 넘길 문맥을 만든다',
  '근거 판정': '넘길 근거가 너무 약하면 LLM을 부르지 않고 "찾지 못했습니다"',
  '답 모양': '목록이 크면 되묻고, 목록 답이면 문맥을 거른다',
  생성: 'AI에게 주는 답 지시(프롬프트)',
};
const CAND_STATUS: Record<string, string> = { proposed: '제안', approved: '실험 중', held: '나중에', rejected: '안 하기', adopted: '운영 반영' };

function Card({ t }: { t: Technique }) {
  const off = t.values.some((v) => v.value === '꺼짐');
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-2" id={`tech-${t.key}`} data-testid={`technique-${t.key}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium text-slate-100">{t.name}{off && <span className="ml-2 text-xs text-slate-500">(꺼짐)</span>}</p>
        {t.values.length > 0 && (
          <p className="text-xs text-slate-300">
            {t.values.map((v) => (
              <span key={v.label} className="ml-3" title={v.setting ? `설정 키 ${v.setting} — 서버 운영값` : '코드에 정한 값'}>
                {v.label} <b>{v.value}</b>
              </span>
            ))}
          </p>
        )}
      </div>
      <p className="text-xs text-slate-300"><span className="text-slate-500">하는 일 </span>{t.does}</p>
      <div className="text-xs space-y-0.5">
        <p className="text-slate-500">왜 있나 · 효과</p>
        {t.evidence.length === 0 ? <p className="text-slate-500 pl-3">기록된 근거 없음 — 측정해서 남길 대상</p> : t.evidence.map((e) => (
          <p key={e.date + e.note} className="text-slate-300 pl-3">
            {e.date} · {e.note}
            {e.gate_run_id != null && <span className="text-indigo-600 dark:text-indigo-400" title="평가 게이트 [판정 이력]에서 이 번호"> (판정 #{e.gate_run_id})</span>}
          </p>
        ))}
      </div>
      {t.candidates.length > 0 && (
        <div className="text-xs space-y-0.5">
          <p className="text-slate-500">지금 걸린 개선 후보</p>
          {t.candidates.map((c) => (
            <p key={c.id} className="text-slate-300 pl-3">{c.title} <span className="text-slate-500">· {CAND_STATUS[c.status] ?? c.status} · 대상 {c.n_target}문항</span></p>
          ))}
        </div>
      )}
      <p className="text-xs text-slate-500" title="값을 바로 바꾸지 않는다 — 평가 게이트 [개선]에서 후보로 실험·측정(통과)한 뒤 설정·코드에 반영">
        바꾸려면: 평가 게이트 [개선] → 실험 → 측정 통과 → {t.values.some((v) => v.setting) ? `설정 ${t.values.filter((v) => v.setting).map((v) => v.setting).join(', ')}` : '코드'}
        {t.where && <span className="font-mono text-slate-600"> · {t.where}</span>}
      </p>
    </div>
  );
}

export function AnswerTechniques() {
  const { data = [], isLoading, error } = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, staleTime: 60_000 });
  const [stage, setStage] = useState<string | null>(null);
  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(error as Error).message}</p>;
  const shown = stage ? data.filter((t) => t.stage === stage) : data;
  return (
    <div className="space-y-4">
      {/* 흐름 그림 — 단계를 누르면 그 단계 기법만 */}
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 overflow-x-auto">
        <div className="flex items-stretch gap-2 min-w-[640px]">
          <div className="flex items-center text-xs text-slate-400 px-2">질문</div>
          {STAGES.map((s) => {
            const items = data.filter((t) => t.stage === s);
            const active = stage === s;
            return (
              <div key={s} className="flex items-stretch gap-2 flex-1">
                <span className="flex items-center text-slate-600">▶</span>
                <button onClick={() => setStage(active ? null : s)} title={STAGE_HINT[s]}
                  className={`flex-1 text-left rounded-md border px-2.5 py-2 space-y-1 ${active ? 'border-indigo-500 dark:border-indigo-400' : 'border-slate-700 hover:border-slate-500'}`}>
                  <p className="text-xs font-medium text-slate-200">{s}</p>
                  {items.map((t) => (
                    <p key={t.key} className={`text-[11px] ${t.candidates.length ? 'text-amber-600 dark:text-amber-400' : 'text-slate-400'}`}
                      title={t.candidates.length ? '지금 개선 후보가 걸려 있음' : undefined}>
                      {t.name}{t.values.some((v) => v.value === '꺼짐') ? ' (꺼짐)' : ''}
                    </p>
                  ))}
                </button>
              </div>
            );
          })}
          <div className="flex items-center text-xs text-slate-400 px-2">▶ 답</div>
        </div>
        <p className="mt-2 text-xs text-slate-500">단계를 누르면 그 단계 기법만 · <span className="text-amber-600 dark:text-amber-400">주황</span> = 지금 개선 후보가 걸린 기법</p>
      </div>
      {shown.map((t) => <Card key={t.key} t={t} />)}
    </div>
  );
}
