import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { getGateReport, getLatestImprovement, listTechniques, setRealQuestion, type ReportRow, type ReportTechnique } from '../../api/eval';
import { showConfirm, showError } from '../../store/useDialogStore';
import { useAuthStore } from '../../store/useAuthStore';
import { CandidateCard } from './GateImprove';
import { TechniqueCard } from './TechniqueCard';
import { useQuestionPicker } from './GateRuns';
import { Modal } from '../ui/Modal';

/**
 * 평가 게이트 [리포트] — 틀린 문항 리포트(v2.172, 사용자 10/26 "AI가 내리는 답을 관리자가 리포트 받고 개선할 수 있게").
 * 위: 지금 점수 한 줄(측정 안 되는 문항은 숫자만, 누르면 창). 아래: 왼쪽 = 고칠 방법을 단계별로 묶고 겨냥 문항 수(또는 원인별),
 * 오른쪽 = 고른 방법의 결정·측정 + 그 문항 페이지(8개씩). 문항↔고칠 방법 연결을 한눈에(10/27 사용자 "매핑해서 가시적으로").
 * 문항을 누르면 질문·답·채점 이유·저장 구조(결과 파일에서, 관리자만).
 * 서버가 한 번에 묶어 준다(service/eval/report.py) — 진단·후보·숫자는 scripts/eval_improve.py·check_ingest.py 기록을 읽기만.
 */

const STATUS: Record<string, { label: string; cls: string }> = {
  proposed: { label: '결정 전', cls: 'text-slate-400' },
  approved: { label: '실험 중', cls: 'text-indigo-600 dark:text-indigo-400' },
  held: { label: '나중에', cls: 'text-amber-600 dark:text-amber-400' },
  rejected: { label: '안 하기', cls: 'text-slate-500' },
  adopted: { label: '운영 반영', cls: 'text-emerald-600 dark:text-emerald-400' },
};

const pct = (x: number | null | undefined) => (x == null ? '-' : `${(x * 100).toFixed(1)}%`);
const fmtDate = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

const chip = 'inline-block rounded px-1.5 py-0.5 text-xs';

function Effect({ t }: { t: ReportTechnique }) {
  const e = t.effect;
  if (!e) return <span className="text-slate-500">아직 안 재 봄</span>;
  if (!e.locked) {
    return (
      <span className="text-amber-600 dark:text-amber-400" title="AI가 만든 문항에서만 잰 측정 — 사람이 쓴 질문(잠근 시험지)이 없어서 실제 효과는 모릅니다">
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

/** 실사용 문항 — 정정 승인 때 자동으로 시험지에 들어가므로, 이상한 질문은 여기서 뺀다(다음 측정부터, 리뷰 M4) */
function RealToggle({ r }: { r: ReportRow }) {
  const qc = useQueryClient();
  const id = Number(r.qid.slice(5));
  const out = r.real_status === 'dismissed';
  const m = useMutation({
    mutationFn: () => setRealQuestion(id, out ? 'added' : 'dismissed'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['eval-report'] }),
    onError: (e) => showError(e, '시험지 상태를 바꾸지 못했습니다'),
  });
  const click = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!out && !(await showConfirm('이 실사용 문항을 시험지에서 뺄까요? 다음 측정부터 빠집니다.'))) return;
    m.mutate();
  };
  return (
    <span role="button" tabIndex={0} onClick={click} onKeyDown={(e) => e.key === 'Enter' && click(e as unknown as React.MouseEvent)}
      className="ml-1 text-xs text-slate-400 hover:text-slate-200 hover:underline" aria-disabled={m.isPending}
      title={out ? '빼기로 한 문항 — 다시 넣으면 다음 측정부터 잰다' : '질문이 이상하거나 정답이 애매하면 — 다음 측정부터 시험지에서 뺀다'}>
      {out ? '· 빼기로 함 (다시 넣기)' : '· 시험지에서 빼기'}
    </span>
  );
}

