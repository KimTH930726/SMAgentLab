import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { BarChart2, BookOpen, ChevronDown, ChevronUp, Layers, Mail, Settings, Users, type LucideIcon } from 'lucide-react';
import { useAuthStore } from '../../store/useAuthStore';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { getCorrectionPendingCount } from '../../api/corrections';
import { adminPath, resolveAdminMenu, visibleAdminMenu, type AdminMenuId } from '../../pages/adminMenu';

const OPEN_KEY = 'opslens.adminNav.open';

// 최상위 메뉴 아이콘 — 6개가 같은 왼쪽 선에서 시작해 같은 급으로 보이게(사용자 10/21: 화살표가 앞에 있으면 하위처럼 보인다)
const GROUP_ICON: Record<string, LucideIcon> = { collections: Layers, knowledge: BookOpen, quality: BarChart2, voc: Mail, system: Settings };

function loadOpen(): string[] | null {
  try {
    const raw = localStorage.getItem(OPEN_KEY);
    return raw ? (JSON.parse(raw) as string[]) : null;
  } catch {
    return null;   // 사생활 보호 창 등 — 기본(지금 묶음만 펼침)으로
  }
}

/**
 * 관리자 메뉴 — 왼쪽 아코디언(사용자 결정 10/21). 하위가 하나인 묶음은 누르면 바로 그 화면,
 * 지금 화면이 속한 묶음은 펼쳐서 시작하되 접을 수도 있다(10/22 사용자: 화살표를 눌러도 안 닫혀 고장처럼 보임), 묶음 이름은 접고 펼치기만(여러 묶음 동시 가능, 펼친 상태 기억).
 */
export function AdminNav() {
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const groups = visibleAdminMenu(user);
  const current = resolveAdminMenu(location.pathname, groups);
  const [open, setOpen] = useState<string[]>(() => loadOpen() ?? []);
  const [closedCurrent, setClosedCurrent] = useState<string | null>(null);   // 지금 화면이 든 묶음을 사람이 접었으면 그 묶음 id — 화면을 옮기면 풀린다
  useEffect(() => { setClosedCurrent(null); }, [current]);

  // 정정 요청 대기 건수(지금 고른 업무 컬렉션, 처리 권한이 있을 때) — 지식 탭 안에 있던 배지를 옮겨 옴(방치 방지)
  const { selectedNs, canModifyNs } = useNamespaceAccess();
  const { data: correctionCount } = useQuery({
    queryKey: ['corrections-pending-count', selectedNs],
    queryFn: () => getCorrectionPendingCount(selectedNs),
    enabled: !!selectedNs && canModifyNs,
    staleTime: 30_000,
  });
  const badge = (id: AdminMenuId) => (id === 'q.corrections' ? correctionCount?.by_namespace[selectedNs] ?? 0 : 0);

  const toggle = (gid: string) => setOpen((prev) => {
    const next = prev.includes(gid) ? prev.filter((x) => x !== gid) : [...prev, gid];
    try { localStorage.setItem(OPEN_KEY, JSON.stringify(next)); } catch { /* 저장 못 해도 지금 화면엔 반영 */ }
    return next;
  });

  return (
    <nav aria-label="관리자 메뉴" className="flex-1 overflow-y-auto px-2 py-3 space-y-0.5 text-sm">
      {groups.map((g) => {
        const single = g.items.length === 1;
        const containsCurrent = g.items.some((it) => it.id === current);
        const Icon = GROUP_ICON[g.id] ?? Layers;
        if (single) {
          const it = g.items[0];
          return (
            <button key={g.id} onClick={() => navigate(adminPath(it.id))} title={it.hint}
              aria-current={it.id === current ? 'page' : undefined}
              className={clsx('w-full flex items-center gap-2.5 px-3 py-2 rounded-lg font-medium transition-colors text-left',
                it.id === current ? 'bg-indigo-600 text-white' : 'text-slate-300 hover:bg-slate-700')}>
              <Icon className="w-4 h-4 flex-shrink-0" />
              <span className="flex-1">{g.label}</span>
            </button>
          );
        }
        const isOpen = containsCurrent ? closedCurrent !== g.id : open.includes(g.id);
        return (
          <div key={g.id}>
            <button onClick={() => { if (containsCurrent) setClosedCurrent(isOpen ? g.id : null); else toggle(g.id); }} aria-expanded={isOpen}
              className={clsx('w-full flex items-center gap-2.5 px-3 py-2 rounded-lg font-medium transition-colors text-left hover:bg-slate-700',
                containsCurrent ? 'text-slate-100' : 'text-slate-300')}>
              <Icon className="w-4 h-4 flex-shrink-0" />
              <span className="flex-1">{g.label}</span>
              {isOpen ? <ChevronUp className="w-4 h-4 text-slate-500" /> : <ChevronDown className="w-4 h-4 text-slate-500" />}
            </button>
            {isOpen && (
              <div className="mt-0.5 space-y-0.5">
                {g.items.map((it) => (
                  <button key={it.id} onClick={() => navigate(adminPath(it.id))}
                    title={badge(it.id) > 0 ? `${it.hint ?? it.label} — 대기 ${badge(it.id)}건` : it.hint}
                    aria-current={it.id === current ? 'page' : undefined}   // 이름 = 항목 글자(배지는 aria-hidden이라 이름에 안 섞임)
                    className={clsx('w-full flex items-center justify-between text-left pl-[2.375rem] pr-3 py-1.5 rounded-md transition-colors',
                      it.id === current ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:bg-slate-700 hover:text-slate-200')}>
                    <span>{it.label}</span>
                    {badge(it.id) > 0 && (
                      <span aria-hidden="true" className="text-[10px] bg-rose-100 text-rose-700 dark:bg-rose-900/60 dark:text-rose-400 px-1.5 py-0.5 rounded-full">{badge(it.id)}</span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );
}
