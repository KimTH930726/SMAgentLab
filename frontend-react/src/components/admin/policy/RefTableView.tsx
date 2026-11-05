import { useMemo, useState } from 'react';
import { clsx } from 'clsx';
import { useQuery } from '@tanstack/react-query';
import { getRefTable, type RefTableDetail } from '../../../api/policy';
import { Modal } from '../../ui/Modal';

/**
 * 참고표 한 장(11/05 태훈 "보기 너무 어렵다" — 시안 승인). 참고표 탭과 원문 확인 [참고표 보기] 창이 같이 쓴다.
 * 머리 고정 · 줄무늬 · 결정 열(●, 결과를 정하는 조건) 남색 · 결과 열(→) 초록 · 표 안 검색(맞는 줄만, 글자 강조).
 */
export function RefTableView({ table }: { table: RefTableDetail }) {
  const [q, setQ] = useState('');
  const needle = q.trim().toLowerCase();
  const rows = useMemo(() => table.rows
    .map((r, i) => ({ r, n: i + 1 }))
    .filter(({ r }) => !needle || table.columns.some((c) => String(r[c.name] ?? '').toLowerCase().includes(needle))), [table, needle]);
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
      <div className="max-h-[65vh] overflow-auto rounded-lg border border-slate-700">
        <table className="text-[13px] border-collapse min-w-full">
          <thead>
            <tr>
              <th className="sticky top-0 z-10 bg-slate-800 px-2 py-2 text-right font-normal text-slate-500 border-b-2 border-slate-600">#</th>
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
            {rows.map(({ r, n }, i) => (
              <tr key={n} className={clsx('border-t border-slate-700/60 hover:bg-indigo-50 dark:hover:bg-indigo-500/10', i % 2 === 1 && 'bg-slate-800/50')}>
                <td className="px-2 py-1.5 text-right text-slate-500 tabular-nums align-top">{n}</td>
                {table.columns.map((c) => (
                  <td key={c.name} className={clsx('px-2.5 py-1.5 align-top whitespace-pre-wrap min-w-[6rem]',
                    tone(c) === 'result' ? 'font-medium text-emerald-800 dark:text-emerald-300' : tone(c) === 'decisive' ? 'text-slate-100' : 'text-slate-300')}>
                    <Hit text={String(r[c.name] ?? '')} needle={needle} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <p className="px-3 py-6 text-center text-xs text-slate-500">"{q}"이(가) 든 줄이 없어요.</p>}
      </div>
      <p className="text-[11px] text-slate-500">
        {needle ? `${rows.length} / ${table.rows.length}행` : `${table.rows.length}행`} ·
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

/** 원문 확인 [참고표 보기] — 참고표 id(그 버전)로 창 하나 */
export function RefTableModal({ namespace, id, onClose }: { namespace: string; id: number; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['ref-table', namespace, id], queryFn: () => getRefTable(namespace, id), staleTime: 60_000 });
  return (
    <Modal isOpen onClose={onClose} title="참고표" maxWidth="max-w-6xl">
      {error ? <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{(error as Error).message}</p>
        : isLoading || !data ? <p className="text-xs text-slate-500">표를 불러오는 중…</p> : <RefTableView table={data} />}
    </Modal>
  );
}
