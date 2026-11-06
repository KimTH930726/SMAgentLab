import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { archiveReview, getArchivedReview, getKnowledge, getReviewDue, markReviewed, setReviewInterval, snoozeReview, updateKnowledge, type ReviewDueDoc } from '../../api/knowledge';
import { createCorrection } from '../../api/corrections';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { useAuthStore } from '../../store/useAuthStore';
import { showAlert, showConfirm, showError } from '../../store/useDialogStore';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';

/**
 * 운영 지식 › 검토 필요(11/04 태훈 5번 — 시안 승인) — 검토 주기가 지난 원문 페이지를 채팅에 많이 쓰인 순으로. 사람은 [검토 완료]만.
 * 11/05 태훈 "줄글 안 읽음 · 찾아서 고치라는 건 불친절": 카드(상태 칩 + 큰 숫자 하나), 카드 아무 데나 누르면 상세에서 조각을 바로 고침.
 * 11/05 태훈 "주기마다 전부 검수는 너무 큰 일": 사건 기반 — 원문 바뀜 · 신고 · 많이 쓰이는데 오래됨(위 10개)만. 나머지는 수만.
 * 업계: Guru 검증(담당·검증 주기·미검증) · Confluence 페이지 검증 · Zendesk 검토 기한. 서버: agents/knowledge_rag/knowledge/review.py.
 * 검토 주기는 이 탭 머리에서(관리자) — 30 · 90 · 180일 · 안 함.
 */
const fmtDay = (s: string) => new Date(s).toLocaleDateString('ko-KR', { month: 'numeric', day: 'numeric', timeZone: 'Asia/Seoul' });
const INTERVALS: (number | null)[] = [30, 90, 180, null];
type Reason = ReviewDueDoc['reason'];

/** 상태 — 칩 이름 · 색 · 한 줄 풀이(툴팁) */
const STATUS: Record<Reason, { label: string; chip: string; big: string; hint: string }> = {
  source_changed: {
    label: '원문 바뀜', chip: 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300', big: 'text-rose-600 dark:text-rose-400',
    hint: '컨플 원문이 바뀌었는데 사람이 고친 조각이 있어 자동 반영하지 않았어요 — 채팅은 옛 내용으로 답하는 중',
  },
  used: {
    label: '많이 쓰임', chip: 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300', big: 'text-amber-700 dark:text-amber-300',
    hint: '검토 기한이 지났는데 채팅 답에 자주 근거로 쓰여요',
  },
  reported: {
    label: '신고', chip: 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300', big: 'text-rose-600 dark:text-rose-400',
    hint: '채팅에서 [답변 틀림 · 내용이 낡음] 신고가 들어왔어요 — 정정 요청에서도 처리할 수 있어요',
  },
};

export function useReviewDueCount(ns: string | null, enabled: boolean) {
  const q = useQuery({ queryKey: ['review-due', ns], queryFn: () => getReviewDue(ns!), enabled: !!ns && enabled, staleTime: 30_000 });
  // 홈 카드·탭 배지 = 목록 수(서버가 사건 기반으로 고른 것만 보낸다)
  return { n: q.data?.due.length, isLoading: q.isLoading, isError: q.isError, interval: q.data?.interval_days };
}

