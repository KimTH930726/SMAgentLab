import type { NamespaceDetail } from '../types';

/**
 * 컬렉션 구분(11/02, docs/domains/knowledge/taxonomy-standard.md) — 정책 컬렉션(area=policy) / 시스템 컬렉션(area=system).
 * 둘 다 kind='domain'이고 area로만 갈린다(옛 서버는 area가 없다 → 업무). 검색·권한·담당은 같게 동작한다.
 * (조직 컬렉션은 11/02 폐지 — 서버가 목록에 싣지 않는다.)
 */

/** 시스템 컬렉션(IT 시스템, 최상위만) */
export const isSystemCollection = (d: Pick<NamespaceDetail, 'kind' | 'area'> | null | undefined) =>
  !!d && (d.kind ?? 'domain') === 'domain' && d.area === 'system';

/** 정책 컬렉션만(테스트용 policy_test 포함 — E2E가 정책 컬렉션 화면에서 다룬다) — 시스템 제외 */
export const isBusinessCollection = (d: Pick<NamespaceDetail, 'kind' | 'area'>) => !isSystemCollection(d);

/** 화면에 보일 컬렉션 이름 — display_name이 있으면 그것, 없으면 이름 그대로 */
export function collectionLabel(name: string | null | undefined, details: NamespaceDetail[]): string {
  if (!name) return '';
  const d = details.find((x) => x.name === name);
  // 시스템 화면 이름은 " 시스템"을 뗀 것(11/02) — 정책 컬렉션과 섞여 보이는 곳(칩·경로)에선 "시스템 · 딜리버스"로 구분
  if (d && isSystemCollection(d)) return `시스템 · ${d.display_name || d.name}`;
  return d?.display_name || name;
}
