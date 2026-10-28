import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { Search, Flag } from 'lucide-react';
import { addPreviewQuestion, previewSearch, type PreviewItem, type PreviewResult } from '../../api/eval';
import { flagKnowledgeForReview } from '../../api/knowledge';
import { showAlert, showError } from '../../store/useDialogStore';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';

/**
 * 평가 게이트 › 검색 미리보기(10/27 밤, 옛 즉석 질의를 바꿈) — 질문 하나를 채팅과 같은 검색으로(서버 build_chat_context, LLM 0).
 * 채팅이 할 일(되묻기·근거 없음·목록 답·답) → 용어 → 문맥 후보(갈래·순위·점수·채택). 정답 정책을 골라 [시험지에 추가] = 실사용 층, 다음 측정부터.
 * 지식 줄의 [이상해요]는 옛 즉석 질의와 같이 정정 요청으로(검색 노이즈). 지식 고치기는 지식 문서 화면에서.
 */

const VERDICT_TONE: Record<string, string> = {
  answer: 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-200',
  list: 'border-indigo-300 bg-indigo-50 text-indigo-800 dark:border-indigo-500/40 dark:bg-indigo-500/10 dark:text-indigo-200',
  clarify: 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200',
  abstain: 'border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200',
  empty: 'border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200',
};
const ROW = 'grid grid-cols-[2rem_10rem_minmax(0,1fr)_4.5rem_5.5rem] items-center gap-x-3';

export function SearchPreview() {
  const { selectedNs } = useNamespaceAccess();
  const [question, setQuestion] = useState('');
  const [res, setRes] = useState<PreviewResult | null>(null);
  const [gold, setGold] = useState<number | null>(null);
  const [flagged, setFlagged] = useState<Set<number>>(new Set());
  // 컬렉션이 바뀌면 앞 결과를 지운다 — 남겨 두면 새 컬렉션 이름으로 시험지에 들어간다(옛 즉석 질의 리뷰 M3와 같은 이유)
  useEffect(() => { setRes(null); setGold(null); setFlagged(new Set()); }, [selectedNs]);

  const run = useMutation({
    mutationFn: () => previewSearch(selectedNs!, question.trim()),
    onSuccess: (r) => { setRes(r); setGold(null); setFlagged(new Set()); },
    onError: (e) => showError(e),
  });
  const add = useMutation({
    mutationFn: () => addPreviewQuestion(res!.namespace, res!.question, gold!),
    onSuccess: (r) => showAlert(r.created ? `시험지에 넣었어요(${r.qid}) — 다음 측정부터 실사용 층으로 잽니다` : `이미 시험지에 있는 질문이에요(${r.qid})`),
    onError: (e) => showError(e),
  });
  const flag = useMutation({
    mutationFn: (id: number) => flagKnowledgeForReview(id, res!.namespace, res!.question),
    onSuccess: (_, id) => setFlagged((s) => new Set(s).add(id)),
    onError: (e) => showError(e),
  });

  if (!selectedNs) return <p className="text-sm text-slate-500">왼쪽에서 컬렉션을 고르면 그 컬렉션 채팅과 같은 검색을 돌려 봐요.</p>;
  const policies = res?.items.filter((x) => x.kind === 'policy') ?? [];
  const goldName = policies.find((p) => p.logical_id === gold)?.name;

  return (
    <div className="space-y-3" data-testid="search-preview">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (question.trim()) run.mutate(); }}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder={`${selectedNs}에 물어볼 질문`} aria-label="질문"
          className="flex-1 min-w-0 rounded-lg border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-indigo-500" />
        <button type="submit" disabled={!question.trim() || run.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white hover:bg-indigo-500 disabled:opacity-50">
          <Search className="w-4 h-4" />{run.isPending ? '검색 중…' : '채팅처럼 검색'}
        </button>
      </form>

      {res && (
        <>
          <div className={clsx('rounded-xl border px-4 py-3', VERDICT_TONE[res.verdict.key])} data-testid="preview-verdict">
            <p className="text-[11px] opacity-70">채팅이 할 일</p>
            <p className="text-base font-semibold">{res.verdict.name}</p>
            {res.verdict.detail && <p className="text-xs mt-0.5">{res.verdict.detail}</p>}
            {res.verdict.options.length > 0 && <p className="text-xs mt-1">선택지: {res.verdict.options.join(' · ')}</p>}
          </div>
          {/* 단계별 한 칸(목업 검색 미리보기) — 용어 보강 · 근거 판정 · 답 모양 */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs" data-testid="preview-steps">
            <div className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 min-w-0">
              <p className="text-[11px] text-slate-500">용어 보강</p>
              <p className="text-slate-100 break-words">{res.terms.length ? res.terms.join(' · ') : '질문에 나온 용어 없음'}</p>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 min-w-0" title="근거 판정에 쓰는 신호(채팅과 같은 값)">
              <p className="text-[11px] text-slate-500">근거 판정</p>
              <p className="text-slate-100">{res.verdict.key === 'abstain' ? 'LLM 없이 "찾지 못했습니다"' : '답함(근거 있음)'}</p>
              <p className="text-slate-500">지식 채택 {Number(res.signals.adopted ?? 0)} · 정책 서술 최고 {Number(res.signals.top_narrative ?? 0).toFixed(2)}</p>
            </div>
            <div className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 min-w-0">
              <p className="text-[11px] text-slate-500">답 모양</p>
              <p className="text-slate-100">{res.verdict.key === 'clarify' ? '되묻기' : res.verdict.key === 'list' ? '목록 답' : '되묻기 없음 · 목록 아님'}</p>
            </div>
          </div>
          <p className="text-xs text-slate-400" data-testid="preview-counts">
            찾은 근거 정책 {res.items.filter((x) => x.kind === 'policy').length} · 지식 {res.items.filter((x) => x.kind === 'knowledge').length}
            (채택 {res.items.filter((x) => x.kind === 'knowledge' && x.adopted).length}) · 참고표·개요 {res.items.filter((x) => x.kind === 'ref').length}
            · 공통코드·칼럼 {res.items.filter((x) => x.kind === 'refdata').length} · 문맥 {res.context_chars.toLocaleString()}자
          </p>

          <div className="rounded-xl border border-slate-700 overflow-x-auto">
            <div className={clsx(ROW, 'px-4 py-1.5 text-[11px] text-slate-500 bg-slate-900/60 min-w-[680px]')}>
              <span>순위</span><span>어떻게 찾았나</span><span>찾은 것</span><span>점수</span><span>정답 · 신고</span>
            </div>
            {res.items.length === 0 && <p className="px-4 py-3 text-sm text-slate-500">찾은 것이 없어요.</p>}
            {res.items.map((it, i) => <Row key={i} it={it} gold={gold} setGold={setGold} flagged={it.id != null && flagged.has(it.id)} onFlag={(id) => flag.mutate(id)}
              pickable={it.kind === 'policy' && res.items.findIndex((x) => x.kind === 'policy' && x.logical_id === it.logical_id) === i} />)}
          </div>

          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-700 bg-slate-800 px-4 py-2.5">
            <span className="text-sm text-slate-300 min-w-0 flex-1">
              {goldName ? <>정답 정책: <b className="text-slate-100">{goldName}</b></> : policies.length ? '정답인 정책을 고르면 시험지에 넣을 수 있어요' : '정책이 안 걸렸어요 — 정답 정책이 없는 질문은 시험지에 넣을 수 없어요'}
            </span>
            <button onClick={() => add.mutate()} disabled={gold == null || add.isPending} data-testid="preview-add"
              title="실사용 층(잠근 시험지)에 넣어 다음 측정부터 잽니다 — 이 질문으로 고치고 같은 질문으로 채점하지 않게 진단엔 안 씀"
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-500 disabled:opacity-50">시험지에 추가</button>
          </div>
        </>
      )}
    </div>
  );
}

