import { test, expect } from '@playwright/test';
import { domainSelect, goAdmin, selectDomain } from './admin-nav';
import { createSystem, dropSystem, psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 정책 컬렉션 현황(10/23) — 수정 창은 이름만(담당 파트는 시스템·권한 › 조직·권한 › 담당 컬렉션 한 곳), 줄마다 [지식 등록 →]·[보기 →].
// 시스템 컬렉션 줄도 같은 두 링크(11/02 — 옛 조직 컬렉션 카드). 이름·담당은 화면 문구가 아니라 DB 값으로 전/후를 확인한다(200 OK ≠ 저장됨).
// 임시 컬렉션은 테스트 종류(policy_test)로 만들어 채팅에 안 섞인다.
const NS = 'E2E영역수정';
const NS2 = 'E2E영역수정2';
const SYS = 'E2E영역수정시스템';
const ownersOf = (ns: string) => psql(`SELECT coalesce(string_agg(p.name, ',' ORDER BY p.name), '') FROM ops_namespace n
  JOIN ops_namespace_owner o ON o.namespace_id = n.id JOIN ops_part p ON p.id = o.part_id WHERE n.name = '${ns}'`);
const cleanup = () => psql(`DELETE FROM ops_namespace WHERE name IN ('${NS}', '${NS2}')`);
const selectedNs = (page: import('@playwright/test').Page) => page.evaluate(() => (v => { try { return v && v.startsWith('{') ? JSON.parse(v).name : v; } catch { return v; } })(localStorage.getItem('opslens.namespace:admin')));   // 저장 값 = {id, name}(키 전환 2단계)
// 운영 지식 화면의 서브탭(지식 조회 · 지식 등록 · 승인 대기) — 고른 탭만 indigo 밑줄
const docsTab = (page: import('@playwright/test').Page, name: string) => page.getByRole('button', { name, exact: true });

test.afterEach(cleanup);

test('정책 컬렉션 — 이름만 수정(담당은 그대로) · [보기 →]만(운영 지식은 시스템에만 — [지식 등록 →] 없음)', async ({ page }) => {
  cleanup();
  psql(`INSERT INTO ops_namespace (name, description, kind) VALUES ('${NS}', 'e2e', 'policy_test')`);
  const part = psql(`SELECT id || ':' || name FROM ops_part ORDER BY name LIMIT 1`).trim();
  const i = part.indexOf(':');
  const [pid, pname] = [part.slice(0, i), part.slice(i + 1)];
  psql(`INSERT INTO ops_namespace_owner (namespace_id, part_id) SELECT id, ${pid} FROM ops_namespace WHERE name = '${NS}'`);
  expect(ownersOf(NS)).toBe(pname);

  await loginAdmin(page);
  await goAdmin(page, '컬렉션', '정책 컬렉션');

  // ① 수정 창 = 이름만(담당 칸 없음). 안 바꾸면 저장 꺼짐 → 이름 바꿔 저장 → DB 이름 바뀜 · 담당 그대로
  const row = page.getByRole('row').filter({ hasText: NS });
  await row.getByTitle('이름 수정').click();
  const form = page.locator('form').filter({ has: page.locator('#ns-rename') });   // FormModal = <form>(공용 Modal엔 dialog 역할 없음)
  await expect(form.locator('[id^="ns-owner"]')).toHaveCount(0);
  await expect(form).toContainText('시스템·권한 › 조직·권한 › 담당 컬렉션');
  await expect(form.getByRole('button', { name: '저장' })).toBeDisabled();
  await form.locator('#ns-rename').fill(NS2);
  await form.getByRole('button', { name: '저장' }).click();
  await expect(form).toHaveCount(0);
  await expect.poll(() => psql(`SELECT count(*) FROM ops_namespace WHERE name = '${NS2}'`)).toBe('1');
  expect(psql(`SELECT count(*) FROM ops_namespace WHERE name = '${NS}'`)).toBe('0');
  expect(ownersOf(NS2)).toBe(pname);

  // ② [보기 →] — 그 컬렉션을 고르고 운영 지식 목록으로
  const row2 = page.getByRole('row').filter({ hasText: NS2 });
  await expect(row2).toContainText(pname);
  await row2.getByRole('button', { name: '보기 →' }).click();
  await expect(page).toHaveURL(/\/admin\/k\.docs$/);
  await expect.poll(() => selectedNs(page)).toBe(NS2);   // Sidebar effect가 쓴다 — 한 번 읽기는 가끔 이름(리뷰 L3)
  await expect(docsTab(page, '지식 조회')).toHaveClass(/border-indigo-500/);

  // ③ 정책 컬렉션 줄엔 [지식 등록 →]이 없다(11/02 — 운영 지식은 시스템 컬렉션에만, 정책서는 정책서 가져오기로)
  await goAdmin(page, '컬렉션', '정책 컬렉션');
  await expect(page.getByRole('row').filter({ hasText: NS2 }).getByRole('button', { name: '지식 등록 →' })).toHaveCount(0);
});

test('시스템 컬렉션 줄 — [지식 등록 →]·[보기 →] 둘 다, [보기 →]는 그 컬렉션을 고르고 운영 지식 목록으로', async ({ page }) => {
  createSystem(SYS);
  try {
    await loginAdmin(page);
    await goAdmin(page, '컬렉션', '시스템 컬렉션');
    const row = page.getByTestId('system-collections').getByRole('row').filter({ hasText: SYS });
    await expect(row.getByRole('button', { name: '지식 등록 →' })).toBeVisible();   // 관리자 = 쓰기 가능
    await row.getByRole('button', { name: '보기 →' }).click();
    await expect(page).toHaveURL(/\/admin\/k\.docs$/);
    await expect.poll(() => selectedNs(page)).toBe(SYS);
    await expect(docsTab(page, '지식 조회')).toHaveClass(/border-indigo-500/);
  } finally {
    dropSystem(SYS);
  }
});

test('고른 컬렉션 기억은 id로 — 이름이 바뀌어도 새로고침 뒤 같은 컬렉션(키 전환 2단계, 옛 이름 값도 읽음)', async ({ page }) => {
  cleanup();
  psql(`INSERT INTO ops_namespace (name, description, kind) VALUES ('${NS}', 'e2e', 'policy_test')`);
  const id = Number(psql(`SELECT id FROM ops_namespace WHERE name = '${NS}'`).trim());
  await loginAdmin(page);
  await selectDomain(page, NS);   // 옛 형식(이름 글자)으로 넣는다 → 앱이 {id, name}으로 다시 쓴다
  await expect.poll(() => page.evaluate(() => localStorage.getItem('opslens.namespace:admin'))).toBe(JSON.stringify({ id, name: NS }));
  psql(`UPDATE ops_namespace SET name = '${NS2}' WHERE id = ${id}`);   // 다른 곳(다른 관리자·스크립트)에서 이름을 바꿈
  await page.reload();
  await expect(domainSelect(page)).toContainText(NS2);
  await expect.poll(() => selectedNs(page)).toBe(NS2);
});
