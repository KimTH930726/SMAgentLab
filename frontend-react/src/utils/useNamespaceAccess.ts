import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getNamespaces, getNamespacesDetail } from '../api/namespaces';
import { useAppStore } from '../store/useAppStore';
import { useAuthStore } from '../store/useAuthStore';
import { sortNamespacesByUserPart } from './sortNamespaces';

/**
 * KnowledgeTable / GlossaryTable 공통 네임스페이스 선택 + 권한 훅
 */
export function useNamespaceAccess() {
  const { namespace: storeNamespace } = useAppStore();
  const user = useAuthStore((s) => s.user);
  const [selectedNs, setSelectedNs] = useState(storeNamespace || '');

  // 스토어 namespace 변경(파트 관리 네비게이션 등) 시 동기화
  useEffect(() => {
    if (storeNamespace) setSelectedNs(storeNamespace);
  }, [storeNamespace]);

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

  // 삭제된 네임스페이스 선택 상태 자동 리셋
  useEffect(() => {
    if (selectedNs && namespaces.length > 0 && !namespaces.includes(selectedNs)) {
      setSelectedNs('');
    }
  }, [namespaces, selectedNs]);

  const nsOwnerPart = nsDetails.find((n) => n.name === selectedNs)?.owner_part;
  const canModifyNs = user?.role === 'admin'
    || (user?.role !== 'viewer' && (!nsOwnerPart || nsOwnerPart === user?.part));

  return { selectedNs, setSelectedNs, canModifyNs, sortedNamespaces, namespaces, nsDetails, user };
}
