import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getTeams } from '../../api/auth';
import {
  confirmImport, confluencePageUrl, getImportBatch, listImportBatches, reviewImportSample, rollbackImport, startImport, updateImportPages,
  type ImportBatchDetail, type ImportCollection, type ImportPage,
} from '../../api/teamImport';
import { useAuthStore } from '../../store/useAuthStore';
import { showAlert, showConfirm, showError } from '../../store/useDialogStore';

/**
 * 지식 › 팀 적재(컨플 수집기, 10/29 목업 Qnt3dJ5agwBhczty75h6Nc · confluence-curation-layer-design §12).
 * 수집 대상(주소) → 분류 미리보기(자동 분류 · 검토 필요 · 표본 검토 · 제외 — 확정 전엔 아무것도 적재 안 됨) → 분류 확정·적재 → 게이트 · 롤백.
 * 정확도 우선: 자동 분류는 확신 기준을 넘은 것만, 나머지는 사람이 AI 제안별로 묶어 분류. 팀 첫 적재는 표본 검토가 게시 전 필수.
 */
export function TeamImportPanel() {
  const { data: batches = [] } = useQuery({
    queryKey: ['team-import'], queryFn: listImportBatches,
    refetchInterval: (q) => ((q.state.data ?? []).some((b) => b.status === 'previewing' || b.status === 'loading') ? 5000 : false),
  });
  const [openId, setOpenId] = useState<number | null>(null);
  const current = openId ?? batches[0]?.id ?? null;
  return (
    <div className="space-y-5" data-testid="team-import">
      <StartForm onStarted={setOpenId} />
      {batches.length > 0 && (
        <div className="flex flex-wrap gap-2 text-xs">
          {batches.map((b) => (
            <button key={b.id} onClick={() => setOpenId(b.id)}
              className={`px-3 py-1.5 rounded-lg border ${b.id === current ? 'border-indigo-500 dark:border-indigo-400 text-slate-100 bg-indigo-50 dark:bg-indigo-500/10' : 'border-slate-700 text-slate-400 hover:text-slate-200'}`}
              title={b.root_url}>
              #{b.id} {b.team_name ?? ''} · {b.root_title ?? b.root_url} · {STATUS_TEXT[b.status]}
            </button>
          ))}
        </div>
      )}
      {current != null && <BatchView key={current} id={current} />}
    </div>
  );
}

const STATUS_TEXT: Record<string, string> = {
  previewing: '분류 중', preview: '분류 미리보기', failed: '실패', loading: '적재 중', loaded: '적재됨', rolled_back: '롤백됨',
};

function StartForm({ onStarted }: { onStarted: (id: number) => void }) {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';
  const { data: teams = [] } = useQuery({ queryKey: ['teams'], queryFn: getTeams, enabled: isAdmin, staleTime: 300_000 });
  const [url, setUrl] = useState('');
  const [teamId, setTeamId] = useState<number | null>(null);
  const qc = useQueryClient();
  const start = useMutation({
    mutationFn: () => startImport(url.trim(), isAdmin ? teamId : null),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['team-import'] }); onStarted(r.id); setUrl(''); },
    onError: (e) => showError(e),
  });
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800 p-4 space-y-3">
      <div className="text-sm font-semibold text-slate-100">수집 대상</div>
      <div className="flex flex-wrap gap-2">
        {isAdmin && (
          <select value={teamId ?? ''} onChange={(e) => setTeamId(e.target.value ? Number(e.target.value) : null)}
            className="text-sm bg-slate-900 border border-slate-700 rounded-lg px-2 py-2 text-slate-200" aria-label="팀">
            <option value="">팀 고르기</option>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        )}
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="컨플 주소 — 팀·파트 공간(/display/KEY) 또는 최상위 페이지"
          className="flex-1 min-w-[260px] text-sm bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-slate-200" aria-label="컨플 주소" />
        <button disabled={!url.trim() || (isAdmin && !teamId) || start.isPending} onClick={() => start.mutate()}
          className="text-sm font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40"
          title="페이지를 받아 AI가 분류만 한다 — 확정 전엔 아무것도 적재되지 않음">
          분류 미리보기 만들기
        </button>
      </div>
    </div>
  );
}

