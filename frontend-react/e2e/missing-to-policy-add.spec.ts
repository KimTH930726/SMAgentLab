import { test, expect } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { createPolicyPart, dropPolicyPart, psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 정책 컬렉션의 "빠진 내용" 신고(11/03 태훈 "정책은 운영 지식과 성향이 달라 정책 등록·정정으로") — [승인](운영 지식 등록) 대신
// [정책 추가 제안으로] → 추가 제안 창(AI 초안·질문이 채워짐) → 올리면 새 '정책 추가' 제안이 생기고 원래 신고는 처리됨(넘긴 번호).
// 정책 컬렉션엔 운영 지식이 하나도 안 생겨야 한다. LLM 호출 없음(초안은 시험 데이터로 넣음).

const NS = 'zz_e2e_missing_forward';
const nsId = () => psql(`SELECT id FROM ops_namespace WHERE name = '${NS}'`);

test.beforeAll(async () => {
  test.setTimeout(180_000);
  createPolicyPart(NS, [{ name: 'E2E 기존 정책', path: ['주문'], body: '주문은 결제 후 확정된다.' }]);
});

test.afterAll(() => {
  psql(`DELETE FROM ops_improvement_item WHERE namespace_id = ${nsId()}`);
  dropPolicyPart(NS);
});

test('정책 컬렉션 "빠진 내용" → [정책 추가 제안으로] → 추가 제안 1건 · 원래 신고 처리됨 · 운영 지식 0', async ({ page }) => {
  test.setTimeout(120_000);
  const id = Number(psql(`INSERT INTO ops_improvement_item (kind, source, namespace_id, asked_namespace_id, target_type, user_input, original, proposed)
    VALUES ('missing_knowledge', 'auto', ${nsId()}, ${nsId()}, 'missing', '픽업 취소 기한이 없어요', '{}'::jsonb,
            '{"content": "픽업 주문은 준비 시작 전까지 취소할 수 있다."}'::jsonb) RETURNING id`).split(/\s+/)[0]);
  const knowledgeBefore = psql(`SELECT count(*) FROM rag_knowledge WHERE namespace_id = ${nsId()}`);

  await loginAdmin(page);
  await selectDomain(page, NS);
  await goAdmin(page, '정정 요청');
  const card = page.locator(`#correction-${id}`);
  await expect(card.getByRole('button', { name: '승인', exact: true })).toHaveCount(0);   // 운영 지식으로 승인하는 길은 없다
  await card.getByRole('button', { name: '정책 추가 제안으로' }).click();

  await expect(page.getByTestId('policy-add-note')).toContainText(`정정 #${id}`);
  await expect(page.locator('#padd-body')).toHaveValue('픽업 주문은 준비 시작 전까지 취소할 수 있다.');   // AI 초안이 원문 칸에
  await page.locator('#padd-name').fill('픽업 취소 기한');
  await page.locator('#padd-lv0').fill('주문');
  const sent = page.waitForResponse((r) => r.url().includes(`/api/corrections/${id}/to-policy-add`));
  await page.getByRole('button', { name: '정정 요청에 올리기' }).click();
  expect((await sent).status()).toBe(201);

  const forwarded = Number(psql(`SELECT ai_verdict->>'forwarded_to' FROM ops_improvement_item WHERE id = ${id}`));
  expect(psql(`SELECT status FROM ops_improvement_item WHERE id = ${id}`)).toBe('approved');
  expect(psql(`SELECT status || '|' || target_type || '|' || (proposed->>'policy_name') FROM ops_improvement_item WHERE id = ${forwarded}`))
    .toBe('pending|policy_add|픽업 취소 기한');
  expect(psql(`SELECT count(*) FROM rag_knowledge WHERE namespace_id = ${nsId()}`)).toBe(knowledgeBefore);   // 정책 컬렉션에 운영 지식 안 생김
});
