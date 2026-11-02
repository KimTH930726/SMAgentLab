import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(10/30 사용자 "장애·오류·바라는 개선을 받을 채널 + 관리자가 골라서 개선",
// 11/02 "이미지도 받아야 · 오류 창에서 바로 신고 · 진행 중일 때 개발 요청서"):
// [피드백 보내기] + 이미지 첨부 → DB 한 줄 + 이미지 → 관리자 목록에 썸네일 → 기각은 사유 필수 · 선정 → 진행 중(개발 요청서) → 완료
// → 보낸 사람 [내 피드백]에 상태·답변. 오류 창 [오류 신고] → 오류 문맥(요청·상태 코드)이 붙은 신고. 끝에 지운다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
const BODY = `E2E 피드백 ${Date.now()} — 새로고침하면 대화가 사라져요`;
const FAIL_MSG = `E2E 일시 오류 ${Date.now()}`;
// 1×1 PNG — 서버는 앞 바이트로 이미지 판정
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');

test('피드백 — 이미지 첨부 보내기 → 관리자 분류(기각 사유 필수 · 선정 · 진행 중 · 개발 요청서 · 완료) → 보낸 사람이 결과를 봄', async ({ page }) => {
  test.setTimeout(120_000);
  try {
    await loginAdmin(page, { openAdmin: false });
    await page.getByTestId('feedback-open').click();
    const m = page.getByTestId('feedback-modal');
    await expect(m.getByTestId('feedback-send')).toBeDisabled();
    await m.getByTestId('feedback-kind-bug').click();
    await m.locator('#feedback-body').fill(BODY);
    await m.getByTestId('feedback-file-input').setInputFiles([{ name: 'capture.png', mimeType: 'image/png', buffer: PNG }]);
    await expect(m.getByTestId('feedback-attachments').locator('img')).toHaveCount(1);
    await m.getByTestId('feedback-file-input').setInputFiles([{ name: 'a.txt', mimeType: 'text/plain', buffer: Buffer.from('x') }]);
    await expect(m.getByTestId('feedback-error')).toContainText('이미지 파일');     // 이미지가 아니면 안 붙음
    await expect(m.getByTestId('feedback-attachments').locator('img')).toHaveCount(1);
    await m.getByTestId('feedback-send').click();
    await expect(page.getByTestId('app-dialog')).toContainText('피드백 접수');
    await page.getByTestId('app-dialog-ok').click();
    await expect(m).toBeHidden();
    const id = await expect.poll(() => psql(`SELECT id FROM ops_user_feedback WHERE body = '${BODY}'`)).not.toBe('')
      .then(() => psql(`SELECT id FROM ops_user_feedback WHERE body = '${BODY}'`));
    expect(psql(`SELECT kind || '|' || status || '|' || (page IS NOT NULL) FROM ops_user_feedback WHERE id = ${id}`)).toBe('bug|new|true');
    expect(psql(`SELECT mime || '|' || size FROM ops_user_feedback_file WHERE feedback_id = ${id}`)).toBe(`image/png|${PNG.length}`);

    await page.goto('/admin');
    await goAdmin(page, '품질', '사용자 피드백');
    const panel = page.getByTestId('user-feedback-panel');
    await expect(panel.getByTestId('feedback-auto-signals')).toContainText('최근 14일 채팅 장애');
    const row = panel.getByTestId(`feedback-${id}`);
    await expect(row).toContainText(BODY);
    const thumb = row.getByTestId(`feedback-images-${id}`).locator('img');
    await expect(thumb).toHaveCount(1);
    await expect.poll(() => thumb.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(1);   // 인증 붙여 받은 실제 이미지
    await row.getByRole('button', { name: '기각…' }).click();
    await expect(page.getByRole('button', { name: '기각', exact: true })).toBeDisabled();   // 사유 없이 못 함
    await page.keyboard.press('Escape');

    // 오류 창 [오류 신고] — 서버 장애를 한 번만 흉내 내(이 요청만 500) 실제 오류 창 → 신고 창에 요청·상태 코드가 붙는지
    await page.route(`**/api/user-feedback/${id}`, (r) => (r.request().method() === 'PATCH'
      ? r.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: FAIL_MSG }) }) : r.fallback()), { times: 1 });
    await row.getByRole('button', { name: '선정' }).click();
    await expect(page.getByTestId('app-dialog')).toContainText(FAIL_MSG);
    await page.getByTestId('app-dialog-report').click();
    await expect(m.getByTestId('feedback-context')).toContainText(`PATCH /api/user-feedback/${id} → 500`);
    await expect(m.getByTestId('feedback-send')).toBeEnabled();                        // 글 없이도 보낼 수 있음(문맥이 있어서)
    await m.getByTestId('feedback-send').click();
    await page.getByTestId('app-dialog-ok').click();
    await expect.poll(() => psql(`SELECT kind || '|' || (context->>'status') || '|' || (context->>'path') || '|' || body FROM ops_user_feedback WHERE context->>'message' = '${FAIL_MSG}'`))
      .toBe(`bug|500|PATCH /api/user-feedback/${id}|`);
    expect(psql(`SELECT status FROM ops_user_feedback WHERE id = ${id}`)).toBe('new');   // 막힌 요청은 상태를 안 바꿈

    await row.getByRole('button', { name: '선정' }).click();
    await expect.poll(() => psql(`SELECT status FROM ops_user_feedback WHERE id = ${id}`)).toBe('planned');
    await expect(row).toHaveCount(0);                     // [신규] 필터에서 빠짐
    await panel.getByTestId('feedback-filters').getByRole('button', { name: /^선정/ }).click();
    await expect(panel.getByTestId(`feedback-${id}-handoff`)).toHaveCount(0);           // 개발 요청서는 진행 중일 때만
    await panel.getByTestId(`feedback-${id}`).getByRole('button', { name: '진행 중' }).click();
    await expect.poll(() => psql(`SELECT status FROM ops_user_feedback WHERE id = ${id}`)).toBe('in_progress');
    await panel.getByTestId('feedback-filters').getByRole('button', { name: /^진행 중/ }).click();
    await panel.getByTestId(`feedback-${id}-handoff`).click();
    const text = page.getByTestId(`handoff-${id}-text`);
    await expect(text).toHaveValue(new RegExp(`사용자 피드백 #${id} — 오류 신고`));
    await expect(text).toHaveValue(/첨부 이미지: 1장 — 운영 화면에서 확인/);
    await expect(text).toHaveValue(new RegExp(BODY.slice(0, 20)));
    await page.keyboard.press('Escape');
    await panel.getByTestId(`feedback-${id}`).getByRole('button', { name: '완료…' }).click();
    await page.locator(`#fb-note-${id}`).fill('새로고침 뒤에도 대화가 남도록 고쳤어요');
    await page.getByRole('button', { name: '완료로', exact: true }).click();
    await expect.poll(() => psql(`SELECT status FROM ops_user_feedback WHERE id = ${id}`)).toBe('done');

    // 보낸 사람 — 새 결과 점 → [내 피드백]에 완료 + 답변 + 첨부, 본 뒤엔 점이 꺼짐
    await page.goto('/');
    await expect(page.getByTestId('feedback-open')).toHaveAttribute('title', /처리 결과가 나온 피드백/);
    await page.getByTestId('feedback-open').click();
    const mine = page.getByTestId('feedback-mine');
    await expect(mine).toContainText('완료');
    await expect(mine).toContainText('새로고침 뒤에도 대화가 남도록 고쳤어요');
    await expect(mine.getByTestId(`feedback-images-${id}`).locator('img')).toHaveCount(1);
    expect(psql(`SELECT reporter_seen_at IS NOT NULL FROM ops_user_feedback WHERE id = ${id}`)).toBe('t');
  } finally {
    psql(`DELETE FROM ops_user_feedback WHERE body LIKE 'E2E 피드백 %' OR context->>'message' LIKE 'E2E 일시 오류 %'`);
  }
});
