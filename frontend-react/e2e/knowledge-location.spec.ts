import { test, expect } from '@playwright/test';
import { goAdmin } from './admin-nav';
import { psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 지식 등록 넣을 곳(E2, 10/23) — 업무 컬렉션의 [지식 등록 →]으로 오면 넣을 곳이 그 컬렉션으로 정해진 채(preset) 넣을 곳 카드가 뜨고,
// 등록하면 그 컬렉션에 저장 + AI 판단 원장 같은 줄이 "preset"으로 확정된다. 판정 LLM 1회(설정대로 로컬). 화면 문구가 아니라 DB 값으로 확인.
const NS = '딜리버스';

test('[지식 등록 →]으로 오면 넣을 곳 = 그 컬렉션 · 등록하면 그곳에 저장 + 원장 확정(preset)', async ({ page }) => {
  const marker = `E2E넣을곳_${Date.now()}`;
  await loginAdmin(page);
  await goAdmin(page, '컬렉션', '업무 컬렉션');
  await page.getByRole('row').filter({ hasText: NS }).first().getByRole('button', { name: '지식 등록 →' }).click();
  await expect(page).toHaveURL((u) => u.searchParams.get('into') === NS);

  await page.getByText('직접 입력').click();
  await page.locator('textarea:visible').first().fill(`${marker} — 배차 화면에서 상태 코드를 확인하는 가상의 절차 설명입니다.`);
  await page.getByRole('button', { name: '넣을 곳 확인' }).click();
  const card = page.getByTestId('location-picker');
  await expect(card).toBeVisible({ timeout: 60_000 });
  await expect(card).toContainText('넣을 곳');
  await expect(card).toContainText(NS);

  const created = page.waitForResponse((r) => r.url().endsWith('/api/knowledge') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '등록', exact: true }).click();
  const res = await created;
  expect(res.ok()).toBeTruthy();
  const sent = res.request().postDataJSON();
  expect(sent.namespace).toBe(NS);
  expect(sent.location_choice).toBe('preset');
  const id: number = (await res.json()).id;
  try {
    expect(psql(`SELECT n.name FROM rag_knowledge k JOIN ops_namespace n ON n.id = k.namespace_id WHERE k.id = ${id}`)).toBe(NS);
    const fin = psql(`SELECT final_value->>'choice' || '|' || (final_value->>'suggested') || '|' || array_to_string(target_ids, ',')
                      FROM ops_ai_decision WHERE id = ${Number(sent.decision_id)}`);
    expect(fin).toBe(`preset|false|${id}`);
  } finally {
    psql(`DELETE FROM rag_knowledge WHERE id = ${id}`);
    psql(`DELETE FROM ops_ai_decision WHERE id = ${Number(sent.decision_id)}`);
  }
});

test('청크 검토 위 [다른 곳…] 창 — Esc는 맨 위 창만 닫는다(청크 검토는 그대로) · 창은 "어디에 넣을까요?"', async ({ page }) => {
  await loginAdmin(page);
  await goAdmin(page, '지식', '지식 문서');
  await page.getByRole('button', { name: '지식 등록', exact: true }).click();
  await page.getByText('대량 텍스트', { exact: true }).first().click();
  await page.locator('textarea:visible').first().fill(`# E2E창겹침_${Date.now()}\n\n가상의 절차 설명 첫 단락입니다.\n\n## 둘째\n\n가상의 절차 설명 둘째 단락입니다.`);
  await page.getByRole('button', { name: '청크 검토 & 등록' }).click();
  const other = page.getByRole('button', { name: '다른 곳…' });
  await other.waitFor({ timeout: 60_000 });
  await expect(page.getByTestId('location-reasons')).toContainText('비슷한 지식');   // 추천 이유가 보인다
  await other.click();
  await expect(page.getByText('어디에 넣을까요?')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByText('어디에 넣을까요?')).toHaveCount(0);
  await expect(page.getByText(/^청크 검토 — /)).toBeVisible();   // 아래 창은 그대로
  await page.keyboard.press('Escape');
  await expect(page.getByText(/^청크 검토 — /)).toHaveCount(0);
  // 이 시험이 남긴 확정 전 판정 줄만(관리자 계정·방금) — 다른 사람 줄은 건드리지 않는다
  psql(`DELETE FROM ops_ai_decision WHERE kind = 'knowledge_location' AND final_value IS NULL AND created_at > now() - interval '5 minutes'
        AND created_by_user_id = (SELECT id FROM ops_user WHERE username = 'admin')`);
});
