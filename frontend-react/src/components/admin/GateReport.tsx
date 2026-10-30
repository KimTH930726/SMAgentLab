import { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { ChevronRight } from 'lucide-react';
import { setRealQuestion, type Candidate, type GateAxis, type GateReport, type Improvement, type ReportRow, type ReportTechnique } from '../../api/eval';
import { showConfirm, showError } from '../../store/useDialogStore';
import { CandidateModal, STATUS_BADGE } from './GateImprove';
import { useQuestionPicker } from './GateRuns';
import { Modal } from '../ui/Modal';
import { CAUSE_STAGE, ingestQids, STAGES, type Stage } from './GateFlow';

/**
 * 평가 게이트 ① 개선할 곳 · ② 진행 중인 개선(10/29 한 템플릿 — 사용자 "전체 정확도 → 개선점 → 도출할 수 있는 것", 업계: 실패는 원인 유형으로 묶고
 * 고칠 방법은 그 원인 안의 제안(Patronus·Galileo), 결정할 것은 한곳에). 옛 [틀린 문항] 탭의 "고칠 방법별" 보기·머리 숫자 줄은 없앴다(원인별의 뒤집기 · 위 카드와 중복).
 * ① 원인 줄(단계 꼬리표·막대) → 펼치면 그 원인의 후보(열린 것 먼저, 결정 끝난 것은 접어 둠) + 문항(8개씩, 누르면 문항 상세).
 * ② 사람이 지금 정할 후보만(결정 전 중 AI가 해 볼 만·재 봐야 앎 + 실험 중) — 안 하기·운영 반영·나중에는 열린 일로 세지 않는다.
 * 서버가 한 번에 묶어 준다(service/eval/report.py, 축마다) — 진단·후보·숫자는 scripts/eval_improve.py·check_ingest.py 기록을 읽기만.
 */

const STATUS: Record<string, { label: string; cls: string }> = {
  proposed: { label: '결정 전', cls: 'text-slate-400' },
  approved: { label: '실험 중', cls: 'text-indigo-600 dark:text-indigo-400' },
  held: { label: '나중에', cls: 'text-amber-600 dark:text-amber-400' },
  rejected: { label: '안 하기', cls: 'text-slate-500' },
  adopted: { label: '운영 반영', cls: 'text-emerald-600 dark:text-emerald-400' },
};
const REC: Record<string, string> = { try: 'AI: 해 볼 만함', review: 'AI: 재 봐야 앎', reject: 'AI: 안 하는 게 좋음' };

/** 사람이 지금 정할 후보 — 맨 위 "결정할 후보" 숫자와 ② 목록이 같은 규칙 */
export const isOpen = (c: { status: string; recommend: string }) =>
  c.status === 'approved' || (c.status === 'proposed' && c.recommend !== 'reject');
const isClosed = (c: { status: string }) => c.status === 'rejected' || c.status === 'adopted';

const pct = (x: number | null | undefined) => (x == null ? '-' : `${(x * 100).toFixed(1)}%`);
const fmtDate = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const chip = 'inline-block rounded px-1.5 py-0.5 text-xs';
const PAGE = 8;

/** 진단 원인이 아닌 후보 묶음 이름(데이터 쪽) */
const EXTRA_CAUSE: Record<string, string> = { ingest: '적재 검사(데이터)', override_location: '운영 번복(등록 위치)' };

function Effect({ t }: { t: ReportTechnique }) {
  const e = t.effect;
  if (!e) return <span className="text-slate-500">아직 안 재 봄</span>;
  if (!e.locked) {
    return (
      <span className="text-amber-600 dark:text-amber-400" title="AI가 만든 문항에서만 잰 측정 — 사람이 쓴 질문(잠근 골든셋)이 없어서 실제 효과는 모릅니다">
        효과 확인 안 됨 · AI 문항에서만 잼(#{e.gate_run_id}) ⓘ
      </span>
    );
  }
  const d = e.locked.after - e.locked.before;
  return (
    <span className={d > 0 ? 'text-emerald-600 dark:text-emerald-400' : d < 0 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-400'}
      title={`측정 #${e.gate_run_id} "${e.label}" — 사람이 쓴 질문 ${e.locked.n}문항 기준`}>
      사람이 쓴 질문 {pct(e.locked.before)} → {pct(e.locked.after)} · {e.verdict === 'pass' ? '통과' : '실패'}(#{e.gate_run_id})
    </span>
  );
}

/** 실사용 문항 — 정정 승인 때 자동으로 골든셋에 들어가므로, 이상한 질문은 여기서 뺀다(다음 측정부터, 리뷰 M4) */
function RealToggle({ r, axis }: { r: ReportRow; axis: GateAxis }) {
  const qc = useQueryClient();
  const id = Number(r.qid.slice(5));
  const out = r.real_status === 'dismissed';
  const m = useMutation({
    mutationFn: () => setRealQuestion(id, out ? 'added' : 'dismissed'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['eval-report', axis] }),
    onError: (e) => showError(e, '골든셋 상태를 바꾸지 못했습니다'),
  });
  const click = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!out && !(await showConfirm('이 실사용 문항을 골든셋에서 뺄까요? 다음 측정부터 빠집니다.'))) return;
    m.mutate();
  };
  return (
    <span role="button" tabIndex={0} onClick={click} onKeyDown={(e) => e.key === 'Enter' && click(e as unknown as React.MouseEvent)}
      className="ml-1 text-xs text-slate-400 hover:text-slate-200 hover:underline" aria-disabled={m.isPending}
      title={out ? '빼기로 한 문항 — 다시 넣으면 다음 측정부터 잰다' : '질문이 이상하거나 정답이 애매하면 — 다음 측정부터 골든셋에서 뺀다'}>
      {out ? '· 빼기로 함 (다시 넣기)' : '· 골든셋에서 빼기'}
    </span>
  );
}

/** 문항 줄 — 질문 한 줄 + 저장 꼬리표 */
function QRow({ r, axis, onClick }: { r: ReportRow; axis: GateAxis; onClick: () => void }) {
  return (
    <button className="w-full text-left px-3 py-2.5 space-y-1 hover:bg-slate-800/60" data-testid={`report-row-${r.qid}`} onClick={onClick}>
      <p className="text-sm text-slate-100 min-w-0">
        {r.type === 'real' && <span className={`${chip} mr-1 bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300`} title="정정 요청이 승인된 실제 채팅 질문">실사용</span>}
        {r.locked && <span className={`${chip} mr-1 bg-slate-700 text-slate-300`} title="잠근 골든셋 — 결과를 잴 때만 보고 고칠 때(진단)는 안 봅니다">잠금</span>}
        <span className="break-words">{r.question || '(질문 글 없음)'}</span>
        {r.type === 'real' && r.qid.startsWith('REAL-') && <RealToggle r={r} axis={axis} />}
      </p>
      <p className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-slate-500">{r.qid}</span>
        {r.storage.map((t) => <span key={t} className={`${chip} bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300`}>{t}</span>)}
        {r.techniques.length > 1 && <span className={`${chip} bg-slate-700 text-slate-300`} title={r.techniques.map((t) => t.title).join('\n')}>방법 {r.techniques.length}개</span>}
      </p>
    </button>
  );
}

/** 문항 목록 — 저장 꼬리표로 거르기 · 8개씩 · 누르면 문항 상세(앞뒤 넘김) */
function QuestionList({ rows, title, axis, onPick }: { rows: ReportRow[]; title: string; axis: GateAxis; onPick?: (qids: string[], i: number, title: string) => void }) {
  const [tag, setTag] = useState('');
  const [page, setPage] = useState(0);
  const tags = [...new Set(rows.flatMap((r) => r.storage))];
  const tagOn = tags.includes(tag) ? tag : '';
  const shownAll = tagOn ? rows.filter((r) => r.storage.includes(tagOn)) : rows;
  const pages = Math.max(1, Math.ceil(shownAll.length / PAGE));
  const cur = Math.min(page, pages - 1);
  const shown = shownAll.slice(cur * PAGE, cur * PAGE + PAGE);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-xs font-medium text-slate-300 mr-2" data-testid="report-list-title">{title} {shownAll.length}{shownAll.length !== rows.length && ` / ${rows.length}`}</h4>
        {tags.length > 0 && (
          <select className="bg-slate-800 border border-slate-600 rounded-md px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
            value={tagOn} onChange={(e) => { setTag(e.target.value); setPage(0); }} aria-label="저장 상태로 거르기">
            <option value="">저장 상태 전체</option>
            {tags.map((t) => <option key={t} value={t}>{t} ({rows.filter((r) => r.storage.includes(t)).length})</option>)}
          </select>
        )}
      </div>
      <div className="rounded-lg border border-slate-700 divide-y divide-slate-700">
        {shown.length === 0 && <p className="p-3 text-sm text-slate-500">해당 문항이 없어요</p>}
        {shown.map((r, i) => <QRow key={r.qid} r={r} axis={axis} onClick={() => onPick?.(shownAll.map((x) => x.qid), cur * PAGE + i, title)} />)}
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-center gap-3 text-sm" data-testid="report-pager">
          <button className="px-2 py-1 rounded text-slate-300 hover:bg-slate-700 disabled:opacity-40" disabled={cur === 0} onClick={() => setPage(cur - 1)}>‹ 이전</button>
          <span className="text-slate-400 tabular-nums" data-testid="report-page">{cur + 1} / {pages}</span>
          <button className="px-2 py-1 rounded text-slate-300 hover:bg-slate-700 disabled:opacity-40" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>다음 ›</button>
        </div>
      )}
    </div>
  );
}

