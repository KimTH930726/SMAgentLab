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

test('개선 탭 — 진단 숫자 = DB, 나중에·결정 취소가 DB 상태를 바꿈, 안 하기는 이유 필수, 단계 막대', async ({ page }) => {
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
      // 단계 막대 — 제안 단계, 버튼은 할 일 이름(실험해 보기·나중에·안 하기)(사용자 10/26)
      await expect(card.getByLabel('진행 단계')).toContainText('제안');
      await expect(card.getByRole('button', { name: '실험해 보기' })).toBeVisible();
      // 안 하기는 이유 없이 못 한다(버튼 꺼짐) — 다음 진단이 이어받을 때 이유가 보여야 해서
      await card.getByRole('button', { name: '안 하기…' }).click();
      await expect(page.getByRole('button', { name: '안 하기', exact: true })).toBeDisabled();
      await page.keyboard.press('Escape');
      // 나중에 → DB held, 단계 막대 옆 "나중에 보기로 함"
      await card.getByRole('button', { name: '나중에…' }).click();
      await page.getByRole('button', { name: '나중에', exact: true }).click();
      await expect.poll(() => psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`)).toBe('held');
      await expect(card.getByText('나중에 보기로 함', { exact: false })).toBeVisible();
      // 결정 취소 → proposed(검수 흔적도 비움)
      await card.getByRole('button', { name: '결정 취소' }).click();
      await expect.poll(() => psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`)).toBe('proposed');
      await expect(card.getByRole('button', { name: '실험해 보기' })).toBeVisible();
      expect(psql(`SELECT reviewed_at IS NULL FROM eval_candidate WHERE id = ${cid}`)).toBe('t');
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

test('AI 묶음 [맞아요] → 진단 기록에 확인 표시 → 취소', async ({ page }) => {
  const runId = psql('SELECT id FROM eval_improvement_run ORDER BY created_at DESC, id DESC LIMIT 1');
  test.skip(!runId, '진단 없음');
  // 사람이 아직 결정하지 않은 묶음만 — 이미 [맞아요]·[다시 묶기] 한 묶음을 건드리면 그 결정을 지운다(재리뷰 M)
  const row = psql(`SELECT c.key || '|' || (h->>'name') || '|' || c.cause FROM eval_candidate c, jsonb_array_elements(c.hypotheses) h
                    WHERE c.run_id = ${runId} AND coalesce(h->>'status', 'open') = 'open' ORDER BY c.id LIMIT 1`);
  test.skip(!row, '결정 전 묶음 있는 후보 없음');
  const [key, name, cause] = row.split('|');
  const status = () => psql(`SELECT h->>'status' FROM eval_improvement_run r, jsonb_array_elements(r.hypotheses) h
                             WHERE r.id = ${runId} AND h->>'name' = ${"'" + name.replace(/'/g, "''") + "'"} AND h->>'cause' = '${cause}' LIMIT 1`);
  await loginAdmin(page);
  await goAdmin(page, '품질', '평가 게이트');
  await page.getByRole('button', { name: '개선', exact: true }).click();
  const line = page.getByTestId(`candidate-${key}`).locator('div.pl-3').filter({ hasText: name }).first();
  try {
    await line.getByRole('button', { name: '맞아요' }).click();
    await expect.poll(status).toBe('confirmed');
    await expect(line.getByText('맞음')).toBeVisible();
    await line.getByRole('button', { name: '취소' }).click();
    await expect.poll(status).toBe('open');
  } finally {
    if (!['open', ''].includes(status())) {
      const t = await adminToken();
      await fetch(`${API}/api/eval/improvements/${runId}/hypotheses`, {
        method: 'POST', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ cause, name, status: 'open' }),
      });
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
  if (locTotal !== '0') await expect(page.getByTestId('decision-knowledge_location')).toContainText(`판단 ${locTotal}`);
  // 사람이 바꾼 건마다 한 줄(AI → 사람 · AI 이유 · AI가 틀렸나요?) — 줄 수 = 원장(최근 90일, 최대 50)
  const changed = Number(psql("SELECT LEAST(COUNT(*), 50) FROM ops_ai_decision WHERE human_changed AND created_at > NOW() - interval '90 days'"));
  await expect(page.locator('[data-testid^="changed-"]')).toHaveCount(changed);
  if (changed) await expect(page.locator('[data-testid^="changed-"]').first()).toContainText('AI 이유');
  await expect(page.getByTestId('decision-candidates')).toBeVisible();

  // 답 기법 — 흐름 그림 + 기법 카드(왜 있나·효과·바꾸려면), 단계를 누르면 그 단계만
  await goAdmin(page, '품질', '답 기법');
  const cards = page.locator('[data-testid^="technique-"]');
  await expect(cards).toHaveCount(techniques.length);
  await expect(page.getByTestId('technique-abstain_prefilter')).toContainText('왜 있나');
  await page.getByRole('button', { name: /^근거 판정/ }).click();
  await expect(cards).toHaveCount(techniques.filter((t: { stage: string }) => t.stage === '근거 판정').length);
});

test('실사용 문항 — 목록 = DB, [시험지에 넣기]가 DB 상태를 바꿈(이 테스트가 만든 줄만 쓰고 지움)', async ({ page }) => {
  // 승인된 채팅 신고가 없어도 화면·결정 흐름을 확인하게 — 아직 후보가 없는 정정 한 건에 테스트용 후보 줄을 만들고 끝에 지운다
  const itemId = psql(`SELECT min(id) FROM ops_improvement_item WHERE id NOT IN (SELECT improvement_item_id FROM eval_question_candidate)`);
  test.skip(!itemId, '정정 원장이 비어 있음');
  const cid = psql(`INSERT INTO eval_question_candidate (improvement_item_id, question, gold)
                    VALUES (${itemId}, 'E2E 실사용 문항 확인용 질문', '{"kind":"policy","source_key":null}') RETURNING id`).split(/\s+/)[0];   // psql -tA는 id 다음 줄에 "INSERT 0 1"
  try {
    const proposed = Number(psql("SELECT COUNT(*) FROM eval_question_candidate WHERE status = 'proposed'"));
    await loginAdmin(page);
    await goAdmin(page, '품질', '평가 게이트');
    await page.getByRole('button', { name: '실사용 문항', exact: true }).click();
    await expect(page.locator('[data-testid^="real-q-"]')).toHaveCount(Math.min(proposed, 200));
    const card = page.getByTestId(`real-q-${cid}`);
    await expect(card).toContainText('지금은 잴 수 없음');   // 원천 키 없는 정책 정답 — 측정에 못 들어감을 숨기지 않는다
    await card.getByRole('button', { name: '시험지에 넣기' }).click();
    await expect.poll(() => psql(`SELECT status FROM eval_question_candidate WHERE id = ${cid}`)).toBe('added');
  } finally {
    psql(`DELETE FROM eval_question_candidate WHERE id = ${cid} AND question = 'E2E 실사용 문항 확인용 질문'`);
  }
});