function Row({ it, gold, setGold, flagged, onFlag, pickable }: {
  it: PreviewItem; gold: number | null; setGold: (v: number) => void; flagged: boolean; onFlag: (id: number) => void; pickable: boolean;
}) {
  return (
    <div className={clsx(ROW, 'px-4 py-2 text-sm border-t border-slate-700/70 min-w-[680px]', it.kind === 'knowledge' && it.adopted === false && 'opacity-60')}
      data-testid={`preview-item-${it.kind}`}>
      <span className="text-xs text-slate-500 tabular-nums">{it.rank ?? '—'}</span>
      <span className="text-xs text-slate-400">{it.how}</span>
      <span className="min-w-0">
        <span className="block text-slate-100 break-words">{it.name}</span>
        {(it.path || it.label) && <span className="block text-xs text-slate-500 line-clamp-2 break-words">{[it.path, it.label].filter(Boolean).join(' · ')}</span>}
      </span>
      <span className="text-xs text-slate-300 tabular-nums">
        {it.score != null ? it.score.toFixed(3) : '—'}
        {it.kind === 'knowledge' && <span className={clsx('block text-[11px]', it.adopted ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500')}>{it.adopted ? '채택' : '기준 미달'}</span>}
      </span>
      <span>
        {pickable && it.logical_id != null && (
          <label className="inline-flex items-center gap-1 text-xs text-slate-300 cursor-pointer">
            <input type="radio" name="preview-gold" checked={gold === it.logical_id} onChange={() => setGold(it.logical_id!)} />정답
          </label>
        )}
        {it.kind === 'knowledge' && it.id != null && (
          <button onClick={() => onFlag(it.id!)} disabled={flagged} title={flagged ? '정정 요청으로 보냄' : '이 결과가 이상해요 — 정정 요청으로(검색 노이즈)'}
            className={clsx('inline-flex items-center gap-1 text-xs', flagged ? 'text-rose-600 dark:text-rose-400' : 'text-slate-400 hover:text-rose-600 dark:hover:text-rose-400')}>
            <Flag className="w-3 h-3" />{flagged ? '보냄' : '이상해요'}
          </button>
        )}
      </span>
    </div>
  );
}
