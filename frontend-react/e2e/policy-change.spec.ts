import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { createPolicyPart, dropPolicyPart, psql } from './v0-fixture';
import { API, adminToken, loginAdmin } from './auth';

// 정책 추가·분류 옮기기·폐기 제안(C3 2단계, 2026-10-27 — docs/tech/policy-lifecycle-design.md).
// 핵심 약속: 제안은 정책 테이블을 건드리지 않고 정정 요청에 1건만 만든다 → 승인해야 반영. 화면 문구가 아니라 DB 값·검색 API로 전/후를 본다.
// - 추가: 새 분류(대·중·소 직접 입력)도 됨 → 승인하면 새 정책(v1, 원천 키 sys:<logical_id>, 조각 1개 + 임베딩), 검색에 나옴
// - 옮기기: 새 버전(분류만 다름, 원천 키·logical_id 그대로 — 골든 정답 유지), 옛 버전 deprecated, 조각 다시 임베딩
// - 폐기: 상태만 deprecated(지우지 않음), 검색에서 빠짐. 폐기·옮기기는 그 정책의 다른 대기 제안과 함께 못 올림(409)
// LLM 호출 없음(임베딩만).

const NS = 'zz_e2e_policy_change';
const NAME = 'E2E 반품 기한';
const BODY = '반품은 수령 후 7일 이내에 신청할 수 있다.';
const ADD_NAME = 'E2E 교환 횟수';
const ADD_BODY = '교환은 주문 1건당 2회까지 신청할 수 있다.';

test.describe.configure({ mode: 'serial' });

const nsId = () => psql(`SELECT id FROM ops_namespace WHERE name = '${NS}'`);
const rowsOf = (name: string) => psql(`SELECT version || ':' || status || ':' || array_to_string(category_path, '>') FROM policy_item `
  + `WHERE namespace_id = ${nsId()} AND policy_name = '${name}' ORDER BY version`).split('\n').filter(Boolean);

async function openList(page: Page) {
  await page.getByRole('link', { name: 'Admin' }).click();
  await selectDomain(page, NS);
  await goAdmin(page, '지식', '정책서');
  await expect(page.getByRole('button', { name: /^테스트/ }).first()).toBeVisible();
}

async function approveCard(page: Page, id: number) {
  // 앱 안에서 이동 — 주소로 새로 열면 에이전트 선택부터 다시 나온다. 한 건씩 보기 대신 [목록]으로 그 카드를 찾는다
  if (await page.getByRole('dialog').count()) await page.keyboard.press('Escape');   // 정책 상세가 열려 있으면 닫고
  await goAdmin(page, '품질', '정정 요청');
  await page.getByRole('group', { name: '보기' }).getByRole('button', { name: '목록' }).click();
  const card = page.getByTestId(`policy-change-${id}`);
  await expect(card).toBeVisible();
  const done = page.waitForResponse((r) => r.url().includes(`/api/corrections/${id}/approve`));
  await card.getByRole('button', { name: '승인', exact: true }).click();
  expect((await done).ok()).toBeTruthy();
  await expect.poll(() => psql(`SELECT status FROM ops_improvement_item WHERE id = ${id}`)).toBe('approved');
}

// API는 Playwright request로 — node fetch는 서버가 닫은 keep-alive 연결을 다시 집어 "other side closed"로 한 번씩 실패했다(10/27)
async function search(request: APIRequestContext, q: string): Promise<string[]> {
  const tok = await adminToken();
  const s = await request.get(`${API}/api/policy/search?namespace=${encodeURIComponent(NS)}&q=${encodeURIComponent(q)}`, { headers: { Authorization: `Bearer ${tok}` } });
  expect(s.ok()).toBeTruthy();
  return ((await s.json()).narratives as { chunk_text: string }[]).map((n) => n.chunk_text);
}
const post = async (request: APIRequestContext, path: string, body: object) =>
  request.post(`${API}/api/corrections${path}`, { headers: { Authorization: `Bearer ${await adminToken()}` }, data: { namespace: NS, ...body } });

