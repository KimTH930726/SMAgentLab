import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getTeams } from '../../api/auth';
import {
  confirmImport, confluencePageUrl, connectImport, getImportBatch, listConnections, syncConnection, getImportCollections, listImportBatches, reviewImportSample, rollbackImport, publishCheckImport, publishImport, sampleKey,
  setImportDefault, startImport, updateImportPages, listSyncChanges, getSyncChange, getOverlapDetail, type SyncChange,
  type HeldSummary, type OverlapDetail, type PublishCheck, type ImportBatchDetail, type ImportCollection, type ImportPage, type SourceConnection,
} from '../../api/teamImport';
import { useSearchParams } from 'react-router-dom';
import { useAuthStore } from '../../store/useAuthStore';
import { ReviewDiff } from './ReviewDiff';
import { Modal } from '../ui/Modal';
import { ProgressBar } from '../ui/ProgressBar';
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
  const [openChanges, setOpenChanges] = useState<number | null>(null);   // 바뀐 페이지 펼친 연결(11/05)
  const { data: rows = [] } = useQuery({
    queryKey: ['source-connections'], queryFn: listConnections,
    // 누른 직후엔 백그라운드가 아직 '동기화 중'으로 안 바꿨을 수 있어 10초는 계속 본다
    refetchInterval: (q) => ((q.state.data ?? []).some((c) => c.status === 'running') || Date.now() - kicked < 10000 ? 3000 : false),
  });
  const go = useMutation({
    mutationFn: ({ id, mode }: { id: number; mode: 'incremental' | 'full' }) => syncConnection(id, mode),
    onSuccess: () => { setKicked(Date.now()); qc.invalidateQueries({ queryKey: ['source-connections'] }); qc.invalidateQueries({ queryKey: ['sync-changes'] }); },
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
      r.archived && `보관이라 건너뜀 ${r.archived}`, r.failed && `실패 ${r.failed}`].filter(Boolean);
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
                <button onClick={() => setOpenChanges(openChanges === c.id ? null : c.id)} aria-expanded={openChanges === c.id} data-testid={`connection-changes-${c.id}`}
                  className="ml-3 text-xs text-slate-300 hover:text-slate-100" title="최근 30일 동기화로 바뀐 페이지 — 원문 링크·차이 보기">
                  바뀐 페이지 {openChanges === c.id ? '▾' : '▸'}</button>
              </td>
            </tr>
          )).flatMap((row, i) => openChanges === rows[i].id
            ? [row, <tr key={`ch-${rows[i].id}`} className="border-t border-slate-700/40"><td colSpan={6} className="px-4 py-3"><SyncChanges c={rows[i]} /></td></tr>]
            : [row])}
        </tbody>
      </table>
    </section>
  );
}

/**
 * 동기화로 바뀐 페이지(11/05 태훈 "몇백 장이면 다 보기 힘들 것") — 바뀐 줄 수 큰 순, [채팅에 쓰인 것만], 충돌은 맨 위, 처음엔 20개.
 * [원문 ↗] = 컨플 페이지, [차이 보기] = 옛 판 ↔ 새 판(승인 대기 비교와 같은 화면). 업계: Confluence 페이지 기록 '변경 사항 비교'.
 */
