import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { ImagePlus, MessageSquarePlus, X } from 'lucide-react';
import {
  getAutoSignals, getFeedbackFile, listFeedback, MAX_FILE_BYTES, MAX_FILES, myFeedback, myUnseen, sendFeedback, triageFeedback,
  type FeedbackContext, type FeedbackItem, type FeedbackKind, type FeedbackStatus,
} from '../../api/userFeedback';
import { ApiError } from '../../api/client';
import { useAppStore } from '../../store/useAppStore';
import { showAlert, showConfirm, showError } from '../../store/useDialogStore';
import { useAuthStore } from '../../store/useAuthStore';
import { openFeedback, useFeedbackStore } from '../../store/useFeedbackStore';
import { Modal } from '../ui/Modal';
import { FormModal } from '../ui/FormModal';

/**
 * 사용자 피드백(10/30) — [피드백 보내기](사이드바) + 관리자 분류(품질 › 사용자 피드백).
 * 업계: Jira Service Management 요청 유형(오류 신고 · 기능 제안) → 분류 → 백로그, Canny(보낸 사람이 처리 상태를 본다).
 * 답이 틀렸다는 건 채팅 답변 아래 [정정 요청]으로(같은 일이 두 곳으로 갈라지지 않게).
 * 11/02: 화면 캡처 첨부(Ctrl+V·끌어 놓기·파일 선택) · 오류 창/채팅 실패 답에서 바로 [오류 신고](오류 문맥 자동) ·
 * 선정 → 진행 중 → 완료(진행 중일 때 [개발 요청서] — 폐쇄망이라 로컬 CLI에 붙여 넣어 개발).
 */

