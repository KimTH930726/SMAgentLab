import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';

// 이 테스트가 존재하는 이유(2026-10-06, v2.123~124): 정책서는 사내 문서 보안 암호화 때문에 표준 JSON으로 올리고,
// 재업로드 때 같은 정책은 식별키로 찾는다(바뀐 것만 새 버전, 사라진 건 검토 큐). 화면 업로드 → 실제 임포트(LLM 분해 포함)
// → 요약 표시까지를 임시 파트에서 확인하고 파트째 지운다(실데이터 무관).

const NS = 'E2E 정책서 올리기';
const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();

const ROWS = [
  { row: 3, category_path: ['주문', '취소'], policy_name: 'E2E 카드 환불', body: '당일 취소는 승인취소, 익일 이후는 3~7영업일', remark: null },
  { row: 4, category_path: ['주문', '취소'], policy_name: 'E2E 부분 취소', body: '부분 취소는 주문당 1회만 가능', remark: '예외 있음' },
];
const doc = (rows: object[]) => Buffer.from(JSON.stringify({
  schema: 'opslens/policy/v1', source_name: 'E2E_정책서.xlsx', sheets: [{ name: '정책', kind: 'policy', rows }],
}));

test.describe.configure({ mode: 'serial' });
test.setTimeout(240_000);
test.beforeAll(() => {
  psql(`DELETE FROM ops_namespace WHERE name='${NS}'`);
  psql(`INSERT INTO ops_namespace (name, description) VALUES ('${NS}', 'e2e policy import')`);
});
test.afterAll(() => { psql(`DELETE FROM ops_namespace WHERE name='${NS}'`); });

async function open(page: Page) {
  await page.goto('/login');
  await page.getByPlaceholder('사용자 아이디').fill('admin');
  await page.getByPlaceholder('비밀번호').fill('1111');
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('heading', { name: '로그인' })).toBeHidden({ timeout: 10_000 });
  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  if (await agentHeading.isVisible().catch(() => false)) await page.getByText('선택하기').first().click();
  await page.getByRole('link', { name: 'Admin' }).click();
  await page.getByRole('button', { name: '정책' }).click();
  await page.getByRole('button', { name: '정책서 올리기' }).click();
  await page.locator('select').first().selectOption(NS);
  await page.getByRole('button', { name: '파일 올리기' }).click();   // 기본은 '시트 붙여넣기'(v2.127)
}

async function upload(page: Page, name: string, buffer: Buffer) {
  await page.locator('input[type="file"]').setInputFiles({ name, mimeType: 'application/json', buffer });
  await page.getByRole('button', { name: '올리기', exact: true }).click();
}

const cell = (page: Page, col: number) => page.locator('table tbody tr').first().locator('td').nth(col);

test('JSON 업로드 → 재업로드(변경 없음) → 정책 삭제(검토 큐) → 형식 오류 안내', async ({ page }) => {
  await open(page);

  await upload(page, 'E2E_정책서.json', doc(ROWS));
  await expect(page.getByText('반영 완료')).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText('E2E_정책서.xlsx')).toBeVisible();   // source_name이 출처 파일명
  await expect(cell(page, 1)).toHaveText('2');                     // 신규
  expect(psql(`SELECT count(*) FROM policy_item p JOIN ops_namespace n ON n.id=p.namespace_id WHERE n.name='${NS}' AND p.status<>'deprecated'`)).toBe('2');

  await upload(page, 'E2E_정책서.json', doc(ROWS));
  await expect(cell(page, 3)).toHaveText('2', { timeout: 60_000 }); // 변경 없음
  await expect(cell(page, 1)).toHaveText('0');

  await upload(page, 'E2E_정책서.json', doc([ROWS[0]]));
  await expect(page.getByText(/원본에서 사라진 정책 1건이 검토 큐에 있어요/)).toBeVisible({ timeout: 60_000 });
  expect(psql(`SELECT count(*) FROM policy_item p JOIN ops_namespace n ON n.id=p.namespace_id WHERE n.name='${NS}' AND p.source_missing_at IS NOT NULL`)).toBe('1');

  // 틀린 파일 — 아무것도 반영하지 않고, 엑셀에서 찾을 수 있는 위치(시트·행)와 고칠 방법을 표로
  const bad = Buffer.from(JSON.stringify({ schema: 'opslens/policy/v1', sheets: [{ name: '정책', kind: 'policy', rows: [{ ...ROWS[0], policy_name: ' ' }] }] }));
  await upload(page, 'bad.json', bad);
  await expect(page.getByText(/아무것도 반영되지 않았습니다/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('cell', { name: "'정책' 시트 3행" })).toBeVisible();
  await expect(page.getByRole('cell', { name: '정책명이 비어 있습니다.' })).toBeVisible();
  await expect(page.getByText('이렇게 고치세요')).toBeVisible();
  // 모르는 항목은 막지 않고 경고로 — 반영됨 + "확인할 것"
  const extra = Buffer.from(JSON.stringify({ schema: 'opslens/policy/v1', source_name: 'E2E_정책서.xlsx', exported_by: 'tool',
    sheets: [{ name: '정책', kind: 'policy', rows: [ROWS[0]] }] }));
  await upload(page, 'extra.json', extra);
  await expect(page.getByText('반영은 됐지만 확인할 것')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/exported_by/)).toBeVisible();
  await expect(page.getByText('다음에 할 일')).toBeVisible();
});
