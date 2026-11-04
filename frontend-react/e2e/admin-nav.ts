import { expect, type Page } from '@playwright/test';

export type AdminScope = '컬렉션 관리' | '플랫폼 관리';
/** 플랫폼 관리 메뉴(11/03 범위 나누기) — 나머지는 컬렉션 관리. 화면 이름이 겹치는 '홈'은 scope를 넘긴다 */
const PLATFORM_ITEMS = new Set(['평가 게이트', 'AI 감사', '사용자 피드백', '정책 컬렉션', '시스템 컬렉션', '조직·권한', 'VOC 이메일', '설정']);
/** 묶음(아코디언) 안에 든 항목 → 묶음 이름. 나머지는 한 줄 메뉴 */
const IN_GROUP: Record<string, string> = { '정책 컬렉션': '컬렉션', '시스템 컬렉션': '컬렉션' };

/** 관리 범위 전환 [컬렉션 관리 | 플랫폼 관리](관리자만 보임) — 이미 그 범위면 누르지 않는다 */
export async function switchScope(page: Page, scope: AdminScope) {
  const nav = page.getByRole('navigation', { name: '관리자 메뉴' });
  await nav.waitFor();
  const btn = nav.getByRole('group', { name: '관리 범위' }).getByRole('button', { name: scope, exact: true });
  if ((await btn.getAttribute('aria-pressed')) !== 'true') await btn.click();
  await expect(btn).toHaveAttribute('aria-pressed', 'true');
}

/** 관리자 왼쪽 메뉴로 이동(10/21 아코디언 → 11/03 두 범위) — 그 항목의 범위로 바꾼 뒤, 묶음 안 항목이면 묶음을 펼쳐서 누른다
 *  (펼친 묶음을 다시 누르면 접히므로 무조건 누르지 않는다). 관리자가 아니면 범위 전환이 없다 — scope: null. */
export async function goAdmin(page: Page, item: string, scope: AdminScope | null = PLATFORM_ITEMS.has(item) ? '플랫폼 관리' : '컬렉션 관리') {
  const nav = page.getByRole('navigation', { name: '관리자 메뉴' });
  await nav.waitFor();   // 새로고침 직후 아직 안 그려진 메뉴를 "접혀 있음"으로 오인하지 않게
  if (scope) await switchScope(page, scope);
  const group = IN_GROUP[item];
  if (group) {
    const header = nav.getByRole('button', { name: group, exact: true });
    if ((await header.getAttribute('aria-expanded')) === 'false') await header.click();
  }
  await nav.getByRole('button', { name: item, exact: true }).click();
}

/** 보는 컬렉션 칩(사이드바, 채팅·관리 화면 공통) — 10/22부터 select가 아니라 버튼 + 고르는 창 */
export const domainSelect = (page: Page) => page.locator('#sidebar-domain');

/** 지식 등록 — 넣을 곳 카드에서 컬렉션을 직접 고른다(E2). AI 제안에 맡기면 테스트 문장마다 결과가 달라 "다른 곳…"으로 고정.
 *  [다른 곳…] = 보는 컬렉션 고르기와 같은 창(10/23 A안) — 운영 지식은 시스템에만이라 시스템 칸만 뜬다(11/02), 그 키의 카드를 누른다 */
export async function chooseLocation(page: Page, namespace: string) {
  const other = page.getByRole('button', { name: '다른 곳…' });
  await other.waitFor({ timeout: 60_000 });   // 판정(로컬 LLM) 대기
  await other.click();
  const panel = page.locator('#picker-panel-system');
  await panel.getByTestId(`collection-card-${namespace}`).click();   // 카드 testid = 키 이름(화면 이름은 " 시스템"을 뗌) — 이름이 정확히 같은 카드만
  await panel.waitFor({ state: 'detached' });
}

/** 주소로 바로 들어가기(10/23, 메뉴 바로잡기 확인용) — 에이전트 선택 화면이 없어져(10/30) 새로고침해도 그 주소로 */
export async function gotoAdminUrl(page: Page, path: string) {
  await page.goto(path);
  await page.locator('#sidebar-domain').waitFor();
}

/**
 * 보는 컬렉션 고르기 — 테스트 컬렉션(policy_test)은 고르는 창에 안 보이므로(S6), 앱이 기억하는 값(src/utils/selectedDomain.ts의
 * `opslens.namespace:<사용자>`)을 바꾸고 새로고침한다. 지금 주소(관리 메뉴)는 그대로 남는다.
 * 칩에 이름과 다른 글자가 보이는 컬렉션이면 label(칩에 보일 글자)을 같이 넘긴다.
 */
export async function selectDomain(page: Page, name: string, label: string = name) {
  await page.evaluate((ns) => {
    const auth = JSON.parse(localStorage.getItem('ops_auth') || '{}');
    localStorage.setItem(`opslens.namespace:${auth.user?.username}`, ns);
  }, name);
  await page.reload();
  await page.locator('#sidebar-domain', { hasText: label }).waitFor();
}
