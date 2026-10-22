import type { NamespaceDetail, OwnerPart, User } from '../types';

type OwnerFields = Pick<NamespaceDetail, 'owner_parts' | 'effective_owner_parts' | 'owner_part' | 'effective_owner_part' | 'owner_part_id' | 'effective_owner_part_id'>;

/** 옛 서버 응답(목록 칸 없음)이면 단일 칸 하나로 — 담당 N개 전환(10/22) 중 호환 */
function legacyOne(id?: number | null, name?: string | null): OwnerPart[] {
  return name ? [{ id: id ?? -1, name }] : [];
}

/** 직접 지정된 담당 파트들(수정 창 미리 채우기·변경 비교용 — 상속 반영 안 함) */
export function directOwnerParts(ns?: OwnerFields | null): OwnerPart[] {
  return ns?.owner_parts ?? legacyOne(ns?.owner_part_id, ns?.owner_part);
}

/** 권한 판단에 쓰는 담당 파트들 — 실효 담당(하위 영역은 자기 담당이 없으면 상위 담당 전부, 백엔드 check_namespace_ownership과 같음). */
export function effectiveOwnerParts(ns?: OwnerFields | null): OwnerPart[] {
  return ns?.effective_owner_parts ?? (ns?.effective_owner_part ? legacyOne(ns.effective_owner_part_id, ns.effective_owner_part) : directOwnerParts(ns));
}

/** 담당 이름 목록을 한 줄로 — "A · B" */
export function ownerNames(parts: OwnerPart[]): string {
  return parts.map((p) => p.name).join(' · ');
}

/**
 * 업무 컬렉션 데이터(지식·용어 등) 수정 권한 — 백엔드 check_namespace_ownership과 같은 규칙(2026-10-20, 담당 N개 10/22).
 * 관리자 → 가능 / 담당 파트가 정해진 영역 → 담당 파트 중 하나에 속한 사람(viewer 제외, 주 담당 없음) / 담당이 없는 영역 → 관리자만.
 * 영역 자체(만들기·이름 바꾸기·지우기)는 관리자만 — 여기서 다루지 않는다.
 */
export function canEditDomain(user: Pick<User, 'role' | 'part'> | null | undefined, owners: OwnerPart[]): boolean {
  if (!user) return false;
  if (user.role === 'admin') return true;
  return user.role !== 'viewer' && !!user.part && owners.some((o) => o.name === user.part);
}
