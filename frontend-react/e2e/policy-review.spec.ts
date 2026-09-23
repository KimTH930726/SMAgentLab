import { test, expect } from '@playwright/test';

// 이 테스트가 존재하는 이유(2026-09-23): 정책 검토 UI(승인/반려 + 반려 항목 수정→재검토
// 유도)는 이번 세션에 새로 만든 기능이고, 실사용 중 "반려하면 그냥 버려지는데?"라는 지적으로
// edit.py + 인라인 편집 UI가 추가됐다 — 승인/반려/편집이 서로 맞물려 상태를 오가는 구조라
// 유닛테스트만으로는 "실제 화면에서 반려→편집 아이콘 노출→저장→검토대기 복귀"가 눈으로
// 확인한 그대로 계속 동작하는지 보장이 안 된다. 실 dev 스택 그대로 사용(목킹 없음).
//
// 데이터 정합성 주의: policy_item은 지식 등록 테스트처럼 만들었다 지우는 자산이 아니라
// 실제 정책 데이터라, 이 테스트는 반려→편집 폼에서 "원래 값 그대로" 저장해 라운드트립
// 시킨다 — 끝나면 원래 있던 검토대기 상태와 내용이 그대로 복원된다(반려 직전 상태로).

const ADMIN_USER = 'admin';
const ADMIN_PASS = '1111';
const NAMESPACE = '딜리버스 DB';

test.beforeEach(async ({ page }) => {
  await page.goto('/login');
  await page.getByPlaceholder('사용자 아이디').fill(ADMIN_USER);
  await page.getByPlaceholder('비밀번호').fill(ADMIN_PASS);
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('heading', { name: '로그인' })).toBeHidden({ timeout: 10_000 });

  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  if (await agentHeading.isVisible().catch(() => false)) {
    await page.getByText('선택하기').first().click();
  }

  await page.getByRole('link', { name: 'Admin' }).click();
  await page.getByRole('button', { name: '정책' }).click();
  await page.getByRole('button', { name: '항목 브라우저' }).click();
  await page.locator('select').first().selectOption(NAMESPACE);
  await page.waitForTimeout(500);
});

test('정책 항목 반려 → 편집 아이콘 노출 → 저장 시 검토대기로 복귀', async ({ page }) => {
  // 검토대기 항목 중 첫 번째를 대상으로 — 실사용 큐라 어떤 항목인지는 매번 다를 수 있음
  await page.locator('select').nth(2).selectOption('pending_review');
  await page.waitForTimeout(600);

  const firstRow = page.locator('.space-y-2 > div.bg-slate-800').first();
  await expect(firstRow).toBeVisible({ timeout: 10_000 });
  const policyName = (await firstRow.locator('span.font-medium').first().textContent())?.trim() ?? '';
  expect(policyName.length).toBeGreaterThan(0);

  // 반려 — confirm 문구가 "되돌릴 수 있다"고 정확히 안내하는지도 같이 확인
  // (2026-09-23 실사고: 편집→재검토 기능을 만들고 이 문구를 안 고쳐서 "되돌릴 화면이
  // 없다"고 거짓 안내하던 버그가 있었음 — 회귀 방지).
  let dialogMessage = '';
  page.once('dialog', async (d) => { dialogMessage = d.message(); await d.accept(); });
  await firstRow.locator('button[title*="반려"]').click();
  await page.waitForTimeout(800);
  expect(dialogMessage).toContain('다시 검토대기로 되돌릴 수 있습니다');

  // 반려됨 필터로 좁혀서 방금 그 항목을 다시 찾는다
  await page.locator('select').nth(2).selectOption('rejected');
  await page.waitForTimeout(600);
  const rejectedRow = page.locator('.space-y-2 > div.bg-slate-800').filter({ hasText: policyName }).first();
  await expect(rejectedRow).toBeVisible({ timeout: 10_000 });
  await rejectedRow.click();
  await page.waitForTimeout(400);

  // 반려 안내 배너 + 편집(연필) 아이콘 노출 확인
  await expect(page.getByText('반려된 항목입니다')).toBeVisible();
  const editBtn = page.locator('button:has(svg.lucide-pencil)').first();
  await expect(editBtn).toBeVisible();
  await editBtn.click();
  await page.waitForTimeout(300);

  // 라벨이 항상 보이는지 확인(2026-09-23 실사고: placeholder만 있어서 값이 채워지면
  // 뭐가 뭔지 안 보이던 버그 — 라벨로 교체한 회귀 방지)
  await expect(page.getByText('항목명', { exact: false }).first()).toBeVisible();

  // 값은 안 건드리고 그대로 저장 — 원본 내용 보존한 채 상태만 검토대기로 되돌린다
  await page.getByRole('button', { name: /저장/ }).first().click();
  await page.waitForTimeout(1500);

  // 검토대기 필터로 돌아가 방금 그 항목이 복귀했는지 확인 — 반려 이전과 동일한 상태로 원복됨
  await page.locator('select').nth(2).selectOption('pending_review');
  await page.waitForTimeout(600);
  await expect(page.locator('.space-y-2 > div.bg-slate-800').filter({ hasText: policyName }).first()).toBeVisible({ timeout: 10_000 });
});
