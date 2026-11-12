import { test, expect } from '@playwright/test';
import { splitCites, type CitePart } from '../src/components/chat/citeChips';
import { loginAdmin } from './auth';
import { selectDomain } from './admin-nav';
import { psql } from './v0-fixture';

// 근거 칩(11/12 A안) — 표식 나누기는 글자를 잃거나 만들지 않는다(성질 테스트, 브라우저 불필요)
const join = (ps: CitePart[]) => ps.map((p) => (p.t === 'text' ? p.v : p.raw)).join('');

test.describe('근거 표식 나누기', () => {
  test('형식별로 나눈다', () => {
    const ps = splitCites('정산은 02시 [#12] · 재기동 [#12, #13] · 기준 [#7, 8] · [문서 #9] · 환불 [정책] · 주말은 확인 안 됨');
    expect(ps.filter((p) => p.t === 'doc').map((p) => (p as { ids: number[] }).ids)).toEqual([[12], [12, 13], [7, 8], [9]]);
    expect(ps.filter((p) => p.t === 'kind').map((p) => (p as { kind: string }).kind)).toEqual(['정책']);
    expect(ps.filter((p) => p.t === 'unverified')).toHaveLength(1);
  });

  test('같은 번호 중복은 칩 하나', () => {
    const [p] = splitCites('[#12, #12]');
    expect(p).toMatchObject({ t: 'doc', ids: [12] });
  });

  test('표식이 아닌 대괄호·# 는 그대로 글', () => {
    for (const s of ['[참고] 문서', '#12 단독', '[#] 빈 번호', '[#12', 'C#', '[DB서버]']) {
      const ps = splitCites(s);
      expect(ps.every((p) => p.t === 'text')).toBe(true);
      expect(join(ps)).toBe(s);
    }
  });

  test('무작위 글자도 유실·추가 0', () => {
    const bits = ['가', ' ', '[', ']', '#', '12', ',', '문서 ', '정책', '참고표', 'DB', '확인 안 됨', '\n', '[#3]', '[#4, 5]', 'a'];
    let seed = 7;
    const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
    for (let n = 0; n < 2000; n++) {
      const s = Array.from({ length: 1 + Math.floor(rnd() * 20) }, () => bits[Math.floor(rnd() * bits.length)]).join('');
      expect(join(splitCites(s))).toBe(s);
    }
  });
});

// 화면 — 답의 표식이 칩으로 그려지고, 누르면 근거를 펼쳐 그 카드로 이동·강조. 대화·답은 DB에 직접(LLM 0), 끝에 지움
test('답 본문 근거 칩 — 번호·정책·확인 안 됨·없는 번호', async ({ page }) => {
  const NS = '홈페이지 시스템';
  const ids = psql(`SELECT string_agg(id::text, ',') FROM (SELECT id FROM rag_knowledge WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}')
    AND status = 'active' ORDER BY id LIMIT 2) t`).split(',').map(Number);
  test.skip(ids.length < 2 || ids.some(Number.isNaN), '활성 지식이 2건 미만');
  const [a, b] = ids;
  const results = JSON.stringify([a, b].map((id) => ({ id, content: `E2E 근거 ${id}`, final_score: 0.8, relevance: 0.8, confidence: '높음' })));
  const answer = `- 첫 줄 [#${a}]\n- 둘째 줄 [#${a}, #${b}]\n- 셋째 줄 [#999999]\n- 넷째 줄 [정책]\n- 주말 기준은 확인 안 됨\n\n\`코드 [#${a}]\`\n\n<span data-cite="doc:${a}">위장</span> <a href="https://example.com">링크 [#${a}]</a>\n\n| 칸 |\n|---|\n| 표 [문서 #${b}] |`;
  const uid = psql(`SELECT id FROM ops_user WHERE username = 'admin'`);
  const conv = psql(`INSERT INTO ops_conversation (namespace_id, title, user_id, agent_type) SELECT id, 'E2E 근거 칩', ${uid}, 'knowledge_rag' `
    + `FROM ops_namespace WHERE name = '${NS}' RETURNING id`).split('\n')[0];
  try {
    psql(`INSERT INTO ops_message (conversation_id, role, content) VALUES (${conv}, 'user', 'E2E 칩 질문')`);
    psql(`INSERT INTO ops_message (conversation_id, role, content, status, results) VALUES (${conv}, 'assistant', E'${answer}', 'completed', '${results}'::jsonb)`);
    await loginAdmin(page, { openAdmin: false });
    await selectDomain(page, NS, NS.replace(/ 시스템$/, ''));
    await page.getByText('E2E 근거 칩').first().click();
    const docs = page.getByTestId('cite-doc');
    await expect(docs).toHaveText(['1', '1', '2', '2']);                  // 숫자 = 근거 카드 순서(마지막은 표 칸 안 [문서 #b])
    await expect(page.getByTestId('cite-unknown')).toHaveText('?');        // 문맥에 없는 번호
    await expect(page.getByTestId('cite-kind')).toHaveText('정책');        // 정책 근거 없음 → 누를 수 없는 칩
    await expect(page.getByTestId('cite-unverified')).toHaveCount(1);
    await expect(page.locator('code', { hasText: `[#${a}]` })).toHaveCount(1);   // 코드 안은 그대로 글
    await expect(page.getByRole('link', { name: `링크 [#${a}]` })).toHaveCount(1);   // 링크 안도 그대로 글
    await expect(page.locator('[data-cite]')).toHaveCount(0);              // 답에 넣은 data-cite는 sanitize가 지움 — 칩 위장 불가(리뷰)
    await expect(page.getByText('위장')).toBeVisible();
    await docs.nth(2).click();                                              // 2 → 펼치고 #b 카드 강조
    const card = page.locator(`[data-ev="k-${b}"]`);
    await expect(card).toBeVisible();
    await expect(card).toHaveClass(/ring-2/);
    await page.waitForTimeout(300);   // 스크롤·강조 전환
    await page.screenshot({ path: 'test-results/cite-chips.png' });
    await page.getByRole('button', { name: '라이트 모드' }).click();
    await page.waitForTimeout(700);   // 테마 전환
    await page.screenshot({ path: 'test-results/cite-chips-light.png' });
    await page.getByRole('button', { name: '다크 모드' }).click();   // 테마 되돌림
    await expect(card).not.toHaveClass(/ring-2/, { timeout: 3_000 });     // 1.5초 뒤 강조 해제
  } finally {
    psql(`DELETE FROM ops_conversation WHERE id = ${conv}`);
  }
});
