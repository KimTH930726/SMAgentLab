import { test, expect } from '@playwright/test';
import { chooseLocation, goAdmin, selectDomain } from './admin-nav';
import { API, adminToken, apiFetch, loginAdmin } from './auth';
import { createSystem, dropSystem, psql } from './v0-fixture';

// 11/04 — ① 직접 입력 등록 순간 "비슷한 지식이 이미 있어요"(등록 안 함 · 합치기 · 새로 등록) ② 운영 지식 › 검토 필요([검토 완료]).
// 화면 문구가 아니라 DB 값(조각 수·내용·상태·AI 감사 원장)으로 전/후를 본다. LLM은 넣을 곳 판정만(로컬).
const NS = `E2E비슷_${Date.now()} 시스템`;
const BASE = '## 정산 배치 재기동\n정산 배치가 멈추면 배치 상태를 확인한 뒤 재기동한다. 재기동 후 정산 파일 건수를 원장과 대조한다.';
const NEAR = `${BASE}\n재기동은 하루 3회까지만 한다.`;

test.afterAll(() => {
  psql(`DELETE FROM ops_ai_decision WHERE kind = 'duplicate_check' AND target_ids && ARRAY(SELECT id::bigint FROM rag_knowledge WHERE namespace_id IN (SELECT id FROM ops_namespace WHERE name = '${NS}'))`);
  psql(`DELETE FROM rag_knowledge WHERE namespace_id IN (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  dropSystem(NS);
});

const rows = () => psql(`SELECT count(*) FROM rag_knowledge WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}') AND status IN ('active', 'pending_review')`);
// 이 시험 컬렉션의 지식을 가리키는 줄만(다른 테스트와 같이 돌아도 섞이지 않게 — 리뷰 L7)
const lastDup = () => psql(`SELECT final_value->>'action' FROM ops_ai_decision WHERE kind = 'duplicate_check' AND signals->>'stage' = 'register'
  AND target_ids && ARRAY(SELECT id::bigint FROM rag_knowledge WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}')) ORDER BY id DESC LIMIT 1`);

test('등록 순간 비슷한 지식 — 등록 안 함 · 합치기 · 새로 등록 + 검토 필요 [검토 완료]', async ({ page }) => {
  test.setTimeout(240_000);
  createSystem(NS);
  const token = await adminToken();
  const r = await apiFetch(`${API}/api/knowledge`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ namespace: NS, content: BASE }),
  });
  expect(r.status, await r.clone().text()).toBe(201);
  const baseId = (await r.json()).id as number;
  expect(rows()).toBe('1');

  await loginAdmin(page, { openAdmin: false });
  await page.getByRole('link', { name: 'Admin' }).click();
  await goAdmin(page, '운영 지식');
  await selectDomain(page, NS, NS.replace(/ 시스템$/, ''));

  const openStep = async () => {
    await page.getByRole('button', { name: '지식 등록', exact: true }).click();
    if (!(await page.locator('textarea:visible').first().isVisible().catch(() => false))) await page.getByText('직접 입력').click();
    await page.locator('textarea:visible').first().fill(NEAR);
    await page.getByRole('button', { name: '넣을 곳 확인' }).click();
    await chooseLocation(page, NS);
    await page.getByRole('button', { name: '등록', exact: true }).click();
    const step = page.getByTestId('similar-step');
    await expect(step).toBeVisible();
    await expect(step.getByTestId('similar-existing')).toContainText(`#${baseId}`);
    return step;
  };

  // ① 등록 안 함 — 조각 그대로, 원장 reject
  let step = await openStep();
  await step.getByTestId('similar-skip').click();
  await expect(page.getByText(`같은 내용이 #${baseId}에 있어요`)).toBeVisible();
  expect(rows()).toBe('1');
  await expect.poll(lastDup).toBe('reject');

  // ② 합치기 — 새 조각 없이 기존 조각 내용이 바뀜, 원장 merge
  step = await openStep();
  await step.getByTestId('similar-merge-open').click();
  await expect(page.getByLabel('병합한 내용')).toHaveValue(/하루 3회까지만/);
  await step.getByTestId('similar-merge-save').click();
  await expect(page.getByText(`#${baseId}에 합쳤어요`)).toBeVisible();
  expect(rows()).toBe('1');
  expect(psql(`SELECT content LIKE '%하루 3회까지만%' FROM rag_knowledge WHERE id = ${baseId}`)).toBe('t');
  await expect.poll(lastDup).toBe('merge');

  // ③ 새로 등록 — 합친 뒤라 거의 같은 글이 되므로 다른 줄을 하나 더(비슷하지만 다른 내용)
  psql(`UPDATE rag_knowledge SET content = '${BASE.replace(/'/g, "''")}' WHERE id = ${baseId}`);   // 비교 기준을 처음으로(임베딩은 그대로 — 비슷함은 유지)
  step = await openStep();
  const created = page.waitForResponse((x) => x.url().endsWith('/api/knowledge') && x.request().method() === 'POST');
  await step.getByTestId('similar-new').click();
  const body = await (await created).json();
  expect(body.pending_review).toBe(false);   // 사람이 다르다고 정함 — 승인 대기로 안 감(담당 = 관리자)
  expect(rows()).toBe('2');
  await expect.poll(lastDup).toBe('approve');   // AI 감사 '중복 의심' 번복

  // ④ 검토 필요 — 기본 주기 90일, 등록일을 100일 전으로 → 목록 → [검토 완료] → 빠짐 · 검토일·검토한 사람
  // 합치기(②)가 수정일을 오늘로 남겼다 — 마지막 검토 = 검토일 › 수정일 › 등록일이라 둘 다 비우고 등록일만 100일 전으로
  psql(`UPDATE rag_knowledge SET created_at = NOW() - interval '100 days', reviewed_at = NULL, content_edited_at = NULL WHERE id = ${baseId}`);
  await page.getByTestId('tab-review-due').click();
  const due = page.getByTestId(`review-due-k:${baseId}`);
  // 사건 기반(11/05) — 기한만 지나고 채팅에 안 쓰인 지식은 목록에 없고 '기한만 지남' 수에만
  await expect(page.getByTestId('review-counts')).toBeVisible();
  await expect(due).toHaveCount(0);
  await expect(page.getByTestId('review-quiet')).toBeVisible();
  // 채팅 답 3번(USED_MIN)에 근거로 쓰이면 '많이 쓰임'으로 올라온다
  const conv = psql(`INSERT INTO ops_conversation (title) VALUES ('E2E 검토 필요') RETURNING id`).split(/\s+/)[0];
  test.info().annotations.push({ type: 'cleanup', description: `conv ${conv}` });
  try {
  for (let i = 0; i < 3; i++) psql(`INSERT INTO ops_message (conversation_id, role, content, results) VALUES (${conv}, 'assistant', 'E2E 답', '[{"id": ${baseId}}]'::jsonb)`);
  await page.reload();
  await page.getByTestId('tab-review-due').click();
  await expect(due).toContainText('10일 지남');
  await expect(page.getByTestId(`review-reason-k:${baseId}`)).toContainText('많이 쓰임');
  await expect(due).toContainText('채팅 3회');
  await page.getByTestId(`review-done-k:${baseId}`).click();
  await expect(due).toHaveCount(0);
  expect(psql(`SELECT (reviewed_at > NOW() - interval '1 minute') || '|' || owner FROM rag_knowledge WHERE id = ${baseId}`)).toBe('true|admin');

  // ⑤ 카드 눌러 상세 → 조각을 그 자리에서 고침(11/05 — "지식 조회에서 찾아 고치라는 건 불친절") → 내용 바뀜 · 고친 것도 검토라 목록에서 빠짐
  psql(`UPDATE rag_knowledge SET created_at = NOW() - interval '100 days', reviewed_at = NULL, content_edited_at = NULL WHERE id = ${baseId}`);
  await page.reload();   // 목록을 새로(psql로 바꾼 날짜)
  await page.getByTestId('tab-review-due').click();
  await due.click();
  const detail = page.getByTestId('review-detail');
  await detail.getByTestId(`review-edit-${baseId}`).click();
  await detail.getByLabel(`#${baseId} 내용`).fill('택배 송장 재발행은 하루 3회까지만 할 수 있다. (검토 화면에서 고침)');
  await detail.getByTestId(`review-save-${baseId}`).click();
  await expect(detail.getByTestId(`review-chunk-${baseId}`)).toContainText('검토 화면에서 고침');
  expect(psql(`SELECT (content LIKE '%검토 화면에서 고침%') || '|' || (content_edited_at > NOW() - interval '1 minute') FROM rag_knowledge WHERE id = ${baseId}`)).toBe('true|true');
  await page.keyboard.press('Escape');
  await expect(due).toHaveCount(0);
  } finally {
    psql(`DELETE FROM ops_conversation WHERE id = ${conv}`);   // 중간에 실패해도 — 남으면 다른 사용량 집계를 오염(리뷰 L10), 메시지는 CASCADE
  }
});
