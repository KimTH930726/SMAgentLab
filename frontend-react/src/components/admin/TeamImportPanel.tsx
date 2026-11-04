import { Fragment, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getTeams } from '../../api/auth';
import {
  confirmImport, confluencePageUrl, connectImport, getImportBatch, listConnections, syncConnection, getImportCollections, listImportBatches, reviewImportSample, rollbackImport, publishCheckImport, publishImport, sampleKey,
  setImportDefault, startImport, updateImportPages,
  type ImportBatchDetail, type ImportCollection, type ImportPage, type SourceConnection,
} from '../../api/teamImport';
import { useSearchParams } from 'react-router-dom';
import { useAuthStore } from '../../store/useAuthStore';
import { showAlert, showConfirm, showError } from '../../store/useDialogStore';

/**
 * 컨플 공간·하위 전체 넣기(팀 적재, 10/29 목업 Qnt3dJ5agwBhczty75h6Nc v8 · confluence-curation-layer-design §12) — 운영 지식 › 등록 › URL/Confluence 안.
 * 등록 창구는 하나(10/20 결정) — 따로 메뉴를 두지 않는다. 시작(SpaceImportStart)은 URL 폼 안, 배치(ImportBatches)는 등록 탭 아래.
 * 수집 대상 → 분류 미리보기(확정 전엔 적재 안 됨) → **묶음 연결**(10/30: 묶음마다 컬렉션 하나 + 하위 예외, [연결 · 적재] 한 번) → 게이트 · 롤백.
 * 페이지 단위 검토·표본은 [자세히] 아래에만 남음(묶음 연결은 표본을 쓰지 않는다 — 품질은 적재 뒤 평가 게이트로).
 */
const useImportBatchList = () => useQuery({
  queryKey: ['team-import'], queryFn: listImportBatches,
  refetchInterval: (q) => ((q.state.data ?? []).some((b) => b.status === 'previewing' || b.status === 'loading') ? 5000 : false),
});

/** [적재 검토] 탭 숫자 — 미리보기 중인 배치의 사람이 할 일 = 연결할 묶음 수(10/30) */
export function useImportTodo() {
  return useImportTodoState().n;
}

/** 같은 숫자 + 읽는 중·실패(홈 카드가 0으로 오해되지 않게, 11/03 리뷰) */
export function useImportTodoState(): { n: number; isLoading: boolean; isError: boolean } {
  const q = useImportBatchList();
  return { n: (q.data ?? []).reduce((a, b) => a + (b.todo ?? 0), 0), isLoading: q.isLoading, isError: q.isError };
}

/** 지식 › 소스 연결(10/30) — 팀 단위(보는 컬렉션과 무관). 일반 사용자는 서버가 자기 팀 배치만 준다, 관리자는 팀을 고른다. 주소 ?batch=id로 바로 연다 */
export function DataSources() {
  const [params, setParams] = useSearchParams();
  const openId = params.get('batch') ? Number(params.get('batch')) : null;
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-100">소스 연결</h2>
        <p className="text-xs text-slate-400" title="컨플 공간을 묶음(상위 페이지) 단위로 컬렉션에 연결 — 연결 규칙은 다음 동기화가 새 페이지에도 씀. 새 공간 연결은 운영 지식 › 지식 등록 › URL/Confluence › 공간·하위 전체 넣기">
          팀 단위 · 보는 컬렉션과 무관 · 새 공간은 [지식 등록 › 공간·하위 전체 넣기]로 시작</p>
      </div>
      <Connections />
      <ImportBatches openId={openId} onOpen={(id) => setParams({ batch: String(id) })} />
    </div>
  );
}

