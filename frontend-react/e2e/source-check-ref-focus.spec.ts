import { test, expect } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { createPolicyPart, dropPolicyPart, psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 원문 확인 [참고표 보기] — 카드가 짚은 문제 행부터(11/10 태훈 "1번부터 나와서 어딜 수정하라는건지 찾기 어렵다", 시안 승인).
// 약속: 버튼에 문제 행 수 · 창이 첫 문제 행으로 스크롤 · 문제 행·칸 강조 · ◀ ▶ · [문제 행만] = 문제 행 + 위아래 1행 · # = 원표 행 번호.
// 표가 새 버전이면(적어 둔 버전 ≠ 지금) 강조하지 않는다. 임시 정책 컬렉션에 실제 행으로 만들고 지운다(목킹 없음). LLM 호출 없음.

const NS = 'zz_e2e_ref_focus';
const P1 = { name: 'E2E 참고표 정책', path: ['테스트'], body: '응모기회는 표를 따른다.' };
const ROWS = 60;            // 원표 3행부터(머리글 2줄 있는 표처럼) — # 칸이 화면 순번이 아니라 원표 번호인지 본다
const FOCUS = [40, 41];
const nsId = () => psql(`SELECT id FROM ops_namespace WHERE name = '${NS}'`);

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  test.setTimeout(180_000);   // 임시 정책 임베딩
  createPolicyPart(NS, [P1]);
  const ns = nsId();
  const cols = JSON.stringify([{ name: '유형', role: 'condition', decisive: true }, { name: '수량', role: 'result', decisive: false }]);
  const tid = psql(`INSERT INTO ref_table (namespace_id, title, columns, content_hash) VALUES (${ns}, 'E2E 응모기회 표', '${cols}'::jsonb, 'e2e') RETURNING id`).split('\n')[0];   // psql이 RETURNING 뒤에 "INSERT 0 1"도 찍는다
  psql(`UPDATE ref_table SET logical_id = id WHERE id = ${tid}`);
  psql(`INSERT INTO ref_table_row (ref_table_id, row_no, cells) SELECT ${tid}, n + 2, jsonb_build_object('유형', '유형' || n, '수량', CASE WHEN n + 2 = ANY('{${FOCUS}}') THEN '' ELSE n::text END) `
    + `FROM generate_series(1, ${ROWS}) n`);
  const original = JSON.stringify({ type: 'ref_error', policies: [], ref_tables: [Number(tid)], hash: 'e2e-focus', seed_id: 'e2e',
    focus: [{ table: Number(tid), table_id: Number(tid), rows: FOCUS, columns: ['수량'] }] });
  psql(`INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_input, original) `
    + `VALUES ('source_check', 'golden_set', ${ns}, 'ref_table', ${tid}, 'E2E 수량 칸이 비어 있습니다(원표 40~41행).', '${original}'::jsonb)`);
});

test.afterAll(() => dropPolicyPart(NS));

test('참고표 보기 — 문제 행부터 · 강조 · 이동 · 문제 행만', async ({ page }) => {
  await loginAdmin(page, { openAdmin: false });
  await page.getByRole('link', { name: 'Admin' }).click();
  await selectDomain(page, NS);
  await goAdmin(page, '정책서');
  await page.getByRole('tab', { name: /원문 확인/ }).click();

  const open = page.getByRole('button', { name: `참고표 보기 · 문제 ${FOCUS.length}행` });
  await expect(open).toBeVisible();
  await open.click();

  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('E2E 수량 칸이 비어 있습니다(원표 40~41행).')).toBeVisible();   // 카드 설명을 창 위에
  await expect(dialog.getByTestId('ref-table-focus-bar')).toContainText(`문제 행 ${FOCUS.length}`);
  await expect(dialog.getByTestId('ref-table-focus-bar')).toContainText('1/2 · 원표 40행');
  const first = dialog.getByTestId('ref-table-focus-row-40');
  await expect(first).toBeInViewport();                       // 1행부터가 아니라 문제 행으로 스크롤
  await expect(first.locator('td').first()).toHaveText('40'); // # = 원표 행 번호(화면 순번이면 38)
  await expect(first.locator('td.ring-2')).toHaveCount(1);    // 짚은 칸(수량)만 테두리

  await dialog.getByRole('button', { name: '다음 문제 행' }).click();
  await expect(dialog.getByTestId('ref-table-focus-bar')).toContainText('2/2 · 원표 41행');
  await expect(dialog.getByTestId('ref-table-focus-row-41')).toBeInViewport();

  await dialog.getByRole('button', { name: '문제 행만' }).click();
  await expect(dialog.locator('tbody tr')).toHaveCount(4);    // 39 · 40 · 41 · 42
  await expect(dialog.locator('tbody tr td:first-child')).toHaveText(['39', '40', '41', '42']);
  await page.screenshot({ path: 'test-results/ref-focus.png' });
  await dialog.getByRole('button', { name: '전체' }).click();
  await expect(dialog.locator('tbody tr')).toHaveCount(ROWS);

  // 표 안 검색은 문제 행으로 끌고 가지 않고, 지금 문제 행이 검색에 가려지면 그렇다고 알린다(리뷰 11/10)
  await dialog.getByRole('searchbox', { name: '표 안 검색' }).fill('유형5');
  await expect(dialog.getByTestId('ref-table-focus-bar')).toContainText('원표 41행 (검색에 가려짐)');
  await expect(dialog.getByTestId('ref-table-focus-row-41')).toHaveCount(0);
});

test('표가 새 버전이면 문제 행을 강조하지 않음', async ({ page }) => {
  // 카드가 적어 둔 버전 id를 다른 값으로 — 지금 버전과 달라진 상황
  psql(`UPDATE ops_improvement_item SET original = jsonb_set(original, '{focus,0,table_id}', '0'::jsonb) WHERE namespace_id = ${nsId()} AND kind = 'source_check'`);
  await loginAdmin(page, { openAdmin: false });
  await page.getByRole('link', { name: 'Admin' }).click();
  await selectDomain(page, NS);
  await goAdmin(page, '정책서');
  await page.getByRole('tab', { name: /원문 확인/ }).click();
  await page.getByRole('button', { name: '참고표 보기', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('표가 새 버전이라 문제 행은 표시하지 않아요.')).toBeVisible();
  await expect(dialog.getByTestId('ref-table-focus-bar')).toHaveCount(0);
  await expect(dialog.locator('tbody tr td:first-child').first()).toHaveText('3');
});
