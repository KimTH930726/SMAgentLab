import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { CheckCircle2, Lock, TriangleAlert } from 'lucide-react';
import { getTechniqueBaseline, listTechniques, restoreTechniqueBaseline, switchTechnique, type Technique } from '../../api/eval';
import { useAuthStore } from '../../store/useAuthStore';
import { showConfirm, showError } from '../../store/useDialogStore';
import { TechniqueCard } from './TechniqueCard';
import { Modal } from '../ui/Modal';

/**
 * 기법 대장 흐름도(11/03, 태훈 "흐름도상 어떤 단계에서 쓰이는 기법인지 · 필수는 필수 · 끄고 켤 수 있는 건 끄고 켜게 · 적용 중/미적용이 보이게").
 * 흐름(채팅·적재·평가) 탭 → 단계 칸마다 기법 카드(필수 🔒 / 적용 중 / 미적용) → 누르면 상세 창(모달, 11/03 태훈 — 옆 칸이 단계 칸을 가려서)에 하는 일·기준값·근거(TechniqueCard) + 스위치.
 * 단계 순서는 서버 techniques.FLOWS와 같다. 스위치는 관리자만, 바꾸면 바로 적용되고 답변 캐시를 비운다(서버).
 */

const FLOWS: { key: Technique['flow']; label: string; steps: string[] }[] = [
  { key: 'chat', label: '채팅 흐름', steps: ['질문 이해', '검색', '근거 판정', '문맥 구성', '답 모양', '생성', '답 뒤'] },
  { key: 'ingest', label: '적재 흐름', steps: ['수집', '넣을 곳', '분할·저장', '점검', '게시'] },
  { key: 'eval', label: '평가·개선', steps: ['측정', '원인·개선'] },
];

