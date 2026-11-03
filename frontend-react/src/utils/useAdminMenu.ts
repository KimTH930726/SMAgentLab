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
 * kindKnown = 보는 컬렉션의 종류를 목록으로 확인했는지 — 모르는 동안엔 주소를 바로잡지 않는다
 * (운영 지식이 시스템에만 보이게 된 뒤, 시스템 컬렉션의 /admin/k.docs 새로고침이 목록 전 '업무'로 판정돼 홈으로 튕겼다 — 리뷰 M1 11/03)
 */
export function useAdminMenu(): { groups: AdminMenuGroup[]; kind: CollectionKind; kindKnown: boolean } {
  const user = useAuthStore((s) => s.user);
  const { selectedNs, nsDetails, nsError, namespaces, namespacesFetched } = useNamespaceAccess();
  const detail = nsDetails.find((d) => d.name === selectedNs);
  const kind: CollectionKind = isSystemCollection(detail) ? 'system' : 'domain';
  // 선택 값은 처음엔 ''이고 사이드바가 목록을 읽은 뒤 되살린다 — 빈 값도 '모름'(상세에서 찾았을 때만 앎)
  // 영영 알 수 없는 경우(상세 목록 실패·컬렉션 0개)는 '확정'으로 친다 — 대체로 업무로 보고 정상 바로잡기(재리뷰 L1)
  const kindKnown = !!detail || nsError || (namespacesFetched && namespaces.length === 0);
  const { data } = useQuery({ queryKey: ['menu-visibility'], queryFn: getMenuVisibility, staleTime: 60_000, enabled: !!user });
  return { groups: visibleAdminMenu(user, { kind, hidden: data?.hidden }), kind, kindKnown };
}