async function submit(page: Page, title: RegExp) {
  const dialog = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: title }) });
  const created = page.waitForResponse((r) => r.url().endsWith('/api/corrections/policy-change') && r.request().method() === 'POST');
  await dialog.getByRole('button', { name: '정정 요청에 올리기' }).click();
  const res = await created;
  expect(res.status()).toBe(201);
  const id = (await res.json()).id as number;
  await page.getByTestId('app-dialog').getByRole('button', { name: '확인' }).click();
  return id;
}

test.beforeAll(async () => {
  test.setTimeout(180_000);
  createPolicyPart(NS, [{ name: NAME, path: ['테스트', '반품'], body: BODY }]);
});

test.afterAll(() => {
  psql(`DELETE FROM ops_improvement_item WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  dropPolicyPart(NS);
});

test('추가 제안(새 분류) → 승인 전 정책 그대로 → 승인 → 새 정책 v1·원천 키 sys:·조각 임베딩·검색에 나옴', async ({ page, request }) => {
  test.setTimeout(180_000);
  const before = psql(`SELECT count(*) FROM policy_item WHERE namespace_id = ${nsId()}`);
  await loginAdmin(page, { openAdmin: false });
  await openList(page);
  await page.getByRole('button', { name: '정책 추가 제안 — 테스트' }).click();
  const dialog = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: '정책 추가 제안' }) });
  await expect(dialog.locator('#padd-lv0')).toHaveValue('테스트');   // 절 줄에서 열면 그 절이 채워져 있다
  await dialog.locator('#padd-lv1').fill('교환');
  await expect(dialog.getByTestId('new-category')).toContainText('테스트 › 교환');
  await dialog.locator('#padd-name').fill(ADD_NAME);
  await dialog.locator('#padd-body').fill(ADD_BODY);
  const id = await submit(page, /정책 추가 제안/);

  // 승인 전: 정책 테이블 그대로, 원장에 제안 1건
  expect(psql(`SELECT count(*) FROM policy_item WHERE namespace_id = ${nsId()}`)).toBe(before);
  expect(psql(`SELECT target_type || '|' || status || '|' || (original->>'new_category') FROM ops_improvement_item WHERE id = ${id}`))
    .toBe('policy_add|pending|true');
  expect(await search(request, '교환 신청 횟수')).not.toContain(ADD_BODY);

  await approveCard(page, id);
  expect(rowsOf(ADD_NAME)).toEqual(['1:active:테스트>교환']);
  const lid = psql(`SELECT logical_id FROM policy_item WHERE namespace_id = ${nsId()} AND policy_name = '${ADD_NAME}'`);
  expect(psql(`SELECT source_key FROM policy_item WHERE logical_id = ${lid}`)).toBe(`sys:${lid}`);
  expect(psql(`SELECT count(*) || ':' || bool_and(embedding IS NOT NULL) || ':' || bool_and(chunk_text = '${ADD_BODY}') FROM policy_chunk c `
    + `JOIN policy_item i ON i.id = c.policy_item_id WHERE i.logical_id = ${lid}`)).toBe('1:true:true');
  expect(await search(request, '교환 신청 횟수')).toContain(ADD_BODY);

  // 같은 분류에 같은 이름은 못 올림(400)
  const dup = await post(request, '/policy-change', { change: 'add', policy_name: ADD_NAME, category_path: ['테스트', '교환'], raw_body: '다른 원문' });
  expect(dup.status()).toBe(400);
});

test('분류 옮기기 제안 → 다른 제안 409 → 승인 → 새 버전(분류만 다름, 원천 키·logical_id 그대로)·옛 버전 deprecated·조각 다시 임베딩', async ({ page, request }) => {
  test.setTimeout(180_000);
  const old = psql(`SELECT id || '|' || logical_id || '|' || coalesce(source_key, '') FROM policy_item WHERE namespace_id = ${nsId()} AND policy_name = '${NAME}' AND status = 'active'`).split('|');
  const oldEmb = psql(`SELECT md5(embedding::text) FROM policy_chunk WHERE policy_item_id = ${old[0]}`);
  await loginAdmin(page, { openAdmin: false });
  await openList(page);
  await page.getByRole('button', { name: new RegExp(NAME) }).click();
  await page.getByRole('button', { name: '분류 옮기기 제안' }).click();
  const dialog = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: /분류 옮기기 제안/ }) });
  await expect(dialog.getByRole('button', { name: '정정 요청에 올리기' })).toBeDisabled();   // 지금 분류 그대로면 못 올림
  await dialog.locator('#pmove-lv1').fill('반품·교환');
  const id = await submit(page, /분류 옮기기 제안/);
  expect(rowsOf(NAME)).toEqual(['1:active:테스트>반품']);   // 승인 전 그대로
  await expect(page.getByText(`정정 #${id} 검토 중`).first()).toBeVisible();
  await expect(page.getByRole('button', { name: '폐기 제안' })).toBeDisabled();

  const dup = await post(request, '/policy-change', { change: 'retire', target_id: Number(old[0]), reason: '중복' });
  expect(dup.status()).toBe(409);
  expect((await dup.json()).detail.existing_id).toBe(id);

  await approveCard(page, id);
  expect(rowsOf(NAME)).toEqual(['1:deprecated:테스트>반품', '2:active:테스트>반품·교환']);
  const now = psql(`SELECT id || '|' || logical_id || '|' || coalesce(source_key, '') FROM policy_item WHERE namespace_id = ${nsId()} AND policy_name = '${NAME}' AND status = 'active'`).split('|');
  expect(now.slice(1)).toEqual(old.slice(1));   // logical_id·원천 키 그대로
  expect(psql(`SELECT md5(embedding::text) FROM policy_chunk WHERE policy_item_id = ${now[0]}`)).not.toBe(oldEmb);   // 머리(분류)가 바뀌어 다시 임베딩
  expect(await search(request, '반품 신청 기한')).toContain(BODY);
});

test('폐기 제안(이유 필수) → 승인 → 상태만 deprecated(행 남음)·검색에서 빠짐·목록에서 사라짐', async ({ page, request }) => {
  test.setTimeout(180_000);
  await loginAdmin(page, { openAdmin: false });
  await openList(page);
  await page.getByRole('button', { name: new RegExp(NAME) }).click();
  await page.getByRole('button', { name: '폐기 제안' }).click();
  const dialog = page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: /폐기 제안/ }) });
  await expect(dialog.getByRole('button', { name: '정정 요청에 올리기' })).toBeDisabled();   // 이유 없이 못 올림
  await dialog.locator('#pretire-reason').fill('E2E — 다른 정책으로 합침');
  const id = await submit(page, /폐기 제안/);
  const count = psql(`SELECT count(*) FROM policy_item WHERE namespace_id = ${nsId()} AND policy_name = '${NAME}'`);

  await approveCard(page, id);
  expect(rowsOf(NAME)).toEqual(['1:deprecated:테스트>반품', '2:deprecated:테스트>반품·교환']);
  expect(psql(`SELECT count(*) FROM policy_item WHERE namespace_id = ${nsId()} AND policy_name = '${NAME}'`)).toBe(count);   // 지우지 않음
  expect(await search(request, '반품 신청 기한')).not.toContain(BODY);
  await openList(page);
  await expect(page.getByRole('button', { name: new RegExp(NAME) })).toHaveCount(0);
  await expect(page.getByRole('button', { name: new RegExp(ADD_NAME) })).toBeVisible();   // 다른 정책은 그대로
});

