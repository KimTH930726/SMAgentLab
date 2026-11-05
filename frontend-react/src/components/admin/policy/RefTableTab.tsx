import { useEffect, useMemo, useState } from 'react';
import { clsx } from 'clsx';
import { useQuery } from '@tanstack/react-query';
import { listRefTables, getRefTable } from '../../../api/policy';
import { useNamespaceAccess } from '../../../utils/useNamespaceAccess';
import { RefTableView } from './RefTableView';

/** 참고표(10/21) — 왼쪽 표 목록(검색) + 오른쪽 원표(RefTableView: 머리 고정·줄무늬·결정/결과 열 구분·표 안 검색, 11/05). 읽기 전용. */
export function RefTableTab() {
  const { selectedNs } = useNamespaceAccess();
  const [tableId, setTableId] = useState<number | null>(null);
  const [find, setFind] = useState('');
  const { data: tables = [], isLoading, error } = useQuery({
    queryKey: ['ref-tables', selectedNs],
    queryFn: () => listRefTables(selectedNs),
    enabled: !!selectedNs,
    staleTime: 60_000,
  });
  useEffect(() => {   // 영역을 바꾸면 첫 표로
    setTableId((cur) => (cur !== null && tables.some((t) => t.id === cur) ? cur : tables[0]?.id ?? null));
  }, [tables]);
  const { data: table, isLoading: loadingTable, error: tableError } = useQuery({
    queryKey: ['ref-table', selectedNs, tableId],
    queryFn: () => getRefTable(selectedNs, tableId as number),
    enabled: !!selectedNs && tableId !== null,
    staleTime: 60_000,
  });
  const shown = useMemo(() => {
    const f = find.trim().toLowerCase();
    return f ? tables.filter((t) => `${t.title} ${t.category_path.join(' ')}`.toLowerCase().includes(f)) : tables;
  }, [tables, find]);

  if (!selectedNs) return <p className="text-sm text-slate-400 py-6 text-center">왼쪽에서 정책 컬렉션을 고르세요.</p>;
  if (error) return <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{(error as Error).message}</p>;
  if (isLoading) return <p className="text-xs text-slate-500">불러오는 중…</p>;
  if (tables.length === 0) return <p className="text-sm text-slate-500 py-10 text-center">이 정책 컬렉션엔 참고표가 없어요.</p>;

  return (
    <div className="grid gap-3 md:grid-cols-[16rem_minmax(0,1fr)]">
      <aside className="min-w-0 space-y-2">
        <input type="search" value={find} onChange={(e) => setFind(e.target.value)} placeholder={`표 이름 검색 (${tables.length}개)`} aria-label="표 이름 검색"
          className="w-full bg-slate-900 border border-slate-600 rounded-lg px-2.5 py-1.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500" />
        <ul className="max-h-[30vh] md:max-h-[65vh] overflow-y-auto space-y-1" aria-label="참고표">
          {shown.map((t) => (
            <li key={t.id}>
              <button type="button" aria-current={t.id === tableId ? 'true' : undefined} onClick={() => setTableId(t.id)} data-testid={`ref-table-pick-${t.id}`}
                className={clsx('w-full rounded-lg border px-2.5 py-1.5 text-left', t.id === tableId
                  ? 'border-indigo-500 bg-indigo-50 dark:border-indigo-400 dark:bg-indigo-500/15' : 'border-slate-700 hover:bg-slate-800')}>
                <span className="block text-sm text-slate-100 break-words">{t.title}</span>
                <span className="block text-[11px] text-slate-500 break-words">{t.category_path.join(' › ') || '분류 없음'} · {t.n_rows}행</span>
              </button>
            </li>
          ))}
          {shown.length === 0 && <li className="px-2 py-3 text-xs text-slate-500">"{find}"이(가) 든 표가 없어요.</li>}
        </ul>
      </aside>
      <div className="min-w-0">
        {tableError && <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{(tableError as Error).message}</p>}
        {loadingTable || !table ? <p className="text-xs text-slate-500">표를 불러오는 중…</p> : <RefTableView key={table.id} table={table} />}
      </div>
    </div>
  );
}