export const KIND: Record<FeedbackKind, { label: string; cls: string }> = {
  bug: { label: '오류 신고', cls: 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300' },
  idea: { label: '기능 제안', cls: 'bg-sky-100 text-sky-700 dark:bg-sky-500/15 dark:text-sky-300' },
};
export const FB_STATUS: Record<FeedbackStatus, { label: string; cls: string; hint: string }> = {
  new: { label: '신규', cls: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300', hint: '접수됨 — 관리자가 아직 안 봄' },
  planned: { label: '선정', cls: 'bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300', hint: '고치기로 함 — 작업 예정' },
  in_progress: { label: '진행 중', cls: 'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300', hint: '고치는 중 — 반영되면 완료' },
  done: { label: '완료', cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300', hint: '반영됨' },
  duplicate: { label: '중복', cls: 'bg-slate-700 text-slate-300', hint: '같은 내용의 피드백이 이미 있음' },
  declined: { label: '기각', cls: 'bg-slate-700 text-slate-400', hint: '하지 않기로 함(사유 있음)' },
};
const fmt = (s: string) => new Date(s).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];

/** 채팅 답이 장애 문구인가 — 서버 helpers.py의 고정 문구 + 스트림 끊김(useStreamStore). 이때 답 아래 [오류 신고] */
const CHAT_FAILURES = [
  '[LLM 서버에 연결할 수 없습니다. 검색 결과를 참고하세요.]',
  '[AI가 빈 답변을 보냈습니다. 잠시 후 다시 질문해 주세요.]',
  '[처리 중 오류가 발생했습니다. 잠시 후 다시 질문해 주세요.]',
  '[오류가 발생했습니다. 다시 시도해 주세요.]',
];
export const isChatFailure = (content: string | undefined | null) => !!content && CHAT_FAILURES.includes(content.trim());

/** 오류 문맥 한 줄 — "오류: 요청을 처리하지 못했습니다 · POST /api/chat → 500" */
export function contextLine(c: FeedbackContext | null | undefined): string {
  if (!c?.message && !c?.path) return '';
  const req = c.path ? ` · ${c.path}${c.status ? ` → ${c.status}` : ''}` : c.status ? ` · ${c.status}` : '';
  return `${c.source === 'chat' ? '채팅 답' : '오류'}: ${c.message ?? ''}${req}`;
}

/** 사이드바 버튼 — 처리 결과가 바뀐 내 피드백이 있으면 점 */
export function FeedbackButton() {
  const unseen = useQuery({ queryKey: ['feedback-unseen'], queryFn: myUnseen, staleTime: 60_000, refetchInterval: 5 * 60_000 });
  const n = unseen.data?.count ?? 0;
  return (
    <button onClick={() => openFeedback({ tab: n ? 'mine' : 'send' })} data-testid="feedback-open"
      className="relative p-1.5 rounded-lg text-slate-500 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-slate-700 transition-colors"
      title={n ? `피드백 보내기 — 처리 결과가 나온 피드백 ${n}건` : '피드백 보내기 — 오류 신고 · 기능 제안'}>
      <MessageSquarePlus className="w-3.5 h-3.5" />
      {n > 0 && <span className="absolute top-0.5 right-0.5 w-1.5 h-1.5 rounded-full bg-rose-500" aria-label={`새 결과 ${n}건`} />}
    </button>
  );
}

/** 앱에 하나 — 사이드바·오류 창·채팅 실패 답이 같은 창을 연다 */
export function FeedbackHost() {
  const s = useFeedbackStore();
  const closeFeedback = s.closeFeedback;
  useEffect(() => { closeFeedback(); }, [closeFeedback]);   // 로그아웃 전 열려 있던 창이 다시 로그인하면 뜨던 것(리뷰 L3)
  return <FeedbackModal isOpen={s.open} seq={s.seq} onClose={s.closeFeedback} initialTab={s.tab} initialKind={s.kind} report={s.report} />;
}

interface Attachment { file: File; url: string }

function FeedbackModal({ isOpen, seq, onClose, initialTab, initialKind, report }: {
  isOpen: boolean; seq: number; onClose: () => void; initialTab: 'send' | 'mine'; initialKind: FeedbackKind; report: FeedbackContext | null;
}) {
  const [tab, setTab] = useState<'send' | 'mine'>(initialTab);
  const [kind, setKind] = useState<FeedbackKind>(initialKind);
  const [body, setBody] = useState('');
  const [files, setFiles] = useState<Attachment[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const filesRef = useRef<Attachment[]>([]);
  filesRef.current = files;
  const dropAll = () => { filesRef.current.forEach((a) => URL.revokeObjectURL(a.url)); setFiles([]); };
  useEffect(() => {   // 열 때마다 처음부터(오류 신고로 열면 오류 신고 · 새 결과가 있으면 내 피드백)
    if (!isOpen) return;
    setTab(initialTab); setKind(initialKind); setErr(null); setBody(''); dropAll();
  }, [isOpen, seq]);   // eslint-disable-line react-hooks/exhaustive-deps -- 열 때마다 한 번(열린 채로 다시 열어도)
  useEffect(() => () => filesRef.current.forEach((a) => URL.revokeObjectURL(a.url)), []);
  const fileInput = useRef<HTMLInputElement>(null);
  const loc = useLocation();
  const conversationId = useAppStore((s) => s.conversationId);
  const namespace = useAppStore((s) => s.namespace);   // 보던 컬렉션 — 관리자 보드 컬렉션 필터(11/05)
  const qc = useQueryClient();
  const mine = useQuery({ queryKey: ['feedback-mine'], queryFn: myFeedback, enabled: isOpen && tab === 'mine' });
  const send = useMutation({
    mutationFn: () => sendFeedback({
      kind, body: body.trim(), page: loc.pathname, conversation_id: !loc.pathname.startsWith('/admin') ? conversationId : null,
      context: kind === 'bug' ? report : null, files: files.map((a) => a.file), namespace: namespace || undefined,
    }),
    onSuccess: () => {
      setBody(''); dropAll();
      qc.invalidateQueries({ queryKey: ['feedback-mine'] });
      onClose();
      showAlert('보내 주셔서 고맙습니다. 처리 결과는 [피드백 보내기] › 내 피드백에서 볼 수 있어요.', { title: '피드백 접수', tone: 'success' });
    },
    onError: (e) => setErr(e instanceof Error ? e.message : '보내지 못했습니다'),
  });
  const addFiles = (list: File[]) => {
    const imgs = list.filter((f) => IMAGE_TYPES.includes(f.type));
    const ok = imgs.filter((f) => f.size <= MAX_FILE_BYTES);
    const room = Math.max(0, MAX_FILES - filesRef.current.length);
    const problem = imgs.length < list.length ? '이미지 파일(PNG·JPEG·GIF·WEBP)만 붙일 수 있어요.'
      : ok.length < imgs.length ? '이미지 한 장은 5MB까지예요.'
        : ok.length > room ? `이미지는 ${MAX_FILES}장까지예요.` : null;
    setErr(problem);
    const next = ok.slice(0, room).map((file) => ({ file, url: URL.createObjectURL(file) }));
    if (next.length) setFiles((cur) => [...cur, ...next]);
  };
  const removeFile = (i: number) => setFiles((cur) => { URL.revokeObjectURL(cur[i].url); return cur.filter((_, j) => j !== i); });
  const onPaste = (e: React.ClipboardEvent) => {   // 화면 캡처 Ctrl+V — 글자 붙여넣기는 그대로 둔다
    if (e.clipboardData.getData('text/plain')) return;   // 엑셀·워드에서 복사한 글은 그림도 같이 실려 와서 — 글이면 글로(리뷰 M1)
    const imgs = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith('image/'));
    if (!imgs.length) return;
    e.preventDefault();
    addFiles(imgs);
  };
  const close = () => {
    if (send.isPending) return;   // 보내는 중엔 닫지 않음 — 닫고 다시 연 창을 앞 요청의 결과가 닫아 버리던 것(재리뷰 L2)
    setErr(null); dropAll();
    if (tab === 'mine') qc.invalidateQueries({ queryKey: ['feedback-unseen'] });   // 본 뒤 점 끄기
    onClose();
  };
  const ctx = kind === 'bug' ? contextLine(report) : '';
  const canSend = body.trim().length >= 2 || (kind === 'bug' && !!report?.message);
  const tabBtn = (t: 'send' | 'mine', label: string) => (
    <button onClick={() => { setTab(t); setErr(null); }} aria-pressed={tab === t}
      className={clsx('rounded-md px-3 py-1 text-xs', tab === t ? 'bg-indigo-600 text-white' : 'text-slate-300 hover:bg-slate-700')}>{label}</button>
  );
  return (
    <Modal isOpen={isOpen} onClose={close} title="피드백" maxWidth="max-w-lg">
      <div className="space-y-3" data-testid="feedback-modal" onPaste={tab === 'send' ? onPaste : undefined}
        onDragOver={(e) => { if (tab === 'send' && e.dataTransfer.types.includes('Files')) e.preventDefault(); }}
        onDrop={(e) => {   // 파일만 가로챈다 — 글자 끌어 놓기는 그대로(리뷰 L1)
          if (tab !== 'send' || !e.dataTransfer.types.includes('Files')) return;
          e.preventDefault(); addFiles(Array.from(e.dataTransfer.files));
        }}>
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
            {ctx && (
              <p className="rounded-lg border border-rose-300 dark:border-rose-500/40 bg-rose-50 dark:bg-rose-500/10 px-2.5 py-1.5 text-xs text-rose-700 dark:text-rose-300 break-words"
                data-testid="feedback-context" title="오류 창에서 연 신고 — 이 내용이 함께 가요">{ctx}</p>
            )}
            <label htmlFor="feedback-body" className="block text-xs text-slate-400">
              {kind === 'bug' ? (ctx ? '무엇을 하다가 났나요? (선택)' : '무엇이 안 됐나요? (언제·어느 화면에서·무엇을 눌렀을 때)') : '어떤 기능이 있으면 좋을까요? (왜 필요한지도)'}
            </label>
            <textarea id="feedback-body" rows={5} maxLength={2000} value={body} onChange={(e) => setBody(e.target.value)} autoFocus
              placeholder="화면 캡처는 Ctrl+V로 바로 붙여 넣을 수 있어요"
              className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-indigo-500 resize-y" />
            <div className="flex flex-wrap items-center gap-2" data-testid="feedback-attachments">
              {files.map((a, i) => (
                <div key={a.url} className="relative">
                  <img src={a.url} alt={`첨부 ${i + 1}`} className="h-16 w-24 rounded-md border border-slate-600 object-cover" />
                  <button onClick={() => removeFile(i)} aria-label={`첨부 ${i + 1} 빼기`}
                    className="absolute -top-1.5 -right-1.5 rounded-full bg-slate-700 p-0.5 text-slate-200 hover:bg-rose-600"><X className="w-3 h-3" /></button>
                </div>
              ))}
              {files.length < MAX_FILES && (
                <button onClick={() => fileInput.current?.click()} data-testid="feedback-attach"
                  className="inline-flex items-center gap-1 rounded-md border border-dashed border-slate-600 px-2.5 py-1.5 text-xs text-slate-400 hover:text-slate-200 hover:border-slate-400"
                  title="화면 캡처를 붙여 넣거나(Ctrl+V) 끌어 놓아도 돼요">
                  <ImagePlus className="w-3.5 h-3.5" /> 이미지 첨부</button>
              )}
              <span className="text-[11px] text-slate-500">최대 {MAX_FILES}장 · 장당 5MB</span>
              <input ref={fileInput} type="file" accept={IMAGE_TYPES.join(',')} multiple hidden data-testid="feedback-file-input"
                onChange={(e) => { addFiles(Array.from(e.target.files ?? [])); e.target.value = ''; }} />
            </div>
            <p className="text-[11px] text-slate-500" title="처리하는 사람이 상황을 다시 찾지 않아도 되게 자동으로 붙여요">
              함께 보냄: 지금 화면({loc.pathname}){!loc.pathname.startsWith('/admin') && conversationId ? ` · 대화 #${conversationId}` : ''} · 보낸 시각</p>
            <p className="text-[11px] text-slate-500">답 내용이 틀렸다면 채팅 답변 아래 [정정 요청]으로 보내 주세요 — 지식 담당자에게 바로 갑니다.</p>
            {err && <p className="text-xs text-rose-600 dark:text-rose-400" data-testid="feedback-error">{err}</p>}
            <div className="flex justify-end gap-2">
              <button onClick={close} className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200">취소</button>
              <button onClick={() => { setErr(null); send.mutate(); }} disabled={send.isPending || !canSend} data-testid="feedback-send"
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
                    {f.body && <p className="text-slate-200 whitespace-pre-wrap break-words line-clamp-3">{f.body}</p>}
                    {f.files.length > 0 && <FeedbackImages fid={f.id} files={f.files} small />}
                    {f.admin_note && <p className="text-slate-400 break-words">답변: {f.admin_note}</p>}
                  </li>
                ))}
              </ul>
            )}
      </div>
    </Modal>
  );
}