export function ReviewDueTab() {
  const qc = useQueryClient();
  const { selectedNs, canModifyNs } = useNamespaceAccess();
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin');
  const [view, setView] = useState<{ doc: ReviewDueDoc; edit: boolean } | null>(null);
  // 필터(11/05 태훈 "카드들 필터링해서 볼 수 있게") — 사유 칩 · 제목 검색 · 정렬
  const [only, setOnly] = useState<Reason | null>(null);
  const [find, setFind] = useState('');
  const [sort, setSort] = useState<'used' | 'overdue'>('used');
  const [showArchived, setShowArchived] = useState(false);
  // 탭을 열 때마다 새로 — 등록·수정·승인이 목록을 바꿔도 30초 동안 옛 목록이 보이던 것(E2E에서 발견)
  const q = useQuery({ queryKey: ['review-due', selectedNs], queryFn: () => getReviewDue(selectedNs), enabled: !!selectedNs, staleTime: 30_000, refetchOnMount: 'always' });
  const done = useMutation({
    mutationFn: (d: ReviewDueDoc) => markReviewed(selectedNs, d.ids),
    onSuccess: () => { setView(null); qc.invalidateQueries({ queryKey: ['review-due', selectedNs] }); qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] }); },
    onError: (e) => showError(e),
  });
  const refresh = () => { qc.invalidateQueries({ queryKey: ['review-due', selectedNs] }); qc.invalidateQueries({ queryKey: ['review-archived', selectedNs] }); qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] }); };
  const snooze = useMutation({
    mutationFn: ({ d, days }: { d: ReviewDueDoc; days: 30 | 90 }) => snoozeReview(selectedNs, d.ids, days),
    onSuccess: () => { setView(null); refresh(); },
    onError: (e) => showError(e),
  });
  const archive = useMutation({
    mutationFn: ({ ids, on }: { ids: number[]; on: boolean }) => archiveReview(selectedNs, ids, on),
    onSuccess: () => { setView(null); refresh(); },
    onError: (e) => showError(e),
  });
  const askArchive = async (d: ReviewDueDoc) => {
    if (await showConfirm(`"${d.title}" 조각 ${d.n}개를 보관할까요?\n검색·채팅에서 빠지고, 아래 [보관됨]에서 되살릴 수 있어요(지우지 않음).`)) archive.mutate({ ids: d.ids, on: true });
  };
  const archived = useQuery({ queryKey: ['review-archived', selectedNs], queryFn: () => getArchivedReview(selectedNs), enabled: !!selectedNs && showArchived });
  const interval = useMutation({
    mutationFn: (days: number | null) => setReviewInterval(selectedNs, days),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['review-due', selectedNs] }),
    onError: (e) => showError(e),
  });
  const d = q.data;
  const count = (r: Reason) => d?.due.filter((x) => x.reason === r).length ?? 0;
  const needle = find.trim().toLowerCase();
  const list = (d?.due ?? [])
    .filter((x) => (!only || x.reason === only) && (!needle || x.title.toLowerCase().includes(needle)))
    .sort((a, b) => a.source_changed !== b.source_changed ? (a.source_changed ? -1 : 1)
      : sort === 'used' ? b.used - a.used || (b.overdue_days ?? 0) - (a.overdue_days ?? 0) : (b.overdue_days ?? 0) - (a.overdue_days ?? 0));
  return (
    <div className="space-y-3" data-testid="review-due">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {d && (
          <div className="flex flex-wrap items-center gap-1.5" data-testid="review-counts">
            {(['source_changed', 'reported', 'used'] as Reason[]).filter((r) => count(r) > 0).map((r) => (
              <button key={r} type="button" aria-pressed={only === r} onClick={() => setOnly(only === r ? null : r)} data-testid={`review-only-${r}`}
                className={clsx('rounded-md px-2 py-0.5 font-semibold ring-offset-1', STATUS[r].chip, only === r && 'ring-2 ring-indigo-500')}
                title={`${r === 'used' ? `${STATUS[r].hint} — 최근 ${d.usage_days}일 ${d.used_min}회 이상, 많이 쓰인 순 위 ${d.used_top}개` : STATUS[r].hint} · 눌러서 이것만 보기`}>
                {STATUS[r].label} <span className="tabular-nums">{count(r)}</span></button>
            ))}
            {d.quiet > 0 && <span className="text-slate-400" data-testid="review-quiet"
              title={`기한은 지났지만 채팅에 ${d.used_min}회 미만 쓰였거나, 많이 쓰인 순 위 ${d.used_top}개 밖이라 목록에 안 올렸어요 — 채팅 근거 카드에 '기한 지남' 배지로만 보여요`}>+ 기한만 지남 {d.quiet} ⓘ</span>}
          </div>
        )}
        <input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder="제목 검색" aria-label="제목 검색"
          className="ml-auto w-40 bg-slate-900 border border-slate-600 rounded-md px-2 py-1 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500" />
        <select value={sort} onChange={(e) => setSort(e.target.value as 'used' | 'overdue')} aria-label="정렬" data-testid="review-sort"
          className="bg-slate-800 border border-slate-600 rounded-md px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500">
          <option value="used">채팅 많은 순</option><option value="overdue">오래 지난 순</option>
        </select>
        <span className="text-slate-400" title="마지막 검토(검토 완료·승인·고친 날, 없으면 등록일)부터 이 기간이 지나면 올라와요">검토 주기</span>
        {isAdmin ? (
          <select className="bg-slate-800 border border-slate-600 rounded-md px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
            aria-label="검토 주기" data-testid="review-interval" disabled={!d || interval.isPending}
            value={d ? String(d.interval_days ?? '') : ''} onChange={(e) => interval.mutate(e.target.value ? Number(e.target.value) : null)}>
            {INTERVALS.map((x) => <option key={x ?? 'off'} value={x ?? ''}>{x ? `${x}일` : '안 함'}</option>)}
          </select>
        ) : <b className="text-slate-200" title="관리자가 정해요">{d ? (d.interval_days ? `${d.interval_days}일` : '안 함') : '—'}</b>}
      </div>
      {q.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
        : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p>
        : !d?.interval_days && !d?.due.length ? <p className="py-10 text-center text-sm text-slate-500">이 컬렉션은 검토 기한을 쓰지 않아요.</p>
        : !d || list.length === 0 ? <p className="py-10 text-center text-sm text-slate-500">{only || needle ? '조건에 맞는 원문이 없어요.' : '지금 검토할 원문이 없어요 ✓'}</p> : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {list.map((x) => <DueCard key={x.key} x={x} usageDays={d.usage_days} canModify={canModifyNs} busy={done.isPending || snooze.isPending || archive.isPending}
              onOpen={() => setView({ doc: x, edit: false })} onEdit={() => setView({ doc: x, edit: true })} onDone={() => done.mutate(x)}
              onSnooze={(days) => snooze.mutate({ d: x, days })} onArchive={() => void askArchive(x)} />)}
          </div>
        )}
      <div className="space-y-2">
        <button type="button" onClick={() => setShowArchived((v) => !v)} aria-expanded={showArchived} data-testid="review-archived-toggle"
          className="text-xs text-slate-400 hover:text-slate-200" title="보관한 페이지 — 검색·채팅에서 빠져 있어요. 되살리면 다시 쓰여요">
          {showArchived ? '▾' : '▸'} 보관됨{archived.data ? ` ${archived.data.docs.length}` : ''}</button>
        {showArchived && (archived.isLoading ? <p className="text-xs text-slate-500">불러오는 중…</p>
          : archived.error ? <p className="text-xs text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(archived.error as Error).message}</p>
          : !archived.data?.docs.length ? <p className="text-xs text-slate-500">보관한 페이지가 없어요.</p> : (
            <ul className="divide-y divide-slate-700/60 rounded-lg border border-slate-700">
              {archived.data.docs.map((a) => (
                <li key={a.key} className="flex items-center gap-3 px-3 py-1.5 text-xs" data-testid={`review-archived-${a.key}`}>
                  <span className="min-w-0 flex-1 truncate text-slate-200">{a.title}</span>
                  <span className="text-slate-500">조각 {a.n} · {fmtDay(a.archived_at)} 보관</span>
                  {canModifyNs && <button type="button" disabled={archive.isPending} onClick={() => archive.mutate({ ids: a.ids, on: false })}
                    className="text-indigo-600 hover:underline dark:text-indigo-400" data-testid={`review-unarchive-${a.key}`}>되살리기</button>}
                </li>
              ))}
            </ul>
          ))}
      </div>
      {view && <DetailModal ns={selectedNs} doc={view.doc} startEdit={view.edit} usageDays={d?.usage_days ?? 30} canModify={canModifyNs} busy={done.isPending}
        onDone={() => done.mutate(view.doc)} onClose={() => setView(null)} />}
    </div>
  );
}

