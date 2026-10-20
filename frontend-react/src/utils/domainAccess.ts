import type { User } from '../types';

/**
 * 업무 영역 데이터(지식·용어·업무구분 등) 수정 권한 — 백엔드 check_namespace_ownership과 같은 규칙(2026-10-20).
 * 관리자 → 가능 / 담당 파트가 정해진 영역 → 그 파트 사람(viewer 제외) / 담당이 없는 영역 → 관리자만.
 * 영역 자체(만들기·이름 바꾸기·지우기)는 관리자만 — 여기서 다루지 않는다.
 */
export function canEditDomain(user: Pick<User, 'role' | 'part'> | null | undefined, ownerPart?: string | null): boolean {
  if (!user) return false;
  if (user.role === 'admin') return true;
  return user.role !== 'viewer' && !!ownerPart && ownerPart === user.part;
}
