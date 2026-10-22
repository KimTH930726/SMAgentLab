import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listRefTables, getRefTable } from '../../../api/policy';
import { useNamespaceAccess } from '../../../utils/useNamespaceAccess';

/** 참고표(10/21) — 표 고르기 + 원표 그대로(머리 고정, 가로 스크롤). 결정 열(결과를 정하는 조건)은 머리에 표시. 읽기 전용. */
export function RefTableTab() {
  const { selectedNs } = useNamespaceAccess();
  const [tableId, setTableId] = useState<number | null>(null);
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

  if (!selectedNs) return <p className="text-sm text-slate-400 py-6 text-center">왼쪽에서 업무 컬렉션을 고르세요.</p>;
  if (error) return <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{(error as Error).message}</p>;
  if (isLoading) return <p className="text-xs text-slate-500">불러오는 중…</p>;
  if (tables.length === 0) return <p className="text-sm text-slate-500 py-10 text-center">이 업무 컬렉션엔 참고표가 없어요.</p>;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <label htmlFor="ref-table-pick" className="text-xs text-slate-500">표</label>
        <select id="ref-table-pick" value={tableId ?? ''} onChange={(e) => setTableId(Number(e.target.value))}
          className="bg-slate-800 border border-slate-600 rounded-lg px-2 py-1 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 max-w-full">
          {tables.map((t) => (
            <option key={t.id} value={t.id}>{t.title} ({t.n_rows}행)</option>
          ))}
        </select>
      </div>
      {tableError && <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">{(tableError as Error).message}</p>}
      {loadingTable || !table ? (
        <p className="text-xs text-slate-500">표를 불러오는 중…</p>
      ) : (
        <>
          {table.category_path.length > 0 && <p className="text-xs text-slate-500">{table.category_path.join(' › ')}</p>}
          <div className="max-h-[65vh] overflow-auto rounded-lg border border-slate-700">
            <table className="text-xs border-collapse min-w-full">
              <thead>
                <tr>
                  <th className="sticky top-0 z-10 bg-slate-800 px-2 py-1.5 text-right font-normal text-slate-500 border-b border-slate-700">#</th>
                  {table.columns.map((c) => (
                    <th key={c.name} title={c.decisive ? '결정 열 — 결과를 정하는 조건' : c.role === 'result' ? '결과' : c.role === 'condition' ? '조건' : undefined}
                      className="sticky top-0 z-10 bg-slate-800 px-2 py-1.5 text-left font-medium text-slate-300 whitespace-nowrap border-b border-slate-700">
                      {c.name}
                      {c.decisive && <span className="ml-1 text-indigo-600 dark:text-indigo-400">●</span>}
                      {c.role === 'result' && <span className="ml-1 text-emerald-600 dark:text-emerald-400">→</span>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((r, i) => (
                  <tr key={i} className="border-t border-slate-700/60 hover:bg-slate-800/40">
                    <td className="px-2 py-1 text-right text-slate-500">{i + 1}</td>
                    {table.columns.map((c) => (
                      <td key={c.name} className="px-2 py-1 text-slate-300 align-top whitespace-pre-wrap min-w-[6rem]">{String(r[c.name] ?? '')}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-slate-500">
            {table.rows.length}행 · <span className="text-indigo-600 dark:text-indigo-400">●</span> 결정 열 · <span className="text-emerald-600 dark:text-emerald-400">→</span> 결과
          </p>
        </>
      )}
    </div>
  );
}
