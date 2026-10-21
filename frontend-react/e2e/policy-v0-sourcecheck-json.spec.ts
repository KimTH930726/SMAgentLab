import { readFileSync } from 'node:fs';
import { test, expect, type Page } from '@playwright/test';
import { goAdmin, domainSelect } from './admin-nav';
import { createPolicyPart, dropPolicyPart, psql } from './v0-fixture';
import { adminToken, loginAdmin } from './auth';

// 정책서 v0 재구축 3단계(2026-10-21) — 원문 확인 + JSON 가져오기.
// 약속 1: 원문 확인은 정정이 아니다 — [해결]은 원장 항목(kind=source_check)만 approved + 메모로 바꾸고, 정정 요청 배지에 안 섞인다.
// 약속 2: JSON 가져오기는 정책 테이블을 건드리지 않고 바뀐 정책마다 정정 제안(source json_import)만 만든다 → 정정 요청에서 승인해야 새 버전.
// 화면 문구가 아니라 DB 값으로 전/후를 본다. LLM 호출 없음.

const NS = 'zz_e2e_policy_srccheck';
const P1 = { name: 'E2E 교환 기한', path: ['테스트', '교환'], body: '교환은 수령 후 7일 이내에 신청한다.', remark: '비고 1' };
const P2 = { name: 'E2E 반품 기한', path: ['테스트', '반품'], body: '반품은 수령 후 7일 이내에 신청한다.' };
const NEW_BODY = '반품은 수령 후 14일 이내에 신청한다.';

test.describe.configure({ mode: 'serial' });

const nsId = () => psql(`SELECT id FROM ops_namespace WHERE name = '${NS}'`);
const rows = (name: string) => psql(`SELECT version || ':' || status || ':' || raw_body FROM policy_item `
  + `WHERE namespace_id = ${nsId()} AND policy_name = '${name}' ORDER BY version`).split('\n').filter(Boolean);

async function login(page: Page) {
  await loginAdmin(page, { openAdmin: false });
}

async function openPolicyTab(page: Page, tab: string) {
  await page.getByRole('link', { name: 'Admin' }).click();
  await domainSelect(page).selectOption(NS);
  await goAdmin(page, '지식', '정책서');
  await page.getByRole('tab', { name: tab }).click();
}

let checkId = 0;

test.beforeAll(async () => {
  test.setTimeout(180_000);   // 임시 정책 임베딩 — 백엔드 컨테이너에 새 프로세스로 임베딩 모델을 올려 20~40초
  createPolicyPart(NS, [P1, P2]);
  // JSON 짝짓기는 원천 키로 한다 — 픽스처 정책에 키를 붙인다
  psql(`UPDATE policy_item SET source_key = 'e2e_src_' || logical_id WHERE namespace_id = ${nsId()}`);
  const lid = psql(`SELECT logical_id FROM policy_item WHERE namespace_id = ${nsId()} AND policy_name = '${P1.name}'`);
  checkId = Number(psql(`INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_input, original) `
    + `VALUES ('source_check', 'golden_set', ${nsId()}, 'policy', ${lid}, 'E2E 원문 확인 — 두 정책이 다르게 말함', `
    + `'{"type": "question", "policies": [${lid}], "ref_tables": [], "hash": "e2e"}'::jsonb) RETURNING id`).split('\n')[0]);
});

test.afterAll(() => {
  psql(`DELETE FROM ops_improvement_item WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  dropPolicyPart(NS);
});

test('원문 확인 → [해결] 메모 → 원장 approved + 메모, 정정 요청 배지엔 안 셈', async ({ page, request }) => {
  await login(page);
  // 원문 확인 항목은 정정 요청 건수에 안 들어간다
  const tok = await adminToken();
  const cnt = await request.get(`http://localhost:8000/api/corrections/pending-count?namespace=${encodeURIComponent(NS)}`,
    { headers: { Authorization: `Bearer ${tok}` } });
  expect((await cnt.json()).count).toBe(0);

  await openPolicyTab(page, '원문 확인');
  const card = page.getByTestId(`source-check-${checkId}`);
  await expect(card).toContainText('E2E 원문 확인');
  await expect(card).toContainText(P1.name);   // 대상 정책 이름
  await card.getByRole('button', { name: '해결' }).click();
  const done = page.waitForResponse((r) => r.url().includes(`/api/policy/source-checks/${checkId}/resolve`));
  await page.getByLabel('해결 메모').fill('담당자 확인 — 원문 수정 불필요');
  await page.getByRole('button', { name: '해결로 표시' }).click();
  expect((await done).ok()).toBeTruthy();

  expect(psql(`SELECT status || '|' || (ai_verdict->>'resolution') || '|' || (ai_verdict->>'note') FROM ops_improvement_item WHERE id = ${checkId}`))
    .toBe('approved|resolved|담당자 확인 — 원문 수정 불필요');
  await expect(card).toBeHidden();                       // "확인할 것"에서 빠짐
  await page.getByRole('button', { name: '처리됨' }).click();
  await expect(page.getByTestId(`source-check-${checkId}`)).toContainText('해결');
  // 정책 자체는 그대로
  expect(rows(P1.name)).toEqual([`1:active:${P1.body}`]);
});

