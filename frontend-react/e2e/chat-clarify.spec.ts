import { test, expect } from '@playwright/test';
import { loginAdmin } from './auth';

// 재질문(v2.134, 2026-10-12) — 주제만 말한 질문의 목록이 크면 답 대신 범위 선택지(①②③ + 전부 보기 + 기타)를 보여주고,
// 고르면 같은 대화에서 그 범위로 답한다(다시 묻지 않음). 재질문은 답이 아니라 "답변 틀림"이 없다. 실 dev 스택 그대로(LLM 1회).
// v0 정책서 파트(2026-10-20 현행화) — "배송"은 정책 36개·하위 절 9개라 재질문이 뜬다(실데이터로 확인: 범위 3 + 전부 보기).
// 정책서 파트를 고르면 정책 검색 범위가 그 파트뿐이다(service/policy/scope.py).
const NS = '온라인스토어';
const Q = '배송 정책 좀';

test('주제만 말한 큰 목록 → 범위 선택지 → 고르면 그 범위로 답', async ({ page }) => {
  test.setTimeout(360_000);   // 선택 후 답은 LLM(E2E는 로컬 LLM — llm-local.setup.ts)
  page.on('dialog', (d) => { throw new Error(`브라우저 기본 대화상자: ${d.message()}`); });
  await loginAdmin(page, { openAdmin: false });
  const authState = await page.evaluate(() => localStorage.getItem('ops_auth'));
  const auth = { Authorization: `Bearer ${authState ? JSON.parse(authState).accessToken : ''}` };
  // 캐시된 예전 답(재질문 전)이 있으면 재질문이 안 나온다 — 이 파트 캐시를 비우고 시작
  await page.request.delete(`/api/admin/cache?namespace=${encodeURIComponent(NS)}`, { headers: auth });
  await page.locator('select').filter({ hasText: NS }).first().selectOption(NS);

  await page.locator('textarea:visible').first().fill(Q);
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
