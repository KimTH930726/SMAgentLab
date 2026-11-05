import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { getKnowledge, getReviewDue, markReviewed, setReviewInterval, updateKnowledge, type ReviewDueDoc } from '../../api/knowledge';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { useAuthStore } from '../../store/useAuthStore';
import { showError } from '../../store/useDialogStore';
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
  const [view, setView] = useState<ReviewDueDoc | null>(null);
  // 탭을 열 때마다 새로 — 등록·수정·승인이 목록을 바꿔도 30초 동안 옛 목록이 보이던 것(E2E에서 발견)
  const q = useQuery({ queryKey: ['review-due', selectedNs], queryFn: () => getReviewDue(selectedNs), enabled: !!selectedNs, staleTime: 30_000, refetchOnMount: 'always' });
  const done = useMutation({
    mutationFn: (d: ReviewDueDoc) => markReviewed(selectedNs, d.ids),
    onSuccess: () => { setView(null); qc.invalidateQueries({ queryKey: ['review-due', selectedNs] }); qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] }); },
    onError: (e) => showError(e),
  });
  const interval = useMutation({
    mutationFn: (days: number | null) => setReviewInterval(selectedNs, days),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['review-due', selectedNs] }),
    onError: (e) => showError(e),
  });
  const d = q.data;
  const count = (r: Reason) => d?.due.filter((x) => x.reason === r).length ?? 0;
  const list = d?.due ?? [];
  return (
    <div className="space-y-3" data-testid="review-due">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {d && (
          <div className="flex flex-wrap items-center gap-1.5" data-testid="review-counts">
            {(['source_changed', 'reported', 'used'] as Reason[]).filter((r) => count(r) > 0).map((r) => (
              <span key={r} className={clsx('rounded-md px-2 py-0.5 font-semibold', STATUS[r].chip)}
                title={r === 'used' ? `${STATUS[r].hint} — 최근 ${d.usage_days}일 ${d.used_min}회 이상, 많이 쓰인 순 위 ${d.used_top}개` : STATUS[r].hint}>
                {STATUS[r].label} <span className="tabular-nums">{count(r)}</span></span>
            ))}
            {d.quiet > 0 && <span className="text-slate-400" data-testid="review-quiet"
              title={`기한은 지났지만 채팅에 ${d.used_min}회 미만 쓰였거나, 많이 쓰인 순 위 ${d.used_top}개 밖이라 목록에 안 올렸어요 — 채팅 근거 카드에 '기한 지남' 배지로만 보여요`}>+ 기한만 지남 {d.quiet} ⓘ</span>}
          </div>
        )}
        <span className="ml-auto text-slate-400" title="마지막 검토(검토 완료·승인·고친 날, 없으면 등록일)부터 이 기간이 지나면 올라와요">검토 주기</span>
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
        : !d || list.length === 0 ? <p className="py-10 text-center text-sm text-slate-500">지금 검토할 원문이 없어요 ✓</p> : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {list.map((x) => <DueCard key={x.key} x={x} usageDays={d.usage_days} canModify={canModifyNs} busy={done.isPending}
              onOpen={() => setView(x)} onDone={() => done.mutate(x)} />)}
          </div>
        )}
      {view && <DetailModal ns={selectedNs} doc={view} usageDays={d?.usage_days ?? 30} canModify={canModifyNs} busy={done.isPending}
        onDone={() => done.mutate(view)} onClose={() => setView(null)} />}
    </div>
  );
}

/** 카드 하나 = 상태 칩 + 큰 숫자 하나(기한 N일 지남 / 원문 바뀜) + 제목 + 한 줄 사실. 카드 아무 데나 누르면 상세 */
function DueCard({ x, usageDays, canModify, busy, onOpen, onDone }: {
  x: ReviewDueDoc; usageDays: number; canModify: boolean; busy: boolean; onOpen: () => void; onDone: () => void;
}) {
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
        {canModify && !x.source_changed && (
          <Button size="sm" className="ml-auto" disabled={busy} data-testid={`review-done-${x.key}`}
            onClick={(e) => { e.stopPropagation(); onDone(); }} title="원문이 지금도 맞아요 — 조각 전체에 오늘 검토일 · 내 이름이 남고 목록에서 빠져요">검토 완료 ✓</Button>
        )}
      </div>
    </div>
  );
}

/** 상세 — 페이지 조각을 그 자리에서 고친다(고치면 그 조각은 검토한 것으로 셈). 다 맞으면 [검토 완료] */
function DetailModal({ ns, doc, usageDays, canModify, busy, onDone, onClose }: {
  ns: string; doc: ReviewDueDoc; usageDays: number; canModify: boolean; busy: boolean; onDone: () => void; onClose: () => void;
}) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['knowledge', ns], queryFn: () => getKnowledge(ns), staleTime: 15_000 });
  const want = new Set(doc.ids);
  const rows = (q.data ?? []).filter((k) => want.has(k.id));
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
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
                    {canModify && editing?.id !== k.id && (
                      <button type="button" onClick={() => setEditing({ id: k.id, text: k.content })} data-testid={`review-edit-${k.id}`}
                        className="ml-auto rounded-md border border-slate-600 px-2 py-0.5 text-xs text-slate-200 hover:bg-slate-700">수정</button>
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