type Tab = 'preview' | 'gate';

function BatchView({ id }: { id: number }) {
  const qc = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ['team-import', id], queryFn: () => getImportBatch(id),
    refetchInterval: (q) => (['previewing', 'loading'].includes(q.state.data?.batch.status ?? '') ? 3000 : false),
  });
  const [tab, setTab] = useState<Tab>('preview');
  const refresh = () => { qc.invalidateQueries({ queryKey: ['team-import', id] }); qc.invalidateQueries({ queryKey: ['team-import'] }); };
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했어요: {String((error as Error).message)}</p>;
  if (!data) return <p className="text-sm text-slate-400">불러오는 중…</p>;
  const b = data.batch;
  if (b.status === 'previewing' || b.status === 'failed') {
    return (
      <div className="rounded-xl border border-slate-700 bg-slate-800 p-4 space-y-2 text-sm">
        <div className="text-slate-100 font-semibold">#{b.id} {b.root_title ?? b.root_url}</div>
        {b.status === 'failed'
          ? <p className="text-rose-600 dark:text-rose-400">실패: {b.error}</p>
          : <>
              <p className="text-slate-300">분류 중 {b.done_pages} / {b.total_pages}페이지 — 아직 아무것도 적재되지 않음</p>
              <div className="h-2 rounded-full bg-slate-700 overflow-hidden"><div className="h-full bg-indigo-500" style={{ width: `${b.total_pages ? (100 * b.done_pages) / b.total_pages : 0}%` }} /></div>
            </>}
        {b.truncated && <p className="text-xs text-amber-700 dark:text-amber-300">페이지가 많아 앞 {b.total_pages}개만 받았어요 — 하위 페이지 주소로 나눠 넣어 주세요.</p>}
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        {(['preview', 'gate'] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`text-sm font-semibold px-4 py-2 rounded-lg border ${tab === t ? 'border-indigo-500 dark:border-indigo-400 bg-indigo-50 dark:bg-indigo-500/10 text-slate-100' : 'border-slate-700 text-slate-400'}`}>
            {t === 'preview' ? '분류 미리보기' : '게이트 · 게시'}
          </button>
        ))}
      </div>
      {tab === 'preview' ? <Preview data={data} onChange={refresh} /> : <GateTab data={data} onChange={refresh} />}
    </div>
  );
}

function useNames(cols: ImportCollection[]) {
  return useMemo(() => new Map(cols.map((c) => [c.id, c])), [cols]);
}

function CollectionSelect({ cols, value, onChange, placeholder = '분류할 곳' }: {
  cols: ImportCollection[]; value: number | null; onChange: (id: number | null) => void; placeholder?: string;
}) {
  const biz = cols.filter((c) => c.kind === 'domain');
  const org = cols.filter((c) => c.kind === 'org_space');
  return (
    <select value={value ?? ''} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
      className="text-sm bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-slate-200" aria-label={placeholder}>
      <option value="">— {placeholder} —</option>
      <optgroup label="업무 컬렉션">
        {biz.map((c) => <option key={c.id} value={c.id} disabled={!c.writable}>{c.label}{c.unowned ? ' (담당 없음)' : c.parts.length ? ` · ${c.parts.join(', ')}` : ''}{c.writable ? '' : ' — 담당 아님'}</option>)}
      </optgroup>
      <optgroup label="조직 컬렉션">
        {org.map((c) => <option key={c.id} value={c.id} disabled={!c.writable}>{c.label}{c.writable ? '' : ' — 담당 아님'}</option>)}
      </optgroup>
    </select>
  );
}

