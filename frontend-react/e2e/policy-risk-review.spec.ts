import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

// 이 테스트가 존재하는 이유(2026-10-01): 정책 승인 대기 큐를 "위험한 것만 사람이 본다"로 바꾸면서, 위험도 분류 →
// 낮음 자동 통과(표본은 사람 큐) → 자동 통과 되돌리기 → 표본 반려 시 규칙 정지가 화면에서 실제로 이어지는지 확인한다.
// 데이터: 실 정책을 건드리지 않도록 임시 파트에 SQL로 등급별 항목을 직접 심는다(LLM 분해 없이 결정론적).
// 표본 반려는 전역 규칙을 멈추므로, 시작 전 규칙 설정을 저장해 두고 끝나면 그대로 복원한다.

const ADMIN_USER = 'admin';
const ADMIN_PASS = '1111';
const NS = 'zz_e2e_policy_risk';
const CONFIG_KEY = 'policy_auto_review';

function psql(sql: string): string {
  return execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
    { encoding: 'utf-8' }).trim();
}

// 서버 auto_review.is_sample과 같은 결정론적 표본(md5(id) % 1000 < 100)
const isSample = (id: number) => BigInt('0x' + createHash('md5').update(String(id)).digest('hex')) % 1000n < 100n;

let savedConfig: string | null = null;
let logIdBefore = 0;
let nonSampleLow = 0;

function seedItem(name: string, parseStatus: string, chunks: number, unresolved = 0): number {
  const ns = `(SELECT id FROM ops_namespace WHERE name='${NS}')`;
  const segs = unresolved ? `'${JSON.stringify(Array.from({ length: unresolved }, () => ({ text: 'x', reason: 'e2e' })))}'::jsonb` : 'NULL';
  const id = Number(psql(
    `INSERT INTO policy_item (namespace_id, system_key, category_path, policy_name, raw_body, content_hash, status, version, parse_status, unresolved_segments) ` +
    `VALUES (${ns}, '', ARRAY['E2E'], '${name}', '${name} 본문', md5(random()::text), 'pending_review', 1, '${parseStatus}', ${segs}) RETURNING id`,
  ).split('\n')[0]);
  psql(`UPDATE policy_item SET logical_id = id WHERE id = ${id}`);
  for (let i = 0; i < chunks; i++) {
    psql(`INSERT INTO policy_chunk (policy_item_id, chunk_text, chunk_idx) VALUES (${id}, '${name} 서술 ${i}', ${i})`);
  }
  return id;
}

test.describe.configure({ mode: 'serial' });
test.setTimeout(120_000);

test.beforeAll(() => {
  savedConfig = psql(`SELECT value FROM ops_system_config WHERE key='${CONFIG_KEY}'`) || null;
  logIdBefore = Number(psql('SELECT COALESCE(MAX(id), 0) FROM policy_review_log'));
  psql(`DELETE FROM ops_namespace WHERE name='${NS}'`);
  psql(`INSERT INTO ops_namespace (name, description) VALUES ('${NS}', 'e2e policy risk')`);
  psql(`DELETE FROM ops_system_config WHERE key='${CONFIG_KEY}'`);  // 기본값(규칙 켜짐)에서 시작
  seedItem('E2E 미해결 정책', 'unresolved', 1, 2);
  seedItem('E2E 분할 정책', 'parsed', 3);
  // 낮음 — 표본 1개 이상 + 자동 통과 2개 이상이 될 때까지(표본은 id로 결정되므로 미리 알 수 있음)
  let samples = 0;
  for (let n = 0; (samples < 1 || nonSampleLow < 2) && n < 200; n++) {
    const id = seedItem(`E2E 단순 정책 ${n}`, 'parsed', 1);
    if (isSample(id)) samples++; else nonSampleLow++;
  }
});

test.afterAll(() => {
  psql(`DELETE FROM policy_review_log WHERE id > ${logIdBefore}`);
  psql(`DELETE FROM ops_namespace WHERE name='${NS}'`);
  psql(`DELETE FROM ops_system_config WHERE key='${CONFIG_KEY}'`);
  if (savedConfig) {
    psql(`INSERT INTO ops_system_config (key, value, updated_at) VALUES ('${CONFIG_KEY}', '${savedConfig.replace(/'/g, "''")}', NOW())`);
  }
});

async function openBrowser(page: Page) {
  await page.goto('/login');
  await page.getByPlaceholder('사용자 아이디').fill(ADMIN_USER);
  await page.getByPlaceholder('비밀번호').fill(ADMIN_PASS);
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('heading', { name: '로그인' })).toBeHidden({ timeout: 10_000 });
  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  if (await agentHeading.isVisible().catch(() => false)) await page.getByText('선택하기').first().click();
  await page.getByRole('link', { name: 'Admin' }).click();
  await page.getByRole('button', { name: '정책' }).click();
  await page.getByRole('button', { name: '항목 브라우저' }).click();
  await page.locator('select').first().selectOption(NS);
}