/** 원인 안의 후보 한 줄 — 누르면 카드(단계 막대·AI 추천·바꿀 것·재는 법·결정 버튼), "문항만"은 아래 문항을 이 후보 것만 */
function MethodRow({ t, cand, imp, axis, isAdmin, onPick, only, onOnly }: {
  t: ReportTechnique; cand?: Candidate; imp?: Improvement | null; axis: GateAxis; isAdmin: boolean;
  onPick?: (qids: string[], i: number) => void; only: boolean; onOnly: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800" data-testid={`report-method-${t.id}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
        {/* 누르면 후보 카드 모달(10/30 사용자 — 원인 모달 안 아코디언도 모달로). 원인 모달(z-40) 위 z-[45], 문항 상세(z-50)·결정 창(z-[60])은 그 위 */}
        <button onClick={() => setOpen(true)} className="flex items-center gap-1.5 min-w-0 text-left" aria-haspopup="dialog" data-testid={`method-open-${t.id}`}>
          <span className="text-sm text-slate-100 break-words hover:underline">{t.title}</span>
          <ChevronRight className="w-3.5 h-3.5 shrink-0 text-slate-400" />
        </button>
        <span className={clsx('text-xs', STATUS[t.status]?.cls)}>{STATUS[t.status]?.label ?? t.status}</span>
        <span className="text-xs text-slate-500">{REC[t.recommend]}</span>
        {t.effect && <span className="text-xs"><Effect t={t} /></span>}{/* 사람이 쓴 질문 기준 효과 — 후보 카드엔 없는 정보라 줄에 둔다 */}
        <span className="ml-auto text-xs text-slate-300 tabular-nums">{t.n_failed}문항</span>
        {t.n_failed > 0 && (
          <button onClick={onOnly} aria-pressed={only} className={clsx('text-xs hover:underline', only ? 'text-indigo-600 dark:text-indigo-400' : 'text-slate-400')}
            title="아래 문항을 이 방법이 겨냥한 문항만 — 다시 누르면 원인 전체">{only ? '이 방법 문항만 보는 중' : '문항만 보기'}</button>
        )}
      </div>
      {cand && imp ? <CandidateModal isOpen={open} onClose={() => setOpen(false)} c={cand} improvementId={imp.run.id} isAdmin={isAdmin} onPick={onPick} axis={axis} zClass="z-[45]" />
        : <Modal isOpen={open} onClose={() => setOpen(false)} title={t.title} zClass="z-[45]"><p className="text-sm text-slate-500">{imp === undefined ? '불러오는 중…' : '후보 정보를 못 불러왔어요'}</p></Modal>}
    </div>
  );
}

