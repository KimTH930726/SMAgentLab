import { test, expect } from '@playwright/test';
import { goAdmin, domainSelect } from './admin-nav';
import { createPolicyPart, dropPolicyPart } from './v0-fixture';

// 이 테스트가 존재하는 이유(2026-09-23): 정책 검토 UI(승인/반려 + 반려 항목 수정→재검토
// 유도)는 이번 세션에 새로 만든 기능이고, 실사용 중 "반려하면 그냥 버려지는데?"라는 지적으로
// edit.py + 인라인 편집 UI가 추가됐다 — 승인/반려/편집이 서로 맞물려 상태를 오가는 구조라
// 유닛테스트만으로는 "실제 화면에서 반려→편집 아이콘 노출→저장→검토대기 복귀"가 눈으로
// 확인한 그대로 계속 동작하는지 보장이 안 된다. 실 dev 스택 그대로 사용(목킹 없음).
//
// 데이터(2026-10-20 v0 현행화): 실제 정책을 반려·편집하지 않도록 임시 정책서 파트에 v0 방식(원문 통째) 검토대기 정책을 만들고
// 끝나면 통째로 지운다. v0 정책은 파라미터가 없어(적재 후 별도 단계) 편집 대상은 서술(= 원문 통째)이다.

const ADMIN_USER = 'admin';
const ADMIN_PASS = '1111';
const NAMESPACE = 'zz_e2e_review';
const BODY = '1. 임시 기준\n  - 이 정책은 E2E 검토 흐름 확인용이다.';

test.beforeAll(() => {
  test.setTimeout(180_000);   // 임시 정책 임베딩 — 백엔드 컨테이너에 새 프로세스로 임베딩 모델을 올려 20~40초
  createPolicyPart(NAMESPACE, [{ name: '임시 검토 정책', path: ['테스트', '검토'], body: BODY }], 'pending_review');
});
test.afterAll(() => dropPolicyPart(NAMESPACE));   // 결정 이력(policy_review_log)도 같이 — 가짜 결정이 남지 않게

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
  await goAdmin(page, '지식', '정책서');
  await page.getByRole('button', { name: '항목 브라우저' }).click();
  await domainSelect(page).selectOption(NAMESPACE);
  await page.waitForTimeout(500);
});

test('정책 항목 반려 → 편집 아이콘 노출 → 저장 시 검토대기로 복귀', async ({ page }) => {
  // "대분류" 드롭다운은 categoryOptions가 있을 때만 렌더링돼(PolicyItemBrowser.tsx)
  // select 순서가 namespace에 따라 달라진다 — 인덱스(nth) 대신 실제 option 값으로 찾는다
  // (/code-review 지적, 2026-09-23: knowledge-registration.spec.ts도 인덱스 대신
  // filter({hasText})를 쓰는 동일한 이유).
  const statusSelect = page.locator('select:has(option[value="rejected"])');

  const listResponse1 = page.waitForResponse((r) => /\/api\/policy\/items\?/.test(r.url()) && r.request().method() === 'GET');
  await statusSelect.selectOption('pending_review');
  await listResponse1;
  await page.waitForTimeout(300); // 응답 후 리스트 렌더 반영

  // 임시 파트엔 정책이 하나뿐 — 그 항목(서술 = 원문 통째)
  const editableRow = page.locator('.space-y-2 > div.bg-slate-800').filter({ hasText: '임시 검토 정책' }).first();
  await expect(editableRow).toBeVisible({ timeout: 10_000 });
  const policyName = (await editableRow.locator('span.font-medium').first().textContent())?.trim() ?? '';
  expect(policyName.length).toBeGreaterThan(0);

  // 반려 — confirm 문구가 "되돌릴 수 있다"고 정확히 안내하는지도 같이 확인
  // (2026-09-23 실사고: 편집→재검토 기능을 만들고 이 문구를 안 고쳐서 "되돌릴 화면이
  // 없다"고 거짓 안내하던 버그가 있었음 — 회귀 방지).
  // (2026-10-07부터 브라우저 confirm 대신 앱 확인 모달)
  const rejectResponse = page.waitForResponse((r) => /\/api\/policy\/items\/\d+\/reject/.test(r.url()));
  await editableRow.locator('button[title*="반려"]').click();
  await expect(page.getByTestId('app-dialog-message')).toContainText('다시 검토대기로 되돌릴 수 있습니다');
  await page.getByTestId('app-dialog-ok').click();
  await rejectResponse;
  await expect(page.getByTestId('app-dialog')).toHaveCount(0);

  // 반려됨 필터로 좁혀서 방금 그 항목을 다시 찾는다
  const listResponse2 = page.waitForResponse((r) => /\/api\/policy\/items\?/.test(r.url()) && r.request().method() === 'GET');
  await statusSelect.selectOption('rejected');
  await listResponse2;
  await page.waitForTimeout(300);
  const rejectedRow = page.locator('.space-y-2 > div.bg-slate-800').filter({ hasText: policyName }).first();
  await expect(rejectedRow).toBeVisible({ timeout: 10_000 });
  await rejectedRow.click();

  // 반려 안내 배너 + 편집(연필) 아이콘 노출 확인
  await expect(page.getByText('반려된 항목입니다')).toBeVisible();
  const editBtn = page.locator('button:has(svg.lucide-pencil)').first();
  await expect(editBtn).toBeVisible();
  await editBtn.click();

  // 서술 편집창에 원문 통째가 그대로 들어 있어야 한다(v0: 조각 = 원문)
  await expect(page.locator('textarea:visible').first()).toHaveValue(BODY);

  // 값은 안 건드리고 그대로 저장 — 원본 내용 보존한 채 상태만 검토대기로 되돌린다
  const saveResponse = page.waitForResponse((r) => /\/api\/policy\/(params|narratives)\/\d+/.test(r.url()) && r.request().method() === 'PATCH');
  await page.getByRole('button', { name: /저장/ }).first().click();
  await saveResponse;

  // 검토대기 필터로 돌아가 방금 그 항목이 복귀했는지 확인 — 반려 이전과 동일한 상태로 원복됨
  const listResponse3 = page.waitForResponse((r) => /\/api\/policy\/items\?/.test(r.url()) && r.request().method() === 'GET');
  await statusSelect.selectOption('pending_review');
  await listResponse3;
  await expect(page.locator('.space-y-2 > div.bg-slate-800').filter({ hasText: policyName }).first()).toBeVisible({ timeout: 10_000 });
});
