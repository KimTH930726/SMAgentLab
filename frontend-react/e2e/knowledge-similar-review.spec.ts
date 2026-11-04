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
  await expect(page.getByLabel('합친 내용')).toHaveValue(/하루 3회까지만/);
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
  await expect(due).toContainText('기한 10일 지남');
  await page.getByTestId(`review-done-k:${baseId}`).click();
  await expect(due).toHaveCount(0);
  expect(psql(`SELECT (reviewed_at > NOW() - interval '1 minute') || '|' || owner FROM rag_knowledge WHERE id = ${baseId}`)).toBe('true|admin');
});
