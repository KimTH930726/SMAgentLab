import { test, expect } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { loginAdmin } from './auth';
import { createSystem, dropSystem, psql } from './v0-fixture';

// 11/03 승인 대기 — 유사도 높은 순 + 좌우 차이(고친 줄은 바뀐 낱말만 강조). 비교할 기존 지식이 없는 항목은 [원문]으로(본문이 안 보이던 결함, 11/03 E2E)
const NS = `E2E차이_${Date.now()} 시스템`;

test.afterAll(() => {
  psql(`DELETE FROM rag_knowledge_duplicate_match WHERE new_knowledge_id IN (SELECT id FROM rag_knowledge WHERE namespace_id IN (SELECT id FROM ops_namespace WHERE name = '${NS}'))`);
  psql(`DELETE FROM rag_knowledge WHERE namespace_id IN (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  dropSystem(NS);
});

test('승인 대기 — 유사도순 · 좌우 차이(바뀐 줄·추가 줄) · 매칭 없는 항목은 원문', async ({ page }) => {
  const nsId = createSystem(NS);
  const id = (content: string, status: string) =>
    Number(psql(`INSERT INTO rag_knowledge (namespace_id, content, status) VALUES (${nsId}, E'${content}', '${status}') RETURNING id`).trim().split('\n')[0]);
  const base = id('## 배치 점검\\n재기동 전 상태 확인\\n담당 운영팀', 'active');
  const near = id('## 배치 점검\\n재기동 전 상태 꼭 확인\\n담당 운영팀\\n완료 후 알림', 'pending_review');
  const far = id('## 배치 점검 요약\\n다른 글', 'pending_review');
  id('## 매칭 없는 항목\\n본문 보임', 'pending_review');
  psql(`INSERT INTO rag_knowledge_duplicate_match (new_knowledge_id, matched_knowledge_id, similarity) VALUES (${near}, ${base}, 0.97), (${far}, ${base}, 0.89)`);

  await loginAdmin(page, { openAdmin: false });
  await page.getByRole('link', { name: 'Admin' }).click();
  await goAdmin(page, '운영 지식');
  await selectDomain(page, NS, NS.replace(/ 시스템$/, ''));
  await page.getByRole('button', { name: /승인 대기/ }).first().click();

  const sims = page.getByTestId('review-row-sim');
  await expect(sims).toHaveText(['97.0%', '89.0%', '—']);   // 가장 비슷한 것부터, 매칭 없는 건 맨 뒤

  await page.getByTestId('review-row').first().click();
  const diff = page.getByTestId('review-diff');
  await expect(diff).toBeVisible();
  await expect(page.getByTestId('review-diff-summary')).toHaveText('−1줄 기존에만 · +2줄 신규에만 · 같은 줄 2');   // 고친 줄 = 기존 1 + 신규 1, 추가 1
  await expect(diff.locator('[data-side="right"][data-kind="change"] mark', { hasText: '꼭' })).toBeVisible();   // 바뀐 낱말만 강조
  await expect(diff.locator('[data-side="right"][data-kind="add"]', { hasText: '완료 후 알림' })).toBeVisible();
  await page.keyboard.press('Escape');

  await page.getByTestId('review-row').nth(2).click();
  await expect(page.getByRole('dialog').getByRole('group', { name: '보기' }).getByRole('button', { name: '차이' })).toBeDisabled();
  await expect(page.getByRole('dialog').getByText('본문 보임')).toBeVisible();   // 원문 보기로 본문이 보인다
});
