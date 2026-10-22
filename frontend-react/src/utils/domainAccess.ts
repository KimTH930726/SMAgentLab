import type { NamespaceDetail, User } from '../types';

/** 권한 판단에 쓰는 담당 파트 — 실효 담당(하위 영역은 비어 있으면 상위 담당, 백엔드 check_namespace_ownership과 같음).
 *  옛 서버 응답(실효 칸 없음)이면 직접 지정 값. 수정 창 미리 채우기·변경 비교는 직접 값(owner_part)을 그대로 쓴다. */
export function effectiveOwnerPart(ns?: Pick<NamespaceDetail, 'owner_part' | 'effective_owner_part'> | null): string | null {
  return ns?.effective_owner_part ?? ns?.owner_part ?? null;
}

/**
 * 업무 컬렉션 데이터(지식·용어·업무구분 등) 수정 권한 — 백엔드 check_namespace_ownership과 같은 규칙(2026-10-20).
 * 관리자 → 가능 / 담당 파트가 정해진 영역 → 그 파트 사람(viewer 제외) / 담당이 없는 영역 → 관리자만.
 * 영역 자체(만들기·이름 바꾸기·지우기)는 관리자만 — 여기서 다루지 않는다.
 */
export function canEditDomain(user: Pick<User, 'role' | 'part'> | null | undefined, ownerPart?: string | null): boolean {
  if (!user) return false;
  if (user.role === 'admin') return true;
  return user.role !== 'viewer' && !!ownerPart && ownerPart === user.part;
}
