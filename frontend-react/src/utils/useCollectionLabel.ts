import { useQuery } from '@tanstack/react-query';
import { getNamespacesDetail } from '../api/namespaces';
import { collectionLabel } from './collections';

/** 컬렉션 이름 → 화면 이름 함수(조직 컬렉션 id 키 → "팀 공통 · ○○팀" / "○○ 파트"). 목록은 다른 화면과 같은 캐시(['namespaces-detail']). */
export function useCollectionLabel(): (name: string | null | undefined) => string {
  const { data: details = [] } = useQuery({ queryKey: ['namespaces-detail'], queryFn: getNamespacesDetail, staleTime: 30_000 });
  return (name) => collectionLabel(name, details);
}
