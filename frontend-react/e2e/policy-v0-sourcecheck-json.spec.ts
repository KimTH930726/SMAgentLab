import { readFileSync } from 'node:fs';
import { test, expect, type Page } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { createPolicyPart, dropPolicyPart, psql } from './v0-fixture';
import { API, adminToken, loginAdmin } from './auth';

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
  await selectDomain(page, NS);
  await goAdmin(page, '정책서');
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
  // 카드 결론이 남긴 AI 판단 원장 줄(원문 확인 오탐률 — v2.175)도 이 테스트 것만 지운다(실데이터 집계에 섞이지 않게)
  psql(`DELETE FROM ops_ai_decision WHERE kind = 'source_check' AND target_ids && ARRAY(SELECT id::bigint FROM ops_improvement_item `
    + `WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}'))`);
  psql(`DELETE FROM ops_improvement_item WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  dropPolicyPart(NS);
});

test('원문 확인 → [처리 완료로 닫기] 메모 → 원장 approved + 메모, 정정 요청 배지엔 안 셈', async ({ page, request }) => {
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
  await card.getByRole('button', { name: '처리 완료로 닫기' }).click();
  const done = page.waitForResponse((r) => r.url().includes(`/api/policy/source-checks/${checkId}/resolve`));
  await page.getByLabel('처리 메모').fill('담당자 확인 — 원문 수정 불필요');
  await page.getByRole('dialog').getByRole('button', { name: '처리 완료로 닫기' }).click();
  expect((await done).ok()).toBeTruthy();

  expect(psql(`SELECT status || '|' || (ai_verdict->>'resolution') || '|' || (ai_verdict->>'note') FROM ops_improvement_item WHERE id = ${checkId}`))
    .toBe('approved|resolved|담당자 확인 — 원문 수정 불필요');
  await expect(card).toBeHidden();                       // "확인할 것"에서 빠짐
  await page.getByRole('button', { name: '처리됨' }).click();
  await expect(page.getByTestId(`source-check-${checkId}`)).toContainText('처리 완료');
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
  await goAdmin(page, '정정 요청');   // 주소로 바로 가면 새로고침돼 고른 업무 영역이 첫 영역으로 돌아간다(10/21)
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

// C3(10/26) — 카드에서 연 정책으로 올린 제안 = 카드 "정정 요청 중 #n" → 반려되면 카드는 처리 전 그대로(반려됨 표시) →
// [이슈 아님으로 닫기](사유 고르기)이 원장 + AI 판단 원장에. 다른 카드: 제안 승인 = 카드 자동 해결(메모 "정정 #n 승인").
test('원문 확인 카드 ↔ 정정 제안 — 반려면 처리 전, 승인이면 자동 해결 · [이슈 아님으로 닫기] 사유가 AI 판단 원장에', async ({ page, request }) => {
  test.setTimeout(240_000);
  const lid = psql(`SELECT logical_id FROM policy_item WHERE namespace_id = ${nsId()} AND policy_name = '${P1.name}' AND status = 'active'`);
  const mk = (desc: string) => Number(psql(`INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_input, original) `
    + `VALUES ('source_check', 'golden_set', ${nsId()}, 'policy', ${lid}, '${desc}', `
    + `'{"type": "conflict", "policies": [${lid}], "ref_tables": [], "hash": "e2e-c3"}'::jsonb) RETURNING id`).split('\n')[0]);
  const c2 = mk('E2E C3 반려 경로');
  const c3 = mk('E2E C3 승인 경로');
  const tok = await adminToken();
  const H = { Authorization: `Bearer ${tok}` };

  async function proposeFrom(card: number, body: string): Promise<number> {
    const el = page.getByTestId(`source-check-${card}`);
    await el.getByTestId(`source-check-fix-${card}-${lid}`).click();   // [이 정책 정정 요청 ▶] = 그 정책 수정 제안 창이 바로(11/04, 정책마다 11/05)
    const form = page.locator('form').filter({ has: page.locator('#policy-edit-body') });
    await form.locator('#policy-edit-body').fill(body);
    const created = page.waitForResponse((r) => r.url().endsWith('/api/corrections') && r.request().method() === 'POST');
    await form.getByRole('button', { name: '정정 요청에 올리기' }).click();
    const id = (await (await created).json()).id as number;
    await page.getByTestId('app-dialog').getByRole('button', { name: '확인' }).click();
    await page.keyboard.press('Escape');   // 정책 상세 닫기
    return id;
  }

  await login(page);
  await openPolicyTab(page, '원문 확인');
  // ① 반려 경로
  const r1 = await proposeFrom(c2, '교환은 수령 후 10일 이내에 신청한다.');
  expect(psql(`SELECT source_check_id FROM ops_improvement_item WHERE id = ${r1}`)).toBe(String(c2));
  await expect(page.getByTestId(`source-check-correction-${c2}`)).toContainText(`정정 요청 중 #${r1}`);
  expect((await request.post(`${API}/api/corrections/${r1}/reject`, { headers: H, data: { reason: 'E2E 반려' } })).ok()).toBeTruthy();
  await page.reload();
  await openPolicyTab(page, '원문 확인');
  await expect(page.getByTestId(`source-check-${c2}`)).toContainText(`정정 #${r1} 반려됨`);
  expect(psql(`SELECT status FROM ops_improvement_item WHERE id = ${c2}`)).toBe('pending');   // 카드는 처리 전 그대로
  // [이슈 아님으로 닫기] — 사유 없이는 못 닫고, 고르면 원장 rejected + 사유, AI 판단 원장에 한 줄
  await page.getByTestId(`source-check-${c2}`).getByRole('button', { name: '이슈 아님으로 닫기' }).click();
  const submit = page.getByRole('dialog').getByRole('button', { name: '이슈 아님으로 닫기' });
  await expect(submit).toBeDisabled();
  await page.getByLabel('중복').check();
  await submit.click();
  await expect.poll(() => psql(`SELECT status || '|' || (ai_verdict->>'reason') FROM ops_improvement_item WHERE id = ${c2}`)).toBe('rejected|duplicate');
  expect(psql(`SELECT final_value->>'reason' FROM ops_ai_decision WHERE kind = 'source_check' AND ${c2} = ANY(target_ids)`)).toBe('duplicate');
  // ② 승인 경로 — 제안 승인 = 카드 자동 해결
  const r2 = await proposeFrom(c3, '교환은 수령 후 10일 이내에 신청한다.');
  const proposed = JSON.parse(psql(`SELECT proposed::text FROM ops_improvement_item WHERE id = ${r2}`));
  expect((await request.post(`${API}/api/corrections/${r2}/approve`, { headers: H, data: { proposed } })).ok()).toBeTruthy();
  expect(psql(`SELECT status || '|' || (ai_verdict->>'note') || '|' || (ai_verdict->>'auto') FROM ops_improvement_item WHERE id = ${c3}`))
    .toBe(`approved|정정 #${r2} 승인|true`);
});

