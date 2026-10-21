import { test, expect } from '@playwright/test';
import { goAdmin, domainSelect } from './admin-nav';
import { execFileSync } from 'node:child_process';

// 사내 LLM 사용량(2026-10-12) — "토큰이 너무 많이 나간다"는데 기능별 숫자가 없던 것. 시스템 설정 화면의 사용량 표가 실제 기록(DB)과
// 같은 호출 수를 보여주는지 확인한다(읽기 전용 — LLM을 부르지 않는다).
const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();

test('시스템 설정 — 사내 LLM 사용량이 기록과 같은 호출 수로 보인다', async ({ page }) => {
  const calls = psql("SELECT count(*) FROM ops_llm_usage WHERE created_at > NOW() - interval '7 days'");
  await page.goto('/login');
  await page.getByPlaceholder('사용자 아이디').fill('admin');
  await page.getByPlaceholder('비밀번호').fill('1111');
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('heading', { name: '로그인' })).toBeHidden({ timeout: 10_000 });
  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  if (await agentHeading.isVisible().catch(() => false)) await page.getByText('선택하기').first().click();
  await page.getByRole('link', { name: 'Admin' }).click();
  await goAdmin(page, '시스템', '설정');
  await page.getByRole('button', { name: 'LLM 프로바이더' }).click();   // 사용량은 LLM 프로바이더 영역 안
  const panel = page.getByTestId('llm-usage');
  await expect(panel).toBeVisible();
  await expect(panel).toContainText(`호출 ${Number(calls).toLocaleString()}`);
  if (calls !== '0') await expect(panel.locator('tbody tr').first()).toBeVisible();
});