const fmtTime = (s: string | null) => (s ? new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—');
const CONN_STATUS: Record<SourceConnection['status'], { label: string; cls: string }> = {
  ok: { label: '정상', cls: 'text-emerald-700 dark:text-emerald-300' },
  running: { label: '동기화 중', cls: 'text-indigo-600 dark:text-indigo-300' },
  error: { label: '오류', cls: 'text-amber-700 dark:text-amber-300' },
  stopped: { label: '중단', cls: 'text-rose-600 dark:text-rose-400' },
};

/** 연결된 공간 — 묶음 연결을 한 공간마다 한 줄. 하루 1회 바뀐 페이지만 같은 규칙으로 다시 넣고, 주 1회 목록 전체를 대조해 사라진·제한된 페이지를 내린다.
 * 담당(토큰 주인)이 볼 수 있는 범위만 보인다 — 토큰이 만료되면 "중단"으로 보이고, 토큰이 있는 사람이 [지금 동기화]를 누르면 그 사람이 담당이 된다 */
function Connections() {
  const qc = useQueryClient();
  const [kicked, setKicked] = useState(0);
  const { data: rows = [] } = useQuery({
    queryKey: ['source-connections'], queryFn: listConnections,
    // 누른 직후엔 백그라운드가 아직 '동기화 중'으로 안 바꿨을 수 있어 10초는 계속 본다
    refetchInterval: (q) => ((q.state.data ?? []).some((c) => c.status === 'running') || Date.now() - kicked < 10000 ? 3000 : false),
  });
  const go = useMutation({
    mutationFn: ({ id, mode }: { id: number; mode: 'incremental' | 'full' }) => syncConnection(id, mode),
    onSuccess: () => { setKicked(Date.now()); qc.invalidateQueries({ queryKey: ['source-connections'] }); },
    onError: (e) => showError(e),
  });
  if (rows.length === 0) return null;
  const summary = (r: SourceConnection['last_result']) => {
    if (!r) return '아직 안 돎';
    if (r.mode === 'full') {
      if (r.incomplete) return '전체 대조 — 목록을 끝까지 못 받아 판단 안 함';
      if (r.too_many_missing) return `전체 대조 — 사라짐 ${r.missing}건이 너무 많아 내리지 않음(담당자 권한 확인)`;
      return `전체 대조 — 사라짐 ${r.missing} · 내림 ${r.held}`;
    }
    const parts = [`바뀜 ${r.changed}`, r.updated && `수정 ${r.updated}`, r.added && `새로 ${r.added}`, r.held && `내림 ${r.held}`,
      r.no_rule && `규칙 없는 새 묶음 ${r.no_rule}`, r.denied && `권한 없음 ${r.denied}`, r.elsewhere && `다른 경로 ${r.elsewhere}`,
      r.failed && `실패 ${r.failed}`].filter(Boolean);
    return parts.join(' · ');
  };
  return (
    <section className="rounded-xl border border-slate-700 bg-slate-800/60 overflow-x-auto" data-testid="source-connections">
      <div className="px-4 py-3 border-b border-slate-700">
        <p className="text-sm font-semibold text-slate-100">연결된 공간 <span className="font-normal text-slate-400">{rows.length}</span></p>
        <p className="text-xs text-slate-400" title="증분 = 마지막 동기화 뒤 수정된 페이지만 · 전체 대조 = 목록 전체를 받아 사라진 페이지 확인(끝까지 받았을 때만). 사람이 고친 조각은 덮지 않고 '확인 필요'">
          하루 1회 바뀐 페이지 반영 · 주 1회 전체 대조 · 담당자 권한으로 읽음</p>
      </div>
      <table className="w-full text-sm min-w-[720px]">
        <thead className="text-xs text-slate-400"><tr>
          <th className="text-left px-4 py-2">공간</th><th className="text-left px-3">담당</th><th className="text-left px-3">마지막 동기화</th>
          <th className="text-left px-3">결과</th><th className="text-left px-3">상태</th><th /></tr></thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id} className="border-t border-slate-700/60 align-top" data-testid="connection-row">
              <td className="px-4 py-2 text-slate-100" title={c.root_url}>{c.title ?? c.root_url}<span className="block text-[11px] text-slate-500">{c.team_name}</span></td>
              <td className="px-3 py-2 text-xs text-slate-300">{c.owner ?? '—'}</td>
              <td className="px-3 py-2 text-xs text-slate-300">{c.last_run_at ? <>증분 {fmtTime(c.last_incremental_at)}<span className="block text-slate-500">전체 {fmtTime(c.last_full_at)}</span></> : '—'}</td>
              <td className="px-3 py-2 text-xs text-slate-300">{summary(c.last_result)}
                {c.retry_pages.some((p) => p.why === 'conflict') && (
                  <span className="block text-amber-700 dark:text-amber-300" data-testid="connection-conflicts"
                    title="우리 쪽에서 사람이 고친 조각이 있어 덮지 않은 페이지 — 운영 지식에서 해당 페이지를 확인해 주세요. 다음 동기화 때 다시 시도">
                    확인 필요 {c.retry_pages.filter((p) => p.why === 'conflict').length}: {c.retry_pages.filter((p) => p.why === 'conflict').slice(0, 3).map((p) => p.title ?? p.page_id).join(', ')}
                  </span>
                )}
              </td>
              <td className="px-3 py-2 text-xs"><span className={CONN_STATUS[c.status].cls}>{CONN_STATUS[c.status].label}</span>
                {c.error && <span className="block text-slate-400 max-w-[16rem]">{c.error}</span>}</td>
              <td className="px-3 py-2 text-right whitespace-nowrap">
                <button disabled={go.isPending || c.status === 'running'} onClick={() => go.mutate({ id: c.id, mode: 'incremental' })}
                  className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline disabled:opacity-40">지금 동기화</button>
                <button disabled={go.isPending || c.status === 'running'} onClick={() => go.mutate({ id: c.id, mode: 'full' })}
                  className="ml-3 text-xs text-slate-400 hover:text-slate-200 disabled:opacity-40" title="목록 전체를 받아 사라진·제한된 페이지 확인(오래 걸림)">전체 대조</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function ImportBatches({ openId, onOpen }: { openId: number | null; onOpen: (id: number) => void }) {
  const { data: all = [] } = useImportBatchList();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const teams = useMemo(() => [...new Map(all.map((b) => [b.team_id, b.team_name ?? `팀 #${b.team_id}`])).entries()], [all]);
  const [team, setTeam] = useState<number | 'all'>('all');
  const batches = team === 'all' ? all : all.filter((b) => b.team_id === team);
  const current = openId ?? batches[0]?.id ?? null;
  if (batches.length === 0) {
    return <p className="text-sm text-slate-400 py-6">적재 배치가 없어요 — [지식 등록] › URL/Confluence › <b>공간·하위 전체 넣기</b>로 시작하면 여기서 넣을 곳을 확인해요.</p>;
  }
  const name = (b: (typeof batches)[number]) => `#${b.id} ${b.team_name ?? ''} · ${b.root_title ?? b.root_url} · ${STATUS_TEXT[b.status]}${b.todo ? ` · 할 일 ${b.todo}` : ''}`;
  const cur = batches.find((b) => b.id === current);
  return (
    <div className="space-y-4" data-testid="team-import">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {isAdmin && teams.length > 1 && (
          <select value={team} onChange={(e) => setTeam(e.target.value === 'all' ? 'all' : Number(e.target.value))} aria-label="팀"
            className="text-sm bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-slate-200">
            <option value="all">팀: 전체</option>
            {teams.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        )}
        <span className="text-xs text-slate-500" title="공간·하위 전체 넣기로 만든 적재 배치 — 연결 · 적재 · 게이트 · 롤백 단위">배치</span>
        <select value={current ?? ''} onChange={(e) => onOpen(Number(e.target.value))} aria-label="배치" title={cur?.root_url}
          className="text-sm bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-slate-200 max-w-full">
          {batches.map((b) => <option key={b.id} value={b.id}>{name(b)}</option>)}
        </select>
      </div>
      {current != null && <BatchView key={current} id={current} />}
    </div>
  );
}

const STATUS_TEXT: Record<string, string> = {
  previewing: '분류 중', preview: '분류 미리보기', failed: '실패', loading: '적재 중', loaded: '적재됨', rolled_back: '롤백됨', cancelled: '취소됨',
  // 적재됨인데 게시 전 = 검수 대기(채팅에 아직 안 나옴) — 상태 칸은 statusText(b)로
};

/** URL 폼 안 "공간·하위 전체 넣기" — 팀(관리자만) · 이 공간 기본 위치 · [분류 미리보기 만들기]. */
export function SpaceImportStart({ url, onStarted, onCancel }: { url: string; onStarted: (id: number) => void; onCancel: () => void }) {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';
  const { data: teams = [] } = useQuery({ queryKey: ['teams'], queryFn: getTeams, enabled: isAdmin, staleTime: 300_000 });
  const [teamId, setTeamId] = useState<number | null>(null);
  const [defaultNs, setDefaultNs] = useState<number | null>(null);
  const { data: cols = [] } = useQuery({
    queryKey: ['team-import-collections', teamId], queryFn: () => getImportCollections(teamId),
    enabled: !isAdmin || teamId != null, staleTime: 60_000,
  });
  const qc = useQueryClient();
  const start = useMutation({
    mutationFn: () => startImport(url.trim(), isAdmin ? teamId : null, defaultNs),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['team-import'] }); onStarted(r.id); },
    onError: (e) => showError(e),
  });
  const biz = cols.filter((c) => c.kind === 'domain' && c.writable && !c.unowned);
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-dashed border-indigo-300 dark:border-indigo-500/50 p-3 space-y-2">
        <div className="text-xs font-semibold text-slate-200">이 공간 기본 위치</div>
        <div className="flex flex-wrap gap-2 items-center">
          {isAdmin && (
            <select value={teamId ?? ''} onChange={(e) => { setTeamId(e.target.value ? Number(e.target.value) : null); setDefaultNs(null); }}
              className="text-sm bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-slate-200" aria-label="팀">
              <option value="">팀 고르기</option>
              {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          )}
          <select value={defaultNs ?? ''} onChange={(e) => setDefaultNs(e.target.value ? Number(e.target.value) : null)}
            className="text-sm bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-slate-200" aria-label="기본 위치">
            <option value="">— 비워 둠: AI가 페이지마다 분류 —</option>
            {biz.map((c) => <option key={c.id} value={c.id}>{c.label}{c.parts.length ? ` · ${c.parts.join(', ')}` : ''}</option>)}
          </select>
        </div>
        <p className="text-[11px] text-slate-500" title="업무가 여럿 섞인 공간(예: 온라인서비스 = 딜리버스 + 제휴서비스)은 비워 둔다">
          공간 전체가 한 시스템이면 고른다 — 페이지는 여기로, AI는 예외(확신하는 다른 시스템)만 묻는다
        </p>
      </div>
      <div className="flex justify-end gap-2">
        <button onClick={onCancel} className="text-sm px-3 py-2 rounded-lg text-slate-400 hover:text-slate-200">취소</button>
        <button disabled={!url.trim() || (isAdmin && !teamId) || start.isPending} onClick={() => start.mutate()}
          className="text-sm font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40"
          title="페이지를 받아 AI가 분류만 한다 — 확정 전엔 아무것도 적재되지 않음">
          분류 미리보기 만들기
        </button>
      </div>
    </div>
  );
}

