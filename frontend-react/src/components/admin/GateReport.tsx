import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { getGateReport, getLatestImprovement, listTechniques, setRealQuestion, type ReportRow, type ReportTechnique } from '../../api/eval';
import { showConfirm, showError } from '../../store/useDialogStore';
import { useAuthStore } from '../../store/useAuthStore';
import { CandidateCard } from './GateImprove';
import { TechniqueCard } from './TechniqueCard';
import { useQuestionPicker } from './GateRuns';

/**
 * 평가 게이트 [리포트] — 틀린 문항 리포트(v2.172, 사용자 10/26 "AI가 내리는 답을 관리자가 리포트 받고 개선할 수 있게").
 * 위에서 아래로: 지금 점수 → 틀린 문항(질문 · 왜 틀렸나 · 정답 데이터가 어떻게 저장돼 있나 · 고칠 기법) → 측정도 안 되는 문항 →
 * 고칠 방법(기법별 잠근 시험지 효과 + 사람 결정). 문항을 누르면 질문·답·채점 이유·저장 구조(결과 파일에서, 관리자만).
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

function TechniqueRow({ t, open, onToggle, children }: { t: ReportTechnique; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  const st = STATUS[t.status] ?? STATUS.proposed;
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800" data-testid={`report-tech-${t.id}`}>
      <button className="w-full flex items-start gap-2 p-3 text-left hover:bg-slate-700/40" onClick={onToggle} aria-expanded={open}>
        {open ? <ChevronDown className="w-4 h-4 mt-0.5 shrink-0 text-slate-400" /> : <ChevronRight className="w-4 h-4 mt-0.5 shrink-0 text-slate-400" />}
        <div className="min-w-0 flex-1 space-y-0.5">
          <p className="text-sm text-slate-100">{t.title} <span className={`text-xs ${st.cls}`}>· {st.label}</span></p>
          <p className="text-xs text-slate-400">
            틀린 문항 {t.n_failed}개 겨냥 · <Effect t={t} />
          </p>
        </div>
        <span className="text-xs text-slate-500 shrink-0">{t.technique_name}</span>
      </button>
      {open && <div className="border-t border-slate-700 p-3 space-y-3">{children}</div>}
    </div>
  );
}

export function ReportTab() {
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  const rep = useQuery({ queryKey: ['eval-report'], queryFn: getGateReport, enabled: isAdmin, staleTime: 30_000 });
  const impId = rep.data?.improvement_id;
  const imp = useQuery({ queryKey: ['eval-improvement', impId], queryFn: () => getLatestImprovement(impId), enabled: impId != null });
  const techs = useQuery({ queryKey: ['eval-techniques'], queryFn: listTechniques, enabled: isAdmin, staleTime: 60_000 });
  const { picker, modal } = useQuestionPicker();
  const [cause, setCause] = useState('');
  const [tag, setTag] = useState('');
  const [tech, setTech] = useState<number | null>(null);
  const [openTech, setOpenTech] = useState<number | null>(null);

  const d = rep.data;
  const options = useMemo(() => {
    const causes = new Map<string, number>();
    const tags = new Map<string, number>();
    for (const r of d?.failed ?? []) {
      const c = r.cause_name ?? (r.locked ? '잠근 문항(진단 안 함)' : '원인 없음');
      causes.set(c, (causes.get(c) ?? 0) + 1);
      for (const t of r.storage) tags.set(t, (tags.get(t) ?? 0) + 1);
    }
    const sorted = (m: Map<string, number>) => [...m.entries()].sort((a, b) => b[1] - a[1]);
    return { causes: sorted(causes), tags: sorted(tags) };
  }, [d]);

  if (!isAdmin) return <p className="text-sm text-slate-500">틀린 문항 리포트는 관리자만 볼 수 있어요(질문·답 원문이 들어 있음).</p>;
  if (rep.isLoading) return <p className="text-sm text-slate-500">불러오는 중…</p>;
  if (rep.error) return <p className="text-sm text-rose-600 dark:text-rose-400">{(rep.error as Error).message}</p>;
  if (!d) return <p className="text-sm text-slate-500">아직 진단이 없어요 — scripts/eval_improve.py --save로 만든 뒤 여기 보입니다.</p>;

  const rows = d.failed.filter((r) => (!cause || (r.cause_name ?? (r.locked ? '잠근 문항(진단 안 함)' : '원인 없음')) === cause)
    && (!tag || r.storage.includes(tag)) && (tech == null || r.techniques.some((t) => t.id === tech)));
  const pick = picker(d.gate_run_id);
  const cand = (id: number) => imp.data?.candidates.find((c) => c.id === id);
  const techInfo = (key: string) => techs.data?.find((t) => t.key === key);
  const sel = 'bg-slate-800 border border-slate-600 rounded-md px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500';

  return (
    <div className="space-y-5" data-testid="gate-report">
      <div className="rounded-lg border border-slate-700 bg-slate-800 p-4 space-y-2">
        <p className="text-xs text-slate-400">AI가 내린 답을 리포트로 받아 왜 틀렸는지 보고, 고칠 방법을 골라 승인하는 곳</p>
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
        </div>
        <p className="text-xs text-slate-500">
          측정 #{d.gate_run_id} "{d.gate_label}" · 진단 {fmtDate(d.diagnosed_at)}
          {d.diagnosed_gate_run_id !== d.gate_run_id && (
            <span className="text-amber-600 dark:text-amber-400" title="지금 점수 측정은 아직 진단 전 — 원인·고칠 방법은 예전 측정 기준(scripts/eval_improve.py --save로 다시 진단)">
              {' '}· 원인·고칠 방법은 측정 #{d.diagnosed_gate_run_id} 기준 ⓘ</span>
          )}
          <span title="[운영에 반영했어요]를 누르면 그 측정이 '지금 점수'가 됩니다"> ⓘ</span>
        </p>
        {d.files_missing.length > 0 && (
          <p className="text-xs text-rose-600 dark:text-rose-400" data-testid="report-files-missing">
            결과 파일이 서버에 없어 틀린 문항을 다 못 보여요: {d.files_missing.join(', ')} (eval_out/)
          </p>
        )}
      </div>

      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium text-slate-200 mr-2">틀린 문항 {rows.length}{rows.length !== d.failed.length && ` / ${d.failed.length}`}</h3>
          <select className={sel} value={cause} onChange={(e) => setCause(e.target.value)} aria-label="원인으로 거르기">
            <option value="">원인 전체</option>
            {options.causes.map(([c, n]) => <option key={c} value={c}>{c} ({n})</option>)}
          </select>
          <select className={sel} value={tag} onChange={(e) => setTag(e.target.value)} aria-label="저장 상태로 거르기">
            <option value="">저장 상태 전체</option>
            {options.tags.map(([t, n]) => <option key={t} value={t}>{t} ({n})</option>)}
          </select>
          <select className={sel} value={tech ?? ''} onChange={(e) => setTech(e.target.value ? Number(e.target.value) : null)} aria-label="기법으로 거르기">
            <option value="">기법 전체</option>
            {d.techniques.filter((t) => t.n_failed > 0).map((t) => <option key={t.id} value={t.id}>{t.title} ({t.n_failed})</option>)}
          </select>
        </div>
        <div className="rounded-lg border border-slate-700 divide-y divide-slate-700">
          {rows.length === 0 && <p className="p-3 text-sm text-slate-500">조건에 맞는 문항이 없어요</p>}
          {rows.map((r, i) => (
            <button key={r.qid} className="w-full text-left p-3 space-y-1 hover:bg-slate-800/60" data-testid={`report-row-${r.qid}`}
              onClick={() => pick?.(rows.map((x) => x.qid), i)}>
              <p className="text-sm text-slate-100 min-w-0">
                <span className="text-xs text-slate-500 mr-2">{r.qid}</span>
                {r.type === 'real' && <span className={`${chip} mr-1 bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300`} title="정정 요청이 승인된 실제 채팅 질문">실사용</span>}
                {r.type === 'real' && r.qid.startsWith('REAL-') && <RealToggle r={r} />}
                {r.locked && <span className={`${chip} mr-1 bg-slate-700 text-slate-300`} title="잠근 시험지 — 결과를 잴 때만 보고 고칠 때(진단)는 안 봅니다. 보기만">잠금</span>}
                <span className="break-words">{r.question || '(질문 글 없음)'}</span>
              </p>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                <span className="text-slate-400"><span className="text-slate-500">왜 </span>{r.cause_name ?? (r.locked ? '보기만(진단 안 함)' : '-')}</span>
                {r.storage.length > 0 && (
                  <span className="flex flex-wrap gap-1"><span className="text-slate-500">저장 </span>
                    {r.storage.map((t) => <span key={t} className={`${chip} bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300`}>{t}</span>)}
                  </span>
                )}
                {r.techniques.length > 0 && (
                  <span className="flex flex-wrap gap-1"><span className="text-slate-500">기법 </span>
                    {r.techniques.map((t) => (
                      <span key={t.id} className={clsx(chip, 'bg-slate-700', STATUS[t.status]?.cls)}>{t.title} · {STATUS[t.status]?.label ?? t.status}</span>
                    ))}
                  </span>
                )}
              </div>
            </button>
          ))}
        </div>
      </section>

      {d.unmeasured.length > 0 && (
        <section className="space-y-2" data-testid="report-unmeasured">
          <h3 className="text-sm font-medium text-slate-200">측정도 안 되는 문항 {d.unmeasured.reduce((s, u) => s + u.n, 0)}
            <span className="ml-2 text-xs font-normal text-slate-500">정답 데이터를 채팅이 못 봐서 틀린 문항 목록에도 안 잡힘</span></h3>
          <div className="rounded-lg border border-slate-700 divide-y divide-slate-700">
            {d.unmeasured.map((u) => (
              <p key={u.store} className="p-3 text-sm text-slate-200">
                {u.store}에만 정답이 있는 문항 {u.n}/{u.total} — 채팅 문맥에 {u.store}이(가) 안 들어감
                <span className="block text-xs text-slate-500">고칠 곳: {u.ref}</span>
              </p>
            ))}
          </div>
        </section>
      )}

      <section className="space-y-2">
        <h3 className="text-sm font-medium text-slate-200">고칠 방법 {d.techniques.length}
          <span className="ml-2 text-xs font-normal text-slate-500" title="효과는 사람이 쓴 질문(잠근 시험지)에서 오른 것만 '효과 있음'으로 봅니다">
            눌러서 내용·결정 ⓘ</span></h3>
        <div className="space-y-2">
          {d.techniques.map((t) => {
            const c = cand(t.id);
            const info = techInfo(t.technique);
            return (
              <TechniqueRow key={t.id} t={t} open={openTech === t.id} onToggle={() => setOpenTech(openTech === t.id ? null : t.id)}>
                {c ? <CandidateCard c={c} improvementId={d.improvement_id} isAdmin={isAdmin} onPick={pick} />
                  : <p className="text-xs text-slate-500">{imp.isLoading ? '불러오는 중…' : '후보 정보를 못 불러왔어요'}</p>}
                {info && (
                  <details className="text-xs">
                    <summary className="cursor-pointer text-slate-400">이 기법은 무엇인가 · 지금 값 · 왜 있나</summary>
                    <div className="pt-2"><TechniqueCard t={info} /></div>
                  </details>
                )}
              </TechniqueRow>
            );
          })}
        </div>
      </section>
      {modal}
    </div>
  );
}
