import type { NamespaceDetail } from '../types';

/**
 * 컬렉션 구분(11/02, docs/domains/knowledge/taxonomy-standard.md) — 정책 컬렉션(area=policy) / 시스템 컬렉션(area=system).
 * 둘 다 kind='domain'이고 area로만 갈린다(옛 서버는 area가 없다 → 업무). 검색·권한·담당은 같게 동작한다.
 * 조직 컬렉션(kind='org_space', 이름 org:team:N · org:part:N)은 폐지 — DB에서 지워지기 전까지 화면 어디에도 안 보이게 걸러 내는 용도로만 남긴다.
 */
const ORG_PREFIX = 'org:';

/** 폐지된 조직 컬렉션(화면에서 숨김) — 상세 목록이 아직 없을 때도 이름 접두사로 안다(DB CHECK로 kind와 같음) */
export const isOrgCollection = (d: Pick<NamespaceDetail, 'kind' | 'name'> | null | undefined) =>
  !!d && (d.kind === 'org_space' || d.name.startsWith(ORG_PREFIX));

export const isOrgName = (name: string | null | undefined) => !!name && name.startsWith(ORG_PREFIX);

/** 시스템 컬렉션(IT 시스템, 최상위만) */
export const isSystemCollection = (d: Pick<NamespaceDetail, 'kind' | 'area'> | null | undefined) =>
  !!d && (d.kind ?? 'domain') === 'domain' && d.area === 'system';

/** 정책 컬렉션만(테스트용 policy_test 포함 — E2E가 정책 컬렉션 화면에서 다룬다) — 조직·시스템 제외 */
export const isBusinessCollection = (d: Pick<NamespaceDetail, 'kind' | 'name' | 'area'>) => !isOrgCollection(d) && !isSystemCollection(d);

/** 화면에 보일 컬렉션 이름 — display_name이 있으면 그것, 없으면 이름 그대로 */
export function collectionLabel(name: string | null | undefined, details: NamespaceDetail[]): string {
  if (!name) return '';
  const d = details.find((x) => x.name === name);
  // 시스템 화면 이름은 " 시스템"을 뗀 것(11/02) — 정책 컬렉션과 섞여 보이는 곳(칩·경로)에선 "시스템 · 딜리버스"로 구분
  if (d && isSystemCollection(d)) return `시스템 · ${d.display_name || d.name}`;
  return d?.display_name || name;
}
