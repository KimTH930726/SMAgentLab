import { test, expect } from '@playwright/test';
import { loginAdmin, adminToken, API } from './auth';
import { goAdmin } from './admin-nav';
import { psql } from './v0-fixture';

// 기법 대장 흐름도 스위치(11/03) — 지금 설정 = 검증된 최적("최적 상태" 표시). 스위치를 최적에서 벗어나게 바꾸면 경고("최적 설정에서 벗어나기") 뒤
// 바로 적용되고 주황 "최적에서 벗어남" 띠 → [최적으로 되돌리기]로 원상. 필수 기법은 서버가 403으로 막는다.
// 다른 컬렉션 제안(최적 = 꺼짐)으로 시험하고, 끝나면 최적으로 되돌린다(채팅 기본 동작에 남기지 않음). LLM 호출 없음.
const KEY = 'collection_suggest';

test('최적 상태 표시 → 스위치로 벗어나면 경고·주황 띠 → 최적으로 되돌리기 · 필수는 403', async ({ page, request }) => {
  const tok = await adminToken();
  const auth = { headers: { Authorization: `Bearer ${tok}` } };
  const baseline = async () => (await (await request.get(`${API}/api/eval/techniques/baseline`, auth)).json()) as { optimal: boolean; off: string[] };
  test.skip(!(await baseline()).optimal, '시작 상태가 최적이 아님 — 다른 시험이 남긴 값');
  try {
    expect((await request.put(`${API}/api/eval/techniques/chat_prompt/switch`, { ...auth, data: { on: false } })).status()).toBe(403);

    await loginAdmin(page);
    await goAdmin(page, '평가 게이트');
    await page.getByTestId('gate-history-section').getByRole('button', { name: /기법 대장/ }).click();
    const flow = page.getByTestId('technique-flow');
    await expect(flow.getByTestId('technique-optimal')).toBeVisible();

    await flow.getByTestId(`flow-tech-${KEY}`).click();
    await page.getByTestId('technique-switch').click();
    await expect(page.getByTestId('app-dialog-message')).toContainText('최적 설정이 깨져요');
    await page.getByTestId('app-dialog-ok').click();
    await expect(flow.getByTestId(`flow-tech-${KEY}`)).toHaveAttribute('data-state', 'on');
    await expect(flow.getByTestId('technique-off-baseline')).toContainText('다른 컬렉션');
    expect(psql(`SELECT value FROM ops_system_config WHERE key = 'chat.collection_suggest'`)).toBe('true');

    await page.keyboard.press('Escape');   // 상세는 모달 — 닫고 띠의 [최적으로 되돌리기]
    await expect(page.getByTestId('technique-flow-panel')).toBeHidden();
    await flow.getByTestId('technique-restore').click();
    await page.getByTestId('app-dialog-ok').click();
    await expect(flow.getByTestId('technique-optimal')).toBeVisible();
    await expect(flow.getByTestId(`flow-tech-${KEY}`)).toHaveAttribute('data-state', 'off');
  } finally {
    await request.post(`${API}/api/eval/techniques/baseline/restore`, auth);
  }
  expect((await baseline()).optimal).toBe(true);
});
