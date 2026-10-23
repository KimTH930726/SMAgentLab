import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { API, adminToken, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(2026-10-23, v2.170 자가 개선 루프): 평가 게이트 [개선]·AI 판단·답 기법은 기록을 읽기만 하고, 사람 검수(보류·기각·되돌리기)만
// 상태를 바꾼다. 화면 숫자가 DB(진단 원인 칸·후보 수·원장 집계)와 같은지, 검수 버튼이 실제로 DB 상태를 바꾸고 화면이 따라오는지,
// 기각은 이유 없이 못 하는지를 실 dev 스택으로 확인한다. 바꾼 후보 상태는 끝에 원래대로 되돌린다. 진단이 없으면 건너뛴다
// (진단은 scripts/eval_improve.py --save로만 생긴다).

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();

test('개선 탭 — 진단 숫자 = DB, 보류·되돌리기가 DB 상태를 바꿈, 기각은 이유 필수', async ({ page }) => {
  test.setTimeout(90_000);
  const runId = psql('SELECT id FROM eval_improvement_run ORDER BY created_at DESC, id DESC LIMIT 1');
  test.skip(!runId, '진단 없음');
  const diag = JSON.parse(psql(`SELECT diagnosis::text FROM eval_improvement_run WHERE id = ${runId}`));
  const nCands = Number(psql(`SELECT COUNT(*) FROM eval_candidate WHERE run_id = ${runId}`));
  const target = psql(`SELECT id || '|' || key || '|' || status FROM eval_candidate WHERE run_id = ${runId} AND status = 'proposed' ORDER BY id LIMIT 1`);

  await loginAdmin(page);
  await goAdmin(page, '품질', '평가 게이트');
  await page.getByRole('button', { name: '개선', exact: true }).click();
  await expect(page.getByText(`틀린 문항 ${diag.n_failed} / ${diag.n}`)).toBeVisible();
  const total = Object.values(diag.causes as Record<string, string[]>).reduce((s, v) => s + v.length, 0);
  expect(total).toBe(diag.n_failed);   // 칸 합 = 틀린 수(한 문항 = 한 칸)
  await expect(page.getByText(`개선 후보 ${nCands}개`)).toBeVisible();
  await expect(page.locator('[data-testid^="candidate-"]')).toHaveCount(nCands);

  if (target) {
    const [cid, key] = target.split('|');
    const card = page.getByTestId(`candidate-${key}`);
    try {
      // 기각은 이유 없이 못 한다(버튼 꺼짐) — 다음 진단이 이어받을 때 이유가 보여야 해서
      await card.getByRole('button', { name: '기각…' }).click();
      await expect(page.getByRole('button', { name: '기각', exact: true })).toBeDisabled();
      await page.keyboard.press('Escape');
      // 보류 → DB held, 화면 상태 글자 보류
      await card.getByRole('button', { name: '보류…' }).click();
      await page.getByRole('button', { name: '보류', exact: true }).click();
      await expect.poll(() => psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`)).toBe('held');
      await expect(card.getByText('보류', { exact: true })).toBeVisible();
      // 되돌리기 → proposed
      await card.getByRole('button', { name: '되돌리기' }).click();
      await expect.poll(() => psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`)).toBe('proposed');
      await expect(card.getByText('제안', { exact: true })).toBeVisible();
    } finally {
      if (psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`) !== 'proposed') {
        const t = await adminToken();
        const res = await fetch(`${API}/api/eval/candidates/${cid}/review`, {
          method: 'POST', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'proposed' }),
        });
        expect(res.ok, '후보 상태 되돌리기 실패 — 수동으로 되돌릴 것').toBe(true);
      }
    }
  }
});

test('AI 판단·답 기법 — 원장 집계·대장 항목 수가 서버와 같음', async ({ page }) => {
  const t = await adminToken();
  const H = { Authorization: `Bearer ${t}` };
  const techniques = await (await fetch(`${API}/api/eval/techniques`, { headers: H })).json();
  const locTotal = psql("SELECT COUNT(*) FROM ops_ai_decision WHERE kind = 'knowledge_location' AND created_at > NOW() - interval '90 days'");

  await loginAdmin(page);
  await goAdmin(page, '품질', 'AI 판단');
  if (locTotal !== '0') await expect(page.getByTestId('decision-knowledge_location')).toContainText(`판단${locTotal}`);
  await expect(page.getByTestId('decision-candidates')).toBeVisible();

  await goAdmin(page, '품질', '답 기법');
  await expect(page.locator('[data-testid^="technique-"]')).toHaveCount(techniques.length);
  await expect(page.getByTestId('technique-abstain_prefilter')).toContainText('근거 없음 즉시 거절');
});
