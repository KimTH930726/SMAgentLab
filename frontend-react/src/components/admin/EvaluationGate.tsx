import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { getBaselineRun, getCandidateBoard, getGateReport, getLatestImprovement, type GateAxis } from '../../api/eval';
import { useAuthStore } from '../../store/useAuthStore';
import { lockedAccuracy } from '../../utils/evalLayers';
import { Modal } from '../ui/Modal';
import { GateFlow, type Stage } from './GateFlow';
import { GateHistory } from './GateHistory';
import { ImproveAreas, InProgress, isOpen } from './GateReport';
import { AnswerTab, RetrievalTab } from './GateRuns';

/**
 * 품질 › 평가 게이트 — 한 템플릿(10/29 사용자 "정책이든 업무지식이든 유형은 구분값으로만", "전체 정확도를 메인으로 보고 개선점 → 도출할 수 있는 것").
 * 축 [정책서 | 업무지식]은 데이터만 바꾼다 — 화면은 같다(업계: 데이터셋 선택기 + 같은 실험 화면, LangSmith·Braintrust).
 * 맨 위 = 큰 숫자 카드(전체 정답률 · 검색 · 사람이 쓴 질문(잠근 골든셋이 있는 축만) · 결정할 후보) + [자세히](옛 [성적] 탭 내용).
 * 단계 줄(적재 → … → 생성, 틀린 문항 수) → ① 개선할 곳(원인별, 그 안에 후보·문항) → ② 개선 후보(한 목록 + 상태 필터, 10/30) → ③ 기법 대장.
 * 옛 구성(10/28: 단계 필터 + [틀린 문항][성적][적용 이력] 탭, 업무지식은 [성적]만)은 같은 숫자가 세 곳에 다른 기준으로 나와 없앴다(독립 UX 검수 10/29).
 */

const AXES: { axis: GateAxis; label: string; hint: string }[] = [
  { axis: 'policy', label: '정책서', hint: '정책서 골든셋 — 팀원 문항·실사용 문항 포함' },
  { axis: 'knowledge', label: '업무지식', hint: '업무지식 골든셋(컨플루언스 등) — 조각마다 로컬 LLM이 만든 질문' },
];

const pct = (x: number | null | undefined) => (x == null ? '-' : `${(x * 100).toFixed(1)}%`);

