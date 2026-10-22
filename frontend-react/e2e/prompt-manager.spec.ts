import { test, expect } from '@playwright/test';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';

// 프롬프트 관리(10/21) — VOC 메일 분석 프롬프트가 "RAG"로 표시되던 것(#79 voc). 채팅 에이전트를 골라 둔 상태에서도
// 공통·VOC 프롬프트가 목록에 보이고 VOC 라벨이 붙는지 확인한다(읽기 전용 — 저장·LLM 호출 없음).
test('프롬프트 관리 — VOC 프롬프트는 VOC로 표시되고 목록에서 빠지지 않는다', async ({ page }) => {
  await loginAdmin(page);
  await goAdmin(page, '시스템·권한', '설정');
  await page.getByRole('button', { name: '프롬프트 관리' }).click();

  for (const name of ['VOC 이메일 분석', 'VOC 이메일 분석 시스템']) {
    const row = page.getByRole('button').filter({ has: page.getByText(name, { exact: true }) });
    await expect(row).toHaveCount(1);
    // 이름에 이미 "VOC"가 있어 줄 전체 글자로는 라벨을 못 가린다 — 첫 배지(분류 라벨)만 본다(리뷰 10/21)
    await expect(row.locator('span.font-mono').first()).toHaveText('VOC');
  }
  await expect(page.getByRole('button').filter({ has: page.getByText('RAG 채팅 시스템', { exact: true }) })
    .locator('span.font-mono').first()).toHaveText('RAG');
  await expect(page.getByRole('button').filter({ has: page.getByText('대화 요약', { exact: true }) })
    .locator('span.font-mono').first()).toHaveText('공통');
  await page.screenshot({ path: test.info().outputPath('prompt-manager.png'), fullPage: true });
});