const PAGE = 8;
const STAGE_ORDER = ['적재', '검색', '근거 판정', '답 모양', '생성'];
const causeOf = (r: ReportRow) => r.cause_name ?? (r.locked ? '잠근 문항(진단 안 함)' : '원인 없음');

/** 왼쪽 목록에서 고른 것 — 전체 / 고칠 방법 하나 / 고칠 방법 없음 / 원인 하나 */
type Pick = { kind: 'all' } | { kind: 'tech'; id: number } | { kind: 'none' } | { kind: 'cause'; name: string };

function NavItem({ active, onClick, label, n, sub, testid }: { active: boolean; onClick: () => void; label: string; n: number; sub?: React.ReactNode; testid?: string }) {
  return (
    <button onClick={onClick} data-testid={testid} aria-pressed={active}
      className={clsx('w-full flex items-start gap-2 rounded-md px-2.5 py-2 text-left',
        active ? 'bg-indigo-50 ring-1 ring-indigo-400 dark:bg-indigo-500/10 dark:ring-indigo-500' : 'hover:bg-slate-700/40')}>
      <span className="min-w-0 flex-1">
        <span className={clsx('block text-sm break-words', n === 0 ? 'text-slate-500' : 'text-slate-100')} title={n === 0 ? '지금 틀린 문항 중 이 방법이 겨냥한 문항 없음' : undefined}>{label}</span>
        {sub && <span className="block text-xs">{sub}</span>}
      </span>
      <span className={clsx('shrink-0 text-sm font-semibold tabular-nums', n === 0 ? 'text-slate-600' : 'text-slate-200')}>{n}</span>
    </button>
  );
}

/** 문항 줄 — 질문 한 줄 + 왜·저장 칩. 고칠 방법이 여럿이면 "방법 n개"만(자세한 건 문항 창) */
function QRow({ r, onClick }: { r: ReportRow; onClick: () => void }) {
  return (
    <button className="w-full text-left px-3 py-2.5 space-y-1 hover:bg-slate-800/60" data-testid={`report-row-${r.qid}`} onClick={onClick}>
      <p className="text-sm text-slate-100 min-w-0">
        {r.type === 'real' && <span className={`${chip} mr-1 bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300`} title="정정 요청이 승인된 실제 채팅 질문">실사용</span>}
        {r.locked && <span className={`${chip} mr-1 bg-slate-700 text-slate-300`} title="잠근 시험지 — 결과를 잴 때만 보고 고칠 때(진단)는 안 봅니다. 보기만">잠금</span>}
        <span className="break-words">{r.question || '(질문 글 없음)'}</span>
        {r.type === 'real' && r.qid.startsWith('REAL-') && <RealToggle r={r} />}
      </p>
      <p className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-slate-500">{r.qid}</span>
        <span className="text-slate-400">· {causeOf(r)}</span>
        {r.storage.map((t) => <span key={t} className={`${chip} bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300`}>{t}</span>)}
        {r.techniques.length > 1 && <span className={`${chip} bg-slate-700 text-slate-300`} title={r.techniques.map((t) => t.title).join('\n')}>방법 {r.techniques.length}개</span>}
      </p>
    </button>
  );
}