/** 카드 하나 = 상태 칩 + 큰 숫자 하나(기한 N일 지남 / 원문 바뀜) + 제목 + 한 줄 사실. 카드 아무 데나 누르면 상세 */
function DueCard({ x, usageDays, canModify, busy, onOpen, onEdit, onDone, onSnooze, onArchive }: {
  x: ReviewDueDoc; usageDays: number; canModify: boolean; busy: boolean; onOpen: () => void; onEdit: () => void; onDone: () => void;
  onSnooze: (days: 30 | 90) => void; onArchive: () => void;
}) {
  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();
  const st = STATUS[x.reason];
  return (
    <div role="button" tabIndex={0} onClick={onOpen} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(); } }}
      data-testid={`review-due-${x.key}`} title="눌러서 조각 보기 · 바로 고치기"
      className="flex min-w-0 cursor-pointer flex-col gap-2 rounded-xl border border-slate-700 bg-slate-800 p-3.5 text-left transition-colors hover:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-500">
      <div className="flex items-start justify-between gap-2" data-testid={`review-reason-${x.key}`}>
        <span className={clsx('rounded-md px-2 py-0.5 text-xs font-semibold', st.chip)} title={st.hint}
          data-testid={x.source_changed ? `review-changed-${x.key}` : undefined}>{st.label}</span>
        <span className={clsx('text-right leading-none', st.big)}>
          {x.reason === 'source_changed' ? <span className="text-sm font-semibold">채팅은 옛 내용</span>
            : x.reason === 'used' ? <><span className="text-xs">채팅 </span><span className="text-2xl font-semibold tabular-nums">{x.used}</span><span className="ml-0.5 text-xs">회</span></>
            : x.reason === 'reported' ? <><span className="text-2xl font-semibold tabular-nums">{x.reported}</span><span className="ml-0.5 text-xs">건 신고</span></>  /* 신고 건수(서버 count) */
            : <><span className="text-2xl font-semibold tabular-nums">{x.overdue_days ?? 0}</span><span className="ml-0.5 text-xs">일 지남</span></>}
        </span>
      </div>
      <h4 className="min-w-0 break-words text-sm font-semibold text-slate-100">{x.title}</h4>
      <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
        {x.reason === 'used' ? <span className="text-amber-700 dark:text-amber-300">기한 {x.overdue_days ?? 0}일 지남</span>
          : <span title={`최근 ${usageDays}일 채팅 답이 근거로 쓴 횟수`}>채팅 <b className={clsx('tabular-nums', x.used ? 'text-slate-100' : 'text-slate-500')}>{x.used}</b>회</span>}
        <span>조각 {x.n}</span>
        <span className="min-w-0 truncate">{x.last_reviewed_at ? `검토 ${fmtDay(x.last_reviewed_at)}${x.last_reviewed_by ? ` · ${x.last_reviewed_by}` : ''}` : `검토 없음 · 등록 ${fmtDay(x.created_at)}`}</span>
      </div>
      {!canModify && (
        // 고칠 권한이 없어도 요청은 할 수 있다 — 정정 요청은 로그인 사용자 누구나(리뷰 L7)
        <div className="flex border-t border-slate-700 pt-2" onClick={stop} onKeyDown={stop}>
          <button type="button" onClick={onOpen} className="text-xs text-slate-300 hover:text-slate-100" data-testid={`review-request-${x.key}`}
            title="담당 파트에 수정 요청 — 상세에서 조각을 골라요">수정 요청</button>
        </div>
      )}
      {canModify && (
        // 동작(11/05, 업계 Guru·Confluence 검토 앱): 검토 완료 · 수정 · 미루기 · 수정 요청 · 보관 — 카드를 눌러도 상세가 열린다
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-700 pt-2" onClick={stop} onKeyDown={stop}>
          {!x.source_changed && (
            <Button size="sm" disabled={busy} data-testid={`review-done-${x.key}`} onClick={onDone}
              title="원문이 지금도 맞아요 — 조각 전체에 오늘 검토일 · 내 이름이 남고 목록에서 빠져요">검토 완료 ✓</Button>
          )}
          <Button size="sm" variant="secondary" disabled={busy} data-testid={`review-edit-open-${x.key}`} onClick={onEdit}
            title="조각을 바로 고쳐요 — 고친 조각은 검토한 것으로 셈">수정</Button>
          <select aria-label="미루기" value="" disabled={busy} data-testid={`review-snooze-${x.key}`}
            onChange={(e) => { if (e.target.value) onSnooze(Number(e.target.value) as 30 | 90); }}
            title="지금은 못 봐요 — 이 기간 동안 목록에서 빼요(원문 바뀜은 그래도 보여요)"
            className="bg-slate-800 border border-slate-600 rounded-md px-1.5 py-1 text-xs text-slate-300 focus:outline-none focus:border-indigo-500">
            <option value="">미루기 ▾</option><option value="30">30일 뒤</option><option value="90">90일 뒤</option>
          </select>
          <button type="button" disabled={busy} onClick={onOpen} className="text-xs text-slate-400 hover:text-slate-200" data-testid={`review-request-${x.key}`}
            title="내가 고치기 어려워요 — 상세에서 조각을 골라 담당 파트에 수정 요청(정정 요청에 올라가요)">수정 요청</button>
          <button type="button" disabled={busy} onClick={onArchive} className="ml-auto text-xs text-rose-600 hover:underline dark:text-rose-400" data-testid={`review-archive-${x.key}`}
            title="이제 안 쓰는 지식 — 검색·채팅에서 빼요(지우지 않음, 보관됨에서 되살리기)">보관</button>
        </div>
      )}
    </div>
  );
}

