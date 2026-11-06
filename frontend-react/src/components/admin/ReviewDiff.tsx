import { useMemo } from 'react';
import { diffLines, sameText, splitRows, wordDiff, type SplitRow } from '../../utils/textDiff';

/* 좌우 줄 차이(승인 대기 비교 · 동기화로 바뀐 페이지, 11/05 공용으로 뺌) */
/** 기존(왼쪽) ↔ 신규(오른쪽) 좌우 차이 — 같은 줄은 흐리게, 고친 줄은 바뀐 낱말 강조, 한쪽에만 있는 줄은 빨강·초록 */
export function ReviewDiff({ before, after, loading, leftLabel = '기존 지식', rightLabel = '신규 등록', leftShort = '기존', rightShort = '신규', emptyText = '비교할 이전 내용이 없어요.' }: {
  before: string | null; after: string; loading: boolean; leftLabel?: string; rightLabel?: string; leftShort?: string; rightShort?: string; emptyText?: string;
}) {
  const lines = useMemo(() => (before == null ? null : diffLines(before, after)), [before, after]);
  // 고친 줄의 낱말 차이도 여기서 한 번에(창의 다른 상태가 바뀔 때마다 다시 계산하지 않게 — 리뷰 L7)
  const rows = useMemo(() => (lines ? splitRows(lines).map((r) => ({ r, w: r.kind === 'change' ? wordDiff(r.left!, r.right!) : null })) : null), [lines]);
  if (loading) return <div className="text-sm text-slate-500 py-4 text-center">불러오는 중...</div>;
  if (before == null) return <p className="text-sm text-slate-500 py-4 text-center">{emptyText}</p>;
  if (rows == null || lines == null) return <p className="text-sm text-slate-500 py-4 text-center">너무 길어 줄 비교를 못 해요 — [원문]으로 보세요.</p>;
  const add = lines.filter((l) => l.kind === 'add').length;
  const del = lines.filter((l) => l.kind === 'del').length;
  return (
    <div className="space-y-1.5" data-testid="review-diff">
      <p className="text-xs text-slate-400" data-testid="review-diff-summary">
        {sameText(before, after)
          ? <span className="font-semibold text-emerald-700 dark:text-emerald-300">내용 같음(공백·줄바꿈만 다름)</span>
          : <><span className="text-rose-700 dark:text-rose-300">−{del}줄 {leftShort}에만</span> · <span className="text-emerald-700 dark:text-emerald-300">+{add}줄 {rightShort}에만</span> · 같은 줄 {lines.length - add - del}
            {rows.some((x) => x.w?.gaveUp) && <span className="ml-1 text-slate-500" title="한 줄이 너무 길어 낱말 단위 비교를 건너뛰고 줄 전체를 바뀜으로 표시">· 긴 줄은 낱말 비교 생략</span>}</>}
      </p>
      <div className="h-[28rem] overflow-auto rounded-lg border border-slate-700 bg-slate-900 font-mono text-xs leading-5">
        <div className="sticky top-0 z-10 grid grid-cols-2 border-b border-slate-700 bg-slate-800 text-[11px] font-sans font-medium">
          <span className="px-2 py-1 text-indigo-600 dark:text-indigo-400">{leftLabel}</span>
          <span className="border-l border-slate-700 px-2 py-1 text-amber-700 dark:text-amber-400">{rightLabel}</span>
        </div>
        {rows.map(({ r, w }, i) => <SplitLine key={i} r={r} w={w} />)}
      </div>
    </div>
  );
}

function SplitLine({ r, w }: { r: SplitRow; w: ReturnType<typeof wordDiff> | null }) {
  const cell = (text: string | null, side: 'left' | 'right') => {
    const changed = side === 'left' ? r.kind === 'del' || r.kind === 'change' : r.kind === 'add' || r.kind === 'change';
    const tone = text == null ? 'bg-slate-800/60'
      : !changed ? 'text-slate-400'
      : side === 'left' ? 'bg-rose-50 text-rose-900 dark:bg-rose-500/10 dark:text-rose-200' : 'bg-emerald-50 text-emerald-900 dark:bg-emerald-500/10 dark:text-emerald-200';
    const mark = side === 'left' ? 'bg-rose-200 dark:bg-rose-500/40' : 'bg-emerald-200 dark:bg-emerald-500/40';
    return (
      <div className={`flex min-w-0 ${side === 'right' ? 'border-l border-slate-700' : ''} ${tone}`} data-side={side} data-kind={text == null ? 'empty' : changed ? r.kind : 'same'}>
        <span className="inline-block w-5 flex-shrink-0 select-none text-center opacity-70">{text == null ? '' : changed ? (side === 'left' ? '−' : '+') : ' '}</span>
        <span className="min-w-0 whitespace-pre-wrap break-all pr-2">
          {text == null ? ' ' : w ? (side === 'left' ? w.left : w.right).map(([t, c], k) => c ? <mark key={k} className={`rounded-sm text-inherit ${mark}`}>{t}</mark> : <span key={k}>{t}</span>) : (text || ' ')}
        </span>
      </div>
    );
  };
  return <div className="grid grid-cols-2">{cell(r.left, 'left')}{cell(r.right, 'right')}</div>;
}

