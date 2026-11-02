import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(10/30 사용자 "장애·오류·바라는 개선을 받을 채널 + 관리자가 골라서 개선"):
// [피드백 보내기] → DB 한 줄(유형·화면 자동) → 품질 › 사용자 피드백 [신규]에 보임 → 기각은 사유 필수 · 선정/완료가 DB 상태를 바꿈
// → 보낸 사람 [내 피드백]에 상태·답변이 보임(새 결과 표시). 끝에 지운다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
const BODY = `E2E 피드백 ${Date.now()} — 새로고침하면 대화가 사라져요`;

test('피드백 — 보내기 → 관리자 분류(기각 사유 필수 · 선정 · 완료) → 보낸 사람이 결과를 봄', async ({ page }) => {
  test.setTimeout(90_000);
  try {
    await loginAdmin(page, { openAdmin: false });
    await page.getByTestId('feedback-open').click();
    const m = page.getByTestId('feedback-modal');
    await expect(m.getByTestId('feedback-send')).toBeDisabled();
    await m.getByTestId('feedback-kind-bug').click();
    await m.locator('#feedback-body').fill(BODY);
    await m.getByTestId('feedback-send').click();
    await expect(page.getByTestId('app-dialog')).toContainText('피드백 접수');
    await page.getByTestId('app-dialog-ok').click();
    await expect(m).toBeHidden();
    const id = await expect.poll(() => psql(`SELECT id FROM ops_user_feedback WHERE body = '${BODY}'`)).not.toBe('')
      .then(() => psql(`SELECT id FROM ops_user_feedback WHERE body = '${BODY}'`));
    expect(psql(`SELECT kind || '|' || status || '|' || (page IS NOT NULL) FROM ops_user_feedback WHERE id = ${id}`)).toBe('bug|new|true');

    await page.goto('/admin');
    await goAdmin(page, '품질', '사용자 피드백');
    const panel = page.getByTestId('user-feedback-panel');
    await expect(panel.getByTestId('feedback-auto-signals')).toContainText('최근 14일 채팅 장애');
    const row = panel.getByTestId(`feedback-${id}`);
    await expect(row).toContainText(BODY);
    await row.getByRole('button', { name: '기각…' }).click();
    await expect(page.getByRole('button', { name: '기각', exact: true })).toBeDisabled();   // 사유 없이 못 함
    await page.keyboard.press('Escape');
    await row.getByRole('button', { name: '선정' }).click();
    await expect.poll(() => psql(`SELECT status FROM ops_user_feedback WHERE id = ${id}`)).toBe('planned');
    await expect(row).toHaveCount(0);                     // [신규] 필터에서 빠짐
    await panel.getByRole('button', { name: /^선정/ }).click();
    await panel.getByTestId(`feedback-${id}`).getByRole('button', { name: '완료…' }).click();
    await page.locator(`#fb-note-${id}`).fill('새로고침 뒤에도 대화가 남도록 고쳤어요');
    await page.getByRole('button', { name: '완료로', exact: true }).click();
    await expect.poll(() => psql(`SELECT status FROM ops_user_feedback WHERE id = ${id}`)).toBe('done');

    // 보낸 사람 — 새 결과 점 → [내 피드백]에 완료 + 답변, 본 뒤엔 점이 꺼짐
    await page.goto('/');
    await expect(page.getByTestId('feedback-open')).toHaveAttribute('title', /처리 결과가 나온 피드백/);
    await page.getByTestId('feedback-open').click();
    const mine = page.getByTestId('feedback-mine');
    await expect(mine).toContainText('완료');
    await expect(mine).toContainText('새로고침 뒤에도 대화가 남도록 고쳤어요');
    expect(psql(`SELECT reporter_seen_at IS NOT NULL FROM ops_user_feedback WHERE id = ${id}`)).toBe('t');
  } finally {
    psql(`DELETE FROM ops_user_feedback WHERE body LIKE 'E2E 피드백 %'`);
  }
});