const STATE: Record<Technique['state'], { label: string; cls: string }> = {
  required: { label: '필수', cls: 'bg-slate-700 text-slate-200' },
  on: { label: '적용 중', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' },
  off: { label: '미적용', cls: 'bg-slate-800 text-slate-400' },
};

function Switch({ on }: { on: boolean }) {
  return (
    <span aria-hidden className={clsx('relative inline-block h-5 w-9 shrink-0 rounded-full border transition-colors',
      on ? 'border-emerald-500 bg-emerald-100 dark:bg-emerald-500/20' : 'border-slate-600 bg-slate-800')}>
      <span className={clsx('absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all',
        on ? 'left-[18px] bg-emerald-600 dark:bg-emerald-400' : 'left-0.5 bg-slate-500')} />
    </span>
  );
}

export function TechniqueFlow() {
  const qc = useQueryClient();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const { data: techs = [], isLoading, error } = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, staleTime: 30_000 });
  const [flow, setFlow] = useState<Technique['flow']>('chat');
  const [selKey, setSelKey] = useState<string | null>(null);
  const { data: baseline } = useQuery({ queryKey: ['eval-technique-baseline'], queryFn: getTechniqueBaseline, staleTime: 30_000 });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['eval-techniques'] });
    qc.invalidateQueries({ queryKey: ['eval-technique-baseline'] });
  };
  const toggle = useMutation({
    mutationFn: ({ key, on }: { key: string; on: boolean }) => switchTechnique(key, on),
    onSuccess: refresh,
    onError: (e: Error) => void showError(e, '기법 켜고 끄기 실패'),
  });
  const restore = useMutation({ mutationFn: restoreTechniqueBaseline, onSuccess: refresh, onError: (e: Error) => void showError(e, '최적으로 되돌리기 실패') });
  const f = FLOWS.find((x) => x.key === flow)!;
  const inFlow = useMemo(() => techs.filter((t) => t.flow === flow), [techs, flow]);
  const sel = inFlow.find((t) => t.key === selKey) ?? null;
  // 닫히는 동안(0.2초 애니메이션) 마지막 기법을 그대로 그린다 — 바로 비우면 빈 상자가 줄어들었다(리뷰 L3)
  const lastSel = useRef<Technique | null>(null);
  if (sel) lastSel.current = sel;
  const shown = sel ?? lastSel.current;
  if (isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(error as Error).message}</p>;
  const count = (s: Technique['state']) => inFlow.filter((t) => t.state === s).length;

  const offNames = techs.filter((t) => baseline?.off.includes(t.key)).map((t) => t.name);
  const onToggle = async (t: Technique) => {
    const next = t.state !== 'on';
    const leaving = t.baseline_on !== null && next !== t.baseline_on;   // 검증된 최적에서 벗어나는 쪽
    const msg = leaving
      ? `⚠ 검증된 최적 설정이 깨져요(${baseline?.evidence ?? '마지막 게이트'}).\n${t.baseline_warn ?? ''}\n바로 채팅에 적용되고, 이 조합의 정확도는 아직 재지 않았어요 — 꼭 평가 게이트를 다시 돌리세요.`
      : `'${t.name}'을(를) ${next ? '켭니다' : '끕니다'} — 검증된 최적 값으로 돌아가요. 바로 채팅에 적용되고 답변 캐시가 비워져요.`;
    const ok = await showConfirm(msg, {
      title: leaving ? '최적 설정에서 벗어나기' : '최적으로 돌아가기',
      confirmLabel: leaving ? `그래도 ${next ? '켜기' : '끄기'}` : (next ? '켜기' : '끄기'), tone: leaving ? 'danger' : 'warning',
    });
    if (ok) toggle.mutate({ key: t.key, on: next });
  };

  return (
    <div className="space-y-3" data-testid="technique-flow">
      <div className="flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="흐름" className="flex gap-1">
          {FLOWS.map((x) => (
            <button key={x.key} role="tab" aria-selected={flow === x.key} onClick={() => { setFlow(x.key); setSelKey(null); }}
              className={clsx('rounded-full border px-3 py-1 text-xs', flow === x.key ? 'border-indigo-600 bg-indigo-600 text-white' : 'border-slate-700 text-slate-300 hover:bg-slate-800')}>
              {x.label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex flex-wrap gap-3 text-[11px] text-slate-400" data-testid="technique-flow-counts">
          <span>기법 {inFlow.length}</span>
          <span title="끌 수 없음 — 끄면 기본 동작이 깨지는 기법"><Lock className="inline h-3 w-3" /> 필수 {count('required')}</span>
          <span className="text-emerald-700 dark:text-emerald-300">적용 중 {count('on')}</span>
          <span>미적용 {count('off')}</span>
        </div>
      </div>

      {baseline && (baseline.optimal ? (
        <p className="flex flex-wrap items-center gap-1.5 rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-300"
          data-testid="technique-optimal" title="게이트로 잰 조합은 이것 하나 — 스위치를 바꾸면 정확도를 모르는 조합이 돼요">
          <CheckCircle2 className="h-3.5 w-3.5" /> 최적 상태 — {baseline.evidence}로 검증된 설정이에요. 바꾸면 최적이 깨져요.
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300"
          data-testid="technique-off-baseline">
          <TriangleAlert className="h-3.5 w-3.5" />
          <span>최적에서 벗어남 — {offNames.join(' · ') || baseline.off.join(' · ')} · 검증 안 된 조합이라 정확도를 몰라요(최적: {baseline.evidence}).</span>
          {isAdmin && (
            <button onClick={async () => { if (await showConfirm('벗어난 스위치를 검증된 최적 값으로 되돌려요.', { title: '최적으로 되돌리기', confirmLabel: '되돌리기' })) restore.mutate(); }}
              disabled={restore.isPending} data-testid="technique-restore"
              className="ml-auto rounded-md bg-amber-600 px-2.5 py-1 font-semibold text-white hover:bg-amber-500 disabled:opacity-40">최적으로 되돌리기</button>
          )}
        </div>
      ))}

      <div className="flex gap-2 overflow-x-auto pb-1">
        {f.steps.map((step, i) => (
          <section key={step} aria-label={`${step} 단계`} className="flex min-w-[160px] flex-1 flex-col gap-2 rounded-xl border border-slate-700 bg-slate-800/60 p-2">
            <h4 className="flex items-center gap-1.5 text-xs font-semibold text-slate-200">
              <span className="grid h-5 w-5 place-items-center rounded-full bg-indigo-100 text-[11px] text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300">{i + 1}</span>{step}
            </h4>
            {inFlow.filter((t) => t.step === step).map((t) => (
              <button key={t.key} onClick={() => setSelKey(t.key)} data-testid={`flow-tech-${t.key}`} data-state={t.state}
                aria-pressed={sel?.key === t.key} title={t.switchable ? '눌러서 상세 · 켜고 끄기' : '눌러서 상세'}
                className={clsx('flex flex-col gap-1 rounded-lg border px-2.5 py-2 text-left',
                  sel?.key === t.key ? 'border-indigo-500 ring-1 ring-indigo-500' : 'border-slate-700 hover:border-slate-500',
                  t.state === 'off' ? 'bg-slate-900/40 opacity-75' : 'bg-slate-900')}>
                <span className="flex items-start justify-between gap-2">
                  <span className={clsx('text-xs font-medium text-slate-100', t.state === 'off' && 'line-through decoration-slate-500')}>{t.name}</span>
                  {t.state === 'required' ? <Lock className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-label="필수" /> : t.switchable ? <Switch on={t.state === 'on'} /> : null}
                </span>
                <span className={clsx('w-fit rounded-full px-1.5 py-0.5 text-[10px] font-semibold', STATE[t.state].cls)}>{STATE[t.state].label}</span>
              </button>
            ))}
            {inFlow.every((t) => t.step !== step) && <p className="text-[11px] text-slate-500">없음</p>}
          </section>
        ))}
      </div>

      {inFlow.length === 0 && <p className="text-sm text-slate-500">이 흐름엔 기법이 없어요.</p>}
      <Modal isOpen={!!sel} onClose={() => setSelKey(null)} title={shown?.name} maxWidth="max-w-xl">
        {shown && (
          <div className="space-y-3 max-h-[70vh] overflow-y-auto" data-testid="technique-flow-panel">
              <div className="flex flex-wrap items-center gap-2">
                <span className={clsx('rounded-full px-2 py-0.5 text-[11px] font-semibold', STATE[shown.state].cls)}>{STATE[shown.state].label}</span>
              </div>
              {shown.state === 'required' && <p className="text-xs text-slate-400"><Lock className="inline h-3 w-3" /> 필수 — 항상 적용돼요. 바꿀 수 없어요(서버가 막음).</p>}
              {shown.switchable && shown.baseline_on !== null && (
                <p className={clsx('text-[11px]', (shown.state === 'on') === shown.baseline_on ? 'text-emerald-700 dark:text-emerald-300' : 'text-amber-700 dark:text-amber-300')}>
                  {(shown.state === 'on') === shown.baseline_on ? `최적 값(${shown.baseline_on ? '켜짐' : '꺼짐'}) 그대로` : `최적 값은 ${shown.baseline_on ? '켜짐' : '꺼짐'} — 지금 벗어나 있어요`}
                </p>
              )}
              {shown.off_reason && <p className="text-xs text-amber-700 dark:text-amber-300">미적용 사유: {shown.off_reason}</p>}
              {shown.switchable && (
                <div className="flex flex-wrap items-center gap-2">
                  {isAdmin ? (
                    <button onClick={() => void onToggle(shown)} disabled={toggle.isPending} data-testid="technique-switch"
                      className={clsx('rounded-lg px-3 py-1.5 text-xs font-semibold disabled:opacity-40',
                        shown.state === 'on' ? 'border border-slate-600 text-slate-200 hover:bg-slate-700' : 'bg-indigo-600 text-white hover:bg-indigo-500')}>
                      {shown.state === 'on' ? '끄기' : '켜기'}
                    </button>
                  ) : <span className="text-[11px] text-slate-500">켜고 끄기는 관리자만</span>}
                  {shown.changed?.at && <span className="text-[11px] text-slate-500">최근 변경 {shown.changed.by ?? ''} · {new Date(shown.changed.at).toLocaleString('ko-KR')}</span>}
                </div>
              )}
              {!shown.switchable && shown.state === 'on' && <p className="text-[11px] text-slate-500" title="코드에 고정 — 바꾸려면 개선 후보·평가 게이트로">적용 중 · 스위치 없음</p>}
              <TechniqueCard t={shown} showName={false} />
          </div>
        )}
      </Modal>
    </div>
  );
}