test('위험도 분류 → 낮음 자동 통과(표본은 사람 큐) → 되돌리기 → 표본 반려 시 규칙 정지', async ({ page }) => {
  page.on('dialog', (d) => d.accept());
  await openBrowser(page);
  const panel = page.locator('div.rounded-xl').filter({ hasText: '사람이 볼 검토 큐' }).first();

  // 실행 전: 사람 큐 = 높음 1 + 중간 1, 낮음은 "자동 통과 대기"
  await expect(panel.getByText('높음 1')).toBeVisible();
  await expect(panel.getByText('중간 1')).toBeVisible();
  await expect(panel.getByText(/자동 통과 대기 \d+건/)).toBeVisible();

  await panel.getByRole('button', { name: /자동 통과 미리보기/ }).click();
  await expect(panel.getByText(new RegExp(`낮음 ${nonSampleLow}건 자동 통과`))).toBeVisible();
  await panel.getByRole('button', { name: '실행', exact: true }).click();
  await expect(panel.getByText(new RegExp(`방금 ${nonSampleLow}건 자동 통과`))).toBeVisible();

  // 위험도 순 검토 큐 — 맨 위는 높음, 표본은 "표본 확인"
  await panel.getByRole('button', { name: /검토 큐 보기/ }).click();
  const rows = page.locator('.space-y-2 > div.bg-slate-800');
  await expect(rows.first().getByText('위험 높음')).toBeVisible();
  await expect(page.getByText('표본 확인').first()).toBeVisible();

  // 자동 통과는 사람 승인과 구분되어 보이고, 되돌릴 수 있다
  await page.locator('select').filter({ has: page.locator('option[value="rejected"]') }).selectOption('active');
  const autoRow = rows.filter({ hasText: '자동 통과' }).first();
  await expect(autoRow).toBeVisible();
  const reverted = page.waitForResponse((r) => /\/revert-auto$/.test(r.url()));
  await autoRow.locator('button[title*="자동 통과 되돌리기"]').click();
  expect((await reverted).ok()).toBeTruthy();
  const autoAfterRevert = Number(psql(
    `SELECT COUNT(*) FROM policy_item WHERE namespace_id=(SELECT id FROM ops_namespace WHERE name='${NS}') AND review_source='auto_rule'`));
  expect(autoAfterRevert).toBe(nonSampleLow - 1);

  // 표본을 반려하면 같은 규칙의 자동 통과가 멈춘다
  await page.locator('select').filter({ has: page.locator('option[value="rejected"]') }).selectOption('pending_review');
  const sampleRow = rows.filter({ hasText: '표본 확인' }).first();
  const rejected = page.waitForResponse((r) => /\/reject$/.test(r.url()));
  await sampleRow.locator('button[title*="반려"]').click();
  expect(await (await rejected).json()).toMatchObject({ status: 'rejected', rule_paused: true });
  await expect(panel.getByText('자동 통과 멈춤')).toBeVisible();
  // 이력: 자동 통과·표본·되돌리기·반려·규칙 정지가 모두 남는다
  const actions = psql(`SELECT string_agg(DISTINCT action, ',' ORDER BY action) FROM policy_review_log WHERE id > ${logIdBefore}`);
  expect(actions.split(',')).toEqual(expect.arrayContaining(['auto_approved', 'sampled', 'auto_reverted', 'rejected', 'rule_paused']));
});

test('미분류 조각은 검토 큐에서 바로 편입 → 위험도 재계산, 미분류 탭은 집계만', async ({ page }) => {
  page.on('dialog', (d) => d.accept());
  await openBrowser(page);
  const panel = page.locator('div.rounded-xl').filter({ hasText: '사람이 볼 검토 큐' }).first();
  await panel.getByRole('button', { name: /검토 큐 보기/ }).click();
  const row = page.locator('.space-y-2 > div.bg-slate-800').filter({ hasText: 'E2E 미해결 정책' }).first();
  await expect(row.getByText('위험 높음')).toBeVisible();
  await row.locator('span.font-medium').first().click();  // 펼치기
  await expect(row.getByText(/미분류 조각 2개/)).toBeVisible();

  // 두 조각을 서술로 편입(편입마다 목록을 다시 받아 인덱스가 당겨짐)
  for (let left = 2; left > 0; left--) {
    const done = page.waitForResponse((r) => /\/unresolved\/\d+\/promote$/.test(r.url()));
    await row.getByRole('button', { name: /서술로 편입/ }).first().click();
    expect((await done).ok()).toBeTruthy();
    if (left > 1) await expect(row.getByText(/미분류 조각 1개/)).toBeVisible();
  }
  const ns = `(SELECT id FROM ops_namespace WHERE name='${NS}')`;
  expect(psql(`SELECT parse_status FROM policy_item WHERE namespace_id=${ns} AND policy_name='E2E 미해결 정책'`)).toBe('parsed');
  // 구조화 실패가 풀려 위험 높음 → 중간(서술 1+2=3개로 나뉨)
  await expect(row.getByText('위험 중간', { exact: true })).toBeVisible();
  await expect(row.getByText(/미분류 조각/)).toHaveCount(0);

  // 미분류 탭은 팀별 집계 — 편입 버튼 없음
  await page.getByRole('button', { name: '미분류 집계' }).click();
  await page.locator('select').first().selectOption(NS);
  await expect(page.getByText(/항목 브라우저 → 검토 큐/)).toBeVisible();
  await expect(page.getByRole('button', { name: /서술로 편입|파라미터로 편입/ })).toHaveCount(0);
});
