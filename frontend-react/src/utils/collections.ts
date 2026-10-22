import type { NamespaceDetail } from '../types';

/**
 * 업무 컬렉션 / 조직 컬렉션 구분(10/22, 컬렉션 설계 §2·§15-13, 백엔드 #83).
 * 조직 컬렉션 이름은 id 키(org:team:1 · org:part:774)라 화면엔 늘 display_name("팀 공통 · ○○팀" / "○○ 파트")을 쓴다.
 * 이름 접두사 org:는 DB CHECK로 종류(kind='org_space')와 같다 — 상세 목록이 아직 없을 때도 이름만으로 알 수 있다.
 */
export const ORG_PREFIX = 'org:';

export const isOrgCollection = (d: Pick<NamespaceDetail, 'kind' | 'name'> | null | undefined) =>
  !!d && (d.kind === 'org_space' || d.name.startsWith(ORG_PREFIX));

export const isOrgName = (name: string | null | undefined) => !!name && name.startsWith(ORG_PREFIX);

/** 업무 컬렉션만(테스트용 포함) — 업무 컬렉션 관리·담당 업무 매핑 화면용 */
export const isBusinessCollection = (d: Pick<NamespaceDetail, 'kind' | 'name'>) => !isOrgCollection(d);

/** 화면에 보일 컬렉션 이름 — 조직이면 display_name, 목록을 아직 못 받았으면 이름 그대로(조직 키면 "조직 컬렉션") */
export function collectionLabel(name: string | null | undefined, details: NamespaceDetail[]): string {
  if (!name) return '';
  const d = details.find((x) => x.name === name);
  if (d?.display_name) return d.display_name;
  return isOrgName(name) ? '조직 컬렉션' : name;
}
