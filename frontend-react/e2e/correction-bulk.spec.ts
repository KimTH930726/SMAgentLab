import { test, expect, type Page, type APIRequestContext } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { createPolicyPart, dropPolicyPart, psql } from './v0-fixture';
import { API, adminToken, loginAdmin } from './auth';

// 정정 요청 일괄 승인(2026-10-21) — 골라서 [선택한 N건 승인] = 건마다 같은 승인 요청을 차례로. 화면에 보인 안을 보내므로
// 그 사이 바뀐 건은 409로 빠지고(이유 표시, 대기 유지) 나머지는 승인된다. DB 값으로 확인한다. LLM 호출 없음(직접 수정 제안).

const NS = 'zz_e2e_correction_bulk';
const P = [
  { name: 'E2E 일괄 A', body: 'A 정책: 적립은 결제 후 1일 뒤.' },
  { name: 'E2E 일괄 B', body: 'B 정책: 쿠폰은 발급 후 7일 사용.' },
  { name: 'E2E 일괄 C', body: 'C 정책: 배송은 3일 이내 출고.' },
  { name: 'E2E 일괄 D', body: 'D 정책: 교환은 1회만 가능.' },
  { name: 'E2E 일괄 E', body: 'E 정책: 반품은 7일 이내.' },   // 한 건씩 보기 시험용(앞 시험이 C·D를 대기로 남김)
  { name: 'E2E 일괄 F', body: 'F 정책: 포인트는 1년 뒤 소멸.' },
];

test.describe.configure({ mode: 'serial' });

const itemId = (name: string) => Number(psql(`SELECT i.id FROM policy_item i JOIN ops_namespace n ON n.id = i.namespace_id `
  + `WHERE n.name = '${NS}' AND i.policy_name = '${name}' AND i.status = 'active'`));
const activeBody = (name: string) => psql(`SELECT i.raw_body FROM policy_item i JOIN ops_namespace n ON n.id = i.namespace_id `
  + `WHERE n.name = '${NS}' AND i.policy_name = '${name}' AND i.status = 'active'`);
const corrStatus = (id: number) => psql(`SELECT status FROM ops_improvement_item WHERE id = ${id}`);

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
  await loginAdmin(page);
  await selectDomain(page, NS);
  await goAdmin(page, '품질', '정정 요청');
  await page.getByTestId('correction-channels').getByRole('button', { name: /^정책 수정/ }).click();   // 채널 칩(10/30, 옛 출처 선택 상자)
}

// 한꺼번에 승인은 [목록] 보기에서만(기본은 한 건씩, 10/27)
async function openList(page: Page) {
  await openQueue(page);
  await page.getByRole('group', { name: '보기' }).getByRole('button', { name: '목록' }).click();
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
  const tok = await adminToken();
  const a = await propose(request, tok, 'E2E 일괄 A', 'A 정책: 적립은 결제 후 2일 뒤.');
  const b = await propose(request, tok, 'E2E 일괄 B', 'B 정책: 쿠폰은 발급 후 14일 사용.');
  await openList(page);
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
  const tok = await adminToken();
  const c = await propose(request, tok, 'E2E 일괄 C', 'C 정책: 배송은 2일 이내 출고.');
  const d = await propose(request, tok, 'E2E 일괄 D', 'D 정책: 교환은 2회까지 가능.');
  await openList(page);
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

test('한 건씩 보기 — 한 장만 보이고 다음·이전으로 넘김, 한꺼번에 승인 버튼은 없음', async ({ page, request }) => {
  test.setTimeout(180_000);
  const tok = await adminToken();
  await propose(request, tok, 'E2E 일괄 A', 'A 정책: 적립은 결제 후 3일 뒤.');
  await propose(request, tok, 'E2E 일괄 B', 'B 정책: 쿠폰은 발급 후 10일 사용.');
  await openQueue(page);
  const pager = page.getByTestId('correction-pager');
  await expect(pager.locator('[id^="correction-"]')).toHaveCount(1);
  const pos = page.getByTestId('correction-position');
  const total = Number((await pos.textContent())!.split('/')[1].trim());
  expect(total).toBeGreaterThanOrEqual(2);
  await expect(pos).toHaveText(`1 / ${total}`);
  await expect(page.getByRole('button', { name: /한꺼번에|선택한 \d+건 승인/ })).toHaveCount(0);
  const first = await pager.locator('[id^="correction-"]').getAttribute('id');
  await page.getByRole('button', { name: '다음 건' }).click();
  await expect(pos).toHaveText(`2 / ${total}`);
  expect(await pager.locator('[id^="correction-"]').getAttribute('id')).not.toBe(first);
  await page.getByRole('button', { name: '이전 건' }).click();
  await expect(pos).toHaveText(`1 / ${total}`);
});

test('한 건씩 보기 — 마지막 건을 처리해도, 거르기로 목록이 줄어도 화면이 안 깨지고 범위 안 카드를 보여 줌(리뷰 10/27)', async ({ page, request }) => {
  test.setTimeout(180_000);
  // 앞 시험이 일부러 남긴 대기 건(그 사이 바뀐 안 → 승인 409)이 마지막 카드가 되면 "마지막 건 승인"이 막힌다 — 이 시험 컬렉션의 대기 건을 비우고 시작(순서 의존 제거, 10/27)
  psql(`DELETE FROM ops_improvement_item WHERE status = 'pending' AND namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  const tok = await adminToken();
  await propose(request, tok, 'E2E 일괄 E', 'E 정책: 반품은 14일 이내.');
  await propose(request, tok, 'E2E 일괄 F', 'F 정책: 포인트는 2년 뒤 소멸.');
  await openQueue(page);
  const pos = page.getByTestId('correction-position');
  const total = Number((await pos.textContent())!.split('/')[1].trim());
  for (let i = 1; i < total; i++) await page.getByRole('button', { name: '다음 건' }).click();
  await expect(pos).toHaveText(`${total} / ${total}`);
  const last = await page.getByTestId('correction-pager').locator('[id^="correction-"]').getAttribute('id');
  await page.getByTestId('correction-pager').getByRole('button', { name: '승인', exact: true }).click();
  await expect.poll(() => corrStatus(Number(last!.slice('correction-'.length)))).toBe('approved');
  if (total > 1) await expect(pos).toHaveText(`${total - 1} / ${total - 1}`);   // 같은 자리 = 이제 마지막 카드
  // 출처 전체에서 뒤쪽으로 간 뒤 좁은 출처로 바꿔도 깨지지 않음
  await page.getByTestId('correction-channels').getByRole('button', { name: /^전체/ }).click();
  const all = Number((await pos.textContent())!.split('/')[1].trim());
  for (let i = 1; i < all; i++) await page.getByRole('button', { name: '다음 건' }).click();
  await page.getByTestId('correction-channels').getByRole('button', { name: /^정책 수정/ }).click();   // 채널 칩(10/30, 옛 출처 선택 상자)
  await expect(pos).toHaveText(/^1 \/ \d+$/);
  await expect(page.getByTestId('correction-pager').locator('[id^="correction-"]')).toHaveCount(1);
});
