import { test, expect } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { loginAdmin } from './auth';
import { createSystem, dropSystem, psql } from './v0-fixture';

// 11/03 보안 리뷰 #2: 지식 본문·답변의 마크다운을 rehype-raw로만 그려, 본문에 넣은 <img onerror>가 관리자 화면에서 실행됐다
// (토큰은 localStorage). 이제 sanitize(utils/markdown.ts) + CSP. 표·굵게 같은 무해한 HTML은 그대로 보여야 한다.
const NS = `E2E보안_${Date.now()} 시스템`;

test.afterAll(() => {
  psql(`DELETE FROM rag_knowledge WHERE namespace_id IN (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  dropSystem(NS);
});

test('지식 본문의 스크립트·이벤트 속성은 실행되지 않고, 무해한 HTML은 그대로', async ({ page }) => {
  const nsId = createSystem(NS);
  const body = `## 보안 확인\\n<img src="x" onerror="window.__xss = 1">\\n<b>굵게 보임</b>\\n<a href="javascript:window.__xss=2">링크</a>`;
  psql(`INSERT INTO rag_knowledge (namespace_id, content, status) VALUES (${nsId}, E'${body}', 'pending_review')`);

  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await loginAdmin(page, { openAdmin: false });
  await page.getByRole('link', { name: 'Admin' }).click();
  await goAdmin(page, '지식', '운영 지식');
  await selectDomain(page, NS, NS.replace(/ 시스템$/, ''));
  // 본문을 마크다운으로 그리는 곳 = 승인 대기 줄을 누르면 뜨는 유사 지식 검토 창
  const dialog = page.getByRole('dialog', { name: '유사 지식 검토' });
  await page.getByRole('button', { name: /승인 대기/ }).first().click();
  if (!(await dialog.isVisible())) await page.getByText('보안 확인').first().click();
  await expect(dialog.locator('b', { hasText: '굵게 보임' })).toBeVisible();     // 무해한 HTML은 그려진다
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined();
  expect(await page.locator('img[onerror]').count()).toBe(0);                    // 이벤트 속성은 지워졌다
  expect(await page.locator('a[href^="javascript:"]').count()).toBe(0);
  expect(errors).toEqual([]);
});
