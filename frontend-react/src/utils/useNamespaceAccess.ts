import { useQuery } from '@tanstack/react-query';
import { getNamespaces, getNamespacesDetail } from '../api/namespaces';
import { useAppStore } from '../store/useAppStore';
import { useAuthStore } from '../store/useAuthStore';
import { sortNamespacesByUserPart } from './sortNamespaces';
import { canEditDomain } from './domainAccess';

/**
 * 업무 영역 선택 + 수정 권한 — 고른 영역은 앱 공용 상태 하나(사이드바 선택기·채팅·관리 화면이 같은 값, 10/21).
 * 예전엔 화면마다 따로 들고 있어 지식 화면을 옮겨 다니면 선택이 풀렸다. 없어진 영역 리셋은 사이드바가 한다.
 */
export function useNamespaceAccess() {
  const selectedNs = useAppStore((s) => s.namespace);
  const setSelectedNs = useAppStore((s) => s.setNamespace);
  const user = useAuthStore((s) => s.user);

  const { data: nsDetails = [] } = useQuery({
    queryKey: ['namespaces-detail'],
    queryFn: getNamespacesDetail,
    staleTime: 30_000,
  });

  const { data: namespaces = [] } = useQuery({
    queryKey: ['namespaces'],
    queryFn: getNamespaces,
    staleTime: 30_000,
  });

  const sortedNamespaces = sortNamespacesByUserPart(namespaces, user?.part, nsDetails);

  const nsOwnerPart = nsDetails.find((n) => n.name === selectedNs)?.owner_part;
  const canModifyNs = canEditDomain(user, nsOwnerPart);

  return { selectedNs, setSelectedNs, canModifyNs, sortedNamespaces, namespaces, nsDetails, user };
}
