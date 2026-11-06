import { test, expect } from '@playwright/test';
import { loginAdmin } from './auth';
import { selectDomain } from './admin-nav';
import { psql } from './v0-fixture';

// 근거 카드 그림 배지(11/06 태훈 "그림 앞뒤 문맥 → 링크", 시안 = 머리 배지 + 펼침 링크) — 원문 앞뒤에 그림이 있는 조각만 "그림 N",
// 펼치면 "원문 페이지에서 그림 보기 ↗"(컨플 pageId 주소). 그림 없는 조각엔 없음. 대화·답을 DB에 직접 만들어 본다(LLM 0) — 끝에 지움.
const NS = '홈페이지 시스템';

test('근거 카드 — 그림 있는 조각만 배지 · 펼치면 원문 링크', async ({ page }) => {
  const img = psql(`SELECT id || '|' || image_count || '|' || confluence_page_id FROM rag_knowledge k WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}')
    AND status = 'active' AND image_count > 0 AND confluence_page_id IS NOT NULL ORDER BY id LIMIT 1`);
  const plain = psql(`SELECT id FROM rag_knowledge WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}')
    AND status = 'active' AND image_count = 0 AND confluence_page_id IS NOT NULL ORDER BY id LIMIT 1`);
  test.skip(!img || !plain, '그림 수를 센 컨플 조각이 없음');
  const [imgId, n, pageId] = img.split('|');
  const results = JSON.stringify([imgId, plain].map((id) => ({ id: Number(id), content: `E2E 근거 ${id}`, final_score: 0.8, relevance: 0.8, confidence: '높음' })));
  const uid = psql(`SELECT id FROM ops_user WHERE username = 'admin'`);
  const conv = psql(`INSERT INTO ops_conversation (namespace_id, title, user_id, agent_type) SELECT id, 'E2E 그림 배지', ${uid}, 'knowledge_rag' `
    + `FROM ops_namespace WHERE name = '${NS}' RETURNING id`).split('\n')[0];
  try {
    psql(`INSERT INTO ops_message (conversation_id, role, content) VALUES (${conv}, 'user', 'E2E 그림 질문')`);
    psql(`INSERT INTO ops_message (conversation_id, role, content, status, results) VALUES (${conv}, 'assistant', 'E2E 그림 답', 'completed', '${results}'::jsonb)`);
    await loginAdmin(page, { openAdmin: false });
    await selectDomain(page, NS, NS.replace(/ 시스템$/, ''));
    await page.getByText('E2E 그림 배지').first().click();
    await page.getByTestId('evidence-toggle').click();
    await expect(page.getByTestId(`images-${imgId}`)).toHaveText(`그림 ${n}`);
    await expect(page.getByTestId(`images-${plain}`)).toHaveCount(0);
    await page.getByTestId(`images-${imgId}`).click();   // 머리를 누르면 펼쳐진다
    const link = page.getByRole('link', { name: '원문 페이지에서 그림 보기 ↗' });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute('href', new RegExp(`pageId=${pageId}$`));
    await page.waitForTimeout(800);   // 펼침 애니메이션
    await page.screenshot({ path: 'test-results/evidence-images.png' });
  } finally {
    psql(`DELETE FROM ops_conversation WHERE id = ${conv}`);
  }
});
