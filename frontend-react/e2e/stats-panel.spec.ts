import { test, expect } from '@playwright/test';
import { goAdmin, domainSelect } from './admin-nav';
import { execFileSync } from 'node:child_process';

// 이 테스트가 존재하는 이유(2026-10-02): 좋아요/싫어요 기반 해결·미해결을 없애고 질의 상태를 "답변 / 지식 공백" 둘로
// 줄였다. 통계는 "답변 / 정정 요청 / 지식 공백 / 공백 메움" — 카드 숫자가 실제 DB 집계(질의 상태·메움 연결·개선 원장
// 대기 건)와 맞는지, 옛 "해결·미해결·해결률"이 다시 나타나지 않는지 실 dev 스택으로 확인한다(읽기 전용).

const NAMESPACE = '온라인스토어';
const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();

test('통계 — 답변/정정 요청/지식 공백/공백 메움 카드가 DB 집계와 일치, 옛 해결·미해결 없음', async ({ page }) => {
  const id = psql(`SELECT id FROM ops_namespace WHERE name = '${NAMESPACE}'`);
  const q = (where: string) => psql(`SELECT COUNT(*) FROM ops_query_log WHERE namespace_id = ${id} AND ${where}`);
  const expected = {
    답변: q("status = 'pending'"),
    '정정 요청': psql(`SELECT COUNT(*) FROM ops_improvement_item WHERE namespace_id = ${id} AND status = 'pending'`),
    '지식 공백': q("status = 'no_knowledge' AND resolved_knowledge_id IS NULL"),
    '공백 해결': q("status = 'no_knowledge' AND resolved_knowledge_id IS NOT NULL"),
  };
  // 상태는 답변·공백 + 통계 밖 시스템 오류뿐 — 해결/미해결 행이 남아 있으면 마이그레이션 #61 회귀
  expect(q("status NOT IN ('pending', 'no_knowledge', 'system_error')")).toBe('0');
  const systemErrors = q("status = 'system_error'");

  await page.goto('/login');
  await page.getByPlaceholder('사용자 아이디').fill('admin');
  await page.getByPlaceholder('비밀번호').fill('1111');
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('heading', { name: '로그인' })).toBeHidden({ timeout: 10_000 });
  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  if (await agentHeading.isVisible().catch(() => false)) await page.getByText('선택하기').first().click();
  await page.getByRole('link', { name: 'Admin' }).click();
  await goAdmin(page, '품질', '통계');
  await domainSelect(page).selectOption(NAMESPACE);

  // 본문(main) 안에서만 — 사이드바 메뉴에도 "정정 요청" 버튼이 있다(10/21 메뉴 재배치)
  const card = (label: string) => page.locator('main').locator('button').filter({ has: page.getByText(label, { exact: true }) }).first();
  for (const [label, value] of Object.entries(expected)) {
    await expect(card(label)).toContainText(value);
  }
  await expect(page.getByText('답변 현황')).toBeVisible();
  // LLM 연결 실패는 전체·답변률에서 빠지고 건수만 따로
  await expect(card('전체 질의')).toContainText(q("status <> 'system_error'"));
  if (systemErrors !== '0') await expect(page.getByText(`LLM 연결 실패 ${systemErrors}건(통계 제외)`)).toBeVisible();
  for (const gone of ['미해결', '해결됨', '해결률', '대기 중']) {
    await expect(page.getByText(gone, { exact: true })).toHaveCount(0);
  }

  // 지식 공백 카드 → 공백 목록(펼치면 "지식 등록"), 정정 요청 카드 → 품질 › 정정 요청(같은 업무 영역) 정정 검토로 이동
  await card('지식 공백').click();
  await expect(page.getByText(`지식 공백 질의 (${expected['지식 공백']}건)`)).toBeVisible();
  if (expected['지식 공백'] !== '0') {
    await page.locator('.max-h-\\[60vh\\] > div').first().locator('button').first().click();
    await expect(page.getByRole('button', { name: '지식 등록' })).toBeVisible();
  }
  await page.keyboard.press('Escape');
  await card('정정 요청').click();
  await expect(page.getByText(/질의 \(\d+건\)/)).toHaveCount(0);
  await expect(page.getByText(/지식·정책을 고치자는 신호.*모두 모이는 곳/)).toBeVisible();
  await expect(domainSelect(page)).toHaveValue(NAMESPACE);
});