function DefaultControl({ data, onChange }: { data: ImportBatchDetail; onChange: () => void }) {
  const b = data.batch;
  const names = useNames(data.collections);
  const [ns, setNs] = useState<number | null>(b.default_namespace_id);
  const biz = data.collections.filter((c) => c.kind === 'domain' && c.writable && !c.unowned);
  const apply = useMutation({
    mutationFn: (v: number | null) => setImportDefault(b.id, v),
    onSuccess: (r) => {
      onChange();
      const n = (k: string) => r[k] ?? 0;
      void showAlert(`기본 위치로 ${n('space_default')} · AI 확신 ${n('ai')} · 검토 필요 ${Object.entries(r).filter(([k]) => k !== 'space_default' && k !== 'ai').reduce((a, [, v]) => a + v, 0)}`,
        { title: '다시 분류했어요' });
    },
    onError: (e) => showError(e),
  });
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-indigo-300 dark:border-indigo-500/50 px-3 py-2"
      title="사람이 아직 손대지 않은 페이지를 저장된 AI 판정으로 다시 분류(LLM 다시 안 부름). 직접 분류·표본 판정한 페이지는 그대로">
      <span className="text-xs font-semibold text-slate-200">이 공간 기본 위치</span>
      <select value={ns ?? ''} onChange={(e) => setNs(e.target.value ? Number(e.target.value) : null)}
        className="text-sm bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-slate-200" aria-label="기본 위치">
        <option value="">— 비워 둠: AI가 페이지마다 분류 —</option>
        {biz.map((c) => <option key={c.id} value={c.id}>{c.label}{c.parts.length ? ` · ${c.parts.join(', ')}` : ''}</option>)}
      </select>
      <button disabled={ns === b.default_namespace_id || apply.isPending}
        onClick={async () => {
          if (await showConfirm('사람이 아직 손대지 않은 페이지를 이 기준으로 다시 분류해요. 아직 안 본 표본은 다시 뽑아요(직접 분류·판정한 것은 그대로).')) apply.mutate(ns);
        }}
        className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-indigo-600 text-white disabled:opacity-40">기본 위치로 분류</button>
      {b.default_namespace_id != null && <span className="text-xs text-slate-400">지금: {names.get(b.default_namespace_id)?.label ?? `#${b.default_namespace_id}(목록에 없음)`}</span>}
    </div>
  );
}


function BatchView({ id }: { id: number }) {
  const qc = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ['team-import', id], queryFn: () => getImportBatch(id),
    refetchInterval: (q) => (['previewing', 'loading'].includes(q.state.data?.batch.status ?? '') ? 3000 : false),
  });
  // 다시 불러오기 끝을 돌려준다 — 확정 뒤 새 값이 오기 전까지 버튼을 잠가 두려고(리뷰 10/30: 그 틈에 기본값으로 보였다가 다시 눌리면 덮어씀)
  const refresh = () => qc.invalidateQueries({ queryKey: ['team-import'] });   // ['team-import', id]도 앞부분 일치로 포함
  if (error) return <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했어요: {String((error as Error).message)}</p>;
  if (!data) return <p className="text-sm text-slate-400">불러오는 중…</p>;
  const b = data.batch;
  if (b.status === 'previewing' || b.status === 'failed' || b.status === 'cancelled') {
    return (
      <div className="rounded-xl border border-slate-700 bg-slate-800 p-4 space-y-2 text-sm">
        <div className="text-slate-100 font-semibold">#{b.id} {b.root_title ?? b.root_url}</div>
        {b.status === 'failed'
          ? <p className="text-rose-600 dark:text-rose-400">실패: {b.error}</p>
          : b.status === 'cancelled'
          ? <p className="text-slate-400" data-testid="batch-cancelled">취소됨 — 아무것도 적재되지 않음{b.error ? ` · ${b.error}` : ''}</p>
          : <>
              <p className="text-slate-300">분류 중 {b.done_pages} / {b.total_pages}페이지 — 아직 아무것도 적재되지 않음</p>
              <div className="h-2 rounded-full bg-slate-700 overflow-hidden"><div className="h-full bg-indigo-500" style={{ width: `${b.total_pages ? (100 * b.done_pages) / b.total_pages : 0}%` }} /></div>
            </>}
        {b.truncated && <p className="text-xs text-amber-700 dark:text-amber-300">페이지가 많아 앞 {b.total_pages}개만 받았어요 — 하위 페이지 주소로 나눠 넣어 주세요.</p>}
      </div>
    );
  }
  return (b.status === 'loaded' || b.status === 'rolled_back')
    ? <GateTab data={data} onChange={refresh} />
    : <Preview data={data} onChange={refresh} />;
}

function useNames(cols: ImportCollection[]) {
  return useMemo(() => new Map(cols.map((c) => [c.id, c])), [cols]);
}

/** 정책 컬렉션 / 시스템 컬렉션(11/02) — 둘 다 kind='domain', area로 가른다 */
const isSystemCol = (c: ImportCollection | undefined) => !!c && c.kind === 'domain' && c.area === 'system';
const isBizCol = (c: ImportCollection | undefined) => !!c && c.kind === 'domain' && c.area !== 'system';

function CollectionSelect({ cols, value, onChange, placeholder = '분류할 곳', disabled, emptyText, noEmpty, aiId }: {
  cols: ImportCollection[]; value: number | null; onChange: (id: number | null) => void; placeholder?: string; disabled?: boolean;
  emptyText?: string;   // 빈 값 보기 글자(기본 "— 분류할 곳 —") — 묶음 위치에서 줄마다 다를 때 "줄마다 다름"
  noEmpty?: boolean;    // 빈 값은 보이기만, 고를 수 없음 — 검토 줄에서 빈 값 고르기는 저장 동작이 없어 화면과 서버가 어긋났다(리뷰 10/30)
  aiId?: number | null; // AI가 처음 고른 곳 — 목록에서 표시(10/30 사용자: 펼쳤을 때 AI 선택이 보이게)
}) {
  const ai = (id: number) => (aiId === id ? '  ◀ AI 판단' : '');
  const biz = cols.filter(isBizCol);
  const sys = cols.filter(isSystemCol);
  return (
    <select value={value ?? ''} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)} disabled={disabled}
      className="text-sm bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-slate-200 disabled:opacity-50" aria-label={placeholder}>
      <option value="" disabled={noEmpty}>— {emptyText ?? placeholder} —</option>
      {biz.length > 0 && (   // 운영 지식은 시스템에만(11/02) — 서버가 정책 컬렉션을 주지 않으면(지금) 빈 묶음을 안 그린다
        <optgroup label="정책 컬렉션">
          {biz.map((c) => <option key={c.id} value={c.id} disabled={!c.writable}>{c.label}{c.unowned ? ' (담당 없음)' : c.parts.length ? ` · ${c.parts.join(', ')}` : ''}{c.writable ? '' : ' — 담당 아님'}{ai(c.id)}</option>)}
        </optgroup>
      )}
      <optgroup label="시스템">
        {sys.map((c) => <option key={c.id} value={c.id} disabled={!c.writable}>{c.label}{c.writable ? '' : ' — 담당 아님'}{ai(c.id)}</option>)}
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
  const byDefault = auto.filter((p) => p.assigned_by === 'space_default').length;
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
  const [more, setMore] = useState(false);
  return (
    <div className="space-y-6">
      {editable && b.default_namespace_id == null && <DefaultControl key="none" data={data} onChange={onChange} />}
      {editable && <BundleConnect data={data} onChange={onChange} />}
      {b.status === 'loading' && <p className="text-sm text-slate-300">적재 중 {b.load_done ?? 0} / {b.load_total ?? 0}</p>}
      <button className="text-xs text-slate-400 hover:text-slate-200" onClick={() => setMore(!more)} aria-expanded={more} data-testid="preview-more">
        {more ? '▾' : '▸'} 자세히 — 전체 목록 · 묶음 · 자동 분류 {auto.length} · 제외·보류 {out.length}</button>
      {more && <div className="space-y-6">
      {editable && b.default_namespace_id != null && <DefaultControl key={b.default_namespace_id} data={data} onChange={onChange} />}
      <div className="text-xs text-slate-400" title="예외: 읽기 제한으로 바뀐 페이지가 이미 적재돼 있으면 그 조각만 승인 대기로 내림">#{b.id} {b.root_title} · 분류 미리보기 — 확정 전까지 적재되지 않음</div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat k={b.default_namespace_id != null ? '기본 위치 · AI 확신' : '자동 분류'} v={auto.length}
          sub={b.default_namespace_id != null ? `기본 위치 ${byDefault} · AI 확신 ${auto.length - byDefault}` : `업무 ${auto.filter((p) => isBizCol(names.get(p.final_namespace_id ?? -1))).length} · 시스템 ${auto.filter((p) => isSystemCol(names.get(p.final_namespace_id ?? -1))).length}`} />
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
            onClick={async () => { if (await showConfirm('분류를 확정하고 적재할까요? 적재한 내용은 검수 대기로 남고, [게시]해야 채팅에 나와요.')) confirm.mutate(); }}
            className="text-sm font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40">
            분류 확정 · 적재
          </button>
        </div>
      )}
      </div>}
    </div>
  );
}

