import { test, expect } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { createSystem, dropSystem, psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 같은 글 사본까지 정정(v2.228, work-os 검수 11/10 · 시안 A 승인) — 검색은 본문이 같은 사본을 한 자리로 합쳐 근거가 1건뿐이라,
// 그것만 정정하면 사본이 옛 글로 남아 다음 검색에 따로 실렸다. 카드에 사본 목록(기본 체크) → 승인 = 체크한 사본도 새 버전.
// 화면 문구가 아니라 DB 값으로 전/후를 본다. 임시 시스템 컬렉션(실제 행), LLM 호출 없음.

const NS = `E2E사본정정_${Date.now()} 시스템`;
const OLD = '결제 취소는 30분 안에 할 수 있다.';
const NEW = '결제 취소는 1시간 안에 할 수 있다.';
const nsId = () => psql(`SELECT id FROM ops_namespace WHERE name = '${NS}'`);
const add = (content: string, page: string) => Number(psql(
  `INSERT INTO rag_knowledge (namespace_id, content, status, confluence_page_id, heading_path, source_type) `
  + `VALUES (${nsId()}, '${content}', 'active', '${page}', '{${page} 페이지}', 'confluence') RETURNING id`).split('\n')[0]);

test.afterAll(() => {
  psql(`DELETE FROM ops_improvement_item WHERE namespace_id IN (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  psql(`UPDATE rag_knowledge SET supersedes_id = NULL WHERE namespace_id IN (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  psql(`DELETE FROM rag_knowledge WHERE namespace_id IN (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  dropSystem(NS);
});

test.describe.configure({ mode: "serial" });

test('같은 글 사본 — 카드에 목록(기본 체크) · 하나 풀고 승인 → 체크한 사본만 새 글, 푼 사본은 옛 글', async ({ page }) => {
  test.setTimeout(120_000);
  createSystem(NS);
  const target = add(OLD, 'A');
  const keep = add(OLD, 'B');
  const fix = add(OLD + '  ', 'C');   // 공백만 다른 같은 글
  const item = Number(psql(`INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_input, original, proposed) `
    + `VALUES ('correction', 'chat', ${nsId()}, 'knowledge', ${target}, '시간이 바뀜', '{"content": "${OLD}"}'::jsonb, '{"content": "${NEW}"}'::jsonb) `
    + `RETURNING id`).split('\n')[0]);

  await loginAdmin(page);
  await selectDomain(page, NS, NS.replace(/ 시스템$/, ''));
  await goAdmin(page, '정정 요청');
  const card = page.locator(`#correction-${item}`);
  const box = page.getByTestId(`correction-copies-${item}`);
  await expect(box).toContainText('같은 글이 다른 곳에도 2곳');
  await expect(page.getByTestId(`correction-copy-${keep}`)).toBeChecked();
  await expect(page.getByTestId(`correction-copy-${fix}`)).toBeChecked();
  await page.getByTestId(`correction-copy-${keep}`).uncheck();
  await expect(box).toContainText('체크를 푼 곳은 옛 글로 남아');
  await page.screenshot({ path: 'test-results/correction-copies.png' });

  const approved = page.waitForResponse((r) => r.url().includes(`/api/corrections/${item}/approve`));
  await card.getByRole('button', { name: '승인 · 사본 1곳 포함' }).click();
  expect((await approved).ok()).toBeTruthy();

  // 전/후 — 대상·고른 사본은 새 버전(옛 행 deprecated), 푼 사본은 옛 글 그대로 사용 중
  const status = (id: number) => psql(`SELECT status FROM rag_knowledge WHERE id = ${id}`);
  const successor = (id: number) => psql(`SELECT content FROM rag_knowledge WHERE supersedes_id = ${id} AND status = 'active'`);
  await expect.poll(() => status(target)).toBe('deprecated');
  expect([successor(target), status(fix), successor(fix)]).toEqual([NEW, 'deprecated', NEW]);
  expect([status(keep), successor(keep)]).toEqual(['active', '']);
});

test('일괄 승인도 카드에서 체크를 푼 사본은 옛 글로 남긴다', async ({ page }) => {
  // 리뷰(11/10): 체크 해제가 카드 안에만 있어 [선택한 N건 승인]이 무시하고 고칠 수 있는 사본 전부를 정정했다
  test.setTimeout(120_000);
  const target = add(OLD, 'D');
  const keep = add(OLD, 'E');
  const item = Number(psql(`INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_input, original, proposed) `
    + `VALUES ('correction', 'chat', ${nsId()}, 'knowledge', ${target}, '시간이 바뀜', '{"content": "${OLD}"}'::jsonb, '{"content": "${NEW}"}'::jsonb) `
    + `RETURNING id`).split('\n')[0]);
  await loginAdmin(page);
  await selectDomain(page, NS, NS.replace(/ 시스템$/, ''));
  await goAdmin(page, '정정 요청');
  await page.getByRole('group', { name: '보기' }).getByRole('button', { name: '목록' }).click();
  await page.getByTestId(`correction-copy-${keep}`).uncheck();
  await page.getByRole('checkbox', { name: `정정 #${item} 선택` }).check();
  const approved = page.waitForResponse((r) => r.url().includes(`/api/corrections/${item}/approve`));
  await page.getByRole('button', { name: /선택한 1건 승인/ }).click();
  expect((await approved).request().postDataJSON().exclude_copy_ids).toEqual([keep]);
  const status = (id: number) => psql(`SELECT status FROM rag_knowledge WHERE id = ${id}`);
  await expect.poll(() => status(target)).toBe('deprecated');
  expect(status(keep)).toBe('active');
});
