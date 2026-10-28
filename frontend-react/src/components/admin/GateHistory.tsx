import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { getHistory, listTechniques, type HistoryItem, type Technique } from '../../api/eval';
import { Modal } from '../ui/Modal';
import { TechniqueCard } from './TechniqueCard';
import { STAGES, type Stage } from './GateFlow';

/**
 * 평가 게이트 › 적용 이력(10/27 밤) — 무엇을 언제 어떤 측정으로 바꿨나 · 효과 · 결과를 날짜순(Langfuse 버전 이력 자리).
 * 기법 대장 근거(측정 기록) + 개선 후보의 사람 결정(운영 반영·안 함·나중에). 줄을 누르면 그 항목 설명 창. 옛 품질 › RAG 파이프라인을 흡수.
 */

const RESULT_TONE: Record<string, string> = {
  '운영 반영': 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
  '안 함': 'bg-slate-700 text-slate-300', '나중에': 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300',
  '실험 중': 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300', '꺼짐': 'bg-slate-700 text-slate-300',
};
// 꺼짐·안 함이 값에 적힌 항목만 표시 — 나머지는 '쓰는 중'이라 단정하지 않는다(옛 RAG 파이프라인 화면과 같은 규칙)
const isOff = (t: Technique) => t.values.some((v) => v.value === '꺼짐' || v.value.includes('기본 꺼짐') || v.value.startsWith('안 함'));
const STAGE_ORDER: string[] = STAGES.map((s) => s.key);
const ROW = 'grid grid-cols-[4.5rem_5rem_minmax(0,1fr)_7rem_6rem] items-center gap-x-3';

/** stage = 평가 게이트 단계 흐름에서 고른 단계(공통 필터, 10/28) — 이력·항목 전체 둘 다 그 단계만 */
export function GateHistory({ stage = null }: { stage?: Stage | null }) {
  const q = useQuery({ queryKey: ['eval-history'], queryFn: () => getHistory(), staleTime: 60_000 });
  const techs = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, staleTime: 60_000 });
  const [open, setOpen] = useState<Technique | null>(null);
  const [view, setView] = useState<'log' | 'all'>('log');
  if (q.isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (q.error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p>;
  const items = (q.data ?? []).filter((x) => !stage || x.stage === stage);
  const staged = (techs.data ?? []).filter((t) => !stage || t.stage === stage);   // 항목 전체도 같은 단계로
  const pick = (it: HistoryItem) => { const t = techs.data?.find((x) => x.key === it.technique); if (t) setOpen(t); };
  const viewBtn = (v: 'log' | 'all', label: string) => (
    <button onClick={() => setView(v)} aria-pressed={view === v}
      className={clsx('rounded-md px-3 py-1 text-xs', view === v ? 'bg-indigo-600 text-white' : 'text-slate-300 hover:bg-slate-700')}>{label}</button>
  );
  const switcher = <div className="inline-flex rounded-lg border border-slate-700 p-0.5">{viewBtn('log', '이력')}{viewBtn('all', techs.data ? `항목 전체 ${staged.length}` : '항목 전체')}</div>;
  const modal = (
    <Modal isOpen={open !== null} onClose={() => setOpen(null)} title={open?.name ?? ''} maxWidth="max-w-2xl">
      {open && <TechniqueCard t={open} showName={false} />}
    </Modal>
  );
  if (view === 'all') {
    // 옛 RAG 파이프라인의 단계별 항목 목록 — 근거가 아직 없는 항목도 빠지지 않게
    const all = staged;
    const order = (s: string) => STAGE_ORDER.indexOf(s) + 1 || 99;
    const byStage = [...new Set(all.map((t) => t.stage))].sort((a, b) => order(a) - order(b));
    return (
      <div className="space-y-3" data-testid="gate-techniques">
        {switcher}
        {byStage.map((s) => (
          <section key={s} className="space-y-1.5" aria-label={`${s} 항목`}>
            <h3 className="text-xs font-semibold text-slate-300">{s} <span className="font-normal text-slate-500">{all.filter((t) => t.stage === s).length}</span></h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-2">
              {all.filter((t) => t.stage === s).map((t) => (
                <button key={t.key} onClick={() => setOpen(t)} data-testid={`technique-row-${t.key}`}
                  className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-left hover:border-indigo-500 dark:hover:border-indigo-400 min-w-0">
                  <span className="flex items-start justify-between gap-2">
                    <span className="text-sm text-slate-100 min-w-0">{t.name}</span>
                    {isOff(t) && <span className="shrink-0 rounded px-1.5 py-0.5 text-[11px] bg-slate-700 text-slate-400">꺼짐</span>}
                  </span>
                  <span className="block text-xs text-slate-400 line-clamp-1">{t.does}</span>
                  <span className="block text-[11px] text-slate-500" title="도입·보류 근거로 남긴 측정 수">근거 {t.evidence.length}</span>
                </button>
              ))}
            </div>
          </section>
        ))}
        {modal}
      </div>
    );
  }
  return (
    <div className="space-y-3" data-testid="gate-history">
      {switcher}
      <div className="rounded-xl border border-slate-700 overflow-x-auto">
        <div className={clsx(ROW, 'px-4 py-1.5 text-[11px] text-slate-500 bg-slate-900/60 min-w-[720px]')}>
          <span>날짜</span><span>단계</span><span>바꾼 것 · 효과</span><span>측정</span><span>결과</span>
        </div>
        {items.length === 0 && <p className="px-4 py-3 text-sm text-slate-500">{stage ? `${stage} 단계 기록이 없어요` : '기록이 없어요.'}</p>}
        {items.map((it, i) => {
          // 결과 = 사람 결정(개선 후보). 측정 기록 줄엔 결정이 없어 그 기법의 지금 상태만 옅게("지금 꺼짐") — 그 측정의 결과로 읽히지 않게
          return (
            <button key={i} onClick={() => pick(it)} data-testid="history-row"
              className={clsx(ROW, 'w-full px-4 py-2.5 text-left text-sm border-t border-slate-700/70 hover:bg-slate-800/60 min-w-[720px]')}>
              <span className="text-xs text-slate-500 tabular-nums">{it.date.slice(5).replace('-', '/')}</span>
              <span className="text-xs text-slate-300">{it.stage}</span>
              <span className="min-w-0">
                <span className="block text-slate-100 break-words">{it.what}</span>
                <span className="block text-xs text-slate-500">{it.technique_name}{it.reason ? ` · 사유: ${it.reason}` : ''}</span>
              </span>
              <span className="text-xs text-slate-400">{it.gate ? `#${it.gate.id}${it.gate.verdict === 'pass' ? ' 통과' : it.gate.verdict === 'fail' ? ' 실패' : ''}` : '—'}</span>
              <span>{it.result ? <span className={clsx('rounded-full px-2 py-0.5 text-[11px]', RESULT_TONE[it.result] ?? 'bg-slate-700 text-slate-300')}>{it.result}</span>
                : it.now ? <span className="text-[11px] text-slate-500" title="측정 기록 줄 — 이 기법의 지금 상태">지금 {it.now}</span> : <span className="text-[11px] text-slate-600">측정 기록</span>}</span>
            </button>
          );
        })}
      </div>
      <p className="text-[11px] text-slate-500">측정 기록(기법 대장 근거)과 사람 결정(개선 후보)을 날짜순으로 · 줄을 누르면 그 항목의 하는 일·지금 값·왜 있나</p>
      {modal}
    </div>
  );
}
