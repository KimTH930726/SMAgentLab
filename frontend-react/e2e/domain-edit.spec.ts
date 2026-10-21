import { test, expect } from '@playwright/test';
import { goAdmin } from './admin-nav';
import { psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 업무 영역 수정 모달(10/21) — 이름과 담당 파트를 한 자리에서. 담당은 조직 › 담당 업무 영역과 같은 칸(owner_part_id)이라
// 화면 문구가 아니라 DB 값으로 전/후를 확인한다(200 OK ≠ 저장됨). 임시 영역은 테스트 종류(policy_test)로 만들어 채팅에 안 섞인다.
const NS = 'E2E영역수정';
const NS2 = 'E2E영역수정2';
const ownerOf = (ns: string) => psql(`SELECT coalesce(p.name, '') FROM ops_namespace n LEFT JOIN ops_part p ON p.id = n.owner_part_id WHERE n.name = '${ns}'`);
const cleanup = () => psql(`DELETE FROM ops_namespace WHERE name IN ('${NS}', '${NS2}')`);

test.afterEach(cleanup);

test('업무 영역 수정 — 이름과 담당 파트를 바꾸고(확인 창), 담당을 없앤다', async ({ page }) => {
  cleanup();
  psql(`INSERT INTO ops_namespace (name, description, kind) VALUES ('${NS}', 'e2e', 'policy_test')`);
  const part = psql(`SELECT name FROM ops_part ORDER BY id LIMIT 1`);
  expect(part).not.toBe('');
  expect(ownerOf(NS)).toBe('');

  await loginAdmin(page);
  await goAdmin(page, '업무 영역', '업무 영역');

  // ① 이름 + 담당 함께 바꾸기 → 확인 창 → 저장
  const row = page.getByRole('row').filter({ hasText: NS });
  await row.getByTitle('수정(이름·담당 파트)').click();
  const form = page.locator('form').filter({ has: page.locator('#ns-owner') });   // FormModal = <form>(공용 Modal엔 dialog 역할 없음)
  await form.locator('#ns-rename').fill(NS2);
  await form.locator('#ns-owner').selectOption(part);
  await form.getByRole('button', { name: '저장' }).click();
  const confirm = page.getByRole('alertdialog');
  await expect(confirm).toContainText(part);
  await confirm.getByRole('button', { name: '저장' }).click();
  await expect(form).toHaveCount(0);
  await expect.poll(() => ownerOf(NS2)).toBe(part);
  expect(psql(`SELECT count(*) FROM ops_namespace WHERE name = '${NS}'`)).toBe('0');

  // ② 담당 없앰 → 확인 창 → DB에서 NULL
  await page.getByRole('row').filter({ hasText: NS2 }).getByTitle('수정(이름·담당 파트)').click();
  await expect(form.locator('#ns-owner')).toHaveValue(part);
  await form.locator('#ns-owner').selectOption('');
  await form.getByRole('button', { name: '저장' }).click();
  await expect(confirm).toContainText('관리자만');
  await confirm.getByRole('button', { name: '저장' }).click();
  await expect(form).toHaveCount(0);
  await expect.poll(() => ownerOf(NS2)).toBe('');

  // ③ 아무것도 안 바꾸면 저장 버튼이 꺼져 있다
  await page.getByRole('row').filter({ hasText: NS2 }).getByTitle('수정(이름·담당 파트)').click();
  await expect(form.getByRole('button', { name: '저장' })).toBeDisabled();
});
