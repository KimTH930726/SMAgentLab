import { expect, type Page } from '@playwright/test';

// E2E 공용 로그인(10/21 리뷰 C5) — 12개 spec이 같은 로그인·에이전트 선택 코드를, 7곳이 같은 토큰 받기를 복사해 쓰고 있었다.
export const API = 'http://localhost:8000';
const ADMIN_USER = 'admin';
const ADMIN_PASS = '1111';

/** 화면으로 관리자 로그인 → (나오면) 에이전트 선택 → 기본은 사이드바 Admin 링크로 관리 화면까지.
 *  로그인 직후엔 매번 에이전트 선택 화면부터 나온다(Zustand store, 새 브라우저 컨텍스트라 저장된 선택이 없음) — 하나 골라야
 *  그다음 화면(사이드바+Admin)이 보인다. page.goto('/admin')는 풀 리로드라 선택이 초기화돼 다시 튕기므로 링크 클릭(클라이언트 라우팅)으로 간다.
 *  채팅 화면에 머물 spec은 { openAdmin: false }. */
export async function loginAdmin(page: Page, { openAdmin = true }: { openAdmin?: boolean } = {}) {
  await page.goto('/login');
  await page.getByPlaceholder('사용자 아이디').fill(ADMIN_USER);
  await page.getByPlaceholder('비밀번호').fill(ADMIN_PASS);
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('heading', { name: '로그인' })).toBeHidden({ timeout: 10_000 });
  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  if (await agentHeading.isVisible().catch(() => false)) await page.getByText('선택하기').first().click();
  if (openAdmin) await page.getByRole('link', { name: 'Admin' }).click();
}

/** API용 관리자 토큰 — 전역 fetch라 테스트(request 컨텍스트 없이도)·globalSetup 어디서나 같은 코드. */
export async function adminToken(): Promise<string> {
  const r = await fetch(`${API}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: ADMIN_USER, password: ADMIN_PASS }),
  });
  if (!r.ok) throw new Error(`관리자 로그인 실패: ${r.status} ${await r.text()}`);
  return (await r.json()).access_token as string;
}
