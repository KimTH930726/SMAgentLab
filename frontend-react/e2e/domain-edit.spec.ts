import { test, expect } from '@playwright/test';
import { goAdmin } from './admin-nav';
import { psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 업무 컬렉션 현황(10/23) — 수정 창은 이름만(담당 파트는 시스템·권한 › 조직·권한 › 담당 업무 한 곳), 줄마다 [지식 등록 →]·[보기 →].
// 조직 컬렉션 카드도 같은 두 링크. 이름·담당은 화면 문구가 아니라 DB 값으로 전/후를 확인한다(200 OK ≠ 저장됨).
// 임시 컬렉션은 테스트 종류(policy_test)로 만들어 채팅에 안 섞인다.
const NS = 'E2E영역수정';
const NS2 = 'E2E영역수정2';
const ownersOf = (ns: string) => psql(`SELECT coalesce(string_agg(p.name, ',' ORDER BY p.name), '') FROM ops_namespace n
  JOIN ops_namespace_owner o ON o.namespace_id = n.id JOIN ops_part p ON p.id = o.part_id WHERE n.name = '${ns}'`);
const cleanup = () => psql(`DELETE FROM ops_namespace WHERE name IN ('${NS}', '${NS2}')`);
const selectedNs = (page: import('@playwright/test').Page) => page.evaluate(() => localStorage.getItem('opslens.namespace:admin'));
// 지식 문서 화면의 서브탭(지식 조회 · 지식 등록 · 승인 대기) — 고른 탭만 indigo 밑줄
const docsTab = (page: import('@playwright/test').Page, name: string) => page.getByRole('button', { name, exact: true });

test.afterEach(cleanup);

test('업무 컬렉션 — 이름만 수정(담당은 그대로) · [지식 등록 →]·[보기 →]로 그 컬렉션을 고른 채 지식 문서로', async ({ page }) => {
  cleanup();
  psql(`INSERT INTO ops_namespace (name, description, kind) VALUES ('${NS}', 'e2e', 'policy_test')`);
  const part = psql(`SELECT id || ':' || name FROM ops_part ORDER BY name LIMIT 1`).trim();
  const i = part.indexOf(':');
  const [pid, pname] = [part.slice(0, i), part.slice(i + 1)];
  psql(`INSERT INTO ops_namespace_owner (namespace_id, part_id) SELECT id, ${pid} FROM ops_namespace WHERE name = '${NS}'`);
  expect(ownersOf(NS)).toBe(pname);

  await loginAdmin(page);
  await goAdmin(page, '컬렉션', '업무 컬렉션');

  // ① 수정 창 = 이름만(담당 칸 없음). 안 바꾸면 저장 꺼짐 → 이름 바꿔 저장 → DB 이름 바뀜 · 담당 그대로
  const row = page.getByRole('row').filter({ hasText: NS });
  await row.getByTitle('이름 수정').click();
  const form = page.locator('form').filter({ has: page.locator('#ns-rename') });   // FormModal = <form>(공용 Modal엔 dialog 역할 없음)
  await expect(form.locator('[id^="ns-owner"]')).toHaveCount(0);
  await expect(form).toContainText('시스템·권한 › 조직·권한 › 담당 업무');
  await expect(form.getByRole('button', { name: '저장' })).toBeDisabled();
  await form.locator('#ns-rename').fill(NS2);
  await form.getByRole('button', { name: '저장' }).click();
  await expect(form).toHaveCount(0);
  await expect.poll(() => psql(`SELECT count(*) FROM ops_namespace WHERE name = '${NS2}'`)).toBe('1');
  expect(psql(`SELECT count(*) FROM ops_namespace WHERE name = '${NS}'`)).toBe('0');
  expect(ownersOf(NS2)).toBe(pname);

  // ② [보기 →] — 그 컬렉션을 고르고 지식 문서 목록으로
  const row2 = page.getByRole('row').filter({ hasText: NS2 });
  await expect(row2).toContainText(pname);
  await row2.getByRole('button', { name: '보기 →' }).click();
  await expect(page).toHaveURL(/\/admin\/k\.docs$/);
  await expect.poll(() => selectedNs(page)).toBe(NS2);   // Sidebar effect가 쓴다 — 한 번 읽기는 가끔 이름(리뷰 L3)
  await expect(docsTab(page, '지식 조회')).toHaveClass(/border-indigo-500/);

  // ③ [지식 등록 →] — 같은 컬렉션을 고르고 등록 탭으로(관리자 = 쓸 수 있음)
  await goAdmin(page, '컬렉션', '업무 컬렉션');
  await page.getByRole('row').filter({ hasText: NS2 }).getByRole('button', { name: '지식 등록 →' }).click();
  await expect(page).toHaveURL(/\/admin\/k\.docs\?tab=ingest$/);
  await expect.poll(() => selectedNs(page)).toBe(NS2);
  await expect(docsTab(page, '지식 등록')).toHaveClass(/border-indigo-500/);   // 주소만이 아니라 등록 탭이 실제로 열림(리뷰 L2)
});

test('조직 컬렉션 카드 — [지식 등록 →]·[보기 →] 둘 다, [보기 →]는 그 컬렉션을 고르고 지식 문서 목록으로', async ({ page }) => {
  const team = psql(`SELECT t.id || ':' || t.name FROM ops_team t JOIN ops_namespace n ON n.name = 'org:team:' || t.id ORDER BY t.id LIMIT 1`).trim();
  test.skip(!team, '팀(조직 컬렉션)이 없음');
  const j = team.indexOf(':');
  const [tid, tname] = [team.slice(0, j), team.slice(j + 1)];

  await loginAdmin(page);
  await goAdmin(page, '컬렉션', '조직 컬렉션');
  const panel = page.getByTestId('org-collections');
  await panel.getByRole('button', { name: new RegExp(`^${tname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) }).click();
  const links = panel.getByRole('button', { name: '보기 →' });
  await expect(links.first()).toBeVisible();
  await expect(panel.getByRole('button', { name: '지식 등록 →' })).toHaveCount(await links.count());   // 관리자 = 모든 카드 쓰기 가능
  await links.first().click();   // 첫 카드 = 팀 공통
  await expect(page).toHaveURL(/\/admin\/k\.docs$/);
  await expect.poll(() => selectedNs(page)).toBe(`org:team:${tid}`);
  await expect(docsTab(page, '지식 조회')).toHaveClass(/border-indigo-500/);
});