/** 첨부 이미지 썸네일 — 인증 헤더가 필요해 Blob으로 받아 그린다. 누르면 크게 */
function FeedbackImages({ fid, files, small }: { fid: number; files: number[]; small?: boolean }) {
  const [big, setBig] = useState<string | null>(null);
  return (
    <div className="flex flex-wrap gap-2" data-testid={`feedback-images-${fid}`}>
      {files.map((id, i) => <Thumb key={id} fid={fid} fileId={id} n={i + 1} small={small} onOpen={setBig} />)}
      <Modal isOpen={big !== null} onClose={() => setBig(null)} title="첨부 이미지" maxWidth="max-w-5xl" zClass="z-[60]">
        {big && <img src={big} alt="첨부 이미지 크게" className="max-h-[75vh] w-auto mx-auto rounded-md" />}
      </Modal>
    </div>
  );
}

function Thumb({ fid, fileId, n, small, onOpen }: { fid: number; fileId: number; n: number; small?: boolean; onOpen: (url: string) => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [seen, setSeen] = useState(false);
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {   // 화면에 들어올 때만 받는다 — 관리자 [전체]가 원본 이미지를 한꺼번에 받던 것(리뷰 M3)
    const el = box.current;
    if (seen || !el) return;
    if (typeof IntersectionObserver === 'undefined') { setSeen(true); return; }
    const io = new IntersectionObserver((es) => { if (es.some((x) => x.isIntersecting)) { setSeen(true); io.disconnect(); } }, { rootMargin: '200px' });
    io.observe(el);
    return () => io.disconnect();
  }, [seen]);
  useEffect(() => {
    if (!seen) return;
    let live = true; let made: string | null = null;
    getFeedbackFile(fid, fileId)
      .then((b) => { if (!live) return; made = URL.createObjectURL(b); setUrl(made); })
      .catch((e) => { console.warn('첨부 이미지 불러오기 실패', fid, fileId, e); if (live) setFailed(true); });
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [fid, fileId, seen]);
  const size = small ? 'h-12 w-16' : 'h-20 w-28';
  if (failed) return <span className={`${size} grid place-items-center rounded-md border border-slate-700 text-[11px] text-rose-600 dark:text-rose-400`}>이미지 {n} 못 불러옴</span>;
  if (!url) return <span ref={box} className={`${size} rounded-md border border-slate-700 bg-slate-800 animate-pulse`} aria-label={`이미지 ${n} 불러오는 중`} />;
  return (
    <button onClick={() => onOpen(url)} title="크게 보기" className="rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500">
      <img src={url} alt={`첨부 이미지 ${n}`} className={`${size} rounded-md border border-slate-600 object-cover hover:opacity-90`} />
    </button>
  );
}

/** 채팅 실패 답 아래 [오류 신고] — 답 문구가 오류 문맥으로 붙는다 */
export function ChatFailureReport({ content }: { content: string }) {
  return (
    <button onClick={() => openFeedback({ report: { message: content.trim(), source: 'chat' } })} data-testid="chat-failure-report"
      className="text-xs text-rose-600 dark:text-rose-400 hover:underline" title="이 오류를 관리자에게 바로 보내요 — 대화 번호·화면이 자동으로 붙어요">
      오류 신고</button>
  );
}

// ── 관리자: 품질 › 사용자 피드백 ────────────────────────────────────────────

// 칸반 보드(11/04 태훈 "카드로" — Jira 보드·Canny 로드맵 관례): 진행 단계 네 열 + 종료(중복·기각)는 접힌 줄
const BOARD: FeedbackStatus[] = ['new', 'planned', 'in_progress', 'done'];
const CLOSED: FeedbackStatus[] = ['duplicate', 'declined'];
/** 완료 열은 최근 몇 건만 — 쌓이기만 하는 열이라([n건 더]로 펼침) */
const DONE_SHOW = 5;

export function useFeedbackNewCount(enabled: boolean): number {
  const q = useQuery({ queryKey: ['user-feedback', 'new'], queryFn: () => listFeedback('new'), enabled, staleTime: 60_000 });
  return q.data?.counts.new ?? 0;
}

export function UserFeedbackPanel() {
  // 고른 피드백 — 보드 값이 있으면 그것(최신), 없으면 고른 순간 값 + 바뀐 상태로(11/03 상세 모달)
  const [sel, setSel] = useState<FeedbackItem | null>(null);
  const [allDone, setAllDone] = useState(false);
  const [showClosed, setShowClosed] = useState(false);
  const [ns, setNs] = useState('');   // 컬렉션 필터(11/05 태훈) — '' = 전체, '-' = 미지정
  // 최근 200건(서버) — 열 머리 건수는 그 컬렉션 전체(counts). 'all' 키 앞부분은 단계 이동 후 무효화가 그대로 걸리게
  const q = useQuery({ queryKey: ['user-feedback', 'all', ns], queryFn: () => listFeedback(undefined, ns || undefined), placeholderData: (prev) => prev });
  const counts = q.data?.counts;
  const items = q.data?.items ?? [];
  const by = (s: FeedbackStatus) => items.filter((f) => f.status === s);
  const closed = items.filter((f) => CLOSED.includes(f.status));
  return (
    <div className="space-y-4" data-testid="user-feedback-panel">
      <AutoSignals />
      {q.isLoading ? <p className="text-sm text-slate-500">불러오는 중…</p>
        : q.error ? <p className="text-sm text-rose-600 dark:text-rose-400">불러오지 못했습니다: {(q.error as Error).message}</p> : (
          <section className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <label className="flex items-center gap-1.5 text-slate-400">컬렉션
                <select aria-label="컬렉션" data-testid="feedback-ns-filter" value={ns} onChange={(e) => setNs(e.target.value)}
                  className="bg-slate-800 border border-slate-600 rounded-md px-2 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500">
                  <option value="">전체 ({(q.data?.namespaces ?? []).reduce((a, x) => a + x.n, 0)})</option>
                  {(q.data?.namespaces ?? []).map((x) => (
                    <option key={x.namespace ?? '-'} value={x.namespace ?? '-'}>{x.namespace ?? '미지정'} ({x.n})</option>
                  ))}
                </select>
              </label>
              <span className="text-slate-500" title="단계는 앞뒤 한 칸씩만 — 신규 → 선정 → 진행 중 → 완료. 카드를 누르면 상세 창에서 단계를 바꿔요. 컬렉션 = 보낸 사람이 보던 컬렉션(미지정 = 컬렉션 정보 없이 온 옛 피드백)">
                카드를 누르면 상세 · 단계 이동 ⓘ</span>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" data-testid="feedback-board">
              {BOARD.map((s) => {
                const list = by(s);
                const shown = s === 'done' && !allDone ? list.slice(0, DONE_SHOW) : list;
                return (
                  <div key={s} className="min-w-0 rounded-xl border border-slate-700 bg-slate-900/40 p-2 space-y-2" data-testid={`feedback-col-${s}`}>
                    <h3 className="flex items-center justify-between px-1 text-xs font-medium text-slate-300" title={FB_STATUS[s].hint}>
                      <span className="inline-flex items-center gap-1.5"><span className={`rounded-full px-2 py-0.5 text-[11px] ${FB_STATUS[s].cls}`}>{FB_STATUS[s].label}</span></span>
                      <span className="tabular-nums text-slate-500">{counts?.[s] ?? list.length}</span>
                    </h3>
                    {list.length === 0 ? <p className="px-1 py-3 text-center text-xs text-slate-500">{s === 'new' ? '새 피드백이 없어요' : '없음'}</p>
                      : shown.map((f) => <FeedbackCard key={f.id} f={f} onOpen={() => setSel(f)} />)}
                    {s === 'done' && list.length > DONE_SHOW && (
                      <button type="button" onClick={() => setAllDone((v) => !v)} className="w-full rounded-md py-1 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-200">
                        {allDone ? '접기' : `${list.length - DONE_SHOW}건 더`}</button>
                    )}
                  </div>
                );
              })}
            </div>
            {closed.length > 0 && (
              <div className="space-y-2" data-testid="feedback-closed">
                <button type="button" onClick={() => setShowClosed((v) => !v)} aria-expanded={showClosed}
                  className="text-xs text-slate-400 hover:text-slate-200" title="하지 않기로 했거나 다른 피드백으로 묶은 것 — 상세에서 다시 열 수 있어요">
                  {showClosed ? '▾' : '▸'} 종료 — {CLOSED.map((s) => `${FB_STATUS[s].label} ${counts?.[s] ?? by(s).length}`).join(' · ')}</button>
                {showClosed && (
                  <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
                    {closed.map((f) => <FeedbackCard key={f.id} f={f} onOpen={() => setSel(f)} />)}
                  </div>
                )}
              </div>
            )}
          </section>
        )}
      {sel && <FeedbackDetail f={items.find((x) => x.id === sel.id) ?? sel} onClose={() => setSel(null)}
        onChanged={(patch) => setSel((x) => (x ? { ...x, ...patch } : x))} />}
    </div>
  );
}

/** 개발 요청서 — 진행 중일 때 로컬 CLI에 붙여 넣는 사실 묶음(폐쇄망이라 운영에서 개발 못 함). 확인·수정·검증 절차는 저장소 CLAUDE.md가 정하므로 사실만.
 *  이미지는 담지 않는다(고객 정보가 찍혀 있을 수 있음) — 장수만 */
export function handoffText(f: FeedbackItem): string {
  const lines = [`## 사용자 피드백 #${f.id} — ${KIND[f.kind].label}`];
  if (f.body) lines.push(`- 내용(사용자 글): ${f.body.replace(/\s*\n\s*/g, ' / ')}`);
  const where = [f.page ? `화면 ${f.page}` : '', f.conversation_id ? `대화 #${f.conversation_id}` : '',
    `접수 ${new Date(f.created_at).toLocaleString('ko-KR')}`].filter(Boolean).join(' · ');
  lines.push(`- 어디서: ${where}`);
  const c = f.context;
  if (c?.message || c?.path) lines.push(`- ${c.source === 'chat' ? '채팅 답(장애 문구)' : '화면 오류 문구'}: "${c.message ?? ''}"${c.path ? ` (${c.path}${c.status ? ` → ${c.status}` : ''})` : ''}`);
  if (f.files.length) lines.push(`- 첨부 이미지: ${f.files.length}장 — 운영 화면에서 확인(요청서엔 안 담음)`);
  if (f.admin_note) lines.push(`- 관리자 메모: ${f.admin_note}`);
  lines.push('- 할 일: 재현 → 원인 → 수정 → 테스트·검증(저장소 CLAUDE.md 절차대로)');
  lines.push(`- 끝나면: 운영 품질 › 사용자 피드백 #${f.id}를 [완료]로 + 반영 버전 한 줄`);
  return lines.join('\n');
}

/** 보드 카드 — 번호 · 종류 · 내용 세 줄 · 첨부 수 · 보낸 사람 · 시간. 상태는 열이 말해 주고(종료 줄에선 배지로), 누르면 상세 창(처리는 거기서, 11/03) */
function FeedbackCard({ f, onOpen }: { f: FeedbackItem; onOpen: () => void }) {
  const ctx = contextLine(f.context);
  return (
    <button type="button" onClick={onOpen} data-testid={`feedback-${f.id}`}
      className="w-full rounded-lg border border-slate-700 bg-slate-800 p-2.5 text-left space-y-1.5 hover:border-slate-500 transition-colors">
      <div className="flex items-center gap-1.5 text-[11px]">
        <span className="tabular-nums text-slate-500">#{f.id}</span>
        <span className={`rounded-full px-2 py-0.5 ${KIND[f.kind].cls}`}>{KIND[f.kind].label}</span>
        {f.namespace && <span className="min-w-0 truncate rounded bg-slate-700 px-1.5 py-0.5 text-slate-300" title="보낸 사람이 보던 컬렉션">{f.namespace}</span>}
        <span className={clsx(`ml-auto rounded-full px-2 py-0.5 ${FB_STATUS[f.status].cls}`, BOARD.includes(f.status) && 'sr-only')} title={FB_STATUS[f.status].hint}
          data-testid={`feedback-${f.id}-status`}>{FB_STATUS[f.status].label}</span>
      </div>
      <p className="line-clamp-3 break-words text-sm text-slate-200">{f.body || ctx || '(내용 없음 — 오류 문맥만)'}</p>
      {f.body && ctx && <p className="truncate text-[11px] text-slate-500" title={ctx}>{ctx}</p>}
      <div className="flex items-center gap-2 text-[11px] text-slate-500">
        {f.files.length > 0 && <span title="첨부 이미지">이미지 {f.files.length}</span>}
        <span className="min-w-0 truncate text-slate-400">{f.reporter ?? '탈퇴한 사용자'}</span>
        <span className="ml-auto shrink-0 tabular-nums">{fmt(f.created_at)}</span>
      </div>
    </button>
  );
}

// 상태 고르기 — 바로 바뀌는 것(신규·선정·진행 중)과 사유·번호를 묻는 것(완료·중복·기각)
const STATUS_PICK: { s: FeedbackStatus; form?: 'done' | 'duplicate' | 'declined'; title: string }[] = [
  { s: 'new', title: '결정을 지우고 신규로' },
  { s: 'planned', title: "고치기로 함 — 보낸 사람에게 '선정'으로 보임" },
  { s: 'in_progress', title: "고치기 시작 — 보낸 사람에게 '진행 중'으로 보이고, [개발 요청서]가 열려요" },
  { s: 'done', form: 'done', title: "반영함 — 보낸 사람에게 '완료'와 한 줄 답변이 보임" },
  { s: 'duplicate', form: 'duplicate', title: '같은 내용의 피드백 번호로 묶음' },
  { s: 'declined', form: 'declined', title: '하지 않음(사유 필수 — 보낸 사람이 봄)' },
];

// 단계 이동(11/04 태훈 "전후 관계를 따져 단계별로만") — 다음 단계와 한 단계 되돌리기만. 서버(user_feedback/router.py NEXT)와 같게
const FB_NEXT: Record<FeedbackStatus, FeedbackStatus[]> = {
  new: ['planned', 'duplicate', 'declined'],
  planned: ['in_progress', 'declined', 'new'],
  in_progress: ['done', 'planned'],
  done: ['in_progress'],
  duplicate: ['new'],
  declined: ['new'],
};
const FB_ORDER: FeedbackStatus[] = ['new', 'planned', 'in_progress', 'done'];
/** 되돌리기(흐름상 앞 단계로 · 중복·기각을 다시 열기) */
const isBack = (from: FeedbackStatus, to: FeedbackStatus) =>
  to === 'new' || (FB_ORDER.includes(from) && FB_ORDER.indexOf(to) < FB_ORDER.indexOf(from));

/** 상세 창 — 내용·오류 문맥·첨부·메모 + 상태 고르기 + (진행 중이면) 개발 요청서 */
function FeedbackDetail({ f, onClose, onChanged }: { f: FeedbackItem; onClose: () => void; onChanged: (p: Partial<FeedbackItem>) => void }) {
  const qc = useQueryClient();
  const me = useAuthStore((s) => s.user?.username ?? null);
  const [modal, setModal] = useState<null | 'declined' | 'duplicate' | 'done'>(null);
  const [handoff, setHandoff] = useState(false);
  const [note, setNote] = useState('');
  const [dup, setDup] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const act = useMutation({
    mutationFn: (b: { status: FeedbackStatus; note?: string; duplicate_of?: number | null }) => triageFeedback(f.id, b),
    onSuccess: (_r, b) => {
      setModal(null);
      // 처리자·시각도 같이 — 목록에서 빠진 건은 다시 안 읽혀 옛 처리자가 남았다(리뷰 L3)
      const undo = b.status === 'new';
      onChanged({ status: b.status, admin_note: undo ? null : (b.note?.trim() || null), duplicate_of: b.status === 'duplicate' ? b.duplicate_of ?? null : null,
        decided_by: undo ? null : me, decided_at: undo ? null : new Date().toISOString() });
      qc.invalidateQueries({ queryKey: ['user-feedback'] });
    },
    onError: (e) => {
      // 409 = 그 사이 다른 관리자가 단계를 바꿈 — 목록을 다시 읽고 창을 닫아, 다시 열면 지금 단계의 버튼이 보이게(리뷰)
      if (e instanceof ApiError && e.status === 409) {
        qc.invalidateQueries({ queryKey: ['user-feedback'] });
        setModal(null);
        void showError(e, '저장하지 못했습니다').then(onClose);
        return;
      }
      if (modal) setErr(e instanceof Error ? e.message : '저장하지 못했습니다'); else void showError(e, '저장하지 못했습니다');
    },
  });
  const openModal = (m: 'declined' | 'duplicate' | 'done') => { setErr(null); setNote(''); setDup(''); setModal(m); };
  const ctx = contextLine(f.context);
  return (
    <Modal isOpen onClose={onClose} title={`피드백 #${f.id}`} maxWidth="max-w-2xl">
      <div className="space-y-3" data-testid={`feedback-detail-${f.id}`}>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className={`rounded-full px-2 py-0.5 text-[11px] ${KIND[f.kind].cls}`}>{KIND[f.kind].label}</span>
          <span className="text-slate-400">{f.reporter ?? '탈퇴한 사용자'}{f.reporter_part ? ` · ${f.reporter_part}` : ''}</span>
          <span className="text-slate-500 tabular-nums">{fmt(f.created_at)}</span>
          {f.page && <span className="text-slate-500" title="보낸 화면">{f.page}{f.conversation_id ? ` · 대화 #${f.conversation_id}` : ''}</span>}
        </div>
        {f.body && <p className="text-sm text-slate-100 whitespace-pre-wrap break-words">{f.body}</p>}
        {ctx && <p className="text-xs text-rose-700 dark:text-rose-300 break-words" data-testid={`feedback-${f.id}-context`}>{ctx}</p>}
        {f.files.length > 0 && <FeedbackImages fid={f.id} files={f.files} />}
        {(f.admin_note || f.duplicate_of) && (
          <p className="text-xs text-slate-400 break-words">{f.duplicate_of ? `#${f.duplicate_of}와 같음` : ''}{f.duplicate_of && f.admin_note ? ' · ' : ''}{f.admin_note ?? ''}
            {f.decided_by ? <span className="text-slate-500"> — {f.decided_by}{f.decided_at ? ` · ${fmt(f.decided_at)}` : ''}</span> : null}</p>
        )}
        <div className="border-t border-slate-700 pt-3 space-y-2">
          <p className="text-xs text-slate-400">상태</p>
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="상태" data-testid={`feedback-${f.id}-status-pick`}>
            {STATUS_PICK.filter((x) => x.s === f.status || FB_NEXT[f.status].includes(x.s)).map((x) => {
              const cur = f.status === x.s;
              const back = !cur && isBack(f.status, x.s);
              return (
                <button key={x.s} disabled={cur || act.isPending} aria-pressed={cur} data-back={back || undefined}
                  title={back ? `이전 단계로 — ${x.title}` : x.title}
                  onClick={async () => {
                    // 완료에서 벗어나면 보낸 사람이 보던 완료 답변이 지워진다 — 확인 뒤에(리뷰 L4)
                    if (f.status === 'done' && !(await showConfirm('완료 답변이 지워지고 보낸 사람에게 바뀐 상태가 보여요.', { title: '완료 취소', confirmLabel: '바꾸기', tone: 'warning' }))) return;
                    if (x.form) openModal(x.form); else act.mutate({ status: x.s });
                  }}
                  className={clsx('rounded-full border px-3 py-1 text-xs', cur ? `${FB_STATUS[x.s].cls} border-transparent font-semibold` : 'border-slate-600 text-slate-300 hover:bg-slate-800 disabled:opacity-50')}>
                  {back ? '↩ ' : ''}{FB_STATUS[x.s].label}{x.form && !cur ? '…' : ''}
                </button>
              );
            })}
            {f.status === 'in_progress' && (
              <button className="ml-auto rounded-lg bg-indigo-600 px-3 py-1 text-xs font-semibold text-white hover:bg-indigo-500" data-testid={`feedback-${f.id}-handoff`}
                title="로컬 개발 환경(CLI)에 붙여 넣을 요청서 — 이미지·고객 정보는 담지 않아요" onClick={() => setHandoff(true)}>개발 요청서</button>
            )}
          </div>
        </div>
      </div>
      <HandoffModal isOpen={handoff} onClose={() => setHandoff(false)} text={handoffText(f)} id={f.id} />
      <FormModal isOpen={modal !== null} onClose={() => setModal(null)} zClass="z-[60]"
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
          {modal === 'declined' ? '기각 사유 (보낸 사람이 봅니다)' : modal === 'done' ? '무엇을 고쳤나 한 줄 (선택 — 보낸 사람이 봅니다, 반영 버전도)' : '메모 (선택)'}</label>
        <textarea id={`fb-note-${f.id}`} rows={3} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500}
          className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 resize-y" />
      </FormModal>
    </Modal>
  );
}

