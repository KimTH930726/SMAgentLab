import { test, expect } from '@playwright/test';

// 재질문(v2.134, 2026-10-12) — 주제만 말한 질문의 목록이 크면 답 대신 범위 선택지(①②③ + 전부 보기 + 기타)를 보여주고,
// 고르면 같은 대화에서 그 범위로 답한다(다시 묻지 않음). 재질문은 답이 아니라 "답변 틀림"이 없다. 실 dev 스택 그대로(LLM 1회).
const NS = '온라인스토어 DB';

test('주제만 말한 큰 목록 → 범위 선택지 → 고르면 그 범위로 답', async ({ page }) => {
  test.setTimeout(360_000);   // 선택 후 답은 사내 LLM(수십 초~)
  page.on('dialog', (d) => { throw new Error(`브라우저 기본 대화상자: ${d.message()}`); });
  await page.goto('/login');
  await page.getByPlaceholder('사용자 아이디').fill('admin');
  await page.getByPlaceholder('비밀번호').fill('1111');
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('heading', { name: '로그인' })).toBeHidden({ timeout: 10_000 });
  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  if (await agentHeading.isVisible().catch(() => false)) await page.getByText('선택하기').first().click();
  const authState = await page.evaluate(() => localStorage.getItem('ops_auth'));
  const auth = { Authorization: `Bearer ${authState ? JSON.parse(authState).accessToken : ''}` };
  // 캐시된 예전 답(재질문 전)이 있으면 재질문이 안 나온다 — 이 파트 캐시를 비우고 시작
  await page.request.delete(`/api/admin/cache?namespace=${encodeURIComponent(NS)}`, { headers: auth });
  await page.locator('select').filter({ hasText: NS }).first().selectOption(NS);

  await page.locator('textarea:visible').first().fill('주문 정책 좀');
  await page.getByTitle('전송 (Ctrl+Enter)').click();
  const opts = page.getByTestId('clarify-options').last();
  await expect(opts).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('어느 부분을 볼까요?').last()).toBeVisible();
  const buttons = opts.getByRole('button');
  await expect(buttons).toHaveCount(5);                          // 범위 3 + 전부 보기 + 기타
  await expect(buttons.nth(3)).toContainText('전부 보기');
  await expect(buttons.nth(4)).toContainText('기타');
  await expect(page.getByRole('button', { name: '답변 틀림' })).toHaveCount(0);   // 재질문은 답이 아니다

  // 기타 → 입력창으로
  await buttons.nth(4).click();
  await expect(page.locator('textarea:visible').first()).toBeFocused();

  // ① 선택 → 같은 대화에서 그 범위로 답(다시 묻지 않음)
  const firstLabel = (await buttons.nth(0).innerText()).replace(/^①\s*/, '').replace(/\s*\(\d+개\)$/, '');
  await buttons.nth(0).click();
  await expect(page.getByRole('button', { name: '답변 틀림' })).toHaveCount(1, { timeout: 300_000 });
  await expect(page.getByTestId('clarify-options')).toHaveCount(1);   // 새 재질문 없음
  // 지난 재질문의 선택지는 다시 누를 수 없다(마지막 메시지가 아님)
  await expect(page.getByTestId('clarify-options').first().getByRole('button').first()).toBeDisabled();
  expect(firstLabel.length).toBeGreaterThan(0);
});
