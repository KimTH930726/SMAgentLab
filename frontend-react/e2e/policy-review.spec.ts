import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';

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

// 결정 이력(policy_review_log, 2026-10-01) — 이 테스트의 반려·재제출은 실제 정책 항목에 대한 "가짜" 결정이라
// 이력에 남기면 그 항목이 영구히 "이전에 반려된 항목"(위험 높음)이 되고 자동 승인 기준 데이터가 오염된다 — 지운다.
const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
let logIdBefore = 0;
test.beforeAll(() => { logIdBefore = Number(psql('SELECT COALESCE(MAX(id), 0) FROM policy_review_log')); });
let rejectedItemId = 0;
test.afterAll(() => {
  psql(`DELETE FROM policy_review_log WHERE id > ${logIdBefore} AND action IN ('rejected', 'resubmitted')`);
  // 중간에 실패해도 실제 정책 항목이 반려(검색 제외)로 남지 않게 — 정상 종료면 이미 검토대기라 영향 없음
  if (rejectedItemId) {
    psql(`UPDATE policy_item SET status='pending_review', reviewed_by=NULL, reviewed_at=NULL, review_source=NULL WHERE id=${rejectedItemId} AND status='rejected'`);
  }
});

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
  // "대분류" 드롭다운은 categoryOptions가 있을 때만 렌더링돼(PolicyItemBrowser.tsx)
  // select 순서가 namespace에 따라 달라진다 — 인덱스(nth) 대신 실제 option 값으로 찾는다
  // (/code-review 지적, 2026-09-23: knowledge-registration.spec.ts도 인덱스 대신
  // filter({hasText})를 쓰는 동일한 이유).
  const statusSelect = page.locator('select:has(option[value="rejected"])');

  const listResponse1 = page.waitForResponse((r) => /\/api\/policy\/items\?/.test(r.url()) && r.request().method() === 'GET');
  await statusSelect.selectOption('pending_review');
  await listResponse1;
  await page.waitForTimeout(300); // 응답 후 리스트 렌더 반영

  // param이나 narrative가 실제로 하나라도 있는 항목만 대상으로 고른다 — 아무것도
  // 없는 항목("이 항목엔 param/narrative가 없습니다")은 편집 아이콘 자체가 안 뜬다
  // (/code-review 지적, 2026-09-23: 첫 항목이 우연히 그런 케이스면 테스트가 기능과
  // 무관하게 실패함).
  // 파라미터가 있는 항목으로 — 아래 "항목명" 라벨은 파라미터 편집 폼의 것이다. 검토대기가 위험도 순으로 정렬된 뒤
  // (2026-10-01) 맨 위가 서술만 있는 위험 높음 항목이 되자, 첫 연필이 서술 편집을 열어 라벨 확인이 깨졌다.
  const editableRow = page.locator('.space-y-2 > div.bg-slate-800').filter({
    has: page.locator('[title="RDB 정확조회 파라미터"]'),
  }).first();
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
  rejectedItemId = Number((await rejectResponse).url().match(/items\/(\d+)\/reject/)?.[1] ?? 0);
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

  // 라벨이 항상 보이는지 확인(2026-09-23 실사고: placeholder만 있어서 값이 채워지면
  // 뭐가 뭔지 안 보이던 버그 — 라벨로 교체한 회귀 방지)
  await expect(page.getByText('항목명', { exact: false }).first()).toBeVisible();

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
