import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';

// 이 테스트가 존재하는 이유(2026-10-06, v2.127): 사내 문서 보안(DRM) 엑셀은 서버·브라우저가 못 열고, 예전 안내(담당자 PC에서
// 파이썬 변환기)는 담당자가 쓸 수 없었다. 엑셀에서 시트를 복사해 붙여넣는 게 기본 경로 — 화면 붙여넣기 → 실제 임포트(LLM
// 분해 포함) → 재붙여넣기 시 기존 정책서·시트를 목록에서 골라 "변경 없음"까지 임시 파트에서 확인하고 파트째 지운다.

const NS = 'E2E 정책서 붙여넣기';
const SRC = 'E2E_붙여넣기.xlsx';
const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();

// 엑셀 Ctrl+A → Ctrl+C 결과와 같은 모양: 제목 행, 헤더, 병합으로 빈 분류 칸, 칸 안 줄바꿈(큰따옴표로 감쌈)
const SHEET_TEXT = [
  '비즈니스 정책서\t\t\t\t\t',
  'No\t대분류\t중분류\t정책명\t조건/상세\t비고',
  '1\t주문\t취소\tE2E 카드 환불\t"당일 취소는 승인취소\n익일 이후는 3~7영업일"\t',
  '2\t\t\tE2E 부분 취소\t부분 취소는 주문당 1회만 가능\t예외 있음',
].join('\r\n') + '\r\n';

test.describe.configure({ mode: 'serial' });
test.setTimeout(300_000);
test.beforeAll(() => {
  psql(`DELETE FROM ops_namespace WHERE name='${NS}'`);
  psql(`INSERT INTO ops_namespace (name, description) VALUES ('${NS}', 'e2e policy paste')`);
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
}

const cell = (page: Page, col: number) => page.locator('table tbody tr').first().locator('td').nth(col);

test('시트 붙여넣기 → 신규 반영(병합 칸·줄바꿈 본문) → 기존 정책서·시트 골라 다시 붙여넣기 = 변경 없음', async ({ page }) => {
  await open(page);
  await expect(page.getByRole('button', { name: '시트 붙여넣기' })).toBeVisible();   // 기본 모드
  await expect(page.getByText('excel_to_policy_json')).toHaveCount(0);              // 파이썬 안내 없음

  await page.getByLabel('정책서').selectOption('__new__');
  await page.getByLabel('새 정책서 이름').fill(SRC);
  await page.getByLabel('시트 1 이름').fill('정책');
  await page.getByLabel('시트 1 내용').fill(SHEET_TEXT);
  await expect(page.getByText('4행 붙여넣음')).toBeVisible();
  await page.getByRole('button', { name: '반영', exact: true }).click();
  await expect(page.getByText('반영 완료')).toBeVisible({ timeout: 240_000 });
  await expect(cell(page, 1)).toHaveText('2');                                      // 신규 2
  const rows = psql(`SELECT string_agg(array_to_string(category_path,'>')||'|'||policy_name||'|'||source_row||'|'||source_file, ',' ORDER BY source_row)
    FROM policy_item p JOIN ops_namespace n ON n.id=p.namespace_id WHERE n.name='${NS}' AND p.status<>'deprecated'`);
  expect(rows).toBe(`주문>취소|E2E 카드 환불|3|${SRC},주문>취소|E2E 부분 취소|4|${SRC}`);   // 병합 칸 채움·엑셀 행 번호
  expect(psql(`SELECT raw_body FROM policy_item p JOIN ops_namespace n ON n.id=p.namespace_id
    WHERE n.name='${NS}' AND policy_name='E2E 카드 환불'`)).toBe('당일 취소는 승인취소\n익일 이후는 3~7영업일');

  // 다시: 이번엔 이미 올린 정책서를 목록에서 고른다 → 같은 내용이면 변경 없음(AI 분해 안 탐)
  await open(page);                                                                  // 새로 들어온 것처럼(입력 초기화)
  await page.getByLabel('정책서').selectOption(SRC);
  await page.getByLabel('시트 1 이름').fill('정책');
  await expect(page.getByText('새 시트로 들어갑니다')).toHaveCount(0);             // 기존 시트로 인식
  await page.getByLabel('시트 1 내용').fill(SHEET_TEXT);
  await page.getByRole('button', { name: '반영', exact: true }).click();
  await expect(cell(page, 3)).toHaveText('2', { timeout: 60_000 });                 // 변경 없음 2
  await expect(cell(page, 1)).toHaveText('0');
});