export function ReportTab() {
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  const rep = useQuery({ queryKey: ['eval-report'], queryFn: getGateReport, enabled: isAdmin, staleTime: 30_000 });
  const impId = rep.data?.improvement_id;
  const imp = useQuery({ queryKey: ['eval-improvement', impId], queryFn: () => getLatestImprovement(impId), enabled: impId != null });
  const techs = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, enabled: isAdmin, staleTime: 60_000 });
  const { picker, modal } = useQuestionPicker();
  const [view, setView] = useState<'tech' | 'cause'>('tech');
  const [picked0, setSel] = useState<Pick>({ kind: 'all' });
  const [tag, setTag] = useState('');
  const [page, setPage] = useState(0);
  const [showUnmeasured, setShowUnmeasured] = useState(false);

  const d = rep.data;
  const groups = useMemo(() => {
    const stageOf = (key: string) => techs.data?.find((t) => t.key === key)?.stage ?? '기타';
    const byStage = new Map<string, ReportTechnique[]>();
    for (const t of d?.techniques ?? []) {
      const s = stageOf(t.technique);
      byStage.set(s, [...(byStage.get(s) ?? []), t]);
    }
    const order = (s: string) => (STAGE_ORDER.indexOf(s) + 1 || 99);
    return [...byStage.entries()].sort((a, b) => order(a[0]) - order(b[0]))
      .map(([s, ts]) => [s, [...ts].sort((a, b) => b.n_failed - a.n_failed)] as const);
  }, [d, techs.data]);
  const causes = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of d?.failed ?? []) m.set(causeOf(r), (m.get(causeOf(r)) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [d]);

  if (!isAdmin) return <p className="text-sm text-slate-500">틀린 문항 리포트는 관리자만 볼 수 있어요(질문·답 원문이 들어 있음).</p>;
  if (rep.isLoading || techs.isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (rep.error) return <p className="text-sm text-rose-600 dark:text-rose-400">{(rep.error as Error).message}</p>;
  if (!d) return <p className="text-sm text-slate-500">아직 진단이 없어요 — scripts/eval_improve.py --save로 만든 뒤 여기 보입니다.</p>;

  // 다시 진단해 후보 id·원인이 바뀌면 고른 것이 사라진다 — 그땐 전체로(리뷰 10/27: 빈 "이 방법으로 고칠 문항 0"이 남았다)
  const sel: Pick = (picked0.kind === 'tech' && !d.techniques.some((t) => t.id === picked0.id))
    || (picked0.kind === 'cause' && !causes.some(([c]) => c === picked0.name)) ? { kind: 'all' } : picked0;
  const noTech = d.failed.filter((r) => r.techniques.length === 0);
  const picked = sel.kind === 'all' ? d.failed
    : sel.kind === 'tech' ? d.failed.filter((r) => r.techniques.some((t) => t.id === sel.id))
    : sel.kind === 'none' ? noTech
    : d.failed.filter((r) => causeOf(r) === sel.name);
  const tags = [...new Set(picked.flatMap((r) => r.storage))];
  const tagOn = tags.includes(tag) ? tag : '';   // 다시 진단해 그 꼬리표가 사라졌으면 거르기 해제(재리뷰 10/27)
  const rows = tagOn ? picked.filter((r) => r.storage.includes(tagOn)) : picked;
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const cur = Math.min(page, pages - 1);
  const shown = rows.slice(cur * PAGE, cur * PAGE + PAGE);
  const choose = (p: Pick) => { setSel(p); setTag(''); setPage(0); };
  const pick = picker(d.gate_run_id);
  const selTech = sel.kind === 'tech' ? d.techniques.find((t) => t.id === sel.id) : undefined;
  const cand = selTech ? imp.data?.candidates.find((c) => c.id === selTech.id) : undefined;
  const info = selTech ? techs.data?.find((t) => t.key === selTech.technique) : undefined;
  const unmeasuredN = d.unmeasured.reduce((s, u) => s + u.n, 0);
  const title = sel.kind === 'all' ? '틀린 문항 전체' : sel.kind === 'none' ? '고칠 방법이 없는 문항' : sel.kind === 'cause' ? sel.name : '이 방법으로 고칠 문항';
  const selCls = 'bg-slate-800 border border-slate-600 rounded-md px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500';
  const tab = (v: 'tech' | 'cause', label: string) => (
    <button onClick={() => { setView(v); choose({ kind: 'all' }); }} aria-pressed={view === v}
      className={clsx('rounded-md px-3 py-1 text-xs', view === v ? 'bg-indigo-600 text-white' : 'text-slate-300 hover:bg-slate-700')}>{label}</button>
  );

  return (
    <div className="space-y-4" data-testid="gate-report">
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-2">
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
          <p className="text-2xl font-semibold text-slate-100" data-testid="report-locked">
            사람이 쓴 질문 {pct(d.score.locked)}
            <span className="ml-2 text-sm font-normal text-slate-400"
              title="잠근 시험지 — 팀원이 직접 쓴 질문·실사용 질문. 고칠 때는 안 보고 결과를 잴 때만 봐서 '시험지에만 강해지는' 착시를 막습니다. 목표 숫자">
              {d.score.locked_n}문항 ⓘ
            </span>
          </p>
          <p className="text-sm text-slate-300">전체 {pct(d.score.total)} <span className="text-slate-500">({d.score.n}문항)</span></p>
          <p className="text-sm text-slate-300" title="정답 정책을 AI에게 넘긴 비율">검색 {pct(d.score.retrieval)}</p>
          <p className="text-sm text-slate-300" data-testid="report-failed-n">틀린 문항 <b className="text-slate-100">{d.failed.length}</b></p>
          {unmeasuredN > 0 && (
            <button className="text-sm text-amber-700 dark:text-amber-300 hover:underline" onClick={() => setShowUnmeasured(true)}
              data-testid="report-unmeasured-open" title="정답 데이터를 채팅이 못 봐서 틀린 문항 목록에도 안 잡히는 문항">
              측정 안 됨 {unmeasuredN} ›</button>
          )}
        </div>
        <p className="text-xs text-slate-500">
          측정 #{d.gate_run_id} "{d.gate_label}" · 진단 {fmtDate(d.diagnosed_at)}
          {d.diagnosed_gate_run_id !== d.gate_run_id && (
            <span className="text-amber-600 dark:text-amber-400" title="지금 점수 측정은 아직 진단 전 — 원인·고칠 방법은 예전 측정 기준(scripts/eval_improve.py --save로 다시 진단)">
              {' '}· 원인·고칠 방법은 측정 #{d.diagnosed_gate_run_id} 기준 ⓘ</span>
          )}
        </p>
        {d.files_missing.length > 0 && (
          <p className="text-xs text-rose-600 dark:text-rose-400" data-testid="report-files-missing">
            결과 파일이 서버에 없어 틀린 문항을 다 못 보여요: {d.files_missing.join(', ')} (eval_out/)
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-[17rem_1fr] gap-4 items-start">
        <nav className="rounded-lg border border-slate-700 bg-slate-800 p-2 space-y-2" aria-label="문항 묶음">
          <div className="flex gap-1 p-0.5 rounded-md bg-slate-900 w-fit" role="group" aria-label="묶는 기준">
            {tab('tech', '고칠 방법별')}{tab('cause', '원인별')}
          </div>
          <NavItem active={sel.kind === 'all'} onClick={() => choose({ kind: 'all' })} label="틀린 문항 전체" n={d.failed.length} testid="report-nav-all" />
          {view === 'tech' ? (
            <>
              {groups.map(([stage, ts]) => (
                <div key={stage} className="space-y-0.5">
                  <p className="px-2.5 pt-1 text-xs text-slate-500">{stage}</p>
                  {ts.map((t) => (
                    <NavItem key={t.id} active={sel.kind === 'tech' && sel.id === t.id} onClick={() => choose({ kind: 'tech', id: t.id })}
                      label={t.title} n={t.n_failed} testid={`report-tech-${t.id}`}
                      sub={<span className={STATUS[t.status]?.cls}>{STATUS[t.status]?.label ?? t.status}</span>} />
                  ))}
                </div>
              ))}
              <div className="border-t border-slate-700 pt-1">
                <NavItem active={sel.kind === 'none'} onClick={() => choose({ kind: 'none' })} label="고칠 방법 없음" n={noTech.length} testid="report-nav-none"
                  sub={<span className="text-slate-500">다음 기법 아이디어 후보</span>} />
              </div>
            </>
          ) : causes.map(([c, n]) => (
            <NavItem key={c} active={sel.kind === 'cause' && sel.name === c} onClick={() => choose({ kind: 'cause', name: c })} label={c} n={n} />
          ))}
        </nav>

        <section className="space-y-3 min-w-0" data-testid="report-panel">
          {selTech && (
            <div className="space-y-2">
              <p className="text-xs"><span className="text-slate-500">최근 측정 </span><Effect t={selTech} /></p>
              {cand ? <CandidateCard c={cand} improvementId={d.improvement_id} isAdmin={isAdmin} onPick={pick} hideQids />
                : <p className="text-xs text-slate-500">{imp.isLoading ? '불러오는 중…' : '후보 정보를 못 불러왔어요'}</p>}
              {info && (
                <details className="text-xs">
                  <summary className="cursor-pointer text-slate-400">이 기법은 무엇인가 · 지금 값 · 왜 있나</summary>
                  <div className="pt-2"><TechniqueCard t={info} /></div>
                </details>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-medium text-slate-200 mr-2" data-testid="report-list-title">{title} {rows.length}{rows.length !== picked.length && ` / ${picked.length}`}</h3>
            {tags.length > 0 && (
              <select className={selCls} value={tagOn} onChange={(e) => { setTag(e.target.value); setPage(0); }} aria-label="저장 상태로 거르기">
                <option value="">저장 상태 전체</option>
                {tags.map((t) => <option key={t} value={t}>{t} ({picked.filter((r) => r.storage.includes(t)).length})</option>)}
              </select>
            )}
          </div>
          <div className="rounded-lg border border-slate-700 divide-y divide-slate-700">
            {shown.length === 0 && <p className="p-3 text-sm text-slate-500">해당 문항이 없어요</p>}
            {shown.map((r, i) => <QRow key={r.qid} r={r} onClick={() => pick?.(rows.map((x) => x.qid), cur * PAGE + i, title)} />)}
          </div>
          {pages > 1 && (
            <div className="flex items-center justify-center gap-3 text-sm" data-testid="report-pager">
              <button className="px-2 py-1 rounded text-slate-300 hover:bg-slate-700 disabled:opacity-40" disabled={cur === 0} onClick={() => setPage(cur - 1)}>‹ 이전</button>
              <span className="text-slate-400 tabular-nums" data-testid="report-page">{cur + 1} / {pages}</span>
              <button className="px-2 py-1 rounded text-slate-300 hover:bg-slate-700 disabled:opacity-40" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>다음 ›</button>
            </div>
          )}
        </section>
      </div>

      <Modal isOpen={showUnmeasured} onClose={() => setShowUnmeasured(false)} title={`측정도 안 되는 문항 ${unmeasuredN}`} maxWidth="max-w-2xl">
        <div className="space-y-2" data-testid="report-unmeasured">
          <p className="text-xs text-slate-400">정답이 채팅이 안 보는 곳에만 있어 틀린 문항 목록에도 안 잡힙니다 — 그곳을 채팅에 연결해야 잴 수 있어요.</p>
          {d.unmeasured.map((u) => (
            <div key={u.store} className="rounded-lg border border-slate-700 bg-slate-800 p-3 flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <p className="text-sm text-slate-100 flex-1 min-w-0">{u.store}에만 정답 <span className="text-xs text-slate-500">채팅 문맥에 {u.store}이(가) 안 들어감</span></p>
              <p className="text-lg font-semibold text-slate-100 tabular-nums">{u.n}<span className="text-xs font-normal text-slate-500"> / {u.total}</span></p>
              <p className="basis-full text-xs text-slate-500">고칠 곳: {u.ref}</p>
            </div>
          ))}
        </div>
      </Modal>
      {modal}
    </div>
  );
}