function Card({ label, value, sub, hint, cta, onClick, tone, testid }: {
  label: string; value: string; sub?: string; hint?: string; cta?: string; onClick?: () => void; tone?: 'warn'; testid: string;
}) {
  return (
    <div className={clsx('rounded-xl border bg-slate-800 p-4 space-y-1 min-w-0', tone === 'warn' ? 'border-amber-400 dark:border-amber-500/60' : 'border-slate-700')}
      data-testid={testid}>
      <p className="text-xs text-slate-400" title={hint}>{label}{hint ? ' ⓘ' : ''}</p>
      <p className={clsx('text-3xl font-semibold tabular-nums', tone === 'warn' ? 'text-amber-600 dark:text-amber-300' : 'text-slate-100')}>{value}</p>
      {sub && <p className="text-xs text-slate-500 tabular-nums">{sub}</p>}
      {onClick && cta && <button className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline" onClick={onClick}>{cta}</button>}
    </div>
  );
}

function AxisGate({ axis }: { axis: GateAxis }) {
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  const base = useQuery({ queryKey: ['eval-gate-baseline', axis], queryFn: () => getBaselineRun(axis), staleTime: 30_000 });
  const rep = useQuery({ queryKey: ['eval-report', axis], queryFn: () => getGateReport(axis), enabled: isAdmin, staleTime: 30_000 });
  const imp = useQuery({ queryKey: ['eval-improvement', axis, rep.data?.improvement_id ?? null],
    queryFn: () => getLatestImprovement(rep.data?.improvement_id, axis), enabled: !isAdmin || !rep.isLoading,
    // 실험이 도는 동안엔 진행이 보이게 다시 불러온다(10/30 실험 실행기) — 끝나면 멈춤
    refetchInterval: (q) => (q.state.data?.candidates.some((c) => c.experiment && ['queued', 'running'].includes(c.experiment.status)) ? 5000 : false) });
  // 실험이 끝나면(진행 중 → 없음) 리포트·측정 목록도 새로 — 결과 칸·운영 반영 목록이 늦게 따라오지 않게(리뷰 10/30 L6)
  const running = (imp.data?.candidates ?? []).filter((c) => c.experiment && ['queued', 'running'].includes(c.experiment.status)).length;
  const qc = useQueryClient();
  const prevRunning = useRef(running);
  useEffect(() => {
    if (prevRunning.current > running) {
      qc.invalidateQueries({ queryKey: ['eval-report'] });
      qc.invalidateQueries({ queryKey: ['eval-gate-runs'] });
      qc.invalidateQueries({ queryKey: ['eval-gate-baseline'] });
    }
    prevRunning.current = running;
  }, [running, qc]);
  const [stage, setStage] = useState<Stage | null>(null);
  const [detail, setDetail] = useState<null | 'answers' | 'retrieval'>(null);
  const [historyOpen, setHistoryOpen] = useState(false);

  const a = base.data?.summary.answers;
  const r = base.data?.summary.retrieval;
  const lk = lockedAccuracy(a?.by_type);
  // 결정할 후보 = ② 개선 후보 기본 [진행 중]과 같은 목록(같은 쿼리 키 — 예전 진단의 시도 승인 줄도 포함, 재리뷰 M2)
  const board = useQuery({ queryKey: ['eval-board', axis, imp.data?.run.id ?? null], queryFn: () => getCandidateBoard(axis), enabled: !!imp.data });
  const openN = (board.data?.candidates ?? []).filter(isOpen).length;
  const failedN = a?.failures?.length ?? 0;
  // 내려간 곳을 잠깐 테두리로 강조 — 스크롤만 하면 어디를 가리킨 건지 안 보였다(10/30 사용자)
  const toProgress = () => {
    const el = document.getElementById('gate-in-progress');
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    el.dataset.flash = '1';
    window.setTimeout(() => { delete el.dataset.flash; }, 1800);
  };

  return (
    <div className="space-y-5" data-testid={`gate-axis-${axis}`}>
      {base.error ? <p className="text-sm text-rose-600 dark:text-rose-400">지금 점수를 불러오지 못했습니다: {(base.error as Error).message}</p>
        : base.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
          : !base.data ? <p className="text-sm text-slate-500" title="통과한 측정을 eval_gate.py --save로 기록하거나 [운영에 반영했어요]를 누르면 그 측정이 지금 점수가 됩니다">지금 점수 없음 — 측정 전 ⓘ</p>
            : (
              <div className={clsx('grid grid-cols-1 sm:grid-cols-2 gap-3', lk ? 'xl:grid-cols-4' : 'xl:grid-cols-3')} data-testid="gate-summary">
                <Card testid="card-total" label="전체 정답률" value={pct(a?.accuracy)}
                  sub={a ? `[${pct(a.lo)} – ${pct(a.hi)}] · ${a.n}문항${failedN ? ` · 답 못 냄 ${failedN}(뺌)` : ''}` : '답 측정 없음'}
                  hint={`지금 점수(기준선 #${base.data.id} "${base.data.label}") — 실제 채팅 경로로 답하고 원문과 대조해 채점. 대괄호 = 95% 신뢰구간. 답을 못 낸 문항(게이트웨이 거부 등)은 정답률에서 빼고 따로 셈`}
                  cta="자세히 →" onClick={a ? () => setDetail('answers') : undefined} />
                <Card testid="card-retrieval" label="검색 성적" value={pct(r?.recall)} sub={r ? `정답을 AI에게 넘김 ${r.hit}/${r.n}` : '검색 측정 없음'}
                  hint="정답 데이터를 AI에게 주는 근거 안에 넣었나(Recall)" cta="자세히 →" onClick={r ? () => setDetail('retrieval') : undefined} />
                {lk && (
                  <Card testid="card-locked" label="사람이 쓴 질문" value={pct(lk.accuracy)} sub={`${lk.n}문항 · 목표 숫자`}
                    hint="잠근 골든셋 — 팀원이 쓴 질문·실사용 질문. 고칠 때(진단)는 안 보고 잴 때만 봐서 '골든셋에만 강해지는' 착시를 막는다"
                    cta="자세히 →" onClick={() => setDetail('answers')} />
                )}
                <Card testid="card-open" label="결정할 후보" value={imp.data ? String(openN) : '-'} tone={openN > 0 ? 'warn' : undefined}
                  sub={imp.data ? '제안 · 검토 대기 · 실험 중' : '진단 전'} hint="② 개선 후보 — 제안(AI가 해 볼 만·재 봐야 앎) + 실험 중·실험 오류 + 검토 대기(게이트 통과, 반영할지 정함)"
                  cta={openN > 0 ? '보기 ↓' : undefined} onClick={openN > 0 ? toProgress : undefined} />
              </div>
            )}

      {!isAdmin ? <p className="text-sm text-slate-500">개선할 곳·후보는 관리자만 볼 수 있어요(질문·답 원문이 들어 있음).</p>
        : rep.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
          : rep.error ? <p className="text-sm text-rose-600 dark:text-rose-400">{(rep.error as Error).message}</p>
            : (
              <>
                <GateFlow report={rep.data} stage={stage} onStage={setStage} axis={axis} />
                {rep.data ? <ImproveAreas rep={rep.data} imp={imp.data} axis={axis} stage={stage} isAdmin={isAdmin} />
                  : <section className="space-y-1" data-testid="gate-report"><h3 className="text-sm font-semibold text-slate-100">① 개선할 곳</h3>
                      <p className="text-sm text-slate-500" data-testid="gate-no-diagnosis">진단 전 — scripts/eval_improve.py --axis {axis} --save로 만든 뒤 여기 보여요</p></section>}
                <InProgress imp={imp.data} axis={axis} isAdmin={isAdmin} />
              </>
            )}

      <section className="space-y-2" data-testid="gate-history-section">
        <button className="text-sm font-semibold text-slate-100" onClick={() => setHistoryOpen(!historyOpen)} aria-expanded={historyOpen}>
          {historyOpen ? '▾' : '▸'} ③ 기법 대장</button>
        {/* 후보 결정(반영·실패·기각)은 ② 개선 후보의 필터로 — 여기는 기법마다 기준값·근거(반영·실패 측정은 자동으로 근거에, 10/30) */}
        {historyOpen && <GateHistory recent={5} initial="all" />}
      </section>

      <Modal isOpen={detail !== null} onClose={() => setDetail(null)} title={detail === 'retrieval' ? '검색 성적' : '답 성적'} maxWidth="max-w-5xl">
        <div data-testid="gate-scores" data-which={detail ?? ''}>
          {detail === 'retrieval' ? <RetrievalTab axis={axis} /> : detail === 'answers' ? <AnswerTab axis={axis} /> : null}
        </div>
      </Modal>
    </div>
  );
}

export function EvaluationGate() {
  const [axis, setAxis] = useState<GateAxis>('policy');
  return (
    <div className="space-y-4">
      <div className="inline-flex rounded-lg border border-slate-700 p-0.5" role="group" aria-label="평가 축">
        {AXES.map((x) => (
          <button key={x.axis} onClick={() => setAxis(x.axis)} aria-pressed={axis === x.axis} title={x.hint} data-testid={`axis-${x.axis}`}
            className={clsx('rounded-md px-4 py-1.5 text-sm font-medium', axis === x.axis ? 'bg-indigo-600 text-white' : 'text-slate-300 hover:bg-slate-700')}>
            {x.label}
          </button>
        ))}
      </div>
      <AxisGate key={axis} axis={axis} />
    </div>
  );
}