function Stat({ k, v, sub, hot }: { k: string; v: number | string; sub?: string; hot?: boolean }) {
  return (
    <div className={`rounded-xl bg-slate-800 border p-4 ${hot ? 'border-amber-400 dark:border-amber-500/60' : 'border-slate-700'}`}>
      <div className="text-xs text-slate-400">{k}</div>
      <div className={`text-2xl font-bold tabular-nums ${hot ? 'text-amber-600 dark:text-amber-300' : 'text-slate-100'}`}>{v}</div>
      {sub && <div className="text-xs text-slate-400">{sub}</div>}
    </div>
  );
}

function Preview({ data, onChange }: { data: ImportBatchDetail; onChange: () => void }) {
  const { batch: b, pages, collections } = data;
  const names = useNames(collections);
  const editable = b.status === 'preview';
  const auto = pages.filter((p) => p.status === 'auto');
  const review = pages.filter((p) => p.status === 'review');
  const samples = auto.filter((p) => p.sample_state);
  const sampleDone = samples.filter((p) => p.sample_state !== 'pending').length;
  const sampleTarget = Object.values(data.sample_targets).reduce((a, v) => a + v, 0);
  const wrong = samples.filter((p) => p.sample_state === 'wrong').length;
  const out = pages.filter((p) => p.status === 'excluded' || p.status === 'held');
  const other = (s: string) => pages.filter((p) => p.status === s).length;
  const confirm = useMutation({
    mutationFn: () => confirmImport(b.id), onSuccess: onChange, onError: (e) => showError(e),
  });
  return (
    <div className="space-y-6">
      <div className="text-xs text-slate-400" title="예외: 읽기 제한으로 바뀐 페이지가 이미 적재돼 있으면 그 조각만 승인 대기로 내림">#{b.id} {b.root_title} · 분류 미리보기 — 확정 전까지 적재되지 않음</div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat k="자동 분류" v={auto.length} sub={`업무 ${auto.filter((p) => names.get(p.final_namespace_id ?? -1)?.kind === 'domain').length} · 조직 ${auto.filter((p) => names.get(p.final_namespace_id ?? -1)?.kind === 'org_space').length}`} />
        <Stat k="검토 필요" v={review.filter((p) => p.final_namespace_id == null).length} sub={`전체 ${review.length}건 중 미분류`} hot={review.some((p) => p.final_namespace_id == null)} />
        <Stat k="표본 검토" v={`${sampleDone} / ${sampleTarget}`} sub={`정정 ${wrong}${b.sampling_required ? ' · 게시 전 필수' : ' · 선택'}`} hot={b.sampling_required && sampleDone < sampleTarget} />
        <Stat k="제외 · 보류" v={out.length} />
      </div>
      <p className="text-xs text-slate-400" title="이미 적재됨 = 다른 경로로 이미 사용 중인 페이지 — 사람이 정한 위치를 덮지 않게 다시 넣지 않음. 읽기 제한 페이지가 이미 다른 곳에 적재돼 있으면 그 조각은 미리보기 때 승인 대기로 내려감(보안 — 배치를 버려도 되돌리지 않음)">
        이미 적재됨 {other('existing')} · 읽기 제한 {other('restricted')}(지식 공백 기록) · 내용 없음 {other('empty')} · 실패 {other('failed')}
      </p>

      <Bundles pages={pages} batchId={b.id} editable={editable} onChange={onChange} />
      <ReviewSection data={data} review={review} editable={editable} onChange={onChange} />
      <AutoSection data={data} auto={auto} editable={editable} onChange={onChange} />
      {out.length > 0 && <OutSection data={data} out={out} editable={editable} onChange={onChange} />}

      {editable && (
        <div className="flex flex-wrap items-center justify-end gap-3 border-t border-slate-700 pt-4">
          {data.blockers.length > 0 && <span className="text-xs text-amber-700 dark:text-amber-300">{data.blockers.join(' · ')}</span>}
          <button disabled={data.blockers.length > 0 || confirm.isPending}
            onClick={async () => { if (await showConfirm('분류를 확정하고 적재할까요? 적재 뒤 평가 게이트에서 나빠지면 이번 배치만 롤백할 수 있어요.')) confirm.mutate(); }}
            className="text-sm font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40">
            분류 확정 · 적재
          </button>
        </div>
      )}
    </div>
  );
}

