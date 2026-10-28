import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { Search, Flag, ScanSearch } from 'lucide-react';
import { addPreviewQuestion, previewSearch, type PreviewItem, type PreviewResult } from '../../api/eval';
import { flagKnowledgeForReview } from '../../api/knowledge';
import { showAlert, showError } from '../../store/useDialogStore';
import { useAuthStore } from '../../store/useAuthStore';
import { Modal } from '../ui/Modal';

/**
 * AI가 본 근거(10/28, 옛 평가 게이트 › 검색 미리보기를 바꿈 — 사용자 "개발하는 나조차 인지가 안 되면 아무도 못 쓴다", "검색 미리보기? 뭘 미리 봐").
 * 이 질문에 AI가 무엇을 보고 답하는지 — 채팅과 같은 검색(서버 build_chat_context, LLM 0). 독립 탭이 아니라 쓸 일이 생기는 자리에 붙는다:
 *   정정 요청 신고 카드(그 질문으로 바로 — 왜 그렇게 답했나) · 지식 상세(이 지식이 검색에 잡히나 — 몇 위인지 표시).
 * 결과 아래 "다음에 할 일" = 맞는 답 표시 → 시험지에 넣기(관리자) · 이상한 지식 → 정정 요청.
 */

const VERDICT_TONE: Record<string, string> = {
  answer: 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-200',
  list: 'border-indigo-300 bg-indigo-50 text-indigo-800 dark:border-indigo-500/40 dark:bg-indigo-500/10 dark:text-indigo-200',
  clarify: 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200',
  abstain: 'border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200',
  empty: 'border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200',
};
const ROW = 'grid grid-cols-[2rem_9.5rem_minmax(0,1fr)_4rem_8.5rem] items-center gap-x-3';