test('동시성 — 같은 정책에 수정·폐기·옮기기 제안을 동시에 → 정확히 1건, 같은 제안을 동시에 두 번 승인 → 한 번만 반영', async ({ request }) => {
  test.setTimeout(120_000);
  const item = Number(psql(`SELECT id FROM policy_item WHERE namespace_id = ${nsId()} AND policy_name = '${ADD_NAME}' AND status = 'active'`));
  const res = await Promise.all([
    post(request, '', { target_type: 'policy_narrative', target_id: item, source: 'policy_direct', proposed: { raw_body: '교환은 3회까지.', remark: '' } }),
    post(request, '/policy-change', { change: 'retire', target_id: item, reason: '동시 1' }),
    post(request, '/policy-change', { change: 'move', target_id: item, category_path: ['테스트', '동시'] }),
  ]);
  const codes = res.map((r) => r.status()).sort();
  expect(codes).toEqual([201, 409, 409]);
  expect(psql(`SELECT count(*) FROM ops_improvement_item WHERE status = 'pending' AND target_id = ${item}`)).toBe('1');
  const pending = Number(psql(`SELECT id FROM ops_improvement_item WHERE status = 'pending' AND target_id = ${item}`));
  const shown = JSON.parse(psql(`SELECT proposed::text FROM ops_improvement_item WHERE id = ${pending}`));
  const approvals = await Promise.all([1, 2].map(() => post(request, `/${pending}/approve`, { proposed: shown })));
  expect(approvals.map((r) => r.status()).sort()).toEqual([200, 409]);
  // 불변식: 이 컬렉션의 정책마다 사용 중 버전은 많아야 1개, 버전 번호 겹침 없음
  expect(psql(`SELECT count(*) FROM (SELECT logical_id FROM policy_item WHERE namespace_id = ${nsId()} AND status = 'active' GROUP BY logical_id HAVING count(*) > 1) x`)).toBe('0');
  expect(psql(`SELECT count(*) FROM (SELECT logical_id, version FROM policy_item WHERE namespace_id = ${nsId()} GROUP BY 1, 2 HAVING count(*) > 1) x`)).toBe('0');
  expect(psql(`SELECT count(*) FROM policy_item i WHERE namespace_id = ${nsId()} AND status = 'active' AND NOT EXISTS (SELECT 1 FROM policy_chunk c WHERE c.policy_item_id = i.id)`)).toBe('0');   // 조각 없는 현행 정책 0
});

