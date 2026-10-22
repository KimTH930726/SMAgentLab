import { test, expect } from '@playwright/test';
import { domainSelect } from './admin-nav';
import { psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 보는 컬렉션 고르기(10/22, 컬렉션 설계 S5·S6) — ① 고르는 창에 테스트 컬렉션이 안 보인다 ② 카드를 누르면 칩이 바뀐다
// ③ 새로고침해도 고른 값이 그대로(예전엔 저장이 없어 내 담당으로 덮였다).
const HIDDEN = 'E2E숨김컬렉션';
const cleanup = () => psql(`DELETE FROM ops_namespace WHERE name = '${HIDDEN}'`);
const esc = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

test.afterEach(cleanup);

test('보는 컬렉션 — 테스트 컬렉션 숨김 · 고르면 칩 반영 · 새로고침 유지', async ({ page }) => {
  cleanup();
  psql(`INSERT INTO ops_namespace (name, description, kind) VALUES ('${HIDDEN}', 'e2e', 'policy_test')`);

  await loginAdmin(page);
  const chip = domainSelect(page);
  // 지금 고른 값과 다른 일반 업무를 고른다 — 같으면 바뀐 게 안 보이고, 경로 표시줄 버튼과 이름이 겹친다(리뷰)
  const current = (await chip.innerText()).trim().replace(/'/g, "''");
  const target = psql(`SELECT name FROM ops_namespace WHERE kind = 'domain' AND name <> '${current}' ORDER BY id LIMIT 1`);
  expect(target).not.toBe('');

  await chip.click();
  const title = page.getByRole('heading', { name: '어떤 컬렉션을 볼까요?' });
  await expect(title).toBeVisible();
  // 공용 Modal엔 dialog 역할이 없다 — 제목과 카드를 함께 품은 가장 안쪽 상자로 좁힌다
  const dialog = page.locator('div').filter({ has: title }).filter({ has: page.getByRole('button', { name: /지식 \d+|비어 있음/ }) }).last();
  await expect(dialog.getByRole('button', { name: new RegExp(`^${esc(HIDDEN)}`) })).toHaveCount(0);   // ① 숨김

  await dialog.getByRole('button', { name: new RegExp(`^${esc(target)}`) }).first().click();          // ② 고르기
  await expect(title).toBeHidden();
  await expect(chip).toContainText(target);

  await page.reload();                                                                                 // ③ 새로고침 유지
  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  await expect(agentHeading.or(chip)).toBeVisible();
  if (await agentHeading.isVisible().catch(() => false)) await page.getByText('선택하기').first().click();
  await expect(chip).toContainText(target);
  expect(await page.evaluate(() => {
    const u = JSON.parse(localStorage.getItem('ops_auth') || '{}').user?.username;
    return localStorage.getItem(`opslens.namespace:${u}`);
  })).toBe(target);

});
