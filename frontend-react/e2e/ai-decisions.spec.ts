import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { adminToken, API, apiFetch, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(10/27 품질 › AI 판단 다시 올림 — v2.172에서 화면을 내리며 되묻기 관리·"AI가 틀렸나요?"가 볼 곳 없이 사라졌던 사고):
// 카드 5장이 그대로 있는지, 카드를 열면 그 판단의 "지금 기준"(답 기법 대장)이 보이는지, "AI가 틀렸나요?" 답이 실제 DB에 남는지,
// 남이 바꾼 판단엔 답할 수 없는지(403)를 실 dev 스택으로 본다. 테스트가 넣은 원장 줄은 끝에 지운다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();

const made: string[] = [];
const row = (changedBy: string) => {
  const id = psql(`INSERT INTO ops_ai_decision (kind, ai_value, final_value, confident, signals, human_changed, changed_by_user_id, changed_at)
    SELECT 'knowledge_location', jsonb_build_object('namespace_id', (SELECT min(id) FROM ops_namespace WHERE kind = 'domain')),
           jsonb_build_object('namespace_id', (SELECT max(id) FROM ops_namespace WHERE kind = 'domain')), true,
           '{"reason": "E2E AI 판단 확인용"}'::jsonb, true, u.id, NOW()
    FROM ops_user u WHERE u.username = '${changedBy}' RETURNING id`).split(/\s+/)[0];
  made.push(id);
  return id;
};

test.afterAll(() => {
  if (made.length) psql(`DELETE FROM ops_ai_decision WHERE id IN (${made.join(',')}) AND signals->>'reason' = 'E2E AI 판단 확인용'`);
});

test('AI 감사 로그 — 카드 5장, [기준·분포]에 지금 기준, 카드로 로그 거르기, 로그 줄의 "AI가 틀렸나요?"가 DB에 남고 남의 판단엔 403', async ({ page }) => {
  test.setTimeout(90_000);
  const mine = row('admin');
  const others = psql("SELECT username FROM ops_user WHERE username <> 'admin' ORDER BY id LIMIT 1");
  const theirs = others ? row(others) : '';

  await loginAdmin(page);
  await goAdmin(page, '품질', 'AI 감사 로그');
  for (const k of ['location', 'clarify', 'source', 'suggest', 'candidates']) await expect(page.getByTestId(`decision-card-${k}`)).toBeVisible();

  // 되묻기 카드 — 기준 상자에 대장의 되묻기 항목 이름
  const t = await adminToken();
  const techs: { key: string; name: string }[] = await (await apiFetch(`${API}/api/eval/techniques`, { headers: { Authorization: `Bearer ${t}` } })).json();
  await page.getByTestId('decision-detail-clarify').click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByTestId('decision-criteria')).toContainText(techs.find((x) => x.key === 'clarify')!.name);
  await page.keyboard.press('Escape');

  // 위치 카드 = 감사 로그를 등록 위치로 거름 → 다른 종류 줄은 없음. "사람이 바꾼 것만"이면 테스트가 넣은 두 줄이 보인다
  const log = page.getByTestId('audit-log');
  await page.getByTestId('decision-card-location').click();
  await expect(log).toContainText('등록 위치만');
  await log.getByLabel('사람이 바꾼 것만').check();
  const line = log.getByTestId(`log-${mine}`);
  await expect(line).toContainText('사람이 바꿈');
  await expect(log.locator('[data-testid^="log-"]').filter({ hasText: '되묻기' })).toHaveCount(0);
  // 내가 바꾼 줄에 [AI가 틀림] → DB ai_verdict = wrong
  await line.getByRole('button', { name: 'AI가 틀림' }).click();
  await expect.poll(() => psql(`SELECT ai_verdict FROM ops_ai_decision WHERE id = ${mine}`)).toBe('wrong');
  await expect(line).toContainText('AI가 틀림');
  if (theirs) await expect(log.getByTestId(`log-${theirs}`)).toContainText('답 없음');   // 남의 줄엔 버튼 없음

  // 남이 바꾼 판단엔 API도 막는다
  if (theirs) {
    const res = await apiFetch(`${API}/api/eval/decisions/${theirs}/verdict`, {
      method: 'POST', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ verdict: 'wrong', reason: '' }),
    });
    expect(res.status).toBe(403);
    expect(psql(`SELECT ai_verdict IS NULL FROM ops_ai_decision WHERE id = ${theirs}`)).toBe('t');
  }
});

test('RAG 파이프라인 — 단계마다 항목 수 = 대장, 단계를 누르면 그 단계 항목, 항목을 누르면 설명 창', async ({ page }) => {
  test.setTimeout(60_000);
  const t = await adminToken();
  const techs: { key: string; stage: string }[] = await (await apiFetch(`${API}/api/eval/techniques`, { headers: { Authorization: `Bearer ${t}` } })).json();
  await loginAdmin(page);
  await goAdmin(page, '품질', 'RAG 파이프라인');
  const rows = page.locator('[data-testid^="technique-row-"]');
  let total = 0;
  for (const stage of ['적재', '검색', '근거 판정', '답 모양', '생성']) {
    await page.getByTestId(`stage-${stage}`).click();
    await expect(page.getByTestId(`stage-${stage}`)).toHaveAttribute('aria-pressed', 'true');
    const n = techs.filter((x) => x.stage === stage).length;
    await expect(rows).toHaveCount(n);
    total += n;
  }
  expect(total).toBe(techs.length);   // 모든 항목이 어느 단계에든 보인다(단계 밖으로 빠진 항목 0)
  await page.getByTestId('stage-검색').click();
  await rows.first().click();
  await expect(page.getByRole('dialog')).toContainText('왜 있나');
});
