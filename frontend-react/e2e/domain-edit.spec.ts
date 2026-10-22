import { test, expect } from '@playwright/test';
import { goAdmin } from './admin-nav';
import { psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 업무 영역 수정 모달(10/21) — 이름과 담당 파트를 한 자리에서. 담당은 N개(10/22, 담당 표 ops_namespace_owner — 조직 › 담당 업무와 같은 표)라
// 화면 문구가 아니라 DB 값으로 전/후를 확인한다(200 OK ≠ 저장됨). 임시 영역은 테스트 종류(policy_test)로 만들어 채팅에 안 섞인다.
const NS = 'E2E영역수정';
const NS2 = 'E2E영역수정2';
// 담당 파트 이름들(이름순, 쉼표) — 담당 표가 기준
const ownersOf = (ns: string) => psql(`SELECT coalesce(string_agg(p.name, ',' ORDER BY p.name), '') FROM ops_namespace n
  JOIN ops_namespace_owner o ON o.namespace_id = n.id JOIN ops_part p ON p.id = o.part_id WHERE n.name = '${ns}'`);
const cleanup = () => psql(`DELETE FROM ops_namespace WHERE name IN ('${NS}', '${NS2}')`);

test.afterEach(cleanup);

test('업무 영역 수정 — 이름과 담당 파트 2개를 바꾸고(확인 창), 하나 빼고, 담당을 없앤다', async ({ page }) => {
  cleanup();
  psql(`INSERT INTO ops_namespace (name, description, kind) VALUES ('${NS}', 'e2e', 'policy_test')`);
  const two = psql(`SELECT id || ':' || name FROM ops_part ORDER BY name LIMIT 2`).split('\n').map((l) => l.trim()).filter(Boolean);
  expect(two.length).toBe(2);
  const [a, b] = two.map((l) => { const i = l.indexOf(':'); return { id: l.slice(0, i), name: l.slice(i + 1) }; });
  expect(ownersOf(NS)).toBe('');

  await loginAdmin(page);
  await goAdmin(page, '컬렉션', '컬렉션');

  // ① 이름 + 담당 2개 함께 → 확인 창(더함 두 파트) → 저장
  const row = page.getByRole('row').filter({ hasText: NS });
  await row.getByTitle('수정(이름·담당 파트)').click();
  const form = page.locator('form').filter({ has: page.locator('#ns-owner') });   // FormModal = <form>(공용 Modal엔 dialog 역할 없음)
  await form.locator('#ns-rename').fill(NS2);
  await form.locator(`#ns-owner-${a.id}`).check();
  await form.locator(`#ns-owner-${b.id}`).check();
  await form.getByRole('button', { name: '저장' }).click();
  const confirm = page.getByRole('alertdialog');
  await expect(confirm).toContainText('더함');
  await expect(confirm).toContainText(a.name);
  await expect(confirm).toContainText(b.name);
  await confirm.getByRole('button', { name: '저장' }).click();
  await expect(form).toHaveCount(0);
  await expect.poll(() => ownersOf(NS2)).toBe([a.name, b.name].sort().join(','));
  expect(psql(`SELECT count(*) FROM ops_namespace WHERE name = '${NS}'`)).toBe('0');
  // 목록 줄에 담당 둘 다
  const row2 = page.getByRole('row').filter({ hasText: NS2 });
  await expect(row2).toContainText(a.name);
  await expect(row2).toContainText(b.name);

  // ② 하나 빼기 → 확인 창(뺌) → 다른 하나는 그대로
  await row2.getByTitle('수정(이름·담당 파트)').click();
  await expect(form.locator(`#ns-owner-${a.id}`)).toBeChecked();
  await expect(form.locator(`#ns-owner-${b.id}`)).toBeChecked();
  await form.locator(`#ns-owner-${b.id}`).uncheck();
  await form.getByRole('button', { name: '저장' }).click();
  await expect(confirm).toContainText('뺌');
  await expect(confirm).toContainText(b.name);
  await confirm.getByRole('button', { name: '저장' }).click();
  await expect(form).toHaveCount(0);
  await expect.poll(() => ownersOf(NS2)).toBe(a.name);

  // ③ 담당 없앰 → 확인 창 → 담당 표 0줄
  await row2.getByTitle('수정(이름·담당 파트)').click();
  await form.locator(`#ns-owner-${a.id}`).uncheck();
  await form.getByRole('button', { name: '저장' }).click();
  await expect(confirm).toContainText('관리자만');
  await confirm.getByRole('button', { name: '저장' }).click();
  await expect(form).toHaveCount(0);
  await expect.poll(() => ownersOf(NS2)).toBe('');

  // ④ 아무것도 안 바꾸면 저장 버튼이 꺼져 있다
  await row2.getByTitle('수정(이름·담당 파트)').click();
  await expect(form.getByRole('button', { name: '저장' })).toBeDisabled();
});
