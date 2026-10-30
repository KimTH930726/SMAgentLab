import { expect, type Page } from '@playwright/test';

// E2E 공용 로그인(10/21 리뷰 C5) — 12개 spec이 같은 로그인 코드를, 7곳이 같은 토큰 받기를 복사해 쓰고 있었다.
export const API = 'http://localhost:8000';
const ADMIN_USER = 'admin';
const ADMIN_PASS = '1111';

/** 화면으로 관리자 로그인 → 기본은 사이드바 Admin 링크로 관리 화면까지(에이전트 선택 화면은 10/30 걷어냄).
 *  채팅 화면에 머물 spec은 { openAdmin: false }. */
export async function loginAdmin(page: Page, { openAdmin = true }: { openAdmin?: boolean } = {}) {
  await page.goto('/login');
  await page.getByPlaceholder('사용자 아이디').fill(ADMIN_USER);
  await page.getByPlaceholder('비밀번호').fill(ADMIN_PASS);
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('heading', { name: '로그인' })).toBeHidden({ timeout: 10_000 });
  if (openAdmin) await page.getByRole('link', { name: 'Admin' }).click();
}

/** 전역 fetch + 연결 오류면 한 번 다시 — node fetch가 서버(uvicorn keep-alive 5초)가 이미 닫은 연결을 다시 집어 "other side closed"로
 *  한 번씩 실패했다(10/27 세 번, eval-report·policy-change). 응답을 받은 실패(4xx·5xx)는 다시 하지 않는다. */
export async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (e) {
    if (!(e instanceof TypeError)) throw e;
    return fetch(url, init);
  }
}

/** API용 관리자 토큰 — 전역 fetch라 테스트(request 컨텍스트 없이도)·globalSetup 어디서나 같은 코드. */
export async function adminToken(): Promise<string> {
  const r = await apiFetch(`${API}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: ADMIN_USER, password: ADMIN_PASS }),
  });
  if (!r.ok) throw new Error(`관리자 로그인 실패: ${r.status} ${await r.text()}`);
  return (await r.json()).access_token as string;
}