test('같은 추가 제안 — 대기 중 두 번째는 409(분류 jsonb 비교), 두 건이 동시에 승인돼도 정책은 1개(자리 잠금)', async ({ request }) => {
  test.setTimeout(120_000);
  const NAME2 = 'E2E 동시 추가';
  const body = { change: 'add', policy_name: NAME2, category_path: ['테스트', '동시 추가'], raw_body: '동시 승인 시험 원문.' };
  const first = await post(request, '/policy-change', body);
  expect(first.status()).toBe(201);
  const id1 = (await first.json()).id as number;
  const again = await post(request, '/policy-change', { ...body, category_path: [' 테스트 ', '동시 추가'] });   // 공백만 다른 같은 분류
  expect(again.status()).toBe(409);
  expect((await again.json()).detail.existing_id).toBe(id1);
  // 화면 길로는 둘째가 안 생기므로, 동시에 만들어진 경우를 흉내 — 같은 제안 행을 하나 더(테스트 컬렉션 안, 끝에 afterAll이 지움)
  const id2 = Number(psql(`INSERT INTO ops_improvement_item (kind, source, namespace_id, reporter_user_id, target_type, original, proposed, status)
    SELECT kind, source, namespace_id, NULL, target_type, original, proposed, 'pending' FROM ops_improvement_item WHERE id = ${id1} RETURNING id`).split(/\s+/)[0]);
  const shown = JSON.parse(psql(`SELECT proposed::text FROM ops_improvement_item WHERE id = ${id1}`));
  const res = await Promise.all([id1, id2].map((id) => post(request, `/${id}/approve`, { proposed: shown })));
  expect(res.map((r) => r.status()).sort()).toEqual([200, 400]);
  expect(psql(`SELECT count(*) FROM policy_item WHERE namespace_id = ${nsId()} AND policy_name = '${NAME2}' AND status = 'active'`)).toBe('1');
});