test('참고표 오류 카드 — 정책마다 [이 정책 정정 요청] · [참고표 보기] 창(결정·결과 열, 표 안 검색) · 정정 카드 머리 = 대상 정책·원문 확인 번호', async ({ page, request }) => {
  const lid = psql(`SELECT logical_id FROM policy_item WHERE namespace_id = ${nsId()} AND policy_name = '${P1.name}' AND status = 'active'`);
  const cols = JSON.stringify([{ name: '지역', decisive: true }, { name: '무게' }, { name: '요금', role: 'result' }]);
  const tid = Number(psql(`INSERT INTO ref_table (namespace_id, title, category_path, columns, content_hash) VALUES (${nsId()}, 'E2E 배송비 표', '{테스트,배송}', '${cols}'::jsonb, 'e2e-rt') RETURNING id`).split('\n')[0]);
  psql(`UPDATE ref_table SET logical_id = id WHERE id = ${tid}`);
  psql(`INSERT INTO ref_table_row (ref_table_id, row_no, cells) VALUES (${tid}, 1, '{"지역":"제주","무게":"~5kg","요금":"3000"}'), (${tid}, 2, '{"지역":"서울","무게":"~5kg","요금":"2500"}')`);
  const card = Number(psql(`INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_input, original) `
    + `VALUES ('source_check', 'golden_set', ${nsId()}, 'ref_table', ${tid}, 'E2E 참고표 오류', `
    + `'{"type": "ref_error", "policies": [${lid}], "ref_tables": [${tid}], "hash": "e2e-rt"}'::jsonb) RETURNING id`).split('\n')[0]);

  await login(page);
  await openPolicyTab(page, '원문 확인');
  const el = page.getByTestId(`source-check-${card}`);
  await expect(el.getByTestId(`source-check-fix-${card}-${lid}`)).toBeVisible();   // 관련 정책 줄마다 정정 요청
  await el.getByTestId(`source-check-table-${card}-${tid}`).click();
  const view = page.getByTestId('ref-table-view');
  await expect(view).toContainText('E2E 배송비 표');
  await expect(view.locator('tbody tr')).toHaveCount(2);
  await view.getByLabel('표 안 검색').fill('제주');
  await expect(view.locator('tbody tr')).toHaveCount(1);
  await expect(view).toContainText('1 / 2행');
  await page.keyboard.press('Escape');

  // 정정 요청 → 카드 머리에 대상 정책·원문 확인 번호
  await el.getByTestId(`source-check-fix-${card}-${lid}`).click();
  const form = page.locator('form').filter({ has: page.locator('#policy-edit-body') });
  await form.locator('#policy-edit-body').fill('교환은 수령 후 14일 이내에 신청한다.');
  const created = page.waitForResponse((r) => r.url().endsWith('/api/corrections') && r.request().method() === 'POST');
  await form.getByRole('button', { name: '정정 요청에 올리기' }).click();
  const cid = (await (await created).json()).id as number;
  await page.getByTestId('app-dialog').getByRole('button', { name: '확인' }).click();
  const H = { Authorization: `Bearer ${await adminToken()}` };
  const list = await (await request.get(`${API}/api/corrections?status=pending&namespace=${NS}`, { headers: H })).json() as { id: number; source_check_id: number | null; policy_version: number | null }[];
  const mine = list.find((x) => x.id === cid)!;
  expect([mine.source_check_id, mine.policy_version != null]).toEqual([card, true]);
  expect((await request.post(`${API}/api/corrections/${cid}/reject`, { headers: H, data: { reason: 'E2E 정리' } })).ok()).toBeTruthy();
});
