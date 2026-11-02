import { useQuery } from '@tanstack/react-query';
import { getMenuVisibility } from '../api/menu';
import { useAuthStore } from '../store/useAuthStore';
import { useNamespaceAccess } from './useNamespaceAccess';
import { isSystemCollection } from './collections';
import { visibleAdminMenu, type AdminMenuGroup, type CollectionKind } from '../pages/adminMenu';

/**
 * 왼쪽 메뉴·본문이 같이 쓰는 "지금 보이는 메뉴" — 역할 + 보는 컬렉션 종류(업무/시스템) + 메뉴 노출 설정(10/23).
 * 종류는 이름만으론 모른다(둘 다 kind='domain') — 상세 목록의 area로(11/02). 목록 전이면 업무로 본다.
 * 설정을 아직 못 읽었으면(처음·실패) 숨김 없이 — 담을 수 없는 메뉴만 빠진다(메뉴가 통째로 사라지지 않게).
 */
export function useAdminMenu(): { groups: AdminMenuGroup[]; kind: CollectionKind } {
  const user = useAuthStore((s) => s.user);
  const { selectedNs, nsDetails } = useNamespaceAccess();
  const kind: CollectionKind = isSystemCollection(nsDetails.find((d) => d.name === selectedNs)) ? 'system' : 'domain';
  const { data } = useQuery({ queryKey: ['menu-visibility'], queryFn: getMenuVisibility, staleTime: 60_000, enabled: !!user });
  return { groups: visibleAdminMenu(user, { kind, hidden: data?.hidden }), kind };
}
