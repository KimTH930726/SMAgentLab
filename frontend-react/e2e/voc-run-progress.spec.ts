import { test, expect } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { loginAdmin } from './auth';
import { createSystem, dropSystem } from './v0-fixture';

// VOC [지금 실행](11/06) — 서버가 잡고 바로 응답 → 뒤에서 실행 → 화면이 진행을 읽다가 결과 표. 새로고침해도 마지막 결과가 서버에서 다시 온다.
// 라우팅 없는 임시 시스템으로 돌린다 — 메일 수집·LLM·팀즈 발송 없음(실제 메일함으로 돌리면 발송까지 나간다).
// 진행 막대 중간 모양(끝남/전체·메일함 i/n)은 tests/test_email_voc_manual_run.py · test_email_voc_pipeline.py가 본다.

const SYS = 'E2E지금실행';
const KEY = `${SYS} 시스템`;

test('VOC 지금 실행 — 뒤에서 실행 → 결과 표 · 새로고침해도 마지막 결과', async ({ page }) => {
  test.setTimeout(60_000);
  createSystem(KEY);
  try {
    await loginAdmin(page);
    await goAdmin(page, 'VOC 이메일');
    await selectDomain(page, KEY, SYS);
    await page.getByRole('button', { name: '지금 실행' }).click();
    await expect(page.getByText(/실행 결과 \(0개 메일함\)/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/등록된 라우팅 매핑이 없습니다/)).toBeVisible();
    await expect(page.getByTestId('progress')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '지금 실행' })).toBeEnabled();

    await page.reload();
    await expect(page.getByText(/실행 결과 \(0개 메일함\)/)).toBeVisible({ timeout: 10_000 });   // 결과는 서버가 들고 있다
  } finally {
    dropSystem(KEY);
  }
});