type CauseRow = { key: string; name: string; stage: Stage | undefined; rows: ReportRow[]; techs: ReportTechnique[] };

function CauseBlock({ c, max, rep, imp, axis, isAdmin, onPick }: {
  c: CauseRow; max: number; rep: GateReport; imp?: Improvement | null; axis: GateAxis; isAdmin: boolean;
  onPick?: (qids: string[], i: number, title: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [only, setOnly] = useState<number | null>(null);
  const [showClosed, setShowClosed] = useState(false);
  const openT = c.techs.filter((t) => !isClosed(t));
  const closedT = c.techs.filter(isClosed);
  const sel = only != null ? c.techs.find((t) => t.id === only) : undefined;
  const rows = sel ? c.rows.filter((r) => r.techniques.some((x) => x.id === sel.id)) : c.rows;
  const candOf = (id: number) => imp?.candidates.find((x) => x.id === id);
  const n = c.rows.length;
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/60" data-testid={`report-cause-${c.key}`}>
      {/* 누르면 모달(10/30 사용자 — 아코디언은 아래 원인들을 밀어내 읽기 어려움) */}
      <button onClick={() => setOpen(true)} aria-haspopup="dialog" className="w-full grid grid-cols-[minmax(0,12rem)_4.5rem_minmax(0,1fr)_3rem_1rem] items-center gap-3 px-3 py-2.5 text-left hover:bg-slate-800">
        <span className="text-sm text-slate-100 truncate" title={c.name}>{c.name}</span>
        <span className="text-[11px] text-slate-400 rounded bg-slate-700/60 px-1.5 py-0.5 text-center">{c.stage ?? '—'}</span>
        <span className="h-1.5 rounded-full bg-slate-700 overflow-hidden" aria-hidden>
          <span className="block h-full bg-rose-500 dark:bg-rose-400" style={{ width: `${(n / max) * 100}%` }} />
        </span>
        <span className="text-sm font-semibold text-slate-100 tabular-nums text-right">{n}</span>
        <ChevronRight className="w-4 h-4 text-slate-400" />
      </button>
      {/* 바깥 모달은 z-40 — 안에서 여는 문항 상세·결정 창(z-50)이 늘 위에 뜨게 */}
      <Modal isOpen={open} onClose={() => setOpen(false)} title={`${c.name}${c.stage ? ` · ${c.stage}` : ''} · 틀린 문항 ${n}`} maxWidth="max-w-4xl" zClass="z-40">
        <div className="space-y-3" data-testid={`report-cause-modal-${c.key}`}>
          <div className="space-y-1.5">
            <p className="text-xs text-slate-400" title="이 원인에서 해 볼 수 있는 개선 후보 — AI 추천은 참고, 실험·반영은 사람이 정한다">
              만들 수 있는 개선 {openT.length}{closedT.length > 0 && <span className="text-slate-500"> · 결정 끝남 {closedT.length}</span>}</p>
            {openT.length === 0 && <p className="text-xs text-slate-500">{closedT.length ? '열린 후보 없음 — 모두 결정 끝남' : '후보 없음 — 새 기법 아이디어 자리'}</p>}
            {openT.map((t) => (
              <MethodRow key={t.id} t={t} cand={candOf(t.id)} imp={imp} axis={axis} isAdmin={isAdmin} only={only === t.id}
                onOnly={() => setOnly(only === t.id ? null : t.id)} onPick={onPick ? (q, i) => onPick(q, i, t.title) : undefined} />
            ))}
            {closedT.length > 0 && (
              <button className="text-xs text-slate-500 hover:text-slate-300" onClick={() => setShowClosed(!showClosed)}>
                {showClosed ? '결정 끝난 방법 접기' : `결정 끝난 방법 ${closedT.length} 보기(안 하기 · 운영 반영)`}</button>
            )}
            {showClosed && closedT.map((t) => (
              <MethodRow key={t.id} t={t} cand={candOf(t.id)} imp={imp} axis={axis} isAdmin={isAdmin} only={only === t.id}
                onOnly={() => setOnly(only === t.id ? null : t.id)} onPick={onPick ? (q, i) => onPick(q, i, t.title) : undefined} />
            ))}
          </div>
          {c.rows.length > 0 && <QuestionList key={only ?? 'all'} rows={rows} title={sel ? `${sel.title} 문항` : `${c.name} 문항`} axis={axis} onPick={onPick} />}
          {c.key === 'ingest' && <p className="text-[11px] text-slate-500">적재 검사 문항은 다른 원인 줄에도 들어 있어요(겹침) — {rep.techniques.filter((t) => t.cause === 'ingest').length}개 검사 기준</p>}
        </div>
      </Modal>
    </div>
  );
}

/** ① 개선할 곳 — 원인별(단계 꼬리표). stage = 단계 줄에서 고른 단계(그 단계 원인만) */
export function ImproveAreas({ rep, imp, axis, stage, isAdmin }: {
  rep: GateReport; imp?: Improvement | null; axis: GateAxis; stage: Stage | null; isAdmin: boolean;
}) {
  const { picker, modal } = useQuestionPicker();
  const [showUnmeasured, setShowUnmeasured] = useState(false);
  const causes = useMemo(() => {
    const m = new Map<string, CauseRow>();
    const get = (key: string, name: string) => {
      if (!m.has(key)) m.set(key, { key, name, stage: CAUSE_STAGE[key], rows: [], techs: [] });
      return m.get(key)!;
    };
    for (const r of rep.failed) if (r.cause) get(r.cause, r.cause_name ?? r.cause).rows.push(r);
    const ing = ingestQids(rep);
    for (const t of rep.techniques) {
      const name = EXTRA_CAUSE[t.cause] ?? rep.failed.find((r) => r.cause === t.cause)?.cause_name ?? t.cause;
      get(t.cause, name).techs.push(t);
    }
    if (m.has('ingest')) m.get('ingest')!.rows = rep.failed.filter((r) => ing.has(r.qid));
    const order = (s?: Stage) => (s ? STAGES.findIndex((x) => x.key === s) : 99);
    return [...m.values()].sort((a, b) => order(a.stage) - order(b.stage) || b.rows.length - a.rows.length);
  }, [rep]);
  const shown = stage ? causes.filter((c) => c.stage === stage) : causes;
  const diagnosedN = rep.failed.filter((r) => r.cause).length;
  const locked = rep.failed.filter((r) => r.locked);
  const noCause = rep.failed.filter((r) => !r.cause && !r.locked);
  const unmeasuredN = rep.unmeasured.reduce((s, u) => s + u.n, 0);
  const max = Math.max(1, ...causes.map((c) => c.rows.length));
  const pick = picker(rep.gate_run_id);
  return (
    <section className="space-y-2" data-testid="gate-report">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm font-semibold text-slate-100">① 개선할 곳</h3>
        <span className="text-xs text-slate-400" data-testid="report-failed-n" title="진단한 틀린 문항 — 처음 틀린 단계로 원인을 나눔(잠근 골든셋은 진단 안 함)">
          원인별 · 틀린 문항 {diagnosedN}</span>
        {stage && <span className="text-xs text-indigo-600 dark:text-indigo-300" data-testid="stage-filter">{stage} 단계만</span>}
        <span className="text-[11px] text-slate-500 ml-auto" title={`측정 #${rep.gate_run_id} "${rep.gate_label}"`}>진단 {fmtDate(rep.diagnosed_at)}
          {rep.diagnosed_gate_run_id !== rep.gate_run_id && (
            <span className="text-amber-600 dark:text-amber-400" title="지금 점수 측정은 아직 진단 전 — 원인·후보는 예전 측정 기준(scripts/eval_improve.py --save로 다시 진단)">
              {' '}· 측정 #{rep.diagnosed_gate_run_id} 기준 ⓘ</span>
          )}
        </span>
      </div>
      {rep.files_missing.length > 0 && (
        <p className="text-xs text-rose-600 dark:text-rose-400" data-testid="report-files-missing">
          결과 파일이 서버에 없어 틀린 문항을 다 못 보여요: {rep.files_missing.join(', ')} (eval_out/)
        </p>
      )}
      {shown.length === 0 && <p className="text-sm text-slate-500">{stage ? `${stage} 단계에 틀린 문항·후보가 없어요` : '틀린 문항이 없어요'}</p>}
      {shown.map((c) => <CauseBlock key={c.key} c={c} max={max} rep={rep} imp={imp} axis={axis} isAdmin={isAdmin} onPick={pick} />)}
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500" data-testid="report-footnote">
        {locked.length > 0 && (
          <button className="hover:text-slate-300" onClick={() => pick?.(locked.map((r) => r.qid), 0, '잠근 문항(진단 안 함)')}
            title="잠근 골든셋(사람·실사용 문항) 중 틀린 것 — 고칠 때 안 보고 잴 때만 봄. 보기만">잠근 문항 {locked.length} — 진단 안 함 ›</button>
        )}
        {noCause.length > 0 && <span title="진단 기록에 원인이 없는 틀린 문항(진단 뒤 측정이 바뀜 등)">원인 미분류 {noCause.length}</span>}
        {unmeasuredN > 0 && (
          <button className="text-amber-700 dark:text-amber-300 hover:underline" onClick={() => setShowUnmeasured(true)} data-testid="report-unmeasured-open"
            title="정답 데이터를 채팅이 못 봐서 틀린 문항 목록에도 안 잡히는 문항">측정 안 됨 {unmeasuredN} ›</button>
        )}
      </p>
      <Modal isOpen={showUnmeasured} onClose={() => setShowUnmeasured(false)} title={`측정도 안 되는 문항 ${unmeasuredN}`} maxWidth="max-w-2xl">
        <div className="space-y-2" data-testid="report-unmeasured">
          <p className="text-xs text-slate-400">정답이 채팅이 안 보는 곳에만 있어 틀린 문항 목록에도 안 잡힙니다 — 그곳을 채팅에 연결해야 잴 수 있어요.</p>
          {rep.unmeasured.map((u) => (
            <div key={u.store} className="rounded-lg border border-slate-700 bg-slate-800 p-3 flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <p className="text-sm text-slate-100 flex-1 min-w-0">{u.store}에만 정답 <span className="text-xs text-slate-500">채팅 문맥에 {u.store}이(가) 안 들어감</span></p>
              <p className="text-lg font-semibold text-slate-100 tabular-nums">{u.n}<span className="text-xs font-normal text-slate-500"> / {u.total}</span></p>
              <p className="basis-full text-xs text-slate-500">고칠 곳: {u.ref}</p>
            </div>
          ))}
        </div>
      </Modal>
      {modal}
    </section>
  );
}

/** ② 진행 중인 개선 — 사람이 지금 정할 후보(isOpen). 결정 끝난 것·나중에는 ①의 원인 안에서만 */
export function InProgress({ imp, axis, isAdmin }: { imp?: Improvement | null; axis: GateAxis; isAdmin: boolean }) {
  const { picker, modal } = useQuestionPicker();
  const open = (imp?.candidates ?? []).filter(isOpen)
    .sort((a, b) => Number(b.status === 'approved') - Number(a.status === 'approved') || b.n_target - a.n_target);
  const pick = imp ? picker(imp.run.gate_run_id) : undefined;
  return (
    <section id="gate-in-progress" data-testid="gate-in-progress"
      className="space-y-2 rounded-lg transition-shadow duration-500 data-[flash]:ring-2 data-[flash]:ring-indigo-500 data-[flash]:ring-offset-4 data-[flash]:ring-offset-slate-900">
      <div className="flex items-baseline gap-3">
        <h3 className="text-sm font-semibold text-slate-100">② 진행 중인 개선</h3>
        <span className="text-xs text-slate-400" title="결정 전(AI가 해 볼 만·재 봐야 앎) + 실험 중. 실험 → 다시 재기 → 운영 반영">사람이 정할 후보 {open.length}</span>
      </div>
      {!imp ? <p className="text-sm text-slate-500">진단 전 — scripts/eval_improve.py --axis {axis} --save로 만든 뒤 여기 보여요</p>
        : open.length === 0 ? <p className="text-sm text-slate-500">지금 정할 후보가 없어요</p>
          : <div className="space-y-1.5">{open.map((c) => <InProgressRow key={c.id} c={c} improvementId={imp.run.id} isAdmin={isAdmin} axis={axis}
              onPick={pick ? (q, i) => pick(q, i, c.title) : undefined} />)}</div>}
      {modal}
    </section>
  );
}

/** ② 한 줄 — 제목·상태·AI 추천·겨냥 문항 수, 누르면 후보 카드 모달(10/30 사용자 "진행 중인 개선도 모달로") */
function InProgressRow({ c, improvementId, isAdmin, axis, onPick }: {
  c: Candidate; improvementId: number; isAdmin: boolean; axis: GateAxis; onPick?: (qids: string[], i: number) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)} aria-haspopup="dialog" data-testid={`inprogress-${c.key}`}
        className="w-full flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2.5 text-left hover:bg-slate-800">
        <span className="text-sm text-slate-100 min-w-0 flex-1 truncate" title={c.title}>{c.title}</span>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${STATUS_BADGE[c.status].cls}`}>{STATUS_BADGE[c.status].label}</span>
        <span className="text-xs text-slate-500">{REC[c.recommend]}</span>
        <span className="text-xs text-slate-300 tabular-nums">{c.n_target}문항</span>
        <ChevronRight className="w-4 h-4 text-slate-400" />
      </button>
      <CandidateModal isOpen={open} onClose={() => setOpen(false)} c={c} improvementId={improvementId} isAdmin={isAdmin} axis={axis} onPick={onPick} />
    </>
  );
}
