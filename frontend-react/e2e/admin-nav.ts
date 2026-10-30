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

/** 지식 등록 — 넣을 곳 카드에서 컬렉션을 직접 고른다(E2). AI 제안에 맡기면 테스트 문장마다 결과가 달라 "다른 곳…"으로 고정.
 *  [다른 곳…] = 보는 컬렉션 고르기와 같은 창(10/23 A안) — 업무 탭에서 그 이름 카드를 누른다 */
export async function chooseLocation(page: Page, namespace: string) {
  const other = page.getByRole('button', { name: '다른 곳…' });
  await other.waitFor({ timeout: 60_000 });   // 판정(로컬 LLM) 대기
  await other.click();
  // 창은 지금 고른 곳(AI 제안)이 조직이면 조직 탭으로 열린다 — 업무 탭부터 누르고, 이름이 정확히 같은 카드만(재리뷰 F1)
  await page.locator('#picker-tab-business').click();
  const esc = namespace.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  await page.locator('#picker-panel-business').getByRole('button', { name: new RegExp(`^${esc}(\\s|·|$)`) }).first().click();
  await page.locator('#picker-panel-business').waitFor({ state: 'detached' });
}

/** 주소로 바로 들어가기(10/23, 메뉴 바로잡기 확인용) — 에이전트 선택 화면이 없어져(10/30) 새로고침해도 그 주소로 */
export async function gotoAdminUrl(page: Page, path: string) {
  await page.goto(path);
  await page.locator('#sidebar-domain').waitFor();
}

/**
 * 보는 컬렉션 고르기 — 테스트 컬렉션(policy_test)은 고르는 창에 안 보이므로(S6), 앱이 기억하는 값(src/utils/selectedDomain.ts의
 * `opslens.namespace:<사용자>`)을 바꾸고 새로고침한다. 지금 주소(관리 메뉴)는 그대로 남는다.
 * 조직 컬렉션(org:part:N 같은 id 키)은 칩에 화면 이름이 보이므로 label(칩에 보일 글자)을 같이 넘긴다(10/22 #83).
 */
export async function selectDomain(page: Page, name: string, label: string = name) {
  await page.evaluate((ns) => {
    const auth = JSON.parse(localStorage.getItem('ops_auth') || '{}');
    localStorage.setItem(`opslens.namespace:${auth.user?.username}`, ns);
  }, name);
  await page.reload();
  await page.locator('#sidebar-domain', { hasText: label }).waitFor();
}