const SEP = ' › ';
const EXCLUDE = -1;   // 선택 상자 값 = 제외
const OUT_OF_SCOPE = '대상 아님';   // 위치 판정 종류 — IT 운영 지식이 아님(locator.DOC_TYPES)
const GROUP_TAU = 0.4;   // 갈래 예외 기준 — AI가 묶음 값과 다르게 본 비율(10/30 실측 split_probe)
const GROUP_MIN = 3;

/** AI 판단 분포 막대 + 숫자 — 이 줄 컬렉션과 같음 / 다른 정책 컬렉션 / 다른 시스템 / 미판정 */
function AiBar({ v }: { v: { same: number; otherBiz: number; otherSys: number; none: number; grounded: number } }) {
  const total = Math.max(1, v.same + v.otherBiz + v.otherSys + v.none);
  const seg = [
    { n: v.same, cls: 'bg-emerald-500 dark:bg-emerald-400', t: '제안과 같음' },
    { n: v.otherBiz, cls: 'bg-amber-500 dark:bg-amber-400', t: '다른 정책 컬렉션' },
    { n: v.otherSys, cls: 'bg-sky-500 dark:bg-sky-400', t: '다른 시스템' },
    { n: v.none, cls: 'bg-slate-500', t: 'AI 미판정' },
  ];
  const judged = v.same + v.otherBiz + v.otherSys;
  return (
    <span className="inline-flex flex-col gap-0.5 min-w-[12rem]">
      <span className="flex h-1.5 rounded-full overflow-hidden bg-slate-700" aria-hidden>
        {seg.map((x) => x.n > 0 && <span key={x.t} className={x.cls} style={{ width: `${(x.n / total) * 100}%` }} />)}
      </span>
      <span className="text-[11px] text-slate-400">{seg.filter((x) => x.n > 0).map((x) => `${x.t} ${x.n}`).join(' · ')}
        {judged > 0 && v.grounded === 0 && (
          <span className="ml-1 text-amber-700 dark:text-amber-300" title="이 컬렉션들에 아직 지식이 없어 비슷한 지식과 비교하지 못함 — AI가 내용만 보고 고른 값이라 참고만(첫 적재)">· 비교 근거 없음</span>)}
      </span>
    </span>
  );
}

function ConnectSelect({ cols, value, onPick, aria, disabled }: {
  cols: ImportCollection[]; value: number | null; onPick: (v: number) => void; aria: string; disabled: boolean;
}) {
  return (
    <select value={value ?? ''} onChange={(e) => onPick(Number(e.target.value))} aria-label={aria} disabled={disabled}
      className="text-sm bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-slate-200 disabled:opacity-50">
      <option value="" disabled>— 컬렉션 —</option>
      {cols.filter((c) => c.kind === 'domain').map((c) => <option key={c.id} value={c.id} disabled={!c.writable}>{c.label}{c.writable ? '' : ' — 담당 아님'}</option>)}
      <option value={EXCLUDE}>제외(적재 안 함)</option>
    </select>
  );
}

/** 묶음 안 하위 갈래(2단계 상위 페이지) — 서버 team_import.group_of와 같은 규칙 */
function groupOf(p: ImportPage): string | null {
  if (p.depth <= 1) return null;
  const parts = p.path ? p.path.split(SEP) : [];
  return parts.length >= 2 ? parts[1] : (p.title ?? p.page_id);
}

/** 가장 많은 값과 그 비율 — 정해진 값(공간 기본 위치·사람 결정) 우선, 없으면 AI 판단. 쓸 수 있는 컬렉션만 센다
 * (담당 아닌 곳이 다수면 제안을 비워 둔다 — 미리 채우면 버튼은 켜지는데 서버가 연결 전체를 403으로 막았다, 리뷰 M3) */
function majority(ps: ImportPage[], ok: (id: number) => boolean): { ns: number | null; share: number } {
  const c = new Map<number, number>();
  for (const p of ps) { const v = p.final_namespace_id ?? p.ai_namespace_id; if (v != null && ok(v)) c.set(v, (c.get(v) ?? 0) + 1); }
  let best: number | null = null, n = 0;
  for (const [k, v] of c) if (v > n) { best = k; n = v; }
  return { ns: best, share: ps.length ? n / ps.length : 0 };
}

/** 묶음 단위 연결(10/30 사용자 "업계 수집기처럼 · 사람은 가장 단순한 화면에서 단순한 검수만") — 묶음(1단계 상위 페이지)마다 컬렉션 하나.
 * 미리 채운 값 = 묶음 안 정해진 값(공간 기본 위치 + 사람 결정)의 다수, 없으면 AI 다수. 성격이 다른 하위 갈래(2단계, 다수가 묶음과 다르고 60% 이상)는
 * 그 줄 아래 예외로. [연결 · 적재] 한 번 = 규칙 저장 + 전 페이지 정함 + 적재. 품질은 적재 뒤 평가 게이트·롤백(분류 검수 없음). */
