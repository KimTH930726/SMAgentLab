import { ChevronRight } from 'lucide-react';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { findAdminItem, type AdminMenuId } from '../../pages/adminMenu';

/**
 * 관리 본문 맨 위 경로 표시줄(10/21) — 모든 화면 같은 자리에 "업무 영역 › 묶음 › 화면" + 담당 파트.
 * 화면마다 따로 붙이던 "업무 영역: ○○" 배지는 위치·모양이 들쭉날쭉해 잘 안 보였다(사용자 지적) — 이 줄 하나로.
 * 업무 영역과 상관없는 화면(업무 영역 목록·조직·시스템 설정)은 메뉴 위치만.
 */
export function AdminCrumb({ current }: { current: AdminMenuId }) {
  const found = findAdminItem(current);
  const { selectedNs, nsDetails } = useNamespaceAccess();
  if (!found) return null;
  const { group, item } = found;
  const scoped = !!item.scoped && !!selectedNs;
  const owner = nsDetails.find((n) => n.name === selectedNs)?.owner_part ?? null;
  const focusPicker = () => {
    const el = document.getElementById('sidebar-domain') as HTMLSelectElement | null;
    el?.focus();
    try { (el as HTMLSelectElement & { showPicker?: () => void })?.showPicker?.(); } catch { /* 지원 안 하는 브라우저는 포커스만 */ }
  };
  return (
    <div className="flex-shrink-0 flex items-center gap-3 px-6 py-3 border-b border-slate-700 bg-slate-800/60" data-testid="admin-crumb">
      <nav aria-label="현재 위치" className="flex items-center gap-1.5 text-sm min-w-0">
        {scoped && (
          <>
            <button onClick={focusPicker} title="업무 영역 바꾸기 — 왼쪽 사이드바 선택기"
              className="font-semibold text-indigo-700 dark:text-indigo-300 hover:underline truncate">
              {selectedNs}
            </button>
            <ChevronRight className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
          </>
        )}
        {group.items.length > 1 && (
          <>
            <span className="text-slate-400">{group.label}</span>
            <ChevronRight className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
          </>
        )}
        <span className="text-slate-200 font-medium">{item.label}</span>
      </nav>
      {scoped && (
        <span className="ml-auto text-xs text-slate-400 whitespace-nowrap" title="이 업무 영역을 수정할 수 있는 파트(없으면 관리자만)">
          담당: <span className="text-slate-300">{owner ?? '없음(관리자만)'}</span>
        </span>
      )}
    </div>
  );
}
