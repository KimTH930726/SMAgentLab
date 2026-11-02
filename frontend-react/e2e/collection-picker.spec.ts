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
  const target = psql(`SELECT name FROM ops_namespace WHERE kind = 'domain' AND area = 'policy' AND name <> '${current}' ORDER BY id LIMIT 1`);   // 정책 탭 카드(시스템은 [시스템] 탭)
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
  await expect(chip).toContainText(target);
  expect(await page.evaluate(() => {
    const u = JSON.parse(localStorage.getItem('ops_auth') || '{}').user?.username;
    return localStorage.getItem(`opslens.namespace:${u}`);
  })).toBe(target);

});

test('고르는 창 — 카드 아래 "정정 대기 n"·"원문 확인 n" = DB(처리 전만, 0이면 줄 없음)', async ({ page }) => {
  test.setTimeout(90_000);   // 컬렉션 14개를 하나씩 대조
  const rows = psql(`SELECT n.name, COUNT(*) FILTER (WHERE i.kind <> 'source_check'), COUNT(*) FILTER (WHERE i.kind = 'source_check')
                     FROM ops_namespace n JOIN ops_improvement_item i ON i.namespace_id = n.id AND i.status = 'pending'
                     WHERE n.kind = 'domain' AND n.area = 'policy' GROUP BY n.name`).split('\n').filter(Boolean).map((l) => l.split('|'));
  const empty = psql(`SELECT n.name FROM ops_namespace n WHERE n.kind = 'domain' AND n.area = 'policy' AND NOT EXISTS
                      (SELECT 1 FROM ops_improvement_item i WHERE i.namespace_id = n.id AND i.status = 'pending'
                       ) ORDER BY n.id LIMIT 1`);
  await loginAdmin(page);
  await domainSelect(page).click();
  await expect(page.getByRole('heading', { name: '어떤 컬렉션을 볼까요?' })).toBeVisible();
  for (const [name, corr, src] of rows) {
    const line = page.getByTestId(`collection-card-${name}`).getByTestId('pending-chips');
    if (Number(corr)) await expect(line).toContainText(`정정 대기 ${corr}`); else await expect(line).not.toContainText('정정 대기');
    if (Number(src)) await expect(line).toContainText(`원문 확인 ${src}`); else await expect(line).not.toContainText('원문 확인');
  }
  if (empty) await expect(page.getByTestId(`collection-card-${empty}`).getByTestId('pending-chips')).toHaveCount(0);
});

test('고르는 창 — "정정 대기 n ›"를 누르면 그 컬렉션을 고른 채 정정 요청, "원문 확인 n ›"는 정책서 원문 확인 탭', async ({ page }) => {
  test.setTimeout(90_000);
  const corrNs = psql(`SELECT n.name FROM ops_namespace n JOIN ops_improvement_item i ON i.namespace_id = n.id AND i.status = 'pending'
                       AND i.kind <> 'source_check' WHERE n.kind = 'domain' AND n.area = 'policy' ORDER BY n.id LIMIT 1`);
  const srcNs = psql(`SELECT n.name FROM ops_namespace n JOIN ops_improvement_item i ON i.namespace_id = n.id AND i.status = 'pending'
                      AND i.kind = 'source_check' WHERE n.kind = 'domain' AND n.area = 'policy' ORDER BY n.id LIMIT 1`);
  test.skip(!corrNs && !srcNs, '처리 전 건이 있는 정책 컬렉션 없음');
  await loginAdmin(page);
  const chip = domainSelect(page);
  if (corrNs) {
    await chip.click();
    await page.getByTestId(`collection-card-${corrNs}`).getByRole('button', { name: /정정 대기 \d+/ }).click();
    await expect(page).toHaveURL(/\/admin\/q\.corrections/);
    await expect(chip).toContainText(corrNs);
    await expect(page.getByRole('heading', { name: '어떤 컬렉션을 볼까요?' })).toBeHidden();
  }
  if (srcNs) {
    await chip.click();
    await page.getByTestId(`collection-card-${srcNs}`).getByRole('button', { name: /원문 확인 \d+/ }).click();
    await expect(page).toHaveURL(/\/admin\/k\.policy\?tab=checks/);
    await expect(chip).toContainText(srcNs);
    await expect(page.getByRole('tab', { name: /원문 확인/ })).toHaveAttribute('aria-selected', 'true');
  }
});