test('JSON 내려받기 → 하나 고침 → 미리보기 바뀜 1 → 정정 요청(JSON 가져오기) → 승인 = 새 버전', async ({ page }) => {
  test.setTimeout(240_000);
  await login(page);
  await openPolicyTab(page, 'JSON');

  const dl = page.waitForEvent('download');
  await page.getByRole('button', { name: '내려받기' }).click();
  const file = await (await dl).path();
  const exported = JSON.parse(readFileSync(file, 'utf-8'));
  expect(exported.schema).toBe('opslens/policy-v0/1');
  expect(exported.domain).toBe(NS);
  expect(exported.policies).toHaveLength(2);

  // 반품 정책 원문만 바꿔서 다시 올린다
  for (const p of exported.policies) if (p.policy_name === P2.name) p.raw_body = NEW_BODY;
  const previewed = page.waitForResponse((r) => r.url().endsWith('/api/policy/json/preview'));
  await page.getByLabel('가져올 파일').setInputFiles({ name: 'rev.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(exported)) });
  const prev = await (await previewed).json();
  expect(prev.changed).toHaveLength(1);
  expect(prev.unchanged).toBe(1);
  expect(prev.added).toHaveLength(0);
  expect(prev.missing).toHaveLength(0);
  const box = page.getByTestId('policy-json-preview');
  await expect(box).toContainText('바뀜 1');
  await expect(box).toContainText(P2.name);
  expect(rows(P2.name)).toEqual([`1:active:${P2.body}`]);   // 미리보기는 아무것도 안 바꿈

  const applied = page.waitForResponse((r) => r.url().endsWith('/api/policy/json/apply'));
  await box.getByRole('button', { name: /정정 요청에 올리기/ }).click();
  await page.getByTestId('app-dialog').getByRole('button', { name: '확인' }).click();
  const res = await (await applied).json();
  expect(res.created).toHaveLength(1);
  const corrId = res.created[0].correction_id as number;

  // 승인 전: 정책 테이블 그대로, 원장에 json_import 제안 1건(수정안 = 파일의 원문)
  expect(rows(P2.name)).toEqual([`1:active:${P2.body}`]);
  expect(psql(`SELECT kind || '|' || source || '|' || status || '|' || (proposed->>'raw_body') FROM ops_improvement_item WHERE id = ${corrId}`))
    .toBe(`correction|json_import|pending|${NEW_BODY}`);

  // 정정 요청: 출처 "JSON 가져오기" → 승인
  await goAdmin(page, '품질', '정정 요청');   // 주소로 바로 가면 새로고침돼 고른 업무 영역이 첫 영역으로 돌아간다(10/21)
  const card = page.locator(`#correction-${corrId}`);
  await expect(card).toContainText('JSON 가져오기');
  await expect(card).toContainText('14일');
  const approved = page.waitForResponse((r) => r.url().includes(`/api/corrections/${corrId}/approve`));
  await card.getByRole('button', { name: '승인', exact: true }).click();
  expect((await approved).ok()).toBeTruthy();

  await expect.poll(() => rows(P2.name)).toEqual([`1:deprecated:${P2.body}`, `2:active:${NEW_BODY}`]);
  expect(rows(P1.name)).toEqual([`1:active:${P1.body}`]);   // 안 바꾼 정책은 그대로
  expect(psql(`SELECT status FROM ops_improvement_item WHERE id = ${corrId}`)).toBe('approved');
});
