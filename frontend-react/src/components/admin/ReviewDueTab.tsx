import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getKnowledge, getReviewDue, markReviewed, setReviewInterval, type ReviewDueDoc } from '../../api/knowledge';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { useAuthStore } from '../../store/useAuthStore';
import { showError } from '../../store/useDialogStore';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';
import { TabGuide } from './TabGuide';

/**
 * 운영 지식 › 검토 필요(11/04 태훈 5번 — 시안 승인) — 검토 주기가 지난 원문 페이지를 채팅에 많이 쓰인 순으로. 사람은 [검토 완료]만.
 * 업계: Guru 검증(담당·검증 주기·미검증) · Confluence 페이지 검증 · Zendesk 검토 기한. 서버: agents/knowledge_rag/knowledge/review.py.
 * 검토 주기는 이 탭 머리에서(관리자) — 30 · 90 · 180일 · 안 함.
 */
const fmtDay = (s: string) => new Date(s).toLocaleDateString('ko-KR', { month: 'numeric', day: 'numeric', timeZone: 'Asia/Seoul' });
const INTERVALS: (number | null)[] = [30, 90, 180, null];

export function useReviewDueCount(ns: string | null, enabled: boolean) {
  const q = useQuery({ queryKey: ['review-due', ns], queryFn: () => getReviewDue(ns!), enabled: !!ns && enabled, staleTime: 30_000 });
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
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['review-due', selectedNs] }); qc.invalidateQueries({ queryKey: ['knowledge', selectedNs] }); },
    onError: (e) => showError(e),
  });
  const interval = useMutation({
    mutationFn: (days: number | null) => setReviewInterval(selectedNs, days),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['review-due', selectedNs] }),
    onError: (e) => showError(e),
  });
  const d = q.data;
  return (
    <div className="space-y-3" data-testid="review-due">
      <TabGuide what="검토 주기가 지난 원문 페이지 — 채팅에 많이 쓰인 순"
        when="마지막 검토(승인·[검토 완료]·고친 날 중 늦은 날, 없으면 등록일) + 검토 주기가 지나면"
        todo="원문이 지금도 맞으면 [검토 완료], 틀렸으면 [조각 보기]에서 고쳐요"
        detail="페이지 단위 — 같은 페이지 조각은 한 번에. 가장 오래된 조각이 기한을 정해요" />
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-400">검토 주기</span>
        {isAdmin ? (
          <select className="bg-slate-800 border border-slate-600 rounded-md px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
            aria-label="검토 주기" data-testid="review-interval" disabled={!d || interval.isPending}
            value={d ? String(d.interval_days ?? '') : ''} onChange={(e) => interval.mutate(e.target.value ? Number(e.target.value) : null)}>
            {INTERVALS.map((x) => <option key={x ?? 'off'} value={x ?? ''}>{x ? `${x}일` : '안 함'}</option>)}
          </select>
        ) : <b className="text-slate-200" title="관리자가 정해요">{d ? (d.interval_days ? `${d.interval_days}일` : '안 함') : '—'}</b>}
        {d && <span className="ml-auto text-slate-500">검토 필요 <b className="text-slate-200 tabular-nums">{d.due.length}</b> / 원문 {d.total_docs}개 · 쓰인 횟수 = 최근 {d.usage_days}일 채팅 답</span>}
      </div>
      {q.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
        : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p>
        : !d?.interval_days && !d?.due.length ? <p className="text-sm text-slate-500">이 컬렉션은 검토 기한을 쓰지 않아요.</p>
        : !d || d.due.length === 0 ? <p className="text-sm text-slate-500">검토 기한이 지난 원문이 없어요.</p> : (
          <ul className="space-y-2">
            {d.due.map((x) => (
              <li key={x.key} className="rounded-xl border border-slate-700 bg-slate-800 p-3 space-y-1.5" data-testid={`review-due-${x.key}`}>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  {x.source_changed && (
                    <span className="rounded px-1.5 py-0.5 text-[11px] bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300" data-testid={`review-changed-${x.key}`}
                      title="컨플 원문이 바뀌었는데 사람이 고친 조각이 있어 동기화가 덮지 않았어요 — [조각 보기]로 확인한 뒤 지식 조회의 조각 구조에서 [원문에서 다시 분할]로 새 원문을 반영하세요(고친 조각은 확인을 거쳐 바뀝니다)">원문 바뀜</span>
                  )}
                  <b className="min-w-0 break-words text-sm text-slate-100">{x.title}</b>
                  <span className="text-xs text-slate-500">조각 {x.n}</span>
                </div>
                <p className="text-xs text-slate-400">
                  {x.last_reviewed_at ? `마지막 검토 ${fmtDay(x.last_reviewed_at)}${x.last_reviewed_by ? ` · ${x.last_reviewed_by}` : ''}` : `검토 기록 없음 (등록 ${fmtDay(x.created_at)})`}
                  {x.overdue_days != null && <>{' · '}<span className="text-amber-700 dark:text-amber-300">기한 {x.overdue_days}일 지남</span></>}
                  {' · '}채팅 답 <b className="tabular-nums text-slate-200">{x.used}</b>회
                </p>
                <div className="flex flex-wrap justify-end gap-2">
                  <Button size="sm" variant="ghost" onClick={() => setView(x)} title="이 페이지 조각 내용 보기">조각 보기</Button>
                  {canModifyNs && x.source_changed && (
                    <span className="self-center text-xs text-slate-400" title="검토 완료로는 풀리지 않아요 — 새 원문을 반영해야 목록에서 빠져요">
                      [원문에서 다시 분할]로 반영</span>
                  )}
                  {canModifyNs && !x.source_changed && (
                    <Button size="sm" onClick={() => done.mutate(x)} disabled={done.isPending} data-testid={`review-done-${x.key}`}
                      title="원문이 지금도 맞아요 — 조각 전체에 오늘 검토일 · 내 이름이 남고 목록에서 빠져요">검토 완료 ✓</Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      {view && <ChunksModal ns={selectedNs} doc={view} onClose={() => setView(null)} />}
    </div>
  );
}

function ChunksModal({ ns, doc, onClose }: { ns: string; doc: ReviewDueDoc; onClose: () => void }) {
  const q = useQuery({ queryKey: ['knowledge', ns], queryFn: () => getKnowledge(ns), staleTime: 15_000 });
  const want = new Set(doc.ids);
  const rows = (q.data ?? []).filter((k) => want.has(k.id));
  return (
    <Modal isOpen onClose={onClose} title={doc.title} maxWidth="max-w-3xl">
      {q.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
        : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p> : (
          <div className="space-y-2">
            <p className="text-xs text-slate-500">고칠 곳이 있으면 지식 조회에서 #번호로 찾아 수정해요.</p>
            {rows.map((k) => (
              <div key={k.id} className="rounded-lg border border-slate-700 p-2.5">
                <p className="text-[11px] text-slate-500 tabular-nums">#{k.id}</p>
                <p className="whitespace-pre-wrap break-words text-sm text-slate-200">{k.content}</p>
              </div>
            ))}
          </div>
        )}
    </Modal>
  );
}