function BundleConnect({ data, onChange }: { data: ImportBatchDetail; onChange: () => unknown }) {
  const { batch: b, pages } = data;
  const names = useNames(data.collections);
  const label = (id: number | null) => (id == null || id === EXCLUDE ? '제외' : names.get(id)?.label ?? `#${id}`);
  const writable = (id: number) => !!names.get(id)?.writable;
  // AI 판단 분포(묶음 값과 같음 / 다른 업무 / 다른 시스템 / 미판정) — 기본값과 섞지 않고 AI가 내용만 보고 고른 곳
  // 이견 비율은 이웃 비교가 있던 판단만으로 센다 — 첫 적재처럼 비교할 지식이 없으면 LLM 혼자 고른 값이라 예외 후보로 띄우지 않음(11/02 #85: 420건 전부 비교 없음)
  const aiView = (ps: ImportPage[], base: number | null) => {
    const judged = ps.filter((p) => p.ai_namespace_id != null);
    const other = judged.filter((p) => p.ai_namespace_id !== base);
    const grounded = judged.filter((p) => Object.keys(p.signals?.neighbors ?? {}).length > 0);
    const top = majority(other.map((p) => ({ ...p, final_namespace_id: null })), () => true).ns;
    return {
      same: judged.length - other.length,
      otherBiz: other.filter((p) => isBizCol(names.get(p.ai_namespace_id!))).length,
      otherSys: other.filter((p) => isSystemCol(names.get(p.ai_namespace_id!))).length,
      none: ps.length - judged.length,
      grounded: grounded.length,
      disagree: grounded.length ? grounded.filter((p) => p.ai_namespace_id !== base).length / grounded.length : 0,
      aiTop: top,
    };
  };
  const rows = useMemo(() => {
    const live = pages.filter((p) => p.status === 'auto' || p.status === 'review');
    const by = new Map<string, ImportPage[]>();
    // 묶음 키 = 서버와 같은 값(상위 없음 = '') — 화면 이름만 "(최상위)"(서버 규칙 키가 어긋나면 "정하지 않은 묶음"으로 거절됐다)
    for (const p of live) by.set(p.bundle ?? '', [...(by.get(p.bundle ?? '') ?? []), p]);
    return [...by.entries()].sort((x, y) => y[1].length - x[1].length).map(([bundle, ps]) => {
      const m = majority(ps, writable);
      const gs = new Map<string, ImportPage[]>();
      for (const p of ps) { const g = groupOf(p); if (g) gs.set(g, [...(gs.get(g) ?? []), p]); }
      const groups = [...gs.entries()].map(([name, gp]) => ({ name, n: gp.length, pages: gp, ...aiView(gp, m.ns) }))
        .filter((g) => g.grounded >= GROUP_MIN && g.disagree > GROUP_TAU)
        .sort((x, y) => y.n - x.n);
      // 정해진 값(공간 기본 위치·사람 결정)이 하나도 없으면 제안 = AI 다수 — 화면에 '정해진 값'으로 보이지 않게(11/02 #85: 정해진 값 0/420)
      const fixed = ps.some((p) => p.final_namespace_id != null && p.assigned_by !== 'ai');   // 정해진 값 = 공간 기본값·사람 결정(AI 확신 자동은 아님, 리뷰 2차 L3)
      // AI가 "IT 운영 지식 아님"(행정·친목·개인)으로 본 페이지가 절반 이상이면 제외를 제안(11/02 — OpsLens는 운영 지식만, 확정은 사람)
      const outOfScope = ps.filter((p) => p.signals?.doc_type === OUT_OF_SCOPE).length;
      const exclude = !fixed && outOfScope * 2 >= ps.length;
      return { bundle, n: ps.length, suggest: exclude ? EXCLUDE : m.ns, share: m.share, fixed, outOfScope, exclude, pages: ps, ...aiView(ps, m.ns), groups };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pages, names]);
  const [pick, setPick] = useState<Record<string, number>>({});
  const [gpick, setGpick] = useState<Record<string, number>>({});
  const nsOf = (r: (typeof rows)[number]) => pick[r.bundle] ?? r.suggest ?? null;
  const gKey = (bundle: string, name: string) => JSON.stringify([bundle, name]);
  // 갈래는 손대지 않으면 묶음 값을 따라간다(AI 값으로 미리 채우지 않음)
  const gOf = (r: (typeof rows)[number], g: (typeof rows)[number]['groups'][number]) => gpick[gKey(r.bundle, g.name)] ?? nsOf(r);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [groupsOpen, setGroupsOpen] = useState<Record<string, boolean>>({});
  const blank = rows.filter((r) => nsOf(r) == null).length;
  const total = rows.reduce((a, r) => a + r.n, 0);
  const go = useMutation({
    mutationFn: async () => {   // 연결(규칙 저장 + 전 페이지 정함) → 적재(확정과 같은 검사) — 버튼은 한 번
      await connectImport(b.id, rows.map((r) => {
        const ns = nsOf(r);
        return { bundle: r.bundle, namespace_id: ns === EXCLUDE ? null : ns,
          // 묶음 값과 다르게 정한 갈래만 규칙으로(같으면 묶음 규칙이 덮음)
          groups: r.groups.filter((g) => gOf(r, g) !== ns).map((g) => { const v = gOf(r, g); return { name: g.name, namespace_id: v === EXCLUDE ? null : v }; }) };
      }));
      await confirmImport(b.id);
    },
    onSuccess: () => onChange(),
    onError: (e) => showError(e),
  });
  const Select = ({ value, onPick, aria }: { value: number | null; onPick: (v: number) => void; aria: string }) => (
    <ConnectSelect cols={data.collections} value={value} onPick={onPick} aria={aria} disabled={go.isPending} />
  );
  return (
    <section className="rounded-xl border border-indigo-300 dark:border-indigo-500/40 bg-slate-800/60 overflow-clip" data-testid="bundle-connect">
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-3 px-4 py-3 bg-slate-800 border-b border-slate-700">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-slate-100">묶음 연결 · {rows.length}개 묶음 <span className="font-normal text-slate-400">/ 페이지 {total}</span></p>
          <p className="text-xs text-slate-400">묶음마다 컬렉션을 확인 — 아래 페이지는 모두 따라감 · 적재 뒤 평가 게이트로 확인, 나빠지면 롤백</p>
        </div>
        <button disabled={blank > 0 || go.isPending} data-testid="connect-submit"
          onClick={async () => { if (await showConfirm(`${rows.length}개 묶음 규칙을 저장하고 페이지 ${total}건을 적재합니다. 평가가 나빠지면 이번 배치만 롤백할 수 있습니다.`)) go.mutate(); }}
          className="text-sm font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40">
          {go.isPending ? '연결·적재 중…' : blank > 0 ? `컬렉션 미지정 ${blank}` : '연결 · 적재'}</button>
      </div>
      <table className="w-full text-sm">
        <thead className="text-xs text-slate-400">
          <tr><th className="text-left px-4 py-2">묶음(상위 페이지)</th><th className="text-right px-2">페이지</th>
            <th className="text-left px-3" title="묶음 안 정해진 값(공간 기본 위치·사람 결정) 중 가장 많은 곳 — 없으면 AI 판단">제안 · 근거</th><th className="text-left px-3">컬렉션</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <Fragment key={r.bundle}>
              <tr className="border-t border-slate-700/60 align-top" data-testid="bundle-row">
                <td className="px-4 py-2 text-slate-100">{r.bundle || '(최상위)'}
                  <button className="ml-2 text-[11px] text-slate-400 hover:text-slate-200" onClick={() => setOpenKey(openKey === r.bundle ? null : r.bundle)}
                    aria-expanded={openKey === r.bundle} data-testid="bundle-detail-toggle">{openKey === r.bundle ? '내용 ▾' : '내용 ▸'}</button>
                </td>
                <td className="px-2 py-2 text-right tabular-nums text-slate-300">{r.n}</td>
                <td className="px-3 py-2 text-xs text-slate-400 space-y-1">
                  <span className="block">{r.exclude
                    ? <b className="text-amber-700 dark:text-amber-300" title="AI가 IT 운영 지식이 아니라고 본 페이지(행정·친목·개인 기록) — 넣으려면 컬렉션을 고르기">제외 제안 — 운영 지식 아님 {r.outOfScope}/{r.n}</b>
                    : r.suggest != null ? <><b className="text-slate-200">{label(r.suggest)}</b> — {r.fixed ? '정해진 값' : 'AI 다수'} {Math.round(r.share * 100)}%</> : '판단 근거 없음'}</span>
                  <span className="block" title="AI가 내용만 보고 고른 곳(기본 위치와 섞지 않음). AI의 '다른 곳' 판단은 사람이 본 39건 중 31%만 맞았음(10/30)"><AiBar v={r} /></span>
                </td>
                <td className="px-3 py-2"><Select value={nsOf(r)} onPick={(v) => setPick({ ...pick, [r.bundle]: v })} aria={`${r.bundle || '(최상위)'} 컬렉션`} /></td>
              </tr>
              {openKey === r.bundle && (
                <tr data-testid="bundle-detail"><td colSpan={4} className="px-8 pb-3 text-xs text-slate-400">
                  <p className="text-slate-500 mb-1">대표 페이지</p>
                  <ul className="space-y-0.5">{r.pages.slice(0, 5).map((p) => <li key={p.id}><PageLink rootUrl={b.root_url} p={p} />{p.path && <span className="ml-2 text-[11px] text-slate-500">{p.path}</span>}</li>)}</ul>
                </td></tr>
              )}
              {r.groups.length > 0 && (
                <tr><td colSpan={4} className="px-4 pb-2">
                  <button className="text-xs text-amber-700 dark:text-amber-300 hover:underline" data-testid="bundle-groups-toggle"
                    aria-expanded={!!groupsOpen[r.bundle]} onClick={() => setGroupsOpen({ ...groupsOpen, [r.bundle]: !groupsOpen[r.bundle] })}
                    title="이 묶음 안에서 AI가 묶음 값과 다르게 본 폴더(이웃 비교가 있는 판단 40% 초과) — 따로 넣을 곳을 정할 수 있음">
                    ⚠ AI 이견 폴더 {r.groups.length}{(() => { const n = r.groups.filter((g) => gOf(r, g) !== nsOf(r)).length; return n ? ` · 예외 지정 ${n}` : ''; })()} {groupsOpen[r.bundle] ? '▾' : '▸'}</button>
                  {groupsOpen[r.bundle] && (
                    <div className="mt-2 rounded-lg border border-amber-300 dark:border-amber-500/40 bg-slate-900/40" data-testid="bundle-groups">
                      <p className="px-3 py-1.5 text-xs text-slate-400 border-b border-slate-700/60">예외 지정(선택) — 기본은 묶음 값, 다르게 넣을 폴더만 바꾸기</p>
                      <table className="w-full text-sm"><tbody>
                        {r.groups.map((g) => {
                          const gk = gKey(r.bundle, g.name);
                          return (
                            <Fragment key={g.name}>
                              <tr className="align-top border-t border-slate-700/40 first:border-t-0" data-testid="bundle-group">
                                <td className="px-3 py-2 text-xs text-slate-200">{g.name}
                                  <button className="ml-2 text-[11px] text-slate-400 hover:text-slate-200" onClick={() => setOpenKey(openKey === gk ? null : gk)} aria-expanded={openKey === gk}>{openKey === gk ? '내용 ▾' : '내용 ▸'}</button>
                                </td>
                                <td className="px-2 py-2 text-right tabular-nums text-xs text-slate-400">{g.n}</td>
                                <td className="px-3 py-2 text-xs text-slate-400 space-y-1">
                                  <span className="block">AI 다수: <b className="text-slate-300">{label(g.aiTop)}</b> — 비교 근거 {g.grounded}건 중 다르게 봄 {Math.round(g.disagree * 100)}%</span>
                                  <span className="block"><AiBar v={g} /></span>
                                </td>
                                <td className="px-3 py-2"><Select value={gOf(r, g)} onPick={(v) => setGpick({ ...gpick, [gk]: v })} aria={`${g.name} 컬렉션`} /></td>
                              </tr>
                              {openKey === gk && (
                                <tr><td colSpan={4} className="px-6 pb-3 text-xs text-slate-400">
                                  <ul className="space-y-0.5">{g.pages.slice(0, 5).map((p) => <li key={p.id}><PageLink rootUrl={b.root_url} p={p} /></li>)}</ul>
                                </td></tr>
                              )}
                            </Fragment>
                          );
                        })}
                      </tbody></table>
                    </div>
                  )}
                </td></tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </section>
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

function ReviewSection({ data, review, editable, onChange }: { data: ImportBatchDetail; review: ImportPage[]; editable: boolean; onChange: () => unknown }) {
  // 대분류(수집 대상 바로 아래 묶음)로 묶는다 — 같은 상위 아래 페이지는 대개 같은 곳(사람이 경로를 보고 묶음마다 한 번). 10/29 실측 GIFT 검토 필요 39:
  // 경로 전체로 묶으면 25묶음(대부분 1건), 대분류는 8묶음. 이유(조직으로 보임 등)는 줄마다 보인다
  const groups = useMemo(() => {
    const m = new Map<string, ImportPage[]>();
    for (const p of review) m.set(p.bundle ?? '(최상위)', [...(m.get(p.bundle ?? '(최상위)') ?? []), p]);
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [review]);
  if (review.length === 0) return null;
  const left = review.filter((p) => p.final_namespace_id == null).length;
  return (
    <section className="space-y-2">
      <div className="text-sm font-semibold text-slate-100">검토 필요 · {review.length}
        <span className="ml-2 text-xs font-normal text-slate-400" title="자동 분류 기준(LLM과 비슷한 지식 5개 중 4개 일치)을 못 넘었거나, 기본 위치와 AI 판단이 다른 것 — 미리 채운 값도 [확정]을 눌러야 저장">
          미분류 {left} · 대분류별 — 값을 확인하고 [확정]</span></div>
      {groups.map(([key, ps]) => (
        <ReviewGroup key={key} name={key} data={data} pages={ps} editable={editable} onChange={onChange} />
      ))}
    </section>
  );
}

/** 줄마다 보이는 값 — 바꾼 값(줄 또는 묶음 위치로 일괄) > 저장된 분류 > 기본 위치. 표시 꼬리표는 그 출처 */
type RowSource = 'saved' | 'own' | 'group';

function ReviewGroup({ name, data, pages, editable, onChange }: {
  name: string; data: ImportBatchDetail; pages: ImportPage[]; editable: boolean; onChange: () => unknown;
}) {
  const b = data.batch;
  const names = useNames(data.collections);
  // 묶음 값 = 공간 기본 위치(사람이 정한 것)로 미리 채움 — 10/29 GIFT 사람 분류 22건 중 15건이 기본 위치(조직으로 보임 19건 중 12, 기본 위치와 다름 3건 모두).
  // AI 판단은 미리 채우지 않는다(이웃 쏠림 · 조직 판단 37%). 미리 채운 값도 [확정]을 눌러야 저장 — 넣을 곳은 사람이 정한다
  const preset = b.default_namespace_id != null && names.get(b.default_namespace_id)?.writable ? b.default_namespace_id : null;
  const [own, setOwn] = useState<Record<number, number | null>>({});
  const [open, setOpen] = useState(false);   // 처음엔 접어 둠 — 묶음 이름·건수·묶음 위치만 훑고 필요한 묶음만 연다(10/30 사용자)
  // 기본 위치를 바꾸면 손대지 않은 줄만 따라간다 — 바꾼 값(own)은 그대로(리뷰 10/29)
  const view = (p: ImportPage): { ns: number | null; src: RowSource } =>
    p.id in own ? { ns: own[p.id], src: 'own' } : p.final_namespace_id != null ? { ns: p.final_namespace_id, src: 'saved' } : { ns: preset, src: 'group' };
  // 묶음 위치 = 일괄 지정. 고르면 이 묶음 모든 줄이 그 값으로(저장된 줄 포함) — 줄마다 다르면 "줄마다 다름"으로 비어 보인다(10/30 사용자: 따로 놀아 불편)
  const vals = new Set(pages.map((p) => view(p).ns));
  const groupValue = vals.size === 1 ? [...vals][0] : null;
  const setAll = (ns: number | null) => setOwn(Object.fromEntries(pages.map((p) => [p.id, ns])));
  // 처음 AI 판단으로 일괄 되돌리기(10/30 사용자) — 미리 채우진 않는다(조직 판단 37%), 사람이 눌러야.
  // AI가 판단 못 했거나 담당 아닌 곳을 고른 줄은 바꾼 값만 지운다(저장된 값·기본 위치로) — 빈 값으로 두면 저장 동작 없이 비어 보였다
  const aiOf = (p: ImportPage) => (p.ai_namespace_id != null && names.get(p.ai_namespace_id)?.writable ? p.ai_namespace_id : null);
  const toAi = () => setOwn(Object.fromEntries(pages.flatMap((p) => { const a = aiOf(p); return a != null ? [[p.id, a]] : []; })));
  const isAi = pages.every((p) => { const a = aiOf(p); return a == null ? !(p.id in own) : view(p).ns === a; });
  const pending = pages.filter((p) => { const v = view(p); return v.ns != null && v.ns !== p.final_namespace_id; });
  const left = pages.filter((p) => p.final_namespace_id == null);
  // 다시 불러오기까지 mutationFn 안에서 기다린다 — 호출마다 준 onSuccess는 v5가 기다리지 않아 그 틈에 버튼이 다시 켜졌다(재리뷰 10/30 L1)
  const act = useMutation({
    mutationFn: async ({ action, ids, ns }: { action: 'classify' | 'hold' | 'exclude'; ids: number[]; ns?: number }) => {
      await updateImportPages(b.id, ids, action, ns);
      await onChange();
    },
    // 보류·제외한 줄의 바꾼 값도 지운다 — 남아 있으면 [포함으로 변경]으로 돌아왔을 때 고른 적 없는 값이 "바꿈"으로 되살아났다(리뷰 10/30)
    onSuccess: (_r, v) => drop(v.ids),
    onError: (e) => showError(e),
  });
  const confirmAll = useMutation({
    mutationFn: async () => {   // 넣을 곳별로 한 번씩 — 서버는 한 요청에 한 곳
      const by = new Map<number, number[]>();
      for (const p of pending) { const ns = view(p).ns!; by.set(ns, [...(by.get(ns) ?? []), p.id]); }
      for (const [ns, ids] of by) await updateImportPages(b.id, ids, 'classify', ns);
      return pending.map((p) => p.id);
    },
    onSuccess: async (sent) => {   // 새 값이 올 때까지 기다린 뒤(그동안 버튼 잠김) 보낸 줄만 지운다 — 남은 값은 그대로
      await onChange();
      drop(sent);
    },
    onError: (e) => { showError(e); onChange(); },
  });
  const drop = (ids: number[]) => setOwn((o) => Object.fromEntries(Object.entries(o).filter(([id]) => !ids.includes(Number(id)))));
  const one = (id: number, action: 'hold' | 'exclude') => act.mutate({ action, ids: [id] });
  const busy = act.isPending || confirmAll.isPending;   // 확정은 곳별로 차례 요청 — 그 사이 줄을 보류하면 뒤 요청이 다시 분류할 수 있다(재리뷰)
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/60" data-testid="review-group">
      <div className="flex flex-wrap items-center gap-3 px-3 py-2">
        <button onClick={() => setOpen(!open)} aria-expanded={open} data-testid="group-toggle" className="text-sm font-semibold text-slate-100" title="대분류 = 수집 대상 바로 아래 페이지">{open ? '▾' : '▸'} {name}</button>
        <span className="text-xs text-slate-400">{pages.length}건 · 미분류 {left.length}</span>
        {editable && (
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-400" title="고르면 이 묶음 모든 줄이 이 값으로 바뀐다(확정 전엔 저장 안 됨) — 줄마다 다르면 '줄마다 다름'">묶음 위치</span>
            <span data-testid="group-ns"><CollectionSelect cols={data.collections} value={groupValue} onChange={setAll} disabled={busy} noEmpty
              emptyText={vals.size > 1 ? '줄마다 다름' : undefined} /></span>
            <button disabled={busy || isAi} onClick={toAi} data-testid="group-to-ai"
              className="text-xs px-3 py-1.5 rounded-lg border border-slate-600 text-slate-300 disabled:opacity-40"
              title="모든 줄을 AI가 처음 고른 곳으로 되돌림(판단 못 함·담당 아닌 곳은 빈 값) — [확정] 전엔 저장 안 됨">AI 판단으로</button>
            <button disabled={pending.length === 0 || busy} onClick={() => confirmAll.mutate()} data-testid="group-confirm"
              className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-indigo-600 text-white disabled:opacity-40"
              title="보이는 값으로 저장 — 저장 전엔 검토 필요로 남는다">이 묶음 {pending.length}건 확정</button>
            {left.length > 0 && <button disabled={busy} onClick={() => act.mutate({ action: 'hold', ids: left.map((p) => p.id) })}
              className="text-xs px-3 py-1.5 rounded-lg border border-slate-600 text-slate-300" title="넣을 곳을 지금 못 정함 — 이번 적재에서 빼고, 다음에 다시 분류 대상">미분류 보류</button>}
          </div>
        )}
      </div>
      {open && (
        <div className="overflow-x-auto border-t border-slate-700">
          <table className="w-full text-sm">
            <thead className="text-xs text-slate-400">
              <tr>
                <th className="text-left px-3 py-1.5">페이지 · 경로</th>
                <th className="text-left px-3" title="AI(로컬 LLM)가 글을 읽고 고른 곳과 그 이유 한 문장 — 참고만, 넣을 곳은 사람이 정한다">AI 판단 · 이유</th>
                <th className="text-left px-3" title="이 글과 가장 비슷한 기존 지식 5개가 있는 컬렉션별 수(이웃)">비슷한 지식</th>
                <th className="text-left px-3">넣을 곳</th><th />
              </tr>
            </thead>
            <tbody>
              {pages.map((p) => {
                const v = view(p);
                const tail = p.path && p.bundle && p.path.startsWith(p.bundle) ? p.path.slice(p.bundle.length).replace(/^ › /, '') : p.path;
                return (
                  <tr key={p.id} className="border-t border-slate-700/60 align-top" data-testid="review-row">
                    <td className="px-3 py-1.5 min-w-[14rem]">
                      <PageLink rootUrl={b.root_url} p={p} />
                      {tail && <div className="text-[11px] text-slate-500" title={p.path ?? ''}>{tail}</div>}
                    </td>
                    <td className="px-3 py-2 text-xs w-[36rem] max-w-[36rem] min-w-[24rem]">
                      {/* 위 → 아래: 검토 이유 꼬리표 / AI가 고른 곳 / 그 이유(자르지 않음 — 잘린 "…"는 못 읽는다, 사용자 10/30) */}
                      {p.reason && <span className="inline-block mb-1 px-1.5 py-0.5 rounded text-[11px] bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300">{data.reasons[p.reason]}</span>}
                      <div className="text-sm text-slate-200 font-medium">{p.ai_namespace_id != null ? names.get(p.ai_namespace_id)?.label ?? `#${p.ai_namespace_id}` : '판단 못 함'}</div>
                      {p.signals.reason && <div className="mt-0.5 text-slate-400 leading-relaxed break-words">{p.signals.reason}</div>}
                    </td>
                    <td className="px-3 py-1.5 text-xs text-slate-400 whitespace-nowrap">
                      {Object.entries(p.signals.neighbors ?? {}).map(([ns, n]) => `${names.get(Number(ns))?.label ?? ns} ${n}`).join(' · ') || '없음'}
                    </td>
                    <td className="px-3 py-1.5 whitespace-nowrap">
                      {editable
                        ? <CollectionSelect cols={data.collections} value={v.ns} onChange={(ns) => setOwn({ ...own, [p.id]: ns })} disabled={busy} noEmpty aiId={p.ai_namespace_id} />
                        : <span className="text-slate-300">{names.get(p.final_namespace_id ?? -1)?.label ?? '—'}</span>}
                      {editable && <RowTag src={v.src} saved={p.final_namespace_id != null} dirty={v.ns !== p.final_namespace_id && v.ns != null} />}
                    </td>
                    <td className="px-3 py-1.5 whitespace-nowrap text-right">
                      {editable && <>
                        <button disabled={busy} className="text-xs text-slate-400 hover:text-slate-100 disabled:opacity-40" title="넣을 곳을 지금 못 정함 — 이번 적재에서 빼고 다음에 다시" onClick={() => one(p.id, 'hold')}>보류</button>
                        <button disabled={busy} className="ml-3 text-xs text-slate-400 hover:text-slate-100 disabled:opacity-40" title="지식으로 넣을 문서가 아님 — [포함으로 변경]으로 되돌릴 수 있음" onClick={() => one(p.id, 'exclude')}>제외</button>
                      </>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RowTag({ src, saved, dirty }: { src: RowSource; saved: boolean; dirty: boolean }) {
  // 이름에 "저장됐나"가 드러나게(10/30 사용자 "바꿈 이건 뭐야") — [확정]을 눌러야 저장
  if (saved && !dirty) return <span className="ml-2 text-[11px] text-emerald-700 dark:text-emerald-300">저장됨</span>;
  if (src === 'own' && dirty) return <span className="ml-2 text-[11px] text-indigo-600 dark:text-indigo-300" title="바꾼 값 — [확정]을 눌러야 저장">확정 전</span>;
  if (src === 'group' && dirty) return <span className="ml-2 text-[11px] text-slate-400" title="공간 기본 위치로 미리 채운 값 — [확정]을 눌러야 저장">기본값 · 확정 전</span>;
  return null;
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
  // 표본 묶음(누가 정했나 × 정해진 곳)별 — 서버 sample_key와 같다. 공간 기본값과 AI 확신은 같은 컬렉션이어도 따로 검사
  const byNs = useMemo(() => {
    const m = new Map<string, ImportPage[]>();
    for (const p of auto) { const k = sampleKey(p) ?? 'none'; m.set(k, [...(m.get(k) ?? []), p]); }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [auto]);
  const label = (k: string) => {
    const [by, id] = k.split(':');
    return `${names.get(Number(id))?.label ?? id} · ${by === 'space_default' ? '기본 위치' : 'AI 확신'}`;
  };
  const [open, setOpen] = useState<{ ns: string; mode: 'sample' | 'all' } | null>(null);
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
                  <td className="px-3 py-2 text-slate-200">{label(ns)}</td>
                  <td className="px-3 text-right tabular-nums text-slate-300">{ps.length}</td>
                  <td className="px-3 text-xs">
                    <span className={done >= (data.sample_targets[ns] ?? 0) ? 'text-emerald-700 dark:text-emerald-300' : 'text-slate-400'}>{done} / {data.sample_targets[ns] ?? s.length}</span>
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
              <span>{label(open.ns)} · {open.mode === 'sample' ? '표본 검토' : '전체'} {ps.length}</span>
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
  const held = b.status === 'loaded' && !b.published_at ? data.held : null;   // 검수 대기(게시 전)
  const check = b.publish_check ?? null;
  const rollback = useMutation({
    mutationFn: () => rollbackImport(b.id), onSuccess: onChange, onError: (e) => showError(e),
  });
  const runCheck = useMutation({ mutationFn: () => publishCheckImport(b.id), onSuccess: onChange, onError: (e) => showError(e) });
  const publish = useMutation({
    mutationFn: () => publishImport(b.id),
    onSuccess: (r) => {
      onChange();
      void showAlert(r.failed.length ? `게시 ${r.published}건 · 실패 ${r.failed.length}건 — 실패한 작업은 검수 대기로 남았어요(사유는 위 오류 줄).`
        : `게시했어요 — 이제 채팅에 나와요(같은 페이지 옛 버전은 내렸어요).`, { tone: r.failed.length ? 'warning' : 'success' });
    },
    onError: (e) => showError(e),
  });
  const warnCount = held ? Object.values(held.warnings).reduce((a, n) => a + n, 0) : 0;
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
        <Stat k="상태" v={held ? '검수 대기' : b.status === 'loaded' && b.published_at ? '게시됨' : STATUS_TEXT[b.status]} hot={!!held} />
      </div>
      {held && (
        <div className="space-y-3" data-testid="publish-review">
          <p className="text-sm text-slate-300" title="게시하면 같은 페이지 옛 버전을 내리고 새 조각을 채팅에 내놓아요. 그 전까지는 옛 버전이 그대로 답에 쓰여요">
            검수 대기 — 아직 채팅에 안 나와요. 확인하고 [게시]하면 반영돼요.
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat k="게시할 조각" v={held.chunks} sub={`작업 ${held.jobs}`} />
            <Stat k="중복 의심" v={held.dup} hot={held.dup > 0} sub="게시하면 승인 대기로" />
            <Stat k="품질 경고" v={warnCount} hot={warnCount > 0} sub={Object.entries(held.warnings).map(([k, n]) => `${k} ${n}`).join(' · ') || '없음'} />
            <Stat k="검색 확인" v={check?.asked ? `${check.found}/${check.asked}` : check?.skipped ? '건너뜀' : '—'}
              hot={!!check?.asked && (check.found ?? 0) < (check.asked ?? 0)}
              sub={check?.skipped ?? (check?.asked ? '찾은 질문 / 만든 질문' : '아직 안 함')} />
          </div>
          {check?.misses && check.misses.length > 0 && (
            <details className="text-xs text-slate-400">
              <summary className="cursor-pointer">못 찾은 질문 {check.misses.length}개</summary>
              <ul className="mt-1 space-y-0.5 list-disc pl-5">{check.misses.map((m, i) => <li key={i}>{m.question}{m.title ? ` — ${m.title}` : ''}</li>)}</ul>
            </details>
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <button disabled={runCheck.isPending || publish.isPending} onClick={() => runCheck.mutate()}
              title="이 배치 조각 10개로 로컬 LLM이 질문을 만들고, 그 질문으로 이 페이지가 검색 상위 3에 드는지 봐요(벡터만 — 실제 채팅보다 보수적)"
              className="text-sm px-4 py-2 rounded-lg border border-slate-600 text-slate-200 hover:bg-slate-800 disabled:opacity-40">
              {runCheck.isPending ? '검색 확인 중…' : check ? '검색 확인 다시' : '검색 확인'}
            </button>
            <button disabled={publish.isPending || runCheck.isPending}
              onClick={async () => { if (await showConfirm(`조각 ${held.chunks}개를 채팅에 내놓아요. 같은 페이지 옛 버전은 내려가요.`, { title: '게시', confirmLabel: '게시' })) publish.mutate(); }}
              className="text-sm font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40">게시</button>
          </div>
        </div>
      )}
      {!held && b.status === 'loaded' && (
        <p className="text-xs text-slate-500" title="문제가 보이면 이번 배치만 롤백 — 평가 게이트(정책서 · 업무지식 두 축) 결과로 판단">
          게시됨 — 채팅에 쓰이는 중이에요. 문제가 보이면 이번 배치만 롤백하세요.
        </p>
      )}
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
