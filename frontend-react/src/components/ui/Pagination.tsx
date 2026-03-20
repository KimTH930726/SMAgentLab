import { clsx } from 'clsx';

interface PaginationProps {
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  pageSize?: number;
  onPageSizeChange?: (size: number) => void;
  pageSizeOptions?: number[];
  totalItems?: number;
  className?: string;
}

export function Pagination({
  page,
  totalPages,
  onPageChange,
  pageSize,
  onPageSizeChange,
  pageSizeOptions = [10, 30, 50],
  totalItems,
  className,
}: PaginationProps) {
  if (totalPages <= 1 && !onPageSizeChange) return null;

  return (
    <div className={clsx('flex items-center justify-center gap-3', className)}>
      {onPageSizeChange && pageSize && (
        <select
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
          className="bg-slate-800 border border-slate-600 rounded-lg px-2 py-1.5 text-xs text-slate-400"
        >
          {pageSizeOptions.map((n) => (
            <option key={n} value={n}>{n}건</option>
          ))}
        </select>
      )}
      {totalPages > 1 && (
        <>
          <button
            onClick={() => onPageChange(Math.max(1, page - 1))}
            disabled={page <= 1}
            className="px-2.5 py-1 text-xs rounded-lg border border-slate-600 text-slate-400 hover:text-slate-200 hover:border-slate-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            이전
          </button>
          <span className="text-xs text-slate-400">
            {page} / {totalPages}
            {totalItems !== undefined && (
              <span className="text-slate-600 ml-1">({totalItems}건)</span>
            )}
          </span>
          <button
            onClick={() => onPageChange(Math.min(totalPages, page + 1))}
            disabled={page >= totalPages}
            className="px-2.5 py-1 text-xs rounded-lg border border-slate-600 text-slate-400 hover:text-slate-200 hover:border-slate-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            다음
          </button>
        </>
      )}
    </div>
  );
}

/** 클라이언트 슬라이싱 헬퍼 */
export function useClientPaging<T>(items: T[], pageSize: number) {
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const slice = (page: number) => items.slice((page - 1) * pageSize, page * pageSize);
  return { totalPages, totalItems: items.length, slice };
}