const CHANGES_SHOW = 20;
function SyncChanges({ c }: { c: SourceConnection }) {
  const q = useQuery({ queryKey: ['sync-changes', c.id], queryFn: () => listSyncChanges(c.id), staleTime: 60_000 });
  const [usedOnly, setUsedOnly] = useState(false);
  const [all, setAll] = useState(false);
  const [view, setView] = useState<SyncChange | null>(null);
  if (q.isLoading) return <p className="text-xs text-slate-500">불러오는 중…</p>;
  if (q.error) return <p className="text-xs text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p>;
  const d = q.data!;
  const items = usedOnly ? d.items.filter((x) => x.used > 0) : d.items;
  const shown = all ? items : items.slice(0, CHANGES_SHOW);
  const pageUrl = (pid: string) => confluencePageUrl(c.root_url, pid);
  return (
    <div className="space-y-2" data-testid={`sync-changes-${c.id}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <b className="text-slate-100">최근 {d.days}일 바뀐 페이지 {d.items.length}</b>
        {d.capped && <span className="text-slate-500" title="한 번에 최근 반영 300개까지만 비교해요">(최근 300개까지)</span>}
        {d.conflicts.length > 0 && <span className="rounded-md bg-amber-100 px-2 py-0.5 font-semibold text-amber-800 dark:bg-amber-500/15 dark:text-amber-300"
          title="우리 쪽에서 사람이 고친 조각이 있어 반영하지 않은 페이지 — 운영 지식 › 검토 필요에 '원문 바뀜'으로 떠요">반영 안 함 {d.conflicts.length}</span>}
        <label className="ml-auto flex items-center gap-1.5 text-slate-300" title={`최근 ${d.usage_days}일 채팅 답의 근거로 쓰인 페이지만 — 실제 답에 영향이 가는 변경`}>
          <input type="checkbox" checked={usedOnly} onChange={(e) => setUsedOnly(e.target.checked)} className="accent-indigo-500" data-testid="sync-changes-used" />
          채팅에 쓰인 것만 ({d.items.filter((x) => x.used > 0).length})</label>
      </div>
      {d.conflicts.length > 0 && (
        <ul className="space-y-0.5 text-xs">
          {d.conflicts.slice(0, 5).map((p) => (
            <li key={p.page_id} className="flex gap-2"><span className="text-amber-700 dark:text-amber-300">반영 안 함</span>
              <span className="min-w-0 truncate text-slate-200">{p.title ?? p.page_id}</span>
              {pageUrl(p.page_id) && <a href={pageUrl(p.page_id)!} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline dark:text-indigo-400">원문 ↗</a>}</li>
          ))}
        </ul>
      )}
      {items.length === 0 ? <p className="text-xs text-slate-500">{usedOnly ? '채팅에 쓰인 페이지 중 바뀐 것이 없어요.' : '이 기간에 바뀐 페이지가 없어요.'}</p> : (
        <ul className="divide-y divide-slate-700/60 rounded-lg border border-slate-700">
          {shown.map((x) => (
            <li key={x.new_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 text-xs" data-testid={`sync-change-${x.new_id}`}>
              <span className="w-24 shrink-0 tabular-nums font-semibold" title="바뀐 줄 수(더함 / 뺌)">
                <span className="text-emerald-700 dark:text-emerald-300">+{x.added}</span> <span className="text-rose-700 dark:text-rose-300">−{x.removed}</span></span>
              <span className="min-w-0 flex-1 truncate text-slate-100" title={x.title ?? x.page_id}>{x.title ?? x.page_id}</span>
              {x.used > 0 && <span className="text-slate-400" title={`최근 ${d.usage_days}일 채팅 답 근거`}>채팅 {x.used}회</span>}
              <span className="text-slate-500">{x.activated_at ? fmtTime(x.activated_at) : ''}</span>
              {pageUrl(x.page_id) && <a href={pageUrl(x.page_id)!} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline dark:text-indigo-400">원문 ↗</a>}
              <button type="button" onClick={() => setView(x)} className="text-slate-300 hover:text-slate-100" data-testid={`sync-change-diff-${x.new_id}`}>차이 보기</button>
            </li>
          ))}
        </ul>
      )}
      {items.length > CHANGES_SHOW && (
        <button type="button" onClick={() => setAll((v) => !v)} className="text-xs text-slate-400 hover:text-slate-200">
          {all ? '접기' : `${items.length - CHANGES_SHOW}개 더`}</button>
      )}
      {view && <SyncChangeModal connId={c.id} item={view} onClose={() => setView(null)} />}
    </div>
  );
}

function SyncChangeModal({ connId, item, onClose }: { connId: number; item: SyncChange; onClose: () => void }) {
  const q = useQuery({ queryKey: ['sync-change', connId, item.new_id], queryFn: () => getSyncChange(connId, item.new_id), staleTime: 300_000 });
  return (
    <Modal isOpen onClose={onClose} title={item.title ?? item.page_id} maxWidth="max-w-6xl">
      {q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p>
        : <ReviewDiff before={q.data?.before ?? null} after={q.data?.after ?? ''} loading={q.isLoading}
            leftLabel="옛 원문" rightLabel="새 원문" leftShort="옛 원문" rightShort="새 원문" emptyText="옛 원문이 없어요." />}
    </Modal>
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
  const [withRestricted, setWithRestricted] = useState(false);   // 읽기 제한 페이지 포함(관리자만, 11/05)
  const { data: cols = [] } = useQuery({
    queryKey: ['team-import-collections', teamId], queryFn: () => getImportCollections(teamId),
    enabled: !isAdmin || teamId != null, staleTime: 60_000,
  });
  const qc = useQueryClient();
  const start = useMutation({
    mutationFn: () => startImport(url.trim(), isAdmin ? teamId : null, defaultNs, isAdmin && withRestricted),
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
      {isAdmin && (
        <label className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"
          title="컨플에서 읽기 제한(특정 사람·그룹만)이 걸린 페이지와 그 하위도 넣어요. OpsLens 로그인 사용자 누구나 검색하게 되고, 누가 켰는지 남아요">
          <input type="checkbox" checked={withRestricted} data-testid="import-include-restricted" className="mt-0.5 accent-amber-600"
            onChange={async (e) => {
              const on = e.target.checked;
              if (on && !(await showConfirm('컨플에서 읽기 제한이 걸린 페이지도 넣어요.\nOpsLens 사용자 누구나 검색하게 되고, 켠 사람이 기록에 남아요. 계속할까요?'))) return;
              setWithRestricted(on);
            }} />
          <span><b>읽기 제한 페이지도 넣기</b> (관리자) — 제한 페이지·그 하위까지</span>
        </label>
      )}
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
    // 뒤에서 도는 일이 있으면 다시 읽는다 — 미리보기·적재 3초, 게시·검색 확인 2초(11/05 진행 막대)
    refetchInterval: (q) => {
      const b = q.state.data?.batch;
      if (b?.publishing_at || b?.publish_check?.running) return 2000;
      return ['previewing', 'loading'].includes(b?.status ?? '') ? 3000 : false;
    },
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

function Stat({ k, v, sub, hot, onOpen }: { k: string; v: number | string; sub?: string; hot?: boolean; onOpen?: () => void }) {
  const Tag = onOpen ? 'button' : 'div';
  return (
    <Tag type={onOpen ? 'button' : undefined} onClick={onOpen} title={onOpen ? '눌러서 자세히' : undefined}
      className={`rounded-xl bg-slate-800 border p-4 text-left ${onOpen ? 'hover:border-indigo-400 cursor-pointer' : ''} ${hot ? 'border-amber-400 dark:border-amber-500/60' : 'border-slate-700'}`}>
      <div className="text-xs text-slate-400">{k}</div>
      <div className={`text-2xl font-bold tabular-nums ${hot ? 'text-amber-600 dark:text-amber-300' : 'text-slate-100'}`}>{v}</div>
      {sub && <div className="text-xs text-slate-400">{sub}</div>}
      {onOpen && <div className="mt-1 text-[11px] text-indigo-600 dark:text-indigo-400">자세히 ›</div>}
    </Tag>
  );
}

/** 리포트 위 큰 숫자 칸 — 숫자 하나 + 이름(11/05 "가시적으로") */
function Tile({ k, v, sub, tone = 'plain' }: { k: string; v: number | string; sub?: string; tone?: 'bad' | 'warn' | 'good' | 'plain' }) {
  const color = tone === 'bad' ? 'text-rose-600 dark:text-rose-400' : tone === 'warn' ? 'text-amber-600 dark:text-amber-300' : tone === 'good' ? 'text-emerald-700 dark:text-emerald-300' : 'text-slate-100';
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800 p-3">
      <div className="text-xs text-slate-400">{k}</div>
      <div className={`text-2xl font-bold tabular-nums ${color}`}>{v}</div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

/** 가로 막대 하나에 구간 여러 개 — 범례에 수 */
function SplitBar({ parts }: { parts: { label: string; n: number; cls: string }[] }) {
  const total = parts.reduce((a, p) => a + p.n, 0) || 1;
  return (
    <div className="space-y-1.5">
      <div className="flex h-3 overflow-hidden rounded-full bg-slate-700">
        {parts.filter((p) => p.n > 0).map((p) => <div key={p.label} className={p.cls} style={{ width: `${(100 * p.n) / total}%` }} title={`${p.label} ${p.n}`} />)}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
        {parts.map((p) => <span key={p.label} className="inline-flex items-center gap-1.5"><span className={`h-2.5 w-2.5 rounded-sm ${p.cls}`} />{p.label} <b className="tabular-nums text-slate-200">{p.n}</b></span>)}
      </div>
    </div>
  );
}

function SrcLink({ rootUrl, pageId }: { rootUrl: string; pageId: string | null }) {
  const url = pageId ? confluencePageUrl(rootUrl, pageId) : null;
  return url ? <a href={url} target="_blank" rel="noreferrer" className="shrink-0 text-indigo-600 hover:underline dark:text-indigo-400">원문 ↗</a> : null;
}

/** 검색 확인 리포트(11/05) — 위: 찾음 수 + 순위 분포 / 아래: 못 찾은 질문 먼저(대신 나온 것) · 찾은 질문은 접힘 */
function SearchCheckReport({ check, rootUrl, onClose }: { check: PublishCheck; rootUrl: string; onClose: () => void }) {
  const items = check.items ?? [];
  const miss = items.filter((x) => !x.found);
  const hit = items.filter((x) => x.found).sort((a, b) => (a.rank ?? 9) - (b.rank ?? 9));
  const at = (r: number) => hit.filter((x) => x.rank === r).length;
  return (
    <Modal isOpen onClose={onClose} title="검색 확인 리포트" maxWidth="max-w-4xl">
      <div className="space-y-4 text-sm" data-testid="search-check-report">
        <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-4 rounded-xl border border-slate-700 bg-slate-800 p-4">
          <div className="text-center" title="조각에서 질문을 만들고, 그 질문으로 이 컬렉션(게시된 지식 + 이번 조각)을 벡터로만 찾아 그 페이지가 상위 3에 드는지 봐요 — 키워드·다시 정렬이 없어 실제 채팅보다 엄격해요">
            <div className={`text-4xl font-bold tabular-nums ${miss.length ? 'text-amber-600 dark:text-amber-300' : 'text-emerald-700 dark:text-emerald-300'}`}>{check.found}/{check.asked ?? items.length}</div>
            <div className="text-xs text-slate-400">찾음 ⓘ</div>
          </div>
          <SplitBar parts={[
            { label: '1위', n: at(1), cls: 'bg-emerald-600 dark:bg-emerald-500' },
            { label: '2위', n: at(2), cls: 'bg-emerald-400 dark:bg-emerald-700' },
            { label: '3위', n: at(3), cls: 'bg-lime-400 dark:bg-lime-700' },
            { label: '못 찾음', n: miss.length, cls: 'bg-rose-500 dark:bg-rose-400' },
          ]} />
        </div>
        {!!check.failed && <p className="text-xs text-amber-700 dark:text-amber-300">질문 만들기 실패 {check.failed}개 — 위 수에서 빠짐</p>}

        <section className="space-y-2">
          <h4 className="font-semibold text-slate-100">못 찾음 <span className="text-xs font-normal text-slate-400">{miss.length}</span></h4>
          {miss.length === 0 && <p className="text-xs text-emerald-700 dark:text-emerald-300">모든 질문에서 그 페이지가 상위 3에 들었어요 ✓</p>}
          {miss.map((x, i) => (
            <div key={i} className="space-y-2 rounded-lg border border-rose-300 bg-rose-50 p-3 text-xs dark:border-rose-500/40 dark:bg-rose-500/10">
              <div className="text-sm text-slate-100">{x.question}</div>
              <div className="flex items-center gap-2 text-slate-400"><span className="shrink-0">나와야 할 페이지</span>
                <span className="min-w-0 truncate font-medium text-slate-200">{x.title ?? '—'}</span><SrcLink rootUrl={rootUrl} pageId={x.page_id} /></div>
              {x.got ? (
                <div className="space-y-1">
                  <div className="text-slate-400">대신 나온 것</div>
                  <ol className="space-y-0.5">
                    {x.got.map((g, j) => (
                      <li key={j} className="flex items-center gap-2">
                        <span className="w-4 shrink-0 tabular-nums text-slate-500">{j + 1}</span>
                        <span className="min-w-0 flex-1 truncate text-slate-200">{g.title ?? '—'}</span>
                        <span className="shrink-0 tabular-nums text-slate-500" title="질문과 조각의 벡터 유사도">{g.sim.toFixed(2)}</span>
                        <SrcLink rootUrl={rootUrl} pageId={g.page_id} />
                      </li>
                    ))}
                  </ol>
                </div>
              ) : <div className="text-slate-500">[검색 확인]을 다시 누르면 대신 나온 것이 보여요(예전 결과엔 없음)</div>}
            </div>
          ))}
        </section>

        <details className="rounded-lg border border-slate-700">
          <summary className="cursor-pointer px-3 py-2 font-semibold text-slate-100">찾음 <span className="text-xs font-normal text-slate-400">{hit.length}</span></summary>
          <ul className="divide-y divide-slate-700/60 border-t border-slate-700">
            {hit.map((x, i) => (
              <li key={i} className="grid grid-cols-[3rem_minmax(0,1fr)] gap-2 px-3 py-2 text-xs">
                <span className="font-semibold tabular-nums text-emerald-700 dark:text-emerald-300">{x.rank}위</span>
                <span className="min-w-0 space-y-0.5">
                  <span className="block text-slate-100">{x.question}</span>
                  <span className="flex gap-2 text-slate-500"><span className="min-w-0 truncate">{x.title ?? '—'}</span><SrcLink rootUrl={rootUrl} pageId={x.page_id} /></span>
                </span>
              </li>
            ))}
          </ul>
        </details>
      </div>
    </Modal>
  );
}

/** 페이지 한 줄을 펴면 — 겹친 줄마다 한 번(같은 줄이 여러 쌍에 나오면 묶음) · 표 행/문장 · 원문 몇 번 vs 조각 몇 번 · 어느 조각 쌍 */
function OverlapPairs({ batchId, jobId, defectOnly }: { batchId: number; jobId: number; defectOnly: boolean }) {
  const q = useQuery({ queryKey: ['team-import-overlap', batchId, jobId], queryFn: () => getOverlapDetail(batchId, jobId) });
  if (q.isLoading) return <p className="px-3 py-2 text-xs text-slate-500">불러오는 중…</p>;
  if (q.isError) return <p className="px-3 py-2 text-xs text-rose-600 dark:text-rose-400">상세를 못 불러왔어요 — {(q.error as Error).message}</p>;
  const byLine = new Map<string, { ln: OverlapDetail['pairs'][number]['lines'][number]; pairs: string[] }>();
  for (const p of q.data?.pairs ?? []) {
    if (p.defect !== defectOnly) continue;
    for (const ln of p.lines) {
      const g = byLine.get(ln.h) ?? { ln, pairs: [] };
      g.pairs.push(`${p.a}↔${p.b}`);
      byLine.set(ln.h, g);
    }
  }
  const groups = [...byLine.values()].sort((a, b) => Number(b.ln.extra) - Number(a.ln.extra) || b.pairs.length - a.pairs.length);
  if (!groups.length) return <p className="px-3 py-2 text-xs text-slate-500">표시할 줄이 없어요(기록 뒤 조각이 바뀌었을 수 있어요)</p>;
  return (
    <div className="space-y-1.5 bg-slate-900/40 px-3 py-2">
      <div className="text-[11px] text-slate-500">겹친 줄 {groups.length}종 · 이 페이지 조각 {q.data?.chunks}개</div>
      {groups.map(({ ln, pairs }) => (
        <div key={ln.h} className="grid grid-cols-[3.2rem_minmax(0,1fr)_auto] items-start gap-2 text-[11px]">
          <span className="rounded bg-slate-700 px-1.5 py-0.5 text-center text-slate-300">{ln.t === 'table' ? '표 행' : '문장'}</span>
          <span className="min-w-0 space-y-0.5">
            <code className={`block truncate rounded px-1.5 py-0.5 ${ln.extra ? 'bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-200' : 'bg-slate-800 text-slate-300'}`}
              title={ln.text ?? ''}>{ln.text ?? '(조각이 바뀌어 줄을 못 찾음)'}</code>
            <span className="block truncate text-slate-500" title={pairs.join(', ')}>조각 쌍 {pairs.length}곳 · {pairs.slice(0, 6).join(', ')}{pairs.length > 6 ? ' …' : ''}</span>
          </span>
          <span className={`shrink-0 tabular-nums ${ln.extra ? 'font-semibold text-rose-600 dark:text-rose-400' : 'text-slate-500'}`}
            title="원문에 나온 수 / 이 페이지 조각 전체에 나온 수 — 조각 쪽이 많으면 나누면서 생긴 중복">
            {ln.source == null ? `조각 ${ln.chunks}번 · 원문 대조 못 함` : `원문 ${ln.source}번 · 조각 ${ln.chunks}번`}</span>
        </div>
      ))}
    </div>
  );
}

type OverlapItem = NonNullable<HeldSummary['overlap_items']>[number];

/** 품질 경고 리포트(11/05 태훈 "분석적으로 한눈에") — 위: 결함 의심 / 원문 반복 / 다른 경고 + 겹친 줄 유형 / 아래: 문제 먼저, 펴면 근거 */
function QualityReport({ held, batchId, rootUrl, onClose }: { held: HeldSummary; batchId: number; rootUrl: string; onClose: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const ov = held.overlap;
  const ovItems = held.overlap_items ?? [];
  // 이웃 겹침은 위 칸·목록이 따로 보여 준다 — 다른 경고(제목만·끝에 매달린 제목)만 아래 표로
  const others = (held.warning_items ?? []).filter((x) => !ov || !x.kind.startsWith('이웃 청크'));
  const otherKinds = [...new Set(others.map((x) => x.kind))];
  const otherPlaces = others.reduce((a, x) => a + x.n, 0);
  const defects = ovItems.filter((x) => x.defect > 0);
  const repeats = ovItems.filter((x) => x.source_repeat > 0).sort((a, b) => b.source_repeat - a.source_repeat);
  const row = (x: OverlapItem, defect: boolean) => {
    const key = `${defect ? 'd' : 'r'}${x.job_id}`;
    return (
      <li key={key}>
        <div className="flex items-center gap-3 px-3 py-1.5 text-xs hover:bg-slate-700/40">
          <button type="button" onClick={() => setOpen(open === key ? null : key)} aria-expanded={open === key}
            className="flex min-w-0 flex-1 items-center gap-3 text-left">
            <span className="w-3 shrink-0 text-slate-500">{open === key ? '▾' : '▸'}</span>
            <span className={`w-12 shrink-0 font-semibold tabular-nums ${defect ? 'text-rose-600 dark:text-rose-400' : 'text-slate-300'}`}>{defect ? x.defect : x.source_repeat}곳</span>
            <span className="min-w-0 flex-1 truncate text-slate-200" title={x.title ?? ''}>{x.title ?? `작업 #${x.job_id}`}</span>
            {!x.checked && <span className="shrink-0 text-amber-700 dark:text-amber-300" title="원문 연결이 없거나 다시 읽기에 실패해 예전 기준(원문 반복도 결함)으로 셌어요">원문 대조 못 함</span>}
          </button>
          <SrcLink rootUrl={rootUrl} pageId={x.page_id} />
        </div>
        {open === key && <OverlapPairs batchId={batchId} jobId={x.job_id} defectOnly={defect} />}
      </li>
    );
  };
  return (
    <Modal isOpen onClose={onClose} title="품질 경고 리포트" maxWidth="max-w-4xl">
      <div className="space-y-4 text-sm" data-testid="quality-report">
        {ov ? (
          <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Tile k="결함 의심" v={`${ov.defect}곳`} tone={ov.defect ? 'bad' : 'good'} sub="원문보다 조각에 더 많이 나온 줄" />
              <Tile k="원문 반복" v={`${ov.source_repeat}곳`} sub="원문이 원래 같은 줄을 여러 번 씀 — 문제 아님" />
              <Tile k="다른 경고" v={`${otherPlaces}곳`} tone={otherPlaces ? 'warn' : 'good'} sub={otherKinds.join(' · ') || '없음'} />
            </div>
            {(ov.types.table ?? 0) + (ov.types.text ?? 0) > 0 && (
              <div className="space-y-1"><div className="text-xs text-slate-400">이웃 조각에 같이 나온 줄 유형</div>
                <SplitBar parts={[{ label: '표 행', n: ov.types.table ?? 0, cls: 'bg-indigo-500 dark:bg-indigo-400' }, { label: '문장', n: ov.types.text ?? 0, cls: 'bg-slate-400' }]} /></div>
            )}
            {ov.unchecked_jobs > 0 && <p className="text-xs text-amber-700 dark:text-amber-300">원문 대조 못 한 페이지 {ov.unchecked_jobs}개 — 예전 기준(원문 반복도 결함)으로 셌어요</p>}

            <section className="space-y-1.5">
              <h4 className="font-semibold text-slate-100">결함 의심 <span className="text-xs font-normal text-slate-400">페이지 {defects.length}</span></h4>
              {defects.length ? <ul className="divide-y divide-slate-700/60 rounded-lg border border-rose-300 dark:border-rose-500/40">{defects.map((x) => row(x, true))}</ul>
                : <p className="text-xs text-emerald-700 dark:text-emerald-300">나누면서 생긴 중복은 없어요 ✓</p>}
            </section>
            {repeats.length > 0 && (
              <details className="rounded-lg border border-slate-700">
                <summary className="cursor-pointer px-3 py-2 font-semibold text-slate-100" title="체크리스트 표처럼 원문이 같은 줄을 여러 번 쓰는 경우 — 원문 그대로라 고칠 것 없음">
                  원문 반복 <span className="text-xs font-normal text-slate-400">페이지 {repeats.length} · 문제 아님</span></summary>
                <ul className="divide-y divide-slate-700/60 border-t border-slate-700">{repeats.map((x) => row(x, false))}</ul>
              </details>
            )}
          </>
        ) : <p className="text-xs text-slate-400">예전 기록이라 겹침 분석이 없어요 — 아래는 경고 종류별 페이지예요.</p>}

        {otherKinds.map((k) => (
          <section key={k} className="space-y-1.5">
            <h4 className="flex flex-wrap items-baseline gap-2 font-semibold text-slate-100">{k}
              <span className="text-xs font-normal text-slate-400">페이지 {others.filter((x) => x.kind === k).length}</span></h4>
            <ul className="divide-y divide-slate-700/60 rounded-lg border border-slate-700">
              {others.filter((x) => x.kind === k).sort((a, b) => b.n - a.n).map((x) => (
                <li key={`${x.job_id}-${k}`} className="flex items-center gap-3 px-3 py-1.5 text-xs">
                  <span className="w-12 shrink-0 font-semibold tabular-nums text-amber-700 dark:text-amber-300">{x.n}곳</span>
                  <span className="min-w-0 flex-1 truncate text-slate-200" title={x.title ?? ''}>{x.title ?? `작업 #${x.job_id}`}</span>
                  <SrcLink rootUrl={rootUrl} pageId={x.page_id} />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Modal>
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
      {b.include_restricted_at && (
        <div className="inline-flex rounded-md bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800 dark:bg-amber-500/15 dark:text-amber-300" data-testid="batch-include-restricted"
          title="이 묶음은 읽기 제한 페이지도 넣어요 — OpsLens 사용자 누구나 검색하게 됩니다">
          읽기 제한 포함 · {b.include_restricted_name ?? '관리자'} · {new Date(b.include_restricted_at).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' })}</div>
      )}
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
  const publishing = !!b.publishing_at;
  const wasPublishing = useRef(publishing);
  // 둘 다 뒤에서 돈다 — 누르면 바로 돌아오고, 진행은 배치 상세를 2초마다 다시 읽어 막대로(11/05 "멈춘 것처럼 보인다")
  const runCheck = useMutation({ mutationFn: () => publishCheckImport(b.id), onSuccess: onChange, onError: (e) => showError(e) });
  // 게시 시작을 기억 — 첫 다시 읽기 전에 끝나 버리는 아주 빠른 게시도 끝 안내가 뜨게(리뷰 L3)
  const publish = useMutation({ mutationFn: () => publishImport(b.id), onSuccess: () => { wasPublishing.current = true; onChange(); },
    onError: (e) => showError(e) });
  const checking = !!check?.running;
  // 게시가 끝나는 순간(게시 중 → 아님) 결과 안내 — 성공이면 게시 시각, 실패면 배치 오류 칸
  useEffect(() => {
    if (wasPublishing.current && !publishing) {
      void showAlert(b.published_at ? `게시했어요 — 이제 채팅에 나와요(같은 페이지 옛 버전은 내렸어요).${b.error ? ` 단, ${b.error}` : ''}`
        : `게시가 끝나지 않았어요 — ${b.error ?? '사유를 모름(서버 로그 확인)'}. 남은 작업은 검수 대기로 있어요. 다시 [게시]할 수 있어요.`,
        { tone: b.published_at && !b.error ? 'success' : 'warning' });
    }
    wasPublishing.current = publishing;
  }, [publishing, b.published_at, b.error]);
  const prog = data.publish_progress;
  const warnCount = held ? Object.values(held.warnings).reduce((a, n) => a + n, 0) : 0;
  const [qualityOpen, setQualityOpen] = useState(false);   // 품질 경고 리포트 창(11/05)
  const [checkOpen, setCheckOpen] = useState(false);       // 검색 확인 리포트 창(11/05)
  if (b.status === 'preview') return <p className="text-sm text-slate-400">분류를 확정해 적재하면 여기서 게이트 결과를 보고 롤백할 수 있어요.</p>;
  return (
    <div className="space-y-4">
      {b.status === 'loading' && <ProgressBar label="적재 중" done={b.load_done} total={b.load_total} unit="페이지" />}
      {publishing && (prog?.phase === 'glossary'
        ? <ProgressBar label="자동 용어 추출 중" startedAt={prog.started_at} detail="조각은 모두 게시됐어요 — 용어를 뽑는 마지막 단계예요(로컬 LLM)" />
        : <ProgressBar label="게시 중" done={prog?.done ?? 0} total={prog?.total ?? 0} unit="페이지" startedAt={prog?.started_at ?? b.publishing_at}
            detail="페이지마다 옛 버전 내림 · 원문 확정 · 활성화 — 끝나면 자동 용어 추출. 화면을 닫아도 계속돼요" />)}
      {checking && <ProgressBar label="검색 확인 중" done={(check?.asked ?? 0) + (check?.failed ?? 0)} total={check?.total ?? 0} unit="표본" startedAt={check?.checked_at}
        detail="질문은 로컬 LLM이 조각에서 만들어요 — 화면을 닫아도 계속돼요" />}
      {check?.interrupted && !checking && <p className="text-xs text-amber-700 dark:text-amber-300">검색 확인이 서버 재시작으로 끊겼어요 — 다시 눌러 주세요.</p>}
      {check?.error && !checking && <p className="text-xs text-amber-700 dark:text-amber-300">{check.error}</p>}
      {!checking && !!check?.failed && <p className="text-xs text-amber-700 dark:text-amber-300" title="로컬 LLM이 조각에서 질문을 못 만든 표본 — 찾음 수에서 빠져요(로그)">
        질문 만들기 실패 {check.failed}개{check.asked ? '' : ' — 로컬 LLM 상태를 확인해 주세요'}</p>}
      {b.error && <p className="text-sm text-rose-600 dark:text-rose-400">{b.error}</p>}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat k="이번 적재 배치" v={loaded} sub="페이지" />
        <Stat k="실패" v={failed} hot={failed > 0} />
        <Stat k="상태" v={held ? '검수 대기' : b.status === 'loaded' && b.published_at ? '게시됨' : STATUS_TEXT[b.status]} hot={!!held} />
      </div>
      {held && qualityOpen && <QualityReport held={held} batchId={b.id} rootUrl={b.root_url} onClose={() => setQualityOpen(false)} />}
      {held && checkOpen && check?.items && <SearchCheckReport check={check} rootUrl={b.root_url} onClose={() => setCheckOpen(false)} />}
      {held && (
        <div className="space-y-3" data-testid="publish-review">
          <p className="text-sm text-slate-300" title="게시하면 같은 페이지 옛 버전을 내리고 새 조각을 채팅에 내놓아요. 그 전까지는 옛 버전이 그대로 답에 쓰여요">
            검수 대기 — 아직 채팅에 안 나와요. 확인하고 [게시]하면 반영돼요.
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Stat k="게시할 조각" v={held.chunks} sub={`작업 ${held.jobs}`} />
            <Stat k="중복 의심" v={held.dup} hot={held.dup > 0} sub="게시하면 승인 대기로" />
            <Stat k="품질 경고" v={warnCount} hot={warnCount > 0} onOpen={warnCount > 0 || (held.overlap_items?.length ?? 0) > 0 ? () => setQualityOpen(true) : undefined}
              sub={Object.entries(held.warnings).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${k} ${n}페이지`).join(' · ') || '없음'} />
            <Stat k="검색 확인" v={checking ? '확인 중' : check?.asked ? `${check.found}/${check.asked}` : check?.skipped ? '건너뜀' : '—'} onOpen={!checking && check?.items?.length ? () => setCheckOpen(true) : undefined}
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
            <button disabled={runCheck.isPending || publish.isPending || publishing || checking} onClick={() => runCheck.mutate()}
              title="이 배치 조각 10개로 로컬 LLM이 질문을 만들고, 그 질문으로 이 페이지가 검색 상위 3에 드는지 봐요(벡터만 — 실제 채팅보다 보수적)"
              className="text-sm px-4 py-2 rounded-lg border border-slate-600 text-slate-200 hover:bg-slate-800 disabled:opacity-40">
              {checking || runCheck.isPending ? '검색 확인 중…' : check ? '검색 확인 다시' : '검색 확인'}
            </button>
            <button disabled={publish.isPending || runCheck.isPending || publishing || checking}
              onClick={async () => { if (await showConfirm(`조각 ${held.chunks}개를 채팅에 내놓아요. 같은 페이지 옛 버전은 내려가요.`, { title: '게시', confirmLabel: '게시' })) publish.mutate(); }}
              className="text-sm font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-40">{publishing ? '게시 중…' : '게시'}</button>
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
          <button disabled={rollback.isPending || publishing}
            onClick={async () => { if (await showConfirm(`이번 배치(${loaded}페이지)가 만든 조각을 지우고 원문을 교체됨으로 돌려요. 배치 뒤에 고친 조각도 같이 지워져요.`, { tone: 'danger' })) rollback.mutate(); }}
            className="text-sm font-semibold px-4 py-2 rounded-lg bg-rose-600 text-white hover:bg-rose-500 disabled:opacity-40">이번 배치 롤백</button>
        </div>
      )}
      {rollback.data && <p className="text-xs text-slate-400">롤백: 조각 {rollback.data.chunks} · 원문 {rollback.data.documents} · 자동 용어 {rollback.data.terms}
        {rollback.data.released > 0 && <span title="되돌린 조각만 중복 상대로 가리키던 게시 전 승인 대기 — 중복 근거가 없어져 자동으로 풀림(게시 후 승인 대기는 사람이 처리)"> · 승인 대기 해제 {rollback.data.released}</span>}</p>}
    </div>
  );
}
