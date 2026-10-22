import type { Page } from '@playwright/test';

/** 관리자 왼쪽 아코디언 메뉴로 이동(10/21) — 하위 항목이 안 보이면 묶음을 펼친 뒤 누른다(펼친 묶음을 다시 누르면 접히므로 무조건 누르지 않는다). */
export async function goAdmin(page: Page, group: string, item: string) {
  const nav = page.getByRole('navigation', { name: '관리자 메뉴' });
  await nav.waitFor();   // 새로고침 직후 아직 안 그려진 메뉴를 "접혀 있음"으로 오인하지 않게
  const target = nav.getByRole('button', { name: item, exact: true });
  if (group !== item) {
    const header = nav.getByRole('button', { name: group, exact: true });
    if ((await header.getAttribute('aria-expanded')) === 'false') await header.click();
  }
  await target.click();
}

/** 보는 컬렉션 칩(사이드바, 채팅·관리 화면 공통) — 10/22부터 select가 아니라 버튼 + 고르는 창 */
export const domainSelect = (page: Page) => page.locator('#sidebar-domain');

/**
 * 보는 컬렉션 고르기 — 테스트 컬렉션(policy_test)은 고르는 창에 안 보이므로(S6), 앱이 기억하는 값(src/utils/selectedDomain.ts의
 * `opslens.namespace:<사용자>`)을 바꾸고 새로고침한다. 지금 주소(관리 메뉴)는 그대로 남는다.
 */
export async function selectDomain(page: Page, name: string) {
  await page.evaluate((ns) => {
    const auth = JSON.parse(localStorage.getItem('ops_auth') || '{}');
    localStorage.setItem(`opslens.namespace:${auth.user?.username}`, ns);
  }, name);
  await page.reload();
  // 새로고침하면 에이전트 선택이 비어 선택 화면이 먼저 뜬다(App.tsx) — 고르면 주소(지금 메뉴)는 그대로라 원래 화면으로 돌아온다
  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  await agentHeading.or(page.locator('#sidebar-domain')).first().waitFor();   // or() — 진 쪽 대기가 남지 않게(리뷰)
  if (await agentHeading.isVisible().catch(() => false)) await page.getByText('선택하기').first().click();
  await page.locator('#sidebar-domain', { hasText: name }).waitFor();
}
