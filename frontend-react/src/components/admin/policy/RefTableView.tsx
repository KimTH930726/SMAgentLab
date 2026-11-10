import { useEffect, useMemo, useRef, useState } from 'react';
import { clsx } from 'clsx';
import { useQuery } from '@tanstack/react-query';
import { getRefTable, type RefTableDetail, type RefTableFocus } from '../../../api/policy';
import { Modal } from '../../ui/Modal';

/**
 * 참고표 한 장(11/05 태훈 "보기 너무 어렵다" — 시안 승인). 참고표 탭과 원문 확인 [참고표 보기] 창이 같이 쓴다.
 * 머리 고정 · 줄무늬 · 결정 열(●, 결과를 정하는 조건) 남색 · 결과 열(→) 초록 · 표 안 검색(맞는 줄만, 글자 강조).
 * # = 원표 행 번호(row_nos). focus = 원문 확인 카드가 짚은 문제 행·칸(11/10 시안 승인): 첫 문제 행으로 스크롤 · 행 노랑 · 칸 빨간 테두리 ·
 * ◀ ▶ 이동 · [문제 행만](위아래 1행 포함).
 */
export function RefTableView({ table, focus, note }: { table: RefTableDetail; focus?: RefTableFocus | null; note?: string }) {
  const [q, setQ] = useState('');
  const [onlyFocus, setOnlyFocus] = useState(false);
  const [at, setAt] = useState(0);
  const rowRefs = useRef(new Map<number, HTMLTableRowElement>());
  const needle = q.trim().toLowerCase();
  const focusRows = useMemo(() => (focus?.current ? [...focus.rows].sort((a, b) => a - b) : []), [focus]);
  const focusSet = useMemo(() => new Set(focusRows), [focusRows]);
  const focusCols = useMemo(() => new Set(focus?.current ? focus.columns : []), [focus]);
  const rows = useMemo(() => {
    const all = table.rows.map((r, i) => ({ r, n: table.row_nos?.[i] ?? i + 1 }));
    // [문제 행만] — 문제 행과 위아래 1행(앞뒤 맥락)
    const near = onlyFocus ? new Set(all.flatMap((x, i) => (focusSet.has(x.n) ? [all[i - 1]?.n, x.n, all[i + 1]?.n] : []))) : null;
    return all.filter(({ r, n }) => (!near || near.has(n))
      && (!needle || table.columns.some((c) => String(r[c.name] ?? '').toLowerCase().includes(needle))));
  }, [table, needle, onlyFocus, focusSet]);
  const target = focusRows[at];
  useEffect(() => {   // 창이 열리면 첫 문제 행으로, ◀ ▶로 다음 문제 행으로 — 표 안 검색 중엔 끌고 가지 않는다(리뷰 11/10)
    if (target != null) rowRefs.current.get(target)?.scrollIntoView({ block: 'center' });
  }, [target, onlyFocus]);
  const targetHidden = target != null && !rows.some((x) => x.n === target);
  const tone = (c: RefTableDetail['columns'][number]) => c.decisive ? 'decisive' : c.role === 'result' ? 'result' : null;
  return (
    <div className="space-y-2" data-testid="ref-table-view">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-100 break-words">{table.title} <span className="font-normal text-slate-500">v{table.version}</span></h3>
          {table.category_path.length > 0 && <p className="text-xs text-slate-500 break-words">{table.category_path.join(' › ')}</p>}
        </div>
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="표 안 검색" aria-label="표 안 검색"
          className="w-48 bg-slate-900 border border-slate-600 rounded-lg px-2.5 py-1 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500" />
      </div>
      {note && (
        <p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">⚠ {note}</p>
      )}
      {focus && !focus.current && (
        <p className="text-xs text-slate-500" title="문제를 적은 뒤 표가 새 버전이 돼 행 번호가 맞는지 몰라요">표가 새 버전이라 문제 행은 표시하지 않아요.</p>
      )}
      {focusRows.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="ref-table-focus-bar">
          <span className="font-medium text-amber-700 dark:text-amber-300">문제 행 {focusRows.length}</span>
          <div className="inline-flex rounded-lg border border-slate-600 overflow-hidden">
            {([[true, '문제 행만'], [false, '전체']] as const).map(([v, label]) => (
              <button key={label} type="button" onClick={() => setOnlyFocus(v)} aria-pressed={onlyFocus === v}
                title={v ? '문제 행과 위아래 1행만' : '표 전체'}
                className={clsx('px-2 py-0.5', onlyFocus === v ? 'bg-slate-700 text-slate-100' : 'text-slate-400 hover:text-slate-200')}>{label}</button>
            ))}
          </div>
          <button type="button" aria-label="이전 문제 행" disabled={at === 0} onClick={() => setAt(at - 1)}
            className="px-1.5 text-slate-300 disabled:text-slate-600">◀</button>
          <span className="tabular-nums text-slate-400">{at + 1}/{focusRows.length} · 원표 {target}행{targetHidden && ' (검색에 가려짐)'}</span>
          <button type="button" aria-label="다음 문제 행" disabled={at >= focusRows.length - 1} onClick={() => setAt(at + 1)}
            className="px-1.5 text-slate-300 disabled:text-slate-600">▶</button>
        </div>
      )}
      <div className="max-h-[65vh] overflow-auto rounded-lg border border-slate-700">
        <table className="text-[13px] border-collapse min-w-full">
          <thead>
            <tr>
              <th title="원표 행 번호" className="sticky top-0 z-10 bg-slate-800 px-2 py-2 text-right font-normal text-slate-500 border-b-2 border-slate-600">#</th>
              {table.columns.map((c) => (
                <th key={c.name} title={tone(c) === 'decisive' ? '결정 열 — 결과를 정하는 조건' : tone(c) === 'result' ? '결과' : c.role === 'condition' ? '조건' : undefined}
                  className={clsx('sticky top-0 z-10 px-2.5 py-2 text-left font-semibold whitespace-nowrap border-b-2',
                    tone(c) === 'decisive' ? 'bg-indigo-100 text-indigo-900 border-indigo-400 dark:bg-indigo-950 dark:text-indigo-200 dark:border-indigo-500'
                      : tone(c) === 'result' ? 'bg-emerald-100 text-emerald-900 border-emerald-400 dark:bg-emerald-950 dark:text-emerald-200 dark:border-emerald-500'
                        : 'bg-slate-800 text-slate-200 border-slate-600')}>
                  {tone(c) === 'decisive' && <span className="mr-1">●</span>}{tone(c) === 'result' && <span className="mr-1">→</span>}{c.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ r, n }, i) => {
              const bad = focusSet.has(n);
              return (
                // key·스크롤 대상은 행 번호 + 순번 — 원표 행 번호는 DB에서 유일이 보장되지 않는다(같은 번호면 첫 행으로 스크롤)
                <tr key={`${n}-${i}`} ref={(el) => { if (el && !rowRefs.current.has(n)) rowRefs.current.set(n, el); else if (!el) rowRefs.current.delete(n); }}
                  data-testid={bad ? `ref-table-focus-row-${n}` : undefined}
                  className={clsx('border-t border-slate-700/60',
                    bad ? 'bg-amber-100 dark:bg-amber-500/15' : clsx('hover:bg-indigo-50 dark:hover:bg-indigo-500/10', i % 2 === 1 && 'bg-slate-800/50'))}>
                  <td className={clsx('px-2 py-1.5 text-right tabular-nums align-top',
                    bad ? 'border-l-4 border-amber-500 font-semibold text-amber-800 dark:border-amber-400 dark:text-amber-200' : 'text-slate-500',
                    n === target && 'underline')}>{n}</td>
                  {table.columns.map((c) => (
                    <td key={c.name} className={clsx('px-2.5 py-1.5 align-top whitespace-pre-wrap min-w-[6rem]',
                      bad && focusCols.has(c.name) && 'ring-2 ring-inset ring-rose-500 dark:ring-rose-400',
                      tone(c) === 'result' ? 'font-medium text-emerald-800 dark:text-emerald-300' : tone(c) === 'decisive' ? 'text-slate-100' : 'text-slate-300')}>
                      <Hit text={String(r[c.name] ?? '')} needle={needle} />
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && <p className="px-3 py-6 text-center text-xs text-slate-500">"{q}"이(가) 든 줄이 없어요.</p>}
      </div>
      <p className="text-[11px] text-slate-500">
        {rows.length !== table.rows.length ? `${rows.length} / ${table.rows.length}행` : `${table.rows.length}행`} ·
        <span className="ml-1 text-indigo-700 dark:text-indigo-300">● 결정 열</span> ·
        <span className="ml-1 text-emerald-700 dark:text-emerald-300">→ 결과</span>
      </p>
    </div>
  );
}

/** 검색어가 나오는 곳마다 강조(대소문자 무시) */
function Hit({ text, needle }: { text: string; needle: string }) {
  if (!needle) return <>{text}</>;
  const low = text.toLowerCase();
  const parts: React.ReactNode[] = [];
  let from = 0;
  for (let at = low.indexOf(needle); at >= 0; at = low.indexOf(needle, at + needle.length)) {
    parts.push(text.slice(from, at), <mark key={at} className="rounded bg-amber-200 px-0.5 text-amber-950 dark:bg-amber-500/40 dark:text-amber-50">{text.slice(at, at + needle.length)}</mark>);
    from = at + needle.length;
  }
  parts.push(text.slice(from));
  return <>{parts}</>;
}

/** 원문 확인 [참고표 보기] — 참고표 id(그 버전)로 창 하나. focus·note = 그 카드가 짚은 문제 행·칸과 설명 */
export function RefTableModal({ namespace, id, focus, note, onClose }: {
  namespace: string; id: number; focus?: RefTableFocus | null; note?: string; onClose: () => void;
}) {
  const { data, isLoading, error } = useQuery({ queryKey: ['ref-table', namespace, id], queryFn: () => getRefTable(namespace, id), staleTime: 60_000 });
  return (
    <Modal isOpen onClose={onClose} title="참고표" maxWidth="max-w-6xl">
      {error ? <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{(error as Error).message}</p>
        : isLoading || !data ? <p className="text-xs text-slate-500">표를 불러오는 중…</p> : <RefTableView table={data} focus={focus} note={note} />}
    </Modal>
  );
}
