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

/** 업무 영역 선택 — 사이드바 선택기 하나(채팅·관리 화면 공통) */
export const domainSelect = (page: Page) => page.locator('#sidebar-domain');
