import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { MessageSquarePlus } from 'lucide-react';
import {
  getAutoSignals, listFeedback, myFeedback, myUnseen, sendFeedback, triageFeedback,
  type FeedbackItem, type FeedbackKind, type FeedbackStatus,
} from '../../api/userFeedback';
import { useAppStore } from '../../store/useAppStore';
import { showAlert, showError } from '../../store/useDialogStore';
import { Modal } from '../ui/Modal';
import { FormModal } from '../ui/FormModal';

/**
 * 사용자 피드백(10/30) — [피드백 보내기](사이드바) + 관리자 분류(품질 › 사용자 피드백).
 * 업계: Jira Service Management 요청 유형(오류 신고 · 기능 제안) → 분류 → 백로그, Canny(보낸 사람이 처리 상태를 본다).
 * 답이 틀렸다는 건 채팅 답변 아래 [정정 요청]으로(같은 일이 두 곳으로 갈라지지 않게).
 */

export const KIND: Record<FeedbackKind, { label: string; cls: string }> = {
  bug: { label: '오류 신고', cls: 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300' },
  idea: { label: '기능 제안', cls: 'bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300' },
};
export const FB_STATUS: Record<FeedbackStatus, { label: string; cls: string; hint: string }> = {
  new: { label: '신규', cls: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300', hint: '접수됨 — 관리자가 아직 안 봄' },
  planned: { label: '선정', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300', hint: '고치기로 함 — 작업 예정·진행 중' },
  done: { label: '완료', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300', hint: '반영됨' },
  duplicate: { label: '중복', cls: 'bg-slate-700 text-slate-300', hint: '같은 내용의 피드백이 이미 있음' },
  declined: { label: '기각', cls: 'bg-slate-700 text-slate-400', hint: '하지 않기로 함(사유 있음)' },
};
const fmt = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });

/** 사이드바 버튼 — 처리 결과가 바뀐 내 피드백이 있으면 점 */
export function FeedbackButton() {
  const [open, setOpen] = useState(false);
  const unseen = useQuery({ queryKey: ['feedback-unseen'], queryFn: myUnseen, staleTime: 60_000, refetchInterval: 5 * 60_000 });
  const n = unseen.data?.count ?? 0;
  return (
    <>
      <button onClick={() => setOpen(true)} data-testid="feedback-open"
        className="relative p-1.5 rounded-lg text-slate-500 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-slate-700 transition-colors"
        title={n ? `피드백 보내기 — 처리 결과가 나온 피드백 ${n}건` : '피드백 보내기 — 오류 신고 · 기능 제안'}>
        <MessageSquarePlus className="w-3.5 h-3.5" />
        {n > 0 && <span className="absolute top-0.5 right-0.5 w-1.5 h-1.5 rounded-full bg-rose-500" aria-label={`새 결과 ${n}건`} />}
      </button>
      <FeedbackModal isOpen={open} onClose={() => setOpen(false)} initialTab={n ? 'mine' : 'send'} />
    </>
  );
}

function FeedbackModal({ isOpen, onClose, initialTab }: { isOpen: boolean; onClose: () => void; initialTab: 'send' | 'mine' }) {
  const [tab, setTab] = useState<'send' | 'mine'>(initialTab);
  useEffect(() => { if (isOpen) setTab(initialTab); }, [isOpen]);   // eslint-disable-line react-hooks/exhaustive-deps -- 열 때 한 번만(새 결과가 있으면 내 피드백)
  const [kind, setKind] = useState<FeedbackKind>('bug');
  const [body, setBody] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const loc = useLocation();
  const conversationId = useAppStore((s) => s.conversationId);
  const qc = useQueryClient();
  const mine = useQuery({ queryKey: ['feedback-mine'], queryFn: myFeedback, enabled: isOpen && tab === 'mine' });
  const send = useMutation({
    mutationFn: () => sendFeedback({ kind, body: body.trim(), page: loc.pathname, conversation_id: !loc.pathname.startsWith('/admin') ? conversationId : null }),
    onSuccess: () => {
      setBody('');
      qc.invalidateQueries({ queryKey: ['feedback-mine'] });
      onClose();
      showAlert('보내 주셔서 고맙습니다. 처리 결과는 [피드백 보내기] › 내 피드백에서 볼 수 있어요.', { title: '피드백 접수', tone: 'success' });
    },
    onError: (e) => setErr(e instanceof Error ? e.message : '보내지 못했습니다'),
  });
  const close = () => {
    setErr(null);
    if (tab === 'mine') qc.invalidateQueries({ queryKey: ['feedback-unseen'] });   // 본 뒤 점 끄기
    onClose();
  };
  const tabBtn = (t: 'send' | 'mine', label: string) => (
    <button onClick={() => { setTab(t); setErr(null); }} aria-pressed={tab === t}
      className={clsx('rounded-md px-3 py-1 text-xs', tab === t ? 'bg-indigo-600 text-white' : 'text-slate-300 hover:bg-slate-700')}>{label}</button>
  );
  return (
    <Modal isOpen={isOpen} onClose={close} title="피드백" maxWidth="max-w-lg">
      <div className="space-y-3" data-testid="feedback-modal">
        <div className="inline-flex rounded-lg border border-slate-700 p-0.5">{tabBtn('send', '보내기')}{tabBtn('mine', '내 피드백')}</div>
        {tab === 'send' ? (
          <>
            <div className="flex gap-2" role="radiogroup" aria-label="유형">
              {(Object.keys(KIND) as FeedbackKind[]).map((k) => (
                <button key={k} role="radio" aria-checked={kind === k} onClick={() => setKind(k)} data-testid={`feedback-kind-${k}`}
                  className={clsx('rounded-full px-3 py-1 text-xs border', kind === k ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-slate-600 text-slate-300 hover:bg-slate-800')}>
                  {KIND[k].label}</button>
              ))}
            </div>
            <label htmlFor="feedback-body" className="block text-xs text-slate-400">
              {kind === 'bug' ? '무엇이 안 됐나요? (언제·어느 화면에서·무엇을 눌렀을 때)' : '어떤 기능이 있으면 좋을까요? (왜 필요한지도)'}
            </label>
            <textarea id="feedback-body" rows={5} maxLength={2000} value={body} onChange={(e) => setBody(e.target.value)} autoFocus
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
            <p className="text-[11px] text-slate-500" title="처리하는 사람이 상황을 다시 찾지 않아도 되게 자동으로 붙여요">
              함께 보냄: 지금 화면({loc.pathname}){!loc.pathname.startsWith('/admin') && conversationId ? ` · 대화 #${conversationId}` : ''} · 보낸 시각</p>
            <p className="text-[11px] text-slate-500">답 내용이 틀렸다면 채팅 답변 아래 [정정 요청]으로 보내 주세요 — 지식 담당자에게 바로 갑니다.</p>
            {err && <p className="text-xs text-rose-600 dark:text-rose-400">{err}</p>}
            <div className="flex justify-end gap-2">
              <button onClick={close} className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200">취소</button>
              <button onClick={() => { setErr(null); send.mutate(); }} disabled={send.isPending || body.trim().length < 2} data-testid="feedback-send"
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-500 disabled:opacity-50">{send.isPending ? '보내는 중…' : '보내기'}</button>
            </div>
          </>
        ) : mine.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
          : mine.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(mine.error as Error).message}</p>
            : (mine.data ?? []).length === 0 ? <p className="text-sm text-slate-500">보낸 피드백이 없어요</p> : (
              <ul className="space-y-2 max-h-[50vh] overflow-y-auto" data-testid="feedback-mine">
                {(mine.data ?? []).map((f) => (
                  <li key={f.id} className={clsx('rounded-lg border px-3 py-2 text-xs space-y-1', f.unseen ? 'border-indigo-400 dark:border-indigo-500/60' : 'border-slate-700')}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`rounded-full px-2 py-0.5 text-[11px] ${KIND[f.kind].cls}`}>{KIND[f.kind].label}</span>
                      <span className={`rounded-full px-2 py-0.5 text-[11px] ${FB_STATUS[f.status].cls}`} title={FB_STATUS[f.status].hint}>{FB_STATUS[f.status].label}</span>
                      {f.unseen && <span className="text-indigo-600 dark:text-indigo-300">새 결과</span>}
                      <span className="ml-auto text-slate-500 tabular-nums">{fmt(f.created_at)}</span>
                    </div>
                    <p className="text-slate-200 whitespace-pre-wrap break-words line-clamp-3">{f.body}</p>
                    {f.admin_note && <p className="text-slate-400 break-words">답변: {f.admin_note}</p>}
                  </li>
                ))}
              </ul>
            )}
      </div>
    </Modal>
  );
}

// ── 관리자: 품질 › 사용자 피드백 ────────────────────────────────────────────

type Filter = FeedbackStatus | 'all';
const FILTERS: { key: Filter; label: string }[] = [
  { key: 'new', label: '신규' }, { key: 'planned', label: '선정' }, { key: 'done', label: '완료' },
  { key: 'duplicate', label: '중복' }, { key: 'declined', label: '기각' }, { key: 'all', label: '전체' },
];

export function useFeedbackNewCount(enabled: boolean): number {
  const q = useQuery({ queryKey: ['user-feedback', 'new'], queryFn: () => listFeedback('new'), enabled, staleTime: 60_000 });
  return q.data?.counts.new ?? 0;
}

export function UserFeedbackPanel() {
  const [filter, setFilter] = useState<Filter>('new');
  const q = useQuery({ queryKey: ['user-feedback', filter], queryFn: () => listFeedback(filter === 'all' ? undefined : filter) });
  const counts = q.data?.counts;
  const total = counts ? Object.values(counts).reduce((a, b) => a + b, 0) : 0;
  return (
    <div className="space-y-4" data-testid="user-feedback-panel">
      <AutoSignals />
      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="상태" data-testid="feedback-filters">
          {FILTERS.map((f) => {
            const n = f.key === 'all' ? total : counts?.[f.key] ?? 0;
            if (n === 0 && f.key !== 'new' && f.key !== filter) return null;
            return (
              <button key={f.key} onClick={() => setFilter(f.key)} aria-pressed={filter === f.key}
                title={f.key === 'all' ? undefined : FB_STATUS[f.key].hint}
                className={clsx('rounded-full px-3 py-1 text-xs border', filter === f.key ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-slate-600 text-slate-300 hover:bg-slate-800')}>
                {f.label} <span className="tabular-nums">{n}</span></button>
            );
          })}
        </div>
        {q.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
          : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p>
            : (q.data?.items ?? []).length === 0 ? <p className="text-sm text-slate-500">{filter === 'new' ? '새 피드백이 없어요' : '해당하는 피드백이 없어요'}</p>
              : <div className="space-y-2">{q.data!.items.map((f) => <FeedbackRow key={f.id} f={f} />)}</div>}
      </section>
    </div>
  );
}

function FeedbackRow({ f }: { f: FeedbackItem }) {
  const qc = useQueryClient();
  const [modal, setModal] = useState<null | 'declined' | 'duplicate' | 'done'>(null);
  const [note, setNote] = useState('');
  const [dup, setDup] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const act = useMutation({
    mutationFn: (b: { status: FeedbackStatus; note?: string; duplicate_of?: number | null }) => triageFeedback(f.id, b),
    onSuccess: () => { setModal(null); qc.invalidateQueries({ queryKey: ['user-feedback'] }); },
    onError: (e) => (modal ? setErr(e instanceof Error ? e.message : '저장하지 못했습니다') : showError(e, '저장하지 못했습니다')),
  });
  const openModal = (m: 'declined' | 'duplicate' | 'done') => { setErr(null); setNote(''); setDup(''); setModal(m); };
  const btn = 'text-xs hover:underline disabled:opacity-50';
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2.5 space-y-1.5" data-testid={`feedback-${f.id}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-500 tabular-nums">#{f.id}</span>
        <span className={`rounded-full px-2 py-0.5 text-[11px] ${KIND[f.kind].cls}`}>{KIND[f.kind].label}</span>
        <span className={`rounded-full px-2 py-0.5 text-[11px] ${FB_STATUS[f.status].cls}`} title={FB_STATUS[f.status].hint}>{FB_STATUS[f.status].label}</span>
        <span className="text-slate-400">{f.reporter ?? '탈퇴한 사용자'}{f.reporter_part ? ` · ${f.reporter_part}` : ''}</span>
        <span className="text-slate-500 tabular-nums">{fmt(f.created_at)}</span>
        {f.page && <span className="text-slate-500" title="보낸 화면">{f.page}{f.conversation_id ? ` · 대화 #${f.conversation_id}` : ''}</span>}
      </div>
      <p className="text-sm text-slate-100 whitespace-pre-wrap break-words">{f.body}</p>
      {(f.admin_note || f.duplicate_of) && (
        <p className="text-xs text-slate-400 break-words">{f.duplicate_of ? `#${f.duplicate_of}와 같음` : ''}{f.duplicate_of && f.admin_note ? ' · ' : ''}{f.admin_note ?? ''}
          {f.decided_by ? <span className="text-slate-500"> — {f.decided_by}{f.decided_at ? ` · ${fmt(f.decided_at)}` : ''}</span> : null}</p>
      )}
      <div className="flex flex-wrap gap-4">
        {f.status !== 'planned' && f.status !== 'done' && (
          <button className={`${btn} text-amber-600 dark:text-amber-400 font-medium`} disabled={act.isPending} title="고치기로 함 — 보낸 사람에게 '선정'으로 보임"
            onClick={() => act.mutate({ status: 'planned' })}>선정</button>
        )}
        {f.status !== 'done' && (
          <button className={`${btn} text-emerald-600 dark:text-emerald-400`} title="반영함 — 보낸 사람에게 '완료'와 한 줄 답변이 보임" onClick={() => openModal('done')}>완료…</button>
        )}
        {f.status === 'new' && (
          <>
            <button className={`${btn} text-slate-300`} title="같은 내용의 피드백 번호로 묶음" onClick={() => openModal('duplicate')}>중복…</button>
            <button className={`${btn} text-rose-600 dark:text-rose-400`} title="하지 않음(사유 필수 — 보낸 사람이 봄)" onClick={() => openModal('declined')}>기각…</button>
          </>
        )}
        {f.status !== 'new' && (
          <button className={`${btn} text-slate-400`} disabled={act.isPending} title="결정을 지우고 신규로" onClick={() => act.mutate({ status: 'new' })}>결정 취소</button>
        )}
      </div>
      <FormModal isOpen={modal !== null} onClose={() => setModal(null)}
        title={modal === 'declined' ? '기각' : modal === 'duplicate' ? '중복' : '완료'}
        submitLabel={modal === 'declined' ? '기각' : modal === 'duplicate' ? '중복으로' : '완료로'} danger={modal === 'declined'}
        submitting={act.isPending} error={err}
        submitDisabled={(modal === 'declined' && !note.trim()) || (modal === 'duplicate' && !/^\d+$/.test(dup.trim()))}
        onSubmit={() => act.mutate({ status: modal as FeedbackStatus, note, duplicate_of: modal === 'duplicate' ? Number(dup) : null })}>
        {modal === 'duplicate' && (
          <>
            <label htmlFor={`fb-dup-${f.id}`} className="block text-xs text-slate-400">같은 내용의 피드백 번호</label>
            <input id={`fb-dup-${f.id}`} value={dup} onChange={(e) => setDup(e.target.value)} inputMode="numeric" placeholder="예: 12"
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500" />
          </>
        )}
        <label htmlFor={`fb-note-${f.id}`} className="block text-xs text-slate-400">
          {modal === 'declined' ? '기각 사유 (보낸 사람이 봅니다)' : modal === 'done' ? '무엇을 고쳤나 한 줄 (선택 — 보낸 사람이 봅니다)' : '메모 (선택)'}</label>
        <textarea id={`fb-note-${f.id}`} rows={3} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500}
          className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
      </FormModal>
    </div>
  );
}

/** 자동 감지 — 최근 14일 채팅 장애(질의 기록 system_error) 건수. 원문 없음 */
function AutoSignals() {
  const q = useQuery({ queryKey: ['feedback-auto-signals'], queryFn: () => getAutoSignals(14), staleTime: 60_000 });
  const d = q.data;
  return (
    <section className="rounded-xl border border-slate-700 bg-slate-800/60 px-4 py-3 space-y-2" data-testid="feedback-auto-signals">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="text-sm font-semibold text-slate-100">자동 감지</p>
        <p className="text-xs text-slate-400" title="채팅에서 답을 못 낸 질문(게이트웨이 거부·LLM 연결 실패·처리 오류) — 사용자가 신고하지 않아도 잡힘. 질문 원문은 안 보여요">
          최근 14일 채팅 장애</p>
      </div>
      {q.isLoading ? <p className="text-xs text-slate-500">불러오는 중…</p>
        : q.error ? <p className="text-xs text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p>
          : d && (
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
              <p className={clsx('text-2xl font-semibold tabular-nums', d.failures ? 'text-rose-600 dark:text-rose-400' : 'text-slate-100')}>
                {d.failures}<span className="ml-1 text-xs font-normal text-slate-400">/ 질문 {d.total_questions}건</span></p>
              <p className="text-xs text-slate-400">{Object.entries(d.by_kind).map(([k, n]) => `${k} ${n}`).join(' · ') || '장애 없음'}</p>
              {d.by_day.length > 0 && <p className="text-xs text-slate-500">최근: {d.by_day.slice(0, 3).map((x) => `${x.date.slice(5)} ${x.kind} ${x.n}`).join(' · ')}</p>}
            </div>
          )}
    </section>
  );
}