/** 본문 — namespace에 question을 물으면 AI가 보는 근거. highlightId = 지식 상세에서 연 그 지식(몇 위인지·안 잡히는지 맨 위에) */
/** canFlag = [이상해요](정정 요청) 가능 — 그 컬렉션 담당만(서버 flag-for-review가 담당 아니면 403, 10/28 리뷰) */
export function EvidenceView({ namespace, initialQuestion = '', highlightId, canFlag = false }: {
  namespace: string; initialQuestion?: string; highlightId?: number; canFlag?: boolean;
}) {
  const isAdmin = useAuthStore((st) => st.user?.role === 'admin');
  const [question, setQuestion] = useState(initialQuestion);
  const [res, setRes] = useState<PreviewResult | null>(null);
  const [gold, setGold] = useState<number | null>(null);
  const [flagged, setFlagged] = useState<Set<number>>(new Set());

  const run = useMutation({
    mutationFn: (q: string) => previewSearch(namespace, q),
    onSuccess: (r) => { setRes(r); setGold(null); setFlagged(new Set()); },
    onError: (e) => showError(e),
  });
  // 신고 카드에서 열면 그 질문으로 바로(한 번)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (initialQuestion.trim()) run.mutate(initialQuestion.trim()); }, []);
  const add = useMutation({
    mutationFn: () => addPreviewQuestion(res!.namespace, res!.question, gold!),
    onSuccess: (r) => showAlert(r.created
      ? `시험지에 넣었어요(${r.qid}) — 다음 측정부터 이 질문이 맞는지 채점합니다(잠근 칸: 고칠 방법을 만들 땐 안 봄)`
      : `이미 시험지에 있는 질문이에요(${r.qid})`),
    onError: (e) => showError(e),
  });
  const flag = useMutation({
    mutationFn: (id: number) => flagKnowledgeForReview(id, res!.namespace, res!.question),
    onSuccess: (_, id) => setFlagged((s) => new Set(s).add(id)),
    onError: (e) => showError(e),
  });

  const policies = res?.items.filter((x) => x.kind === 'policy') ?? [];
  const goldName = policies.find((p) => p.logical_id === gold)?.name;
  const mine = highlightId != null && res ? res.items.find((x) => x.kind === 'knowledge' && x.id === highlightId) : undefined;

  return (
    <div className="space-y-3" data-testid="evidence-view">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (question.trim()) run.mutate(question.trim()); }}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} aria-label="질문" autoFocus={!initialQuestion}
          placeholder={highlightId != null ? '이 지식을 찾을 만한 질문을 적어 보세요' : `${namespace}에 물어볼 질문`}
          className="flex-1 min-w-0 rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500" />
        <button type="submit" disabled={!question.trim() || run.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50">
          <Search className="w-4 h-4" />{run.isPending ? '보는 중…' : res ? '다시 보기' : '근거 보기'}
        </button>
      </form>
      {!res && !run.isPending && (
        <p className="text-xs text-slate-500">채팅에 이 질문을 했을 때 AI가 무엇을 보고 답하는지 그대로 보여 줘요(답은 만들지 않음). 질문을 바꿔 가며 볼 수 있어요.</p>
      )}

      {res && (
        <>
          {highlightId != null && (
            <p className={clsx('rounded-lg border px-3 py-2 text-sm', mine
              ? 'border-indigo-300 bg-indigo-50 text-indigo-800 dark:border-indigo-500/40 dark:bg-indigo-500/10 dark:text-indigo-200'
              : 'border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200')} data-testid="evidence-mine">
              {mine ? `이 지식: 지식 ${mine.rank}위 · ${mine.adopted ? 'AI에게 넘어감' : '점수가 기준보다 낮아 AI에게 안 넘어감'}`
                : '이 지식은 이 질문으로 안 잡혀요 — 질문 표현을 바꿔 보거나, 지식 내용에 질문에 쓰는 말이 있는지 보세요'}
            </p>
          )}
          <div className={clsx('rounded-xl border px-4 py-3', VERDICT_TONE[res.verdict.key])} data-testid="preview-verdict">
            <p className="text-[11px] opacity-70">AI가 한 일</p>
            <p className="text-base font-semibold">{res.verdict.name}</p>
            {res.verdict.detail && <p className="text-xs mt-0.5">{res.verdict.detail}</p>}
            {res.verdict.options.length > 0 && <p className="text-xs mt-1">선택지: {res.verdict.options.join(' · ')}</p>}
            <p className="text-xs mt-1 opacity-80">
              용어 {res.terms.length ? res.terms.join(' · ') : '없음'} · 근거 정책 {policies.length} · 지식 {res.items.filter((x) => x.kind === 'knowledge').length}
              (넘어감 {res.items.filter((x) => x.kind === 'knowledge' && x.adopted).length}) · 참고표·개요 {res.items.filter((x) => x.kind === 'ref').length}
            </p>
          </div>

          <div className="rounded-xl border border-slate-700 overflow-x-auto">
            <div className={clsx(ROW, 'px-4 py-1.5 text-[11px] text-slate-500 bg-slate-900/60 min-w-[680px]')}>
              <span>순위</span><span>어떻게 찾았나</span><span>AI가 본 것</span><span>점수</span><span />
            </div>
            {res.items.length === 0 && <p className="px-4 py-3 text-sm text-slate-500">AI가 본 근거가 없어요.</p>}
            {res.items.map((it, i) => <Row key={i} it={it} gold={gold} setGold={setGold} canPick={isAdmin} canFlag={canFlag} mine={it.kind === 'knowledge' && it.id === highlightId}
              flagged={it.id != null && flagged.has(it.id)} onFlag={(id) => flag.mutate(id)}
              pickable={it.kind === 'policy' && res.items.findIndex((x) => x.kind === 'policy' && x.logical_id === it.logical_id) === i} />)}
          </div>

          <div className="rounded-lg border border-slate-700 bg-slate-800 px-4 py-3 space-y-2" data-testid="evidence-next">
            <p className="text-xs font-semibold text-slate-300">다음에 할 일</p>
            <ul className="text-xs text-slate-400 space-y-1 list-disc pl-4">
              <li>맞는 근거가 아래쪽에 있거나 없으면 — 지식·정책 내용에 질문에 쓰는 말이 있는지 보고 고치기(지식 문서 · 정책서)</li>
              {canFlag && <li>엉뚱한 지식이 위에 있으면 — 그 줄의 [이상해요] → 정정 요청으로</li>}
              {isAdmin && <li>이 질문을 계속 지켜보려면 — 맞는 정책에 [이게 맞는 답] 표시 → [시험지에 넣기](다음 측정부터 이 질문이 맞는지 채점)</li>}
            </ul>
            {isAdmin && (
              <div className="flex flex-wrap items-center gap-3 pt-1">
                <span className="text-sm text-slate-300 min-w-0 flex-1">
                  {goldName ? <>맞는 답: <b className="text-slate-100">{goldName}</b></> : policies.length ? '맞는 답인 정책을 표시하면 넣을 수 있어요' : '정책이 안 잡혀서 시험지에 넣을 수 없어요'}
                </span>
                <button onClick={() => add.mutate()} disabled={gold == null || add.isPending} data-testid="preview-add"
                  className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500 disabled:opacity-50">시험지에 넣기</button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function Row({ it, gold, setGold, flagged, onFlag, pickable, canPick, canFlag, mine }: {
  it: PreviewItem; gold: number | null; setGold: (v: number) => void; flagged: boolean; onFlag: (id: number) => void;
  pickable: boolean; canPick: boolean; canFlag: boolean; mine: boolean;
}) {
  return (
    <div className={clsx(ROW, 'px-4 py-2 text-sm border-t border-slate-700/70 min-w-[680px]',
      it.kind === 'knowledge' && it.adopted === false && !mine && 'opacity-60',
      mine && 'bg-indigo-50 ring-1 ring-inset ring-indigo-400 dark:bg-indigo-500/10 dark:ring-indigo-400')}
      data-testid={`preview-item-${it.kind}`}>
      <span className="text-xs text-slate-500 tabular-nums">{it.rank ?? '—'}</span>
      <span className="text-xs text-slate-400">{it.how}</span>
      <span className="min-w-0">
        <span className="block text-slate-100 break-words">{mine && <b className="mr-1 text-indigo-600 dark:text-indigo-300">이 지식</b>}{it.name}</span>
        {(it.path || it.label) && <span className="block text-xs text-slate-500 line-clamp-2 break-words">{[it.path, it.label].filter(Boolean).join(' · ')}</span>}
      </span>
      <span className="text-xs text-slate-300 tabular-nums">
        {it.score != null ? it.score.toFixed(3) : '—'}
        {it.kind === 'knowledge' && <span className={clsx('block text-[11px]', it.adopted ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500')}>{it.adopted ? '넘어감' : '기준 미달'}</span>}
      </span>
      <span>
        {canPick && pickable && it.logical_id != null && (
          <label className="inline-flex items-center gap-1 text-xs text-slate-300 cursor-pointer" title="이 질문의 맞는 답이 이 정책 — 시험지에 넣을 때 정답으로">
            <input type="radio" name="preview-gold" checked={gold === it.logical_id} onChange={() => setGold(it.logical_id!)} />이게 맞는 답
          </label>
        )}
        {canFlag && it.kind === 'knowledge' && it.id != null && (
          <button onClick={() => onFlag(it.id!)} disabled={flagged} title={flagged ? '정정 요청으로 보냄' : '이 질문에 이 지식이 나오면 이상해요 — 정정 요청으로(검색 노이즈)'}
            className={clsx('inline-flex items-center gap-1 text-xs', flagged ? 'text-rose-600 dark:text-rose-400' : 'text-slate-400 hover:text-rose-600 dark:hover:text-rose-400')}>
            <Flag className="w-3 h-3" />{flagged ? '보냄' : '이상해요'}
          </button>
        )}
      </span>
    </div>
  );
}

/** [AI가 본 근거 ▸] 버튼 + 창 — 신고 카드·지식 상세에 붙는다 */
export function EvidenceButton({ namespace, question = '', highlightId, label = 'AI가 본 근거', canFlag = false }: {
  namespace: string; question?: string; highlightId?: number; label?: string; canFlag?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} data-testid="evidence-open"
        title="채팅에 이 질문을 했을 때 AI가 무엇을 보고 답하는지(답은 만들지 않음)"
        className="inline-flex items-center gap-1 text-xs text-indigo-600 hover:underline dark:text-indigo-400">
        <ScanSearch className="w-3.5 h-3.5" />{label} ▸
      </button>
      <Modal isOpen={open} onClose={() => setOpen(false)} title={question ? `AI가 본 근거 — ${question}` : 'AI가 본 근거'} maxWidth="max-w-4xl">
        {open && <EvidenceView namespace={namespace} initialQuestion={question} highlightId={highlightId} canFlag={canFlag} />}
      </Modal>
    </>
  );
}
