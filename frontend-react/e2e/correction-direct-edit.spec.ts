import { test, expect } from '@playwright/test';
import { goAdmin, domainSelect } from './admin-nav';
import { createPolicyPart, dropPolicyPart, psql } from './v0-fixture';
import { API, adminToken, loginAdmin } from './auth';

// 정정 요청 카드의 [직접 수정](2026-10-21 되살림) — 수정안을 담당자가 고쳐 저장 → [승인]이 고친 안으로 새 버전을 만든다.
// 저장만으로는 정책이 안 바뀌고(승인 전 미반영), 누가 고쳤는지 원장에 남는지 DB로 본다. LLM 호출 없음.

const NS = 'zz_e2e_correction_edit';
const NAME = 'E2E 직접 수정 대상';
const OLD = '포인트는 적립 후 30일 뒤 소멸된다.';
const PROPOSED = '포인트는 적립 후 60일 뒤 소멸된다.';
const EDITED = '포인트는 적립 후 90일 뒤 소멸된다.';

const active = () => psql(`SELECT i.version || ':' || i.raw_body FROM policy_item i JOIN ops_namespace n ON n.id = i.namespace_id `
  + `WHERE n.name = '${NS}' AND i.status = 'active'`);

test.beforeAll(async () => {
  test.setTimeout(180_000);
  createPolicyPart(NS, [{ name: NAME, path: ['테스트'], body: OLD }]);
});

test.afterAll(() => {
  psql(`DELETE FROM ops_improvement_item WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  dropPolicyPart(NS);
});

test('카드에서 직접 수정 → 저장(미반영) → 승인 → 고친 안이 새 버전', async ({ page, request }) => {
  test.setTimeout(180_000);
  const tok = await adminToken();
  const itemId = Number(psql(`SELECT i.id FROM policy_item i JOIN ops_namespace n ON n.id = i.namespace_id WHERE n.name = '${NS}' AND i.status = 'active'`));
  const r = await request.post(`${API}/api/corrections`, {
    headers: { Authorization: `Bearer ${tok}` },
    data: { namespace: NS, target_type: 'policy_narrative', target_id: itemId, source: 'policy_direct', proposed: { raw_body: PROPOSED, remark: '' } },
  });
  expect(r.status()).toBe(201);
  const id = (await r.json()).id as number;

  await loginAdmin(page);
  await domainSelect(page).selectOption(NS);
  await goAdmin(page, '품질', '정정 요청');

  const card = page.locator(`#correction-${id}`);
  await expect(card).toContainText('60일');
  await expect(card).toContainText('바뀐 줄');                   // 줄 비교(기존 vs 수정안)
  await card.getByRole('button', { name: '직접 수정' }).click();
  const form = page.locator('form').filter({ has: page.locator(`#cedit-${id}-raw_body`) });
  await expect(form.locator(`#cedit-${id}-raw_body`)).toHaveValue(PROPOSED);   // 지금 수정안으로 채워져 있다
  await expect(form.locator(`#cedit-${id}-chunk_text`)).toHaveCount(0);       // v0는 원문 칸만
  await form.locator(`#cedit-${id}-raw_body`).fill(EDITED);
  const saved = page.waitForResponse((res) => res.url().includes(`/api/corrections/${id}/proposed`));
  await form.getByRole('button', { name: '수정안 저장' }).click();
  expect((await saved).ok()).toBeTruthy();
  await expect(form).toHaveCount(0);

  // 저장만으론 미반영 — 정책은 그대로, 원장 수정안은 고친 안 + 누가 고쳤는지
  expect(active()).toBe(`1:${OLD}`);
  expect(psql(`SELECT (proposed->>'raw_body') || '|' || (proposed->>'chunk_text') || '|' || (ai_verdict->'edits'->0->>'by_name') FROM ops_improvement_item WHERE id = ${id}`))
    .toBe(`${EDITED}|${EDITED}|admin`);
  await expect(card).toContainText('직접 수정한 안');
  await expect(card).toContainText('90일');

  const approved = page.waitForResponse((res) => res.url().includes(`/api/corrections/${id}/approve`));
  await card.getByRole('button', { name: '승인', exact: true }).click();
  expect((await approved).ok()).toBeTruthy();
  await expect.poll(active).toBe(`2:${EDITED}`);
  expect(psql(`SELECT status FROM ops_improvement_item WHERE id = ${id}`)).toBe('approved');
});

test('【확인 필요】 표시를 남긴 수정안은 저장이 막힌다(400 · 수정안 그대로)', async ({ request }) => {
  const tok = await adminToken();
  const itemId = Number(psql(`SELECT i.id FROM policy_item i JOIN ops_namespace n ON n.id = i.namespace_id WHERE n.name = '${NS}' AND i.status = 'active'`));
  const r = await request.post(`${API}/api/corrections`, {
    headers: { Authorization: `Bearer ${tok}` },
    data: { namespace: NS, target_type: 'policy_narrative', target_id: itemId, source: 'policy_direct', proposed: { raw_body: '포인트는 적립 후 120일 뒤 소멸된다.', remark: '' } },
  });
  expect(r.status()).toBe(201);
  const id = (await r.json()).id as number;
  const res = await request.patch(`${API}/api/corrections/${id}/proposed`, {
    headers: { Authorization: `Bearer ${tok}` }, data: { proposed: { raw_body: '포인트는 【확인 필요: 기한】 뒤 소멸된다.' } },
  });
  expect(res.status()).toBe(400);
  expect((await res.json()).detail).toContain('확인 필요');
  expect(psql(`SELECT proposed->>'raw_body' FROM ops_improvement_item WHERE id = ${id}`)).toBe('포인트는 적립 후 120일 뒤 소멸된다.');
});
