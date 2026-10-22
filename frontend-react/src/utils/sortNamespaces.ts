import type { NamespaceDetail } from '../types';
import { effectiveOwnerParts } from './domainAccess';

/**
 * 내 파트가 담당인 namespace(실효 담당 중 하나가 내 파트 — 담당 N개, 10/22)를 맨 앞으로,
 * 나머지는 원래 순서 유지.
 * nsDetails가 있으면 담당 기반으로 정렬,
 * 없으면 namespace name 일치(fallback)로 정렬.
 */
export function sortNamespacesByUserPart(
  namespaces: string[],
  userPart?: string | null,
  nsDetails?: NamespaceDetail[],
): string[] {
  if (!userPart) return namespaces;
  if (nsDetails && nsDetails.length > 0) {
    const myNsNames = new Set(
      nsDetails.filter((n) => effectiveOwnerParts(n).some((o) => o.name === userPart)).map((n) => n.name),
    );
    return [
      ...namespaces.filter((ns) => myNsNames.has(ns)),
      ...namespaces.filter((ns) => !myNsNames.has(ns)),
    ];
  }
  // fallback: namespace name과 파트명 일치
  return [
    ...namespaces.filter((ns) => ns === userPart),
    ...namespaces.filter((ns) => ns !== userPart),
  ];
}