/** 요청서 보기 + 복사 — 폐쇄망은 http라 클립보드 API가 막힐 수 있어, 글을 골라 두고 복사 명령으로 한 번 더 */
function HandoffModal({ isOpen, onClose, text, id }: { isOpen: boolean; onClose: () => void; text: string; id: number }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [copied, setCopied] = useState<null | 'ok' | 'manual'>(null);
  useEffect(() => { if (isOpen) setCopied(null); }, [isOpen]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied('ok');
    } catch {
      ref.current?.select();   // http(폐쇄망)에서 clipboard API가 없을 때의 대체
      setCopied(document.execCommand('copy') ? 'ok' : 'manual');
    }
  };
  return (
    <Modal isOpen={isOpen} onClose={onClose} title={`개발 요청서 — 피드백 #${id}`} maxWidth="max-w-2xl" zClass="z-[60]">
      <div className="space-y-3" data-testid={`handoff-${id}`}>
        <p className="text-xs text-slate-400">로컬 개발 환경(CLI)에 그대로 붙여 넣으세요. 이미지는 담지 않아요 — 사용자 글에 고객 정보가 있으면 지우고 가져가세요.</p>
        <textarea ref={ref} readOnly value={text} rows={Math.min(14, text.split('\n').length + 1)} data-testid={`handoff-${id}-text`}
          className="w-full bg-slate-800 border border-slate-600 rounded-lg px-2.5 py-1.5 font-mono text-xs text-slate-200 focus:outline-none focus:border-indigo-500" />
        <div className="flex items-center justify-end gap-3">
          {copied === 'ok' && <span className="text-xs text-emerald-600 dark:text-emerald-400">복사했어요</span>}
          {copied === 'manual' && <span className="text-xs text-amber-600 dark:text-amber-400">자동 복사가 막혔어요 — 글을 골라 두었으니 Ctrl+C</span>}
          <button onClick={onClose} className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200">닫기</button>
          <button onClick={copy} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs text-white hover:bg-indigo-500">복사</button>
        </div>
      </div>
    </Modal>
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