function PageLink({ rootUrl, p }: { rootUrl: string; p: ImportPage }) {
  const href = confluencePageUrl(rootUrl, p.page_id);
  return href
    ? <a href={href} target="_blank" rel="noreferrer" className="text-slate-200 hover:text-indigo-500 dark:hover:text-indigo-300">{p.title ?? p.page_id}</a>
    : <span className="text-slate-200">{p.title ?? p.page_id}</span>;
}

function Bundles({ pages, batchId, editable, onChange }: { pages: ImportPage[]; batchId: number; editable: boolean; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const groups = useMemo(() => {
    const m = new Map<string, ImportPage[]>();
    for (const p of pages) m.set(p.bundle ?? '(최상위)', [...(m.get(p.bundle ?? '(최상위)') ?? []), p]);
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [pages]);
  const act = useMutation({
    mutationFn: ({ ids, action }: { ids: number[]; action: 'exclude' | 'include' }) => updateImportPages(batchId, ids, action),
    onSuccess: onChange, onError: (e) => showError(e),
  });
  return (
    <section className="space-y-2">
      <button onClick={() => setOpen(!open)} className="text-sm font-semibold text-slate-100">묶음 · {groups.length} {open ? '▾' : '▸'}</button>
      {open && (
        <div className="overflow-x-auto rounded-lg border border-slate-700">
          <table className="w-full text-sm">
            <thead className="bg-slate-800 text-xs text-slate-400"><tr><th className="text-left px-3 py-2">묶음(수집 대상 바로 아래)</th><th className="text-right px-3">페이지</th><th className="text-right px-3">제외</th><th /></tr></thead>
            <tbody>
              {groups.map(([name, ps]) => {
                const movable = ps.filter((p) => p.status === 'auto' || p.status === 'review');
                const excluded = ps.filter((p) => p.status === 'excluded');
                return (
                  <tr key={name} className="border-t border-slate-700">
                    <td className="px-3 py-2 text-slate-200">{name}</td>
                    <td className="px-3 text-right tabular-nums text-slate-300">{ps.length}</td>
                    <td className="px-3 text-right tabular-nums text-slate-400">{excluded.length}</td>
                    <td className="px-3 text-right whitespace-nowrap">
                      {editable && movable.length > 0 && <button className="text-xs text-slate-400 hover:text-slate-100" title="운영 지식이 아닌 묶음(개인 공간·회의록 등) — 나중에 다시 포함 가능"
                        onClick={() => act.mutate({ ids: movable.map((p) => p.id), action: 'exclude' })}>묶음 제외</button>}
                      {editable && excluded.length > 0 && <button className="ml-3 text-xs text-indigo-600 dark:text-indigo-300"
                        onClick={() => act.mutate({ ids: excluded.map((p) => p.id), action: 'include' })}>포함으로 변경</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ReviewSection({ data, review, editable, onChange }: { data: ImportBatchDetail; review: ImportPage[]; editable: boolean; onChange: () => void }) {
  const names = useNames(data.collections);
  // LLM이 고른 곳별로 묶는다 — 사람은 묶음을 훑고 한 번에 분류(판단은 사람, 클릭은 적게). 이웃 우선 제안(proposed)으로 묶지 않는다:
  // 첫 적재는 이웃이 지식이 많은 컬렉션으로 쏠린다(10/29 GIFT 실측: LLM 온라인스토어 209 · 이웃 제안 딜리버스 165)
  const groups = useMemo(() => {
    const m = new Map<string, ImportPage[]>();
    for (const p of review) {
      const key = `${p.reason}:${p.ai_namespace_id ?? 'none'}`;
      m.set(key, [...(m.get(key) ?? []), p]);
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [review]);
  if (review.length === 0) return null;
  return (
    <section className="space-y-2">
      <div className="text-sm font-semibold text-slate-100">검토 필요 · {review.length}
        <span className="ml-2 text-xs font-normal text-slate-400" title="자동 분류 기준(LLM과 비슷한 지식 5개 중 4개 일치)을 못 넘었거나 시스템에 없는 정보(담당)가 필요한 것">LLM이 고른 곳별로 묶음 — 넣을 곳은 사람이 고른다 · 분류하거나 보류해야 확정 가능</span></div>
      {groups.map(([key, ps]) => (
        <ReviewGroup key={key} data={data} pages={ps} names={names} editable={editable} onChange={onChange} />
      ))}
    </section>
  );
}

function ReviewGroup({ data, pages, names, editable, onChange }: {
  data: ImportBatchDetail; pages: ImportPage[]; names: Map<number, ImportCollection>; editable: boolean; onChange: () => void;
}) {
  const first = pages[0];
  const llm = first.ai_namespace_id;
  // 넣을 곳은 미리 골라 두지 않는다 — 검토 필요는 AI가 확신하지 못한 것이고, 어느 신호로 미리 골라도 10/29 실측에서 틀리는 쪽이 있었다
  // (첫 공간: 이웃 우선 제안 5/9 · LLM 0/9, GIFT: 이웃이 딜리버스로 쏠림, "이웃 = LLM"은 대개 이웃이 약해 LLM으로 대체된 것). 사람이 묶음마다 한 번 고른다
  const [target, setTarget] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const left = pages.filter((p) => p.final_namespace_id == null);
  const act = useMutation({
    mutationFn: ({ ids, action, ns }: { ids: number[]; action: 'classify' | 'hold'; ns?: number }) => updateImportPages(data.batch.id, ids, action, ns),
    onSuccess: onChange, onError: (e) => showError(e),
  });
  const reasonText = first.reason ? data.reasons[first.reason] : '';
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/60">
      <div className="flex flex-wrap items-center gap-3 px-3 py-2">
        <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">{reasonText}</span>
        <span className="text-sm text-slate-200" title="LLM = 글을 읽고 고른 곳(참고) — 페이지 보기에서 이웃(비슷한 기존 지식)도 확인">LLM {llm != null ? names.get(llm)?.label ?? `#${llm}` : '판단 못 함'}</span>
        <span className="text-xs text-slate-400">{pages.length}건 · 미분류 {left.length}</span>
        <button onClick={() => setOpen(!open)} className="text-xs text-indigo-600 dark:text-indigo-300">{open ? '접기' : '페이지 보기'}</button>
        {editable && left.length > 0 && (
          <div className="ml-auto flex items-center gap-2">
            <CollectionSelect cols={data.collections} value={target} onChange={setTarget} />
            <button disabled={target == null || act.isPending} onClick={() => target != null && act.mutate({ ids: left.map((p) => p.id), action: 'classify', ns: target })}
              className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-indigo-600 text-white disabled:opacity-40">미분류 {left.length}건 분류</button>
            <button disabled={act.isPending} onClick={() => act.mutate({ ids: left.map((p) => p.id), action: 'hold' })}
              className="text-xs px-3 py-1.5 rounded-lg border border-slate-600 text-slate-300">보류</button>
          </div>
        )}
      </div>
      {open && <PageRows data={data} pages={pages} editable={editable} onChange={onChange} />}
    </div>
  );
}

function PageRows({ data, pages, editable, onChange }: { data: ImportBatchDetail; pages: ImportPage[]; editable: boolean; onChange: () => void }) {
  const names = useNames(data.collections);
  const act = useMutation({
    mutationFn: ({ id, action, ns }: { id: number; action: 'classify' | 'hold' | 'exclude'; ns?: number }) => updateImportPages(data.batch.id, [id], action, ns),
    onSuccess: onChange, onError: (e) => showError(e),
  });
  return (
    <div className="overflow-x-auto border-t border-slate-700">
      <table className="w-full text-sm">
        <thead className="text-xs text-slate-400"><tr><th className="text-left px-3 py-1.5">페이지</th><th className="text-left px-3">묶음</th><th className="text-left px-3">AI 근거</th><th className="text-left px-3">분류</th><th /></tr></thead>
        <tbody>
          {pages.map((p) => (
            <tr key={p.id} className="border-t border-slate-700/60">
              <td className="px-3 py-1.5"><PageLink rootUrl={data.batch.root_url} p={p} /></td>
              <td className="px-3 text-xs text-slate-400">{p.bundle}</td>
              <td className="px-3 text-xs text-slate-400" title={p.signals.reason ?? ''}>
                이웃 {Object.entries(p.signals.neighbors ?? {}).map(([ns, n]) => `${names.get(Number(ns))?.label ?? ns} ${n}`).join(' · ') || '없음'}
                {p.signals.fallback ? ` · ${p.signals.fallback}` : ''}
              </td>
              <td className="px-3">
                {editable
                  ? <CollectionSelect cols={data.collections} value={p.final_namespace_id} onChange={(ns) => ns != null && act.mutate({ id: p.id, action: 'classify', ns })} />
                  : <span className="text-slate-300">{names.get(p.final_namespace_id ?? -1)?.label ?? '—'}</span>}
              </td>
              <td className="px-3 whitespace-nowrap text-right">
                {editable && <>
                  <button className="text-xs text-slate-400 hover:text-slate-100" onClick={() => act.mutate({ id: p.id, action: 'hold' })}>보류</button>
                  <button className="ml-3 text-xs text-slate-400 hover:text-slate-100" onClick={() => act.mutate({ id: p.id, action: 'exclude' })}>제외</button>
                </>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AutoSection({ data, auto, editable, onChange }: { data: ImportBatchDetail; auto: ImportPage[]; editable: boolean; onChange: () => void }) {
  const names = useNames(data.collections);
  const byNs = useMemo(() => {
    const m = new Map<number, ImportPage[]>();
    for (const p of auto) m.set(p.ai_namespace_id ?? -1, [...(m.get(p.ai_namespace_id ?? -1) ?? []), p]);
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [auto]);
  const [open, setOpen] = useState<{ ns: number; mode: 'sample' | 'all' } | null>(null);
  if (auto.length === 0) {
    return <p className="text-xs text-slate-400" title="첫 적재는 비슷한 지식(이웃)이 없어 자동 분류가 거의 없다 — 정확도 우선(10/29 실측)">자동 분류 0 — 이 팀 업무지식이 아직 없어 AI가 확신하지 못했어요. 검토 필요에서 분류해 주세요.</p>;
  }
  return (
    <section className="space-y-2">
      <div className="text-sm font-semibold text-slate-100">자동 분류 · {auto.length}
        <span className="ml-2 text-xs font-normal text-slate-400">컬렉션마다 무작위 {data.sample_size}건 표본 검토 — 정정이 나오면 표본을 더 뽑아요</span></div>
      <div className="overflow-x-auto rounded-lg border border-slate-700">
        <table className="w-full text-sm">
          <thead className="bg-slate-800 text-xs text-slate-400"><tr><th className="text-left px-3 py-2">컬렉션</th><th className="text-right px-3">페이지</th><th className="text-left px-3">표본 검토</th><th /></tr></thead>
          <tbody>
            {byNs.map(([ns, ps]) => {
              const s = ps.filter((p) => p.sample_state);
              const done = s.filter((p) => p.sample_state !== 'pending').length;
              const wrong = s.filter((p) => p.sample_state === 'wrong').length;
              return (
                <tr key={ns} className="border-t border-slate-700">
                  <td className="px-3 py-2 text-slate-200">{names.get(ns)?.label ?? ns} <span className="text-xs text-slate-400">{names.get(ns)?.parts.join(', ')}</span></td>
                  <td className="px-3 text-right tabular-nums text-slate-300">{ps.length}</td>
                  <td className="px-3 text-xs">
                    <span className={done === s.length ? 'text-emerald-700 dark:text-emerald-300' : 'text-slate-400'}>{done} / {s.length}</span>
                    {wrong > 0 && <span className="ml-2 text-rose-600 dark:text-rose-400">정정 {wrong}</span>}
                  </td>
                  <td className="px-3 text-right whitespace-nowrap">
                    <button className="text-xs text-indigo-600 dark:text-indigo-300" onClick={() => setOpen({ ns, mode: 'sample' })}>표본 검토</button>
                    <button className="ml-3 text-xs text-slate-400 hover:text-slate-100" onClick={() => setOpen({ ns, mode: 'all' })}>전체 보기</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {open && (() => {
        const ps = (byNs.find(([ns]) => ns === open.ns)?.[1] ?? []).filter((p) => open.mode === 'all' || p.sample_state);
        return (
          <div className="rounded-lg border border-slate-700">
            <div className="flex items-center justify-between px-3 py-2 text-sm text-slate-200">
              <span>{names.get(open.ns)?.label} · {open.mode === 'sample' ? '표본 검토' : '전체'} {ps.length}</span>
              <button className="text-xs text-slate-400" onClick={() => setOpen(null)}>닫기</button>
            </div>
            {open.mode === 'sample'
              ? <SampleRows data={data} pages={ps} editable={editable} onChange={onChange} />
              : <PageRows data={data} pages={ps} editable={editable} onChange={onChange} />}
          </div>
        );
      })()}
    </section>
  );
}

function SampleRows({ data, pages, editable, onChange }: { data: ImportBatchDetail; pages: ImportPage[]; editable: boolean; onChange: () => void }) {
  const [fix, setFix] = useState<Record<number, number | null>>({});
  const act = useMutation({
    mutationFn: ({ id, ok, ns }: { id: number; ok: boolean; ns?: number | null }) => reviewImportSample(data.batch.id, id, ok, ns),
    onSuccess: async (r) => {
      onChange();
      if (r.added > 0) await showAlert(`정정이 나와 이 컬렉션 표본을 ${r.added}건 더 뽑았어요.`, { tone: 'warning' });
    },
    onError: (e) => showError(e),
  });
  return (
    <div className="overflow-x-auto border-t border-slate-700">
      <table className="w-full text-sm">
        <tbody>
          {pages.map((p) => (
            <tr key={p.id} className="border-t border-slate-700/60">
              <td className="px-3 py-1.5"><PageLink rootUrl={data.batch.root_url} p={p} /></td>
              <td className="px-3 text-xs text-slate-400">{p.bundle}</td>
              <td className="px-3 text-xs">
                {p.sample_state === 'ok' && <span className="text-emerald-700 dark:text-emerald-300">맞음</span>}
                {p.sample_state === 'wrong' && <span className="text-rose-600 dark:text-rose-400">정정</span>}
              </td>
              <td className="px-3 text-right whitespace-nowrap">
                {editable && p.sample_state === 'pending' && <>
                  <button className="text-xs font-semibold px-3 py-1 rounded-lg bg-emerald-600 text-white" onClick={() => act.mutate({ id: p.id, ok: true })}>맞음</button>
                  <span className="ml-2 inline-flex items-center gap-1">
                    <CollectionSelect cols={data.collections} value={fix[p.id] ?? null} onChange={(ns) => setFix({ ...fix, [p.id]: ns })} placeholder="맞는 곳" />
                    <button className="text-xs px-3 py-1 rounded-lg border border-slate-600 text-slate-300" title="맞는 곳을 고르지 않으면 보류"
                      onClick={() => act.mutate({ id: p.id, ok: false, ns: fix[p.id] ?? null })}>틀림</button>
                  </span>
                </>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OutSection({ data, out, editable, onChange }: { data: ImportBatchDetail; out: ImportPage[]; editable: boolean; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const act = useMutation({
    mutationFn: (ids: number[]) => updateImportPages(data.batch.id, ids, 'include'), onSuccess: onChange, onError: (e) => showError(e),
  });
  return (
    <section className="space-y-2">
      <button onClick={() => setOpen(!open)} className="text-sm font-semibold text-slate-100">제외 · 보류 · {out.length} {open ? '▾' : '▸'}</button>
      {open && (
        <div className="overflow-x-auto rounded-lg border border-slate-700">
          <table className="w-full text-sm">
            <tbody>
              {out.map((p) => (
                <tr key={p.id} className="border-t border-slate-700/60">
                  <td className="px-3 py-1.5"><PageLink rootUrl={data.batch.root_url} p={p} /></td>
                  <td className="px-3 text-xs text-slate-400">{p.bundle}</td>
                  <td className="px-3 text-xs text-slate-400">{p.status === 'excluded' ? '제외' : '보류'}</td>
                  <td className="px-3 text-right">{editable && p.prev_status && <button className="text-xs text-indigo-600 dark:text-indigo-300" onClick={() => act.mutate([p.id])}>포함으로 변경</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function GateTab({ data, onChange }: { data: ImportBatchDetail; onChange: () => void }) {
  const b = data.batch;
  const loaded = data.pages.filter((p) => p.status === 'loaded').length;
  const failed = data.pages.filter((p) => p.status === 'failed').length;
  const rollback = useMutation({
    mutationFn: () => rollbackImport(b.id), onSuccess: onChange, onError: (e) => showError(e),
  });
  if (b.status === 'preview') return <p className="text-sm text-slate-400">분류를 확정해 적재하면 여기서 게이트 결과를 보고 롤백할 수 있어요.</p>;
  return (
    <div className="space-y-4">
      {b.status === 'loading' && (
        <div className="space-y-1 text-sm text-slate-300">
          <p>적재 중 {b.load_done} / {b.load_total}페이지</p>
          <div className="h-2 rounded-full bg-slate-700 overflow-hidden"><div className="h-full bg-indigo-500" style={{ width: `${b.load_total ? (100 * b.load_done) / b.load_total : 0}%` }} /></div>
        </div>
      )}
      {b.error && <p className="text-sm text-rose-600 dark:text-rose-400">{b.error}</p>}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat k="이번 적재 배치" v={loaded} sub="페이지" />
        <Stat k="실패" v={failed} hot={failed > 0} />
        <Stat k="상태" v={STATUS_TEXT[b.status]} />
      </div>
      <div className="rounded-lg border border-indigo-200 bg-indigo-50 dark:border-indigo-500/40 dark:bg-indigo-500/10 p-3 text-sm text-slate-200">
        적재 뒤 평가 게이트(정책서 · 업무지식 두 축)를 돌려 나빠짐 0을 확인해 주세요 — 품질 › 평가 게이트. 하나라도 나빠지면 이번 배치만 롤백.
        <div className="mt-1 text-xs text-slate-400">지금은 적재하면 바로 채팅에 쓰여요(로컬 — 실사용자 없음). 운영 반입 전에 "게이트 통과 뒤 게시"로 바꿀 예정.</div>
      </div>
      {b.status === 'loaded' && (
        <div className="flex justify-end">
          <button disabled={rollback.isPending}
            onClick={async () => { if (await showConfirm(`이번 배치(${loaded}페이지)가 만든 조각을 지우고 원문을 교체됨으로 돌려요. 배치 뒤에 고친 조각도 같이 지워져요.`, { tone: 'danger' })) rollback.mutate(); }}
            className="text-sm font-semibold px-4 py-2 rounded-lg bg-rose-600 text-white hover:bg-rose-500 disabled:opacity-40">이번 배치 롤백</button>
        </div>
      )}
      {rollback.data && <p className="text-xs text-slate-400">롤백: 조각 {rollback.data.chunks} · 원문 {rollback.data.documents} · 자동 용어 {rollback.data.terms}</p>}
    </div>
  );
}
