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

/** 목록 위: 건수 + 페이지 사이즈 선택 */
export function PaginationInfo({
  totalItems,
  pageSize,
  onPageSizeChange,
  pageSizeOptions = [10, 30, 50],
  className,
}: Pick<PaginationProps, 'totalItems' | 'pageSize' | 'onPageSizeChange' | 'pageSizeOptions' | 'className'>) {
  return (
    <div className={clsx('flex items-center gap-2', className)}>
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
      {totalItems !== undefined && (
        <span className="text-xs text-slate-500">{totalItems}건</span>
      )}
    </div>
  );
}

/** 목록 아래: 이전/다음 + 페이지 표시 */
export function PaginationNav({
  page,
  totalPages,
  onPageChange,
  className,
}: Pick<PaginationProps, 'page' | 'totalPages' | 'onPageChange' | 'className'>) {
  return (
    <div className={clsx('flex items-center justify-center gap-3', className)}>
      <button
        onClick={() => onPageChange(Math.max(1, page - 1))}
        disabled={page <= 1}
        className="px-2.5 py-1 text-xs rounded-lg border border-slate-600 text-slate-400 hover:text-slate-200 hover:border-slate-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        이전
      </button>
      <span className="text-xs text-slate-400">{page} / {totalPages}</span>
      <button
        onClick={() => onPageChange(Math.min(totalPages, page + 1))}
        disabled={page >= totalPages}
        className="px-2.5 py-1 text-xs rounded-lg border border-slate-600 text-slate-400 hover:text-slate-200 hover:border-slate-500 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        다음
      </button>
    </div>
  );
}

/** 레거시 호환: 단일 컴포넌트 (하단 전용) */
export function Pagination(props: PaginationProps) {
  return <PaginationNav page={props.page} totalPages={props.totalPages} onPageChange={props.onPageChange} className={props.className} />;
}

/** 클라이언트 슬라이싱 헬퍼 */
export function useClientPaging<T>(items: T[], pageSize: number) {
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const slice = (page: number) => items.slice((page - 1) * pageSize, page * pageSize);
  return { totalPages, totalItems: items.length, slice };
}
