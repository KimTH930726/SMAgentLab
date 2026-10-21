import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
import { goAdmin, domainSelect } from './admin-nav';
import { createPolicyPart, dropPolicyPart, psql } from './v0-fixture';

// 정정 요청 일괄 승인(2026-10-21) — 골라서 [선택한 N건 승인] = 건마다 같은 승인 요청을 차례로. 화면에 보인 안을 보내므로
// 그 사이 바뀐 건은 409로 빠지고(이유 표시, 대기 유지) 나머지는 승인된다. DB 값으로 확인한다. LLM 호출 없음(직접 수정 제안).

const NS = 'zz_e2e_correction_bulk';
const API = 'http://localhost:8000';
const P = [
  { name: 'E2E 일괄 A', body: 'A 정책: 적립은 결제 후 1일 뒤.' },
  { name: 'E2E 일괄 B', body: 'B 정책: 쿠폰은 발급 후 7일 사용.' },
  { name: 'E2E 일괄 C', body: 'C 정책: 배송은 3일 이내 출고.' },
  { name: 'E2E 일괄 D', body: 'D 정책: 교환은 1회만 가능.' },
];

test.describe.configure({ mode: 'serial' });

const itemId = (name: string) => Number(psql(`SELECT i.id FROM policy_item i JOIN ops_namespace n ON n.id = i.namespace_id `
  + `WHERE n.name = '${NS}' AND i.policy_name = '${name}' AND i.status = 'active'`));
const activeBody = (name: string) => psql(`SELECT i.raw_body FROM policy_item i JOIN ops_namespace n ON n.id = i.namespace_id `
  + `WHERE n.name = '${NS}' AND i.policy_name = '${name}' AND i.status = 'active'`);
const corrStatus = (id: number) => psql(`SELECT status FROM ops_improvement_item WHERE id = ${id}`);

async function token(request: APIRequestContext): Promise<string> {
  const r = await request.post(`${API}/api/auth/login`, { data: { username: 'admin', password: '1111' } });
  return (await r.json()).access_token as string;
}

/** 정책 화면의 [직접 수정]과 같은 요청 — 화면 흐름은 policy-v0-edit.spec.ts가 본다 */
async function propose(request: APIRequestContext, tok: string, name: string, rawBody: string): Promise<number> {
  const r = await request.post(`${API}/api/corrections`, {
    headers: { Authorization: `Bearer ${tok}` },
    data: { namespace: NS, target_type: 'policy_narrative', target_id: itemId(name), source: 'policy_direct', proposed: { raw_body: rawBody, remark: '' } },
  });
  expect(r.status()).toBe(201);
  return (await r.json()).id as number;
}

async function openQueue(page: Page) {
  await page.goto('/login');
  await page.getByPlaceholder('사용자 아이디').fill('admin');
  await page.getByPlaceholder('비밀번호').fill('1111');
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('heading', { name: '로그인' })).toBeHidden({ timeout: 10_000 });
  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  if (await agentHeading.isVisible().catch(() => false)) await page.getByText('선택하기').first().click();
  await page.getByRole('link', { name: 'Admin' }).click();
  await domainSelect(page).selectOption(NS);
  await goAdmin(page, '품질', '정정 요청');
  await page.getByLabel('출처').selectOption({ label: '정책 직접 수정' });
}

test.beforeAll(async () => {
  test.setTimeout(180_000);
  createPolicyPart(NS, P.map((p) => ({ name: p.name, path: ['테스트'], body: p.body })));
});

test.afterAll(() => {
  psql(`DELETE FROM ops_improvement_item WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  dropPolicyPart(NS);
});

test('두 건 골라 한꺼번에 승인 → 둘 다 새 버전', async ({ page, request }) => {
  test.setTimeout(180_000);
  const tok = await token(request);
  const a = await propose(request, tok, 'E2E 일괄 A', 'A 정책: 적립은 결제 후 2일 뒤.');
  const b = await propose(request, tok, 'E2E 일괄 B', 'B 정책: 쿠폰은 발급 후 14일 사용.');
  await openQueue(page);
  await expect(page.locator(`#correction-${a}`)).toBeVisible();
  await page.getByLabel(`정정 #${a} 선택`).check();
  await page.getByLabel(`정정 #${b} 선택`).check();
  await page.getByRole('button', { name: '선택한 2건 승인' }).click();
  await expect(page.getByRole('status')).toContainText('2건 승인');
  await expect.poll(() => [corrStatus(a), corrStatus(b)]).toEqual(['approved', 'approved']);
  expect(activeBody('E2E 일괄 A')).toBe('A 정책: 적립은 결제 후 2일 뒤.');
  expect(activeBody('E2E 일괄 B')).toBe('B 정책: 쿠폰은 발급 후 14일 사용.');
});

test('그 사이 바뀐 건은 실패(이유 표시·대기 유지), 나머지는 승인', async ({ page, request }) => {
  test.setTimeout(180_000);
  const tok = await token(request);
  const c = await propose(request, tok, 'E2E 일괄 C', 'C 정책: 배송은 2일 이내 출고.');
  const d = await propose(request, tok, 'E2E 일괄 D', 'D 정책: 교환은 2회까지 가능.');
  await openQueue(page);
  await expect(page.locator(`#correction-${d}`)).toContainText('2회까지');
  // 화면을 연 뒤 다른 사람이 D의 수정안을 고침
  const patched = await request.patch(`${API}/api/corrections/${d}/proposed`, {
    headers: { Authorization: `Bearer ${tok}` }, data: { proposed: { raw_body: 'D 정책: 교환은 3회까지 가능.' } },
  });
  expect(patched.ok()).toBeTruthy();
  await page.getByLabel(`승인 가능한 2건 모두`).check();
  await page.getByRole('button', { name: '선택한 2건 승인' }).click();
  const result = page.getByRole('status');
  await expect(result).toContainText('1건 승인');
  await expect(result).toContainText('1건 실패');
  await expect(result).toContainText(`#${d}`);
  await expect(result).toContainText('그 사이 수정안이 바뀌었어요');
  expect(corrStatus(c)).toBe('approved');
  expect(corrStatus(d)).toBe('pending');
  expect(activeBody('E2E 일괄 C')).toBe('C 정책: 배송은 2일 이내 출고.');
  expect(activeBody('E2E 일괄 D')).toBe('D 정책: 교환은 1회만 가능.');      // 보지 못한 안은 반영되지 않는다
  await expect(page.locator(`#correction-${d}`)).toContainText('3회까지');   // 새로 받은 안이 보인다(다시 승인 가능)
});
