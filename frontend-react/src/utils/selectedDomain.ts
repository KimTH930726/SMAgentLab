import type { NamespaceDetail } from '../types';

/**
 * 보는 컬렉션(사이드바 선택) — 사용자별로 브라우저에 기억한다(10/22, 컬렉션 설계 S5).
 * 예전엔 저장을 안 해서 새로고침마다 "내 담당 첫 영역"으로 돌아갔다. 저장이 막힌 브라우저(사생활 보호 등)여도 목록은 정상 — 실패는 조용히 기본값.
 * E2E도 이 키로 테스트 컬렉션을 고른다(e2e/admin-nav.ts selectDomain) — 키 이름을 바꾸면 같이 바꿀 것.
 */
export const domainStorageKey = (username: string) => `opslens.namespace:${username}`;

export function loadSelectedDomain(username: string | undefined): string | null {
  if (!username) return null;
  try {
    return localStorage.getItem(domainStorageKey(username));
  } catch {
    return null;
  }
}

export function saveSelectedDomain(username: string | undefined, name: string): void {
  if (!username || !name) return;
  try {
    localStorage.setItem(domainStorageKey(username), name);
  } catch {
    // 저장 못 해도 이번 화면에선 고른 값이 그대로 쓰인다 — 다음 새로고침 때 기본값으로 돌아갈 뿐
  }
}

/** 고르는 창에 보일 업무 — 일반 업무만(테스트용 policy_test 등은 숨김, S6). 값으로는 여전히 유효(E2E·직접 주소). */
export const isPickableDomain = (d: Pick<NamespaceDetail, 'kind'>) => (d.kind ?? 'domain') === 'domain';

/** 처음 보일 업무: 기억한 값(아직 있으면) → 내 담당 첫 업무 → 고를 수 있는 첫 업무 → 아무 값. */
export function initialDomain(opts: {
  stored: string | null;
  valid: string[];          // API가 준 전체 이름(테스트용 포함)
  pickable: string[];       // 고르는 창에 보이는 이름(내 담당 먼저 정렬됨)
  mine: string[];           // 내 파트 담당 업무
}): string | undefined {
  const { stored, valid } = opts;
  // 목록(valid)과 담당 정보(pickable·mine)는 따로 받아 와 잠깐 어긋날 수 있다(이름 변경 직후) — 목록에 없는 이름은 고르지 않는다
  const pickable = opts.pickable.filter((n) => valid.includes(n));
  const mine = opts.mine.filter((n) => pickable.includes(n));
  if (stored && valid.includes(stored)) return stored;
  return mine[0] ?? pickable[0] ?? valid[0];
}

/** 고르는 창 묶음 안 순서 — 내용(정책·지식) 있는 것 먼저, 그다음 가나다(DB 정렬 규칙과 무관하게 화면에서 같은 순서). 처음 선택도 이 순서를 따른다. */
export function byContentThenName(a: NamespaceDetail, b: NamespaceDetail): number {
  const full = (d: NamespaceDetail) => ((d.policy_count ?? 0) + d.knowledge_count > 0 ? 1 : 0);
  return full(b) - full(a) || a.name.localeCompare(b.name, 'ko');
}