/** 상세 — 페이지 조각을 그 자리에서 고친다(고치면 그 조각은 검토한 것으로 셈). 다 맞으면 [검토 완료] */
function DetailModal({ ns, doc, usageDays, canModify, busy, onDone, onClose, startEdit = false }: {
  ns: string; doc: ReviewDueDoc; usageDays: number; canModify: boolean; busy: boolean; onDone: () => void; onClose: () => void; startEdit?: boolean;
}) {
  const [asking, setAsking] = useState<{ id: number; reason: 'stale' | 'missing_step' | null; text: string } | null>(null);
  const request = useMutation({   // 수정 요청 = 정정 요청 한 건(규격 사유 칩 + 한 줄, 11/04 원칙) — '내용이 낡음'이면 이 목록에 '신고'로도 잡힌다
    mutationFn: (a: { id: number; reason: 'stale' | 'missing_step' | null; text: string }) => createCorrection({
      namespace: ns, target_type: 'knowledge', target_id: a.id, user_reason: a.reason,
      user_input: a.text.trim() || (a.reason === 'stale' ? '내용이 낡음' : a.reason === 'missing_step' ? '단계·조건 빠짐' : '검토 필요에서 수정 요청'),
    }),
    onSuccess: (r) => { setAsking(null); void showAlert(`정정 요청 #${(r as { id?: number }).id ?? ''}에 올렸어요 — 담당 파트가 검토해요.`, { title: '수정 요청' }); },
    onError: (e) => showError(e),
  });
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['knowledge', ns], queryFn: () => getKnowledge(ns), staleTime: 15_000 });
  const want = new Set(doc.ids);
  const rows = (q.data ?? []).filter((k) => want.has(k.id));
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  // [수정]으로 열었으면 첫 조각을 바로 고치는 상태로(11/05 태훈 "수정 버튼이 너무 작다")
  const [opened, setOpened] = useState(false);
  if (startEdit && !opened && rows.length > 0 && canModify) { setOpened(true); setEditing({ id: rows[0].id, text: rows[0].content }); }
  const save = useMutation({
    mutationFn: (e: { id: number; text: string }) => updateKnowledge(e.id, { content: e.text }),
    onSuccess: () => { setEditing(null); qc.invalidateQueries({ queryKey: ['knowledge', ns] }); qc.invalidateQueries({ queryKey: ['review-due', ns] }); },
    onError: (e) => showError(e),
  });
  const st = STATUS[doc.reason];
  return (
    <Modal isOpen onClose={onClose} title={doc.title} maxWidth="max-w-3xl">
      <div className="space-y-3" data-testid="review-detail">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className={clsx('rounded-md px-2 py-0.5 font-semibold', st.chip)}>{st.label}</span>
          <span className="text-slate-300">{doc.reason !== 'used' ? st.hint
            : `기한 ${doc.overdue_days ?? 0}일 지남 · 최근 ${usageDays}일 채팅 ${doc.used}회`}</span>
        </div>
        {q.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
          : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p> : (
            <div className="max-h-[55vh] space-y-2 overflow-y-auto">
              {rows.map((k) => (
                <div key={k.id} className="rounded-lg border border-slate-700 p-2.5 space-y-1.5" data-testid={`review-chunk-${k.id}`}>
                  <div className="flex items-center gap-2 text-[11px] text-slate-500">
                    <span className="tabular-nums">#{k.id}</span>
                    {editing?.id !== k.id && (
                      <span className="ml-auto flex gap-1.5">
                        {canModify && <Button size="sm" variant="secondary" onClick={() => setEditing({ id: k.id, text: k.content })} data-testid={`review-edit-${k.id}`}>수정</Button>}
                        <Button size="sm" variant="ghost" onClick={() => setAsking({ id: k.id, reason: null, text: '' })} data-testid={`review-ask-${k.id}`}
                          title="담당 파트에 수정 요청 — 정정 요청에 올라가요">수정 요청</Button>
                      </span>
                    )}
                  </div>
                  {editing?.id === k.id ? (
                    <>
                      <textarea aria-label={`#${k.id} 내용`} rows={Math.min(14, Math.max(4, editing.text.split('\n').length + 1))} value={editing.text}
                        onChange={(e) => setEditing({ id: k.id, text: e.target.value })}
                        className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
                      <div className="flex justify-end gap-2">
                        <Button size="sm" variant="ghost" disabled={save.isPending} onClick={() => setEditing(null)}>취소</Button>
                        <Button size="sm" loading={save.isPending} disabled={!editing.text.trim() || editing.text === k.content}
                          onClick={() => save.mutate(editing)} data-testid={`review-save-${k.id}`}>저장</Button>
                      </div>
                    </>
                  ) : <p className="whitespace-pre-wrap break-words text-sm text-slate-200">{k.content}</p>}
                  {asking?.id === k.id && (
                    <div className="space-y-1.5 rounded-lg border border-slate-600 p-2" data-testid={`review-ask-form-${k.id}`}>
                      <div className="flex flex-wrap gap-1.5" role="group" aria-label="수정 요청 사유">
                        {([['stale', '내용이 낡음'], ['missing_step', '단계·조건 빠짐']] as const).map(([r, label]) => (
                          <button key={r} type="button" aria-pressed={asking.reason === r} onClick={() => setAsking({ ...asking, reason: asking.reason === r ? null : r })}
                            className={clsx('rounded-full border px-2.5 py-0.5 text-xs', asking.reason === r
                              ? 'border-rose-600 bg-rose-600 text-white dark:border-rose-500 dark:bg-rose-500' : 'border-slate-600 text-slate-300 hover:bg-slate-700')}>{label}</button>
                        ))}
                      </div>
                      <input value={asking.text} onChange={(e) => setAsking({ ...asking, text: e.target.value })} aria-label="수정 요청 한 줄"
                        placeholder={asking.reason === 'missing_step' ? '예) 재기동 전에 배치 중지 단계가 빠졌어요' : '예) 90일이 아니라 60일로 바뀌었어요'}
                        className="w-full bg-slate-900 border border-slate-600 rounded-md px-2 py-1 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500" />
                      <div className="flex justify-end gap-2">
                        <Button size="sm" variant="ghost" onClick={() => setAsking(null)}>취소</Button>
                        <Button size="sm" loading={request.isPending} disabled={!asking.reason && !asking.text.trim()} onClick={() => request.mutate(asking)}
                          data-testid={`review-ask-send-${k.id}`}>요청 올리기</Button>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-700 pt-3">
          {doc.source_changed ? (
            <span className="text-xs text-slate-400" title="검토 완료로는 풀리지 않아요 — 새 원문을 반영해야 목록에서 빠져요">
              새 원문 반영: 지식 조회 › 조각 구조 › [원문에서 다시 분할]</span>
          ) : canModify && (
            <Button size="sm" disabled={busy || !!editing} onClick={onDone} data-testid="review-detail-done"
              title="조각이 지금도 맞아요 — 전체에 오늘 검토일 · 내 이름이 남고 목록에서 빠져요">검토 완료 ✓</Button>
          )}
        </div>
      </div>
    </Modal>
  );
}
