import { test, expect } from '@playwright/test';
import { loginAdmin } from './auth';
import { selectDomain } from './admin-nav';
import { psql } from './v0-fixture';

// 재질문(v2.134, 2026-10-12) — 주제만 말한 질문의 목록이 크면 답 대신 범위 선택지(①②③ + 전부 보기 + 기타)를 보여주고,
// 고르면 같은 대화에서 그 범위로 답한다(다시 묻지 않음). 재질문은 답이 아니라 "답변 틀림"이 없다. 실 dev 스택 그대로(LLM 1회).
// v0 정책서 파트(2026-10-20 현행화) — "배송"은 정책 36개·하위 절 9개라 재질문이 뜬다(실데이터로 확인: 범위 3 + 전부 보기).
// 정책서 파트를 고르면 정책 검색 범위가 그 파트뿐이다(service/policy/scope.py).
const NS = '온라인스토어';
const Q = '배송 정책 좀';
const Q_LIST = '배송 정책에 관련된 정책들 목록 말해줘';

// 되묻기는 AI 판단 원장(kind chat_clarify)에 한 줄씩 남는다 — 시험이 남긴 줄은 지운다(10/28: 원장 되묻기 30줄이 전부 이 시험이었음).
// 시험 시작 뒤에 생긴 줄 중, 그 되묻기를 부른 질문이 이 시험의 질문인 것만(같은 대화의 바로 앞 사용자 질문으로 확인)
let before = 0;
test.beforeAll(() => { before = Number(psql('SELECT coalesce(max(id), 0) FROM ops_ai_decision')); });
test.afterAll(() => {
  const qs = [Q, Q_LIST].map((q) => `'${q}'`).join(',');
  psql(`DELETE FROM ops_ai_decision d USING ops_message m WHERE d.kind = 'chat_clarify' AND d.id > ${before} AND m.id = d.target_ids[1]
    AND (SELECT q.content FROM ops_message q WHERE q.conversation_id = m.conversation_id AND q.role = 'user' AND q.id < m.id ORDER BY q.id DESC LIMIT 1) IN (${qs})`);
});

test('주제만 말한 큰 목록 → 범위 선택지 → 고르면 그 범위로 답', async ({ page }) => {
  test.setTimeout(360_000);   // 선택 후 답은 LLM(E2E는 로컬 LLM — llm-local.setup.ts)
  page.on('dialog', (d) => { throw new Error(`브라우저 기본 대화상자: ${d.message()}`); });
  await loginAdmin(page, { openAdmin: false });
  const authState = await page.evaluate(() => localStorage.getItem('ops_auth'));
  const auth = { Authorization: `Bearer ${authState ? JSON.parse(authState).accessToken : ''}` };
  // 캐시된 예전 답(재질문 전)이 있으면 재질문이 안 나온다 — 이 파트 캐시를 비우고 시작
  await page.request.delete(`/api/admin/cache?namespace=${encodeURIComponent(NS)}`, { headers: auth });
  await selectDomain(page, NS);

  await page.locator('textarea:visible').first().fill(Q);
  await page.getByTitle('전송 (Ctrl+Enter)').click();
  const opts = page.getByTestId('clarify-options').last();
  await expect(opts).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('어느 부분을 볼까요?').last()).toBeVisible();
  const buttons = opts.getByRole('button');
  await expect(buttons).toHaveCount(5);                          // 범위 3 + 전부 보기 + 기타
  await expect(buttons.nth(3)).toContainText('전부 보기');
  await expect(buttons.nth(4)).toContainText('기타');
  await expect(page.getByRole('button', { name: '답변 틀림' })).toHaveCount(0);   // 재질문은 답이 아니다

  // 기타 → 입력창으로
  await buttons.nth(4).click();
  await expect(page.locator('textarea:visible').first()).toBeFocused();

  // ① 선택 → 같은 대화에서 그 범위로 답(다시 묻지 않음)
  const firstLabel = (await buttons.nth(0).innerText()).replace(/^①\s*/, '').replace(/\s*\(\d+개\)$/, '');
  await buttons.nth(0).click();
  await expect(page.getByRole('button', { name: '답변 틀림' })).toHaveCount(1, { timeout: 300_000 });
  await expect(page.getByTestId('clarify-options')).toHaveCount(1);   // 새 재질문 없음
  // 지난 재질문의 선택지는 다시 누를 수 없다(마지막 메시지가 아님)
  await expect(page.getByTestId('clarify-options').first().getByRole('button').first()).toBeDisabled();
  expect(firstLabel.length).toBeGreaterThan(0);
});

// 10/23 실사례 — "목록 말해줘"는 "전부"가 아니다: 36개를 한 번에 늘어놓던 것을 되묻게 고침. 고른 범위 답엔 "목록 답 · n건에서" 표시
// (근거 카드 1건과 답의 목록 줄 수가 어긋나 보이던 것). 선택 후 답은 LLM 1회.
test('"목록 말해줘"도 많으면 되묻는다 → 고른 범위의 목록 답엔 "목록 답 · n건" 표시', async ({ page }) => {
  test.setTimeout(360_000);
  await loginAdmin(page, { openAdmin: false });
  const authState = await page.evaluate(() => localStorage.getItem('ops_auth'));
  const auth = { Authorization: `Bearer ${authState ? JSON.parse(authState).accessToken : ''}` };
  await page.request.delete(`/api/admin/cache?namespace=${encodeURIComponent(NS)}`, { headers: auth });
  await selectDomain(page, NS);

  await page.locator('textarea:visible').first().fill(Q_LIST);
  await page.getByTitle('전송 (Ctrl+Enter)').click();
  const opts = page.getByTestId('clarify-options').last();
  await expect(opts).toBeVisible({ timeout: 60_000 });           // 예전엔 되묻지 않고 36개를 늘어놓았다
  const first = opts.getByRole('button').first();
  const n = Number(((await first.innerText()).match(/\((\d+)개\)/) ?? [])[1]);
  await first.click();
  const note = page.getByTestId('list-answer-note').last();
  await expect(note).toBeVisible({ timeout: 300_000 });
  await expect(note).toContainText(`${n}건`);                    // 고른 범위의 목록 개수
});
