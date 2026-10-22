import { test, expect, type Page } from '@playwright/test';
import { goAdmin, domainSelect } from './admin-nav';
import { createPolicyPart, dropPolicyPart, psql } from './v0-fixture';
import { API, adminToken, loginAdmin } from './auth';

// 정책서 화면 → 직접 수정 → 정정 요청 → 승인 = 새 버전(v0 재구축 2단계, 2026-10-21).
// 핵심 약속: 직접 수정은 정책 테이블을 건드리지 않고 정정 요청에 제안 1건만 만든다(승인 전 검색에 안 나옴) →
// 같은 정책에 두 번째 제안은 409 → 승인하면 옛 버전 deprecated · 새 버전 active(새 원문·비고) · 검색이 새 원문을 찾는다.
// 화면 문구가 아니라 DB 값과 실제 검색 API로 전/후를 본다(200 OK ≠ 반영됨). LLM 호출 없음(직접 수정 경로).

const NS = 'zz_e2e_policy_edit';
const NAME = 'E2E 반품 기한';
const OLD = '반품은 수령 후 7일 이내에 신청할 수 있다.';
const NEW = '반품은 수령 후 14일 이내에 신청할 수 있다.';

test.describe.configure({ mode: 'serial' });

const nsId = () => psql(`SELECT id FROM ops_namespace WHERE name = '${NS}'`);
const rows = () => psql(`SELECT version || ':' || status || ':' || raw_body || ':' || coalesce(remark, '') FROM policy_item `
  + `WHERE namespace_id = ${nsId()} ORDER BY version`).split('\n').filter(Boolean);

async function login(page: Page) {
  await loginAdmin(page, { openAdmin: false });
}

async function openPolicy(page: Page) {
  await page.getByRole('link', { name: 'Admin' }).click();
  await domainSelect(page).selectOption(NS);
  await goAdmin(page, '지식', '정책서');
  const group = page.getByRole('button', { name: /^테스트/ });
  await expect(group).toHaveAttribute('aria-expanded', 'true');   // 처음엔 모든 절이 펼쳐져 있다(10/22)
  await page.getByRole('button', { name: new RegExp(NAME) }).click();
  await expect(page.getByRole('heading', { name: '정책' })).toBeVisible();
}

test.beforeAll(async () => {
  test.setTimeout(180_000);   // 임시 정책 임베딩 — 백엔드 컨테이너에 새 프로세스로 임베딩 모델을 올려 20~40초
  createPolicyPart(NS, [{ name: NAME, path: ['테스트', '반품'], body: OLD, remark: '옛 비고' }]);
});

test.afterAll(() => {
  psql(`DELETE FROM ops_improvement_item WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  dropPolicyPart(NS);
});

test('직접 수정 → 정정 요청(정책 직접 수정) → 승인 → 옛 버전 폐기·새 버전 사용·검색 반영', async ({ page, request }) => {
  test.setTimeout(240_000);
  await login(page);
  await openPolicy(page);
  await expect(page.getByText(OLD)).toBeVisible();
  await expect(page.getByText('옛 비고')).toBeVisible();

  // [AI 수정 제안]과 [직접 수정]은 같은 모양으로 나란히
  await expect(page.getByRole('button', { name: 'AI 수정 제안' })).toBeEnabled();
  await page.getByRole('button', { name: '직접 수정' }).click();
  const form = page.locator('form').filter({ has: page.locator('#policy-edit-body') });
  await expect(form.locator('#policy-edit-body')).toHaveValue(OLD);
  await expect(form.getByRole('button', { name: '정정 요청에 올리기' })).toBeDisabled();   // 안 바꾸면 못 올림
  await form.locator('#policy-edit-body').fill(NEW);
  await form.locator('#policy-edit-remark').fill('새 비고');
  const created = page.waitForResponse((r) => r.url().endsWith('/api/corrections') && r.request().method() === 'POST');
  await form.getByRole('button', { name: '정정 요청에 올리기' }).click();
  const body = await (await created).json();
  const corrId = body.id as number;
  expect(corrId).toBeGreaterThan(0);
  await page.getByTestId('app-dialog').getByRole('button', { name: '확인' }).click();

  // 승인 전: 정책 테이블 그대로(행 1개), 원장에 제안 1건(출처 policy_direct, 수정안 = 쓴 그대로)
  expect(rows()).toEqual([`1:active:${OLD}:옛 비고`]);
  expect(psql(`SELECT source || '|' || status || '|' || (proposed->>'raw_body') || '|' || (proposed->>'remark') FROM ops_improvement_item WHERE id = ${corrId}`))
    .toBe(`policy_direct|pending|${NEW}|새 비고`);

  // 상세에 "정정 #N 검토 중" + 새 제안 막힘, 서버도 409(그 번호를 같이)
  await expect(page.getByText(`정정 #${corrId} 검토 중`).first()).toBeVisible();   // 상세 안내 + 뒤 목록 배지
  await expect(page.getByRole('button', { name: '직접 수정' })).toBeDisabled();
  const tok = await adminToken();
  const itemId = Number(psql(`SELECT id FROM policy_item WHERE namespace_id = ${nsId()} AND status = 'active'`));
  const dup = await request.post(`${API}/api/corrections`, {
    headers: { Authorization: `Bearer ${tok}` },
    data: { namespace: NS, target_type: 'policy_narrative', target_id: itemId, source: 'policy_direct', proposed: { raw_body: '또 다른 안', remark: '' } },
  });
  expect(dup.status()).toBe(409);
  expect((await dup.json()).detail.existing_id).toBe(corrId);

  // 정정 요청으로 이동 → 그 카드(출처 "정책 직접 수정") → 승인
  await page.getByRole('button', { name: '정정 요청에서 보기 →' }).click();
  await expect(page).toHaveURL(new RegExp(`/admin/q\\.corrections\\?focus=${corrId}`));
  const card = page.locator(`#correction-${corrId}`);
  await expect(card).toContainText('정책 직접 수정');
  await expect(card).toContainText('14일');
  const approved = page.waitForResponse((r) => r.url().includes(`/api/corrections/${corrId}/approve`));
  await card.getByRole('button', { name: '승인', exact: true }).click();
  expect((await approved).ok()).toBeTruthy();

  // 승인 후: 옛 버전 deprecated, 새 버전 active(새 원문·새 비고, v2)
  await expect.poll(rows).toEqual([`1:deprecated:${OLD}:옛 비고`, `2:active:${NEW}:새 비고`]);
  expect(psql(`SELECT status FROM ops_improvement_item WHERE id = ${corrId}`)).toBe('approved');

  // 검색(채팅과 같은 대상 기준)이 새 원문을 찾고 옛 원문은 안 찾는다
  const s = await request.get(`${API}/api/policy/search?namespace=${encodeURIComponent(NS)}&q=${encodeURIComponent('반품 신청 기한')}`,
    { headers: { Authorization: `Bearer ${tok}` } });
  expect(s.ok()).toBeTruthy();
  const texts = ((await s.json()).narratives as { chunk_text: string }[]).map((n) => n.chunk_text);
  expect(texts).toContain(NEW);
  expect(texts).not.toContain(OLD);

  // 정책 상세: v2 · 버전 이력에 "정정 #N"
  await openPolicy(page);
  await expect(page.getByText(NEW)).toBeVisible();
  await expect(page.getByText(`· 정정 #${corrId}`)).toBeVisible();
  await expect(page.getByRole('button', { name: '직접 수정' })).toBeEnabled();   // 검토 중 정정이 끝났으니 다시 제안 가능
});
