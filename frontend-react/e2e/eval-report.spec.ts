import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { API, adminToken, apiFetch, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(2026-10-23 v2.170 자가 개선 루프 → 10/26 v2.172 틀린 문항 리포트 → 10/29 ① 개선할 곳 · ② 진행 중인 개선): 리포트는 진단·후보·적재 검수 기록을 읽기만 하고,
// 사람 결정(나중에·안 하기·결정 취소·운영에 반영했어요)만 상태를 바꾼다. 화면 숫자·줄 수가 서버 리포트와 같은지, 거르기가 서버 꼬리표대로 줄을 줄이는지,
// 문항을 누르면 저장 구조까지 보이는지, 결정 버튼이 실제로 DB 상태를 바꾸는지, [운영에 반영했어요]가 그 측정을 지금 점수(기준선)로 옮기는지를
// 실 dev 스택으로 확인한다. 바꾼 상태·테스트가 만든 측정 줄은 끝에 원래대로 되돌린다. 진단이 없으면 건너뛴다(scripts/eval_improve.py --save로만 생김).

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
// 자동 실험이 안 되는 후보(바꿀 것이 글뿐)만 — 실험해 보기(승인)가 진짜 측정(답이면 몇 시간)을 시작하지 않게(10/30 실험 실행기)
const manualOnly = (id: number) => psql(`SELECT change IS NULL FROM eval_candidate WHERE id = ${id}`) === 't';

type Report = {
  gate_run_id: number; improvement_id: number; score: { locked: number | null; total: number | null };
  failed: { qid: string; storage: string[]; techniques: { id: number }[]; cause: string | null; cause_name: string | null; locked: boolean }[];
  techniques: { id: number; key: string; cause: string; title: string; status: string; recommend: string; n_failed: number }[];
  unmeasured: { n: number }[];
};

async function report(): Promise<Report | null> {
  const t = await adminToken();
  return (await apiFetch(`${API}/api/eval/report`, { headers: { Authorization: `Bearer ${t}` } })).json();
}

async function review(cid: string, status: string) {
  const t = await adminToken();
  const res = await apiFetch(`${API}/api/eval/candidates/${cid}/review`, {
    method: 'POST', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
  });
  expect(res.ok, '후보 상태 바꾸기 실패').toBe(true);
}

test('① 개선할 곳 — 원인 줄 수 = 서버, 펼치면 그 원인 후보·문항(8개씩), 저장 상태 거르기, 문항 상세의 저장 구조, 측정도 안 되는 문항', async ({ page }) => {
  test.setTimeout(90_000);
  const d = await report();
  test.skip(!d, '진단 없음');
  await loginAdmin(page);
  await goAdmin(page, '품질', '평가 게이트');
  await expect(page.getByTestId('gate-report')).toBeVisible();
  await expect(page.getByTestId('report-locked')).toHaveCount(0);   // 머리 숫자 줄은 없앰(맨 위 카드와 중복, 10/29)
  const diagnosed = d!.failed.filter((r) => r.cause);
  await expect(page.getByTestId('report-failed-n')).toContainText(`틀린 문항 ${diagnosed.length}`);
  // 원인 줄 = 진단 원인별 문항 수(합 = 진단한 틀린 문항) — 단계 줄 숫자와 같은 규칙
  const byCause = new Map<string, Report['failed']>();
  for (const r of diagnosed) byCause.set(r.cause!, [...(byCause.get(r.cause!) ?? []), r]);
  for (const [key, rs] of byCause) await expect(page.getByTestId(`report-cause-${key}`).locator('> button')).toContainText(String(rs.length));
  const locked = d!.failed.filter((r) => r.locked).length;
  if (locked) await expect(page.getByTestId('report-footnote')).toContainText(`잠근 문항 ${locked} — 진단 안 함`);   // 원인이 아니라 각주

  const [ckey, crows] = [...byCause.entries()].sort((a, b) => b[1].length - a[1].length)[0] ?? [];
  if (ckey) {
    await page.getByTestId(`report-cause-${ckey}`).locator('> button').click();
    const block = page.getByTestId(`report-cause-modal-${ckey}`);   // 원인 줄을 누르면 모달(10/30)
    const cname = crows![0].cause_name ?? ckey;
    const rows = block.locator('[data-testid^="report-row-"]');
    const title = block.getByTestId('report-list-title');
    await expect(title).toHaveText(`${cname} 문항 ${crows!.length}`);
    await expect(rows).toHaveCount(Math.min(8, crows!.length));
    if (crows!.length > 8) {
      await expect(block.getByTestId('report-page')).toHaveText(`1 / ${Math.ceil(crows!.length / 8)}`);
      await block.getByTestId('report-pager').getByRole('button', { name: '다음 ›' }).click();
      await expect(rows).toHaveCount(Math.min(8, crows!.length - 8));
      await block.getByTestId('report-pager').getByRole('button', { name: '‹ 이전' }).click();
    }
    const tag = crows!.flatMap((r) => r.storage)[0];
    if (tag) {
      const n = crows!.filter((r) => r.storage.includes(tag)).length;
      await block.getByLabel('저장 상태로 거르기').selectOption(tag);
      await expect(title).toHaveText(`${cname} 문항 ${n} / ${crows!.length}`);
      await block.getByLabel('저장 상태로 거르기').selectOption('');
    }
    // 고칠 방법은 원인 안에만(고칠 방법별 보기는 없앰) — 열린 후보 줄 수 = 그 원인의 안 끝난 후보, [문항만 보기] = 그 후보 문항
    await expect(page.getByRole('button', { name: '고칠 방법별' })).toHaveCount(0);
    const techs = d!.techniques.filter((t) => t.cause === ckey);
    const openT = techs.filter((t) => t.status !== 'rejected' && t.status !== 'adopted');
    await expect(block.locator('[data-testid^="report-method-"]')).toHaveCount(openT.length);
    const t0 = openT.find((t) => t.n_failed > 0);
    if (t0) {
      const n = crows!.filter((r) => r.techniques.some((x) => x.id === t0.id)).length;
      await expect(block.getByTestId(`report-method-${t0.id}`)).toContainText(`${t0.n_failed}문항`);
      await block.getByTestId(`report-method-${t0.id}`).getByRole('button', { name: '문항만 보기' }).click();
      await expect(title).toHaveText(`${t0.title} 문항 ${n}`);
    }
    // 문항을 누르면 질문·답·이유 + 저장 구조(결과 파일이 서버에 있을 때)
    await rows.first().click();
    const dialog = page.getByRole('dialog').filter({ has: page.getByTestId('question-detail') });   // 원인 모달 위에 뜬다
    await expect(dialog.getByTestId('question-detail')).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByTestId('question-why')).toContainText('저장');   // 왜 틀렸나 한 칸(검색·원인·패턴·저장, 10/30 4칸)
    // AI 답이 실제로 잘렸으면 [더 보기]가 있다(글자 수 짐작 말고 — 재리뷰 10/30 M1)
    const ans = dialog.getByTestId('main-answer');
    if (await ans.count()) {
      const cut = await ans.evaluate((el) => el.scrollHeight > el.clientHeight + 1);
      await expect(dialog.getByRole('button', { name: '더 보기' })).toHaveCount(cut ? 1 : 0);
    }
    // 고칠 방법 줄 → 같은 후보 모달(문항 상세 위), Esc는 후보 모달만 닫고 문항 상세는 남는다
    const qc = dialog.locator('[data-testid^="question-cand-"]');
    if (await qc.count()) {
      await qc.first().click();
      await expect(page.getByRole('dialog').locator('[data-testid^="candidate-"]').first()
        .or(page.getByRole('dialog').getByText('예전 진단의 후보예요'))).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog').locator('[data-testid^="candidate-"]')).toHaveCount(0);
      await expect(page.getByRole('dialog').getByText('예전 진단의 후보예요')).toHaveCount(0);
      await expect(dialog.getByTestId('question-detail')).toBeVisible();
    }
    await page.keyboard.press('Escape');   // Esc는 맨 위 하나만 — 문항 상세 → 원인 모달 순서로 닫힌다
    await expect(dialog).toHaveCount(0);
    await expect(block).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
  }

  const unmeasured = d!.unmeasured.reduce((a, u) => a + u.n, 0);
  if (unmeasured) {
    await page.getByTestId('report-unmeasured-open').click();
    await expect(page.getByRole('dialog')).toContainText(`측정도 안 되는 문항 ${unmeasured}`);
    await page.keyboard.press('Escape');
  }
  // ② = 사람이 정할 후보(결정 전 중 AI 기각 추천 빼고 + 실험 중) — 맨 위 "결정할 후보" 숫자와 같다
  const open = d!.techniques.filter((t) => t.status === 'approved' || (t.status === 'proposed' && t.recommend !== 'reject'));
  await expect(page.getByTestId('gate-in-progress').locator('[data-testid^="inprogress-"]')).toHaveCount(open.length);
  await expect(page.getByTestId('card-open')).toContainText(String(open.length));
});

test('② 진행 중인 개선 — 나중에·결정 취소가 DB 상태를 바꿈(나중에는 ②에서 빠짐), 안 하기는 이유 필수', async ({ page }) => {
  test.setTimeout(90_000);
  const d = await report();
  test.skip(!d, '진단 없음');
  const target = d!.techniques.find((x) => x.status === 'proposed' && x.recommend !== 'reject');
  test.skip(!target, '② 진행 중인 개선에 결정 전 후보 없음');
  const cid = String(target!.id);
  await loginAdmin(page);
  await goAdmin(page, '품질', '평가 게이트');
  const line = page.getByTestId('gate-in-progress').getByTestId(`inprogress-${target!.key}`);   // ② 사람이 정할 후보 — 줄을 누르면 카드 모달(10/30)
  const row = page.getByRole('dialog').getByTestId(`candidate-${target!.key}`);
  try {
    await line.click();
    await expect(row.getByLabel('진행 단계')).toContainText('제안');
    await expect(row.getByRole('button', { name: '실험해 보기' })).toBeVisible();
    await row.getByRole('button', { name: '안 하기…' }).click();
    await expect(page.getByRole('button', { name: '안 하기', exact: true })).toBeDisabled();   // 이유 없이 못 함
    await page.keyboard.press('Escape');
    await row.getByRole('button', { name: '나중에…' }).click();
    await page.getByRole('button', { name: '나중에', exact: true }).click();
    await expect.poll(() => psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`)).toBe('held');
    await expect(line).toHaveCount(0);   // 나중에 = 열린 일 아님 → ②에서 빠지고(카드 모달도 닫힘) ① 원인 안에 "나중에"로
    await expect(row).toHaveCount(0);
    await page.getByTestId(`report-cause-${target!.cause}`).locator('> button').click();
    const cause = page.getByTestId(`report-cause-modal-${target!.cause}`);
    const mrow = cause.getByTestId(`report-method-${cid}`);
    await expect(mrow).toContainText('나중에');
    await mrow.getByTestId(`method-open-${cid}`).click();   // 원인 모달 안 개선 → 같은 후보 모달(10/30 통일)
    const card = page.getByRole('dialog').getByTestId(`candidate-${target!.key}`);
    await expect(card.getByTestId('cand-rows')).toBeVisible();
    await card.getByRole('button', { name: '결정 취소' }).click();
    await expect.poll(() => psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`)).toBe('proposed');
    expect(psql(`SELECT reviewed_at IS NULL FROM eval_candidate WHERE id = ${cid}`)).toBe('t');
    await page.keyboard.press('Escape');   // 후보 모달 → 원인 모달 순서로
    await expect(card).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('gate-in-progress').getByTestId(`inprogress-${target!.key}`)).toHaveCount(1);   // 다시 ②로
  } finally {
    if (psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`) !== 'proposed') await review(cid, 'proposed');
  }
});

test('[운영에 반영했어요] — 통과한 측정을 고르면 후보 = 운영 반영, 그 측정이 지금 점수(기준선)가 됨', async ({ page }) => {
  test.setTimeout(90_000);
  const d = await report();
  test.skip(!d, '진단 없음');
  const target = d!.techniques.find((x) => x.status === 'proposed' && manualOnly(x.id));
  test.skip(!target, '결정 전 후보 없음');
  const baseId = psql(`SELECT id FROM eval_gate_run WHERE is_baseline AND axis = 'policy'`);
  test.skip(!baseId, '기준선 없음');
  const otherAxis = psql(`SELECT coalesce(string_agg(id::text, ',' ORDER BY id), '') FROM eval_gate_run WHERE is_baseline AND axis <> 'policy'`);   // #96 — 다른 축 기준선은 그대로여야
  const cid = String(target!.id);
  let gid = '';
  try {
    await review(cid, 'approved');   // 실험 시작 — 반영할 측정은 이 뒤에 잰 것이어야 한다
    // 테스트용 통과 측정 한 줄(지금 점수를 그대로 복사, 실험 시작 뒤 시각) — 끝에 지운다
    gid = psql(`INSERT INTO eval_gate_run (created_by, label, kind, golden_version, verdict, retrieval, answers, summary, judge_meta)
                SELECT 'e2e', 'E2E 반영 확인용', 'gate', golden_version, 'pass', retrieval,
                       CASE WHEN answers IS NULL THEN NULL ELSE answers || '{"confident_wrong": {"before": 0.1, "after": 0.1, "n": 1, "reps_before": 2, "reps_after": 2}}'::jsonb END,
                       summary, judge_meta
                FROM eval_gate_run WHERE id = ${baseId} RETURNING id`).split(/\s+/)[0];
    // 이 후보를 잰 측정으로 연결(eval_gate.py --candidate와 같은 칸) — 남의 측정은 반영할 수 없다(리뷰 M3)
    psql(`UPDATE eval_candidate SET measured_gate_run_ids = array_append(coalesce(measured_gate_run_ids, '{}'), ${gid}) WHERE id = ${cid}`);
    await loginAdmin(page);
    await goAdmin(page, '품질', '평가 게이트');
    await page.getByTestId('gate-in-progress').getByTestId(`inprogress-${target!.key}`).click();   // 실험 중 = ② — 줄 → 카드 모달
    const row = page.getByRole('dialog').getByTestId(`candidate-${target!.key}`);
    // 실험 결과 칸(10/30) — 연결한 측정의 전/후와 서버의 운영 반영 판정(반영 버튼과 같은 규칙)
    await expect(row.getByTestId('cand-result')).toContainText(`측정 #${gid}`);
    await expect(row.getByTestId('cand-result')).toContainText('단정 오답');
    await expect(row.getByTestId('cand-adopt-check')).toContainText('운영 반영 가능');
    await row.getByRole('button', { name: '운영에 반영했어요…' }).click();
    const dialog = page.getByRole('dialog').filter({ hasText: '반영 완료로 기록' });
    await dialog.getByLabel(new RegExp(`#${gid} E2E 반영 확인용`)).check();
    await dialog.getByRole('button', { name: '반영 완료로 기록' }).click();
    await expect.poll(() => psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`)).toBe('adopted');
    expect(psql(`SELECT adopted_gate_run_id FROM eval_candidate WHERE id = ${cid}`)).toBe(gid);
    expect(psql(`SELECT id FROM eval_gate_run WHERE is_baseline AND axis = 'policy'`)).toBe(gid);   // 반영한 측정 = 지금 점수
    expect(psql(`SELECT COUNT(*) FROM eval_gate_run WHERE is_baseline AND axis = 'policy'`)).toBe('1');
    expect(psql(`SELECT coalesce(string_agg(id::text, ',' ORDER BY id), '') FROM eval_gate_run WHERE is_baseline AND axis <> 'policy'`)).toBe(otherAxis);
  } finally {
    // 운영 반영은 화면에서 되돌릴 수 없어(판정 기록과 묶임) 테스트가 만든 상태만 직접 되돌린다 — 후보 → 결정 전, 기준선 → 원래, 테스트 측정 줄 삭제
    psql(`UPDATE eval_candidate SET status = 'proposed', review_reason = NULL, reviewed_by_user_id = NULL, reviewed_at = NULL,
          adopted_gate_run_id = NULL${gid ? `, measured_gate_run_ids = array_remove(measured_gate_run_ids, ${gid})` : ''} WHERE id = ${cid}`);
    psql(`UPDATE eval_gate_run SET is_baseline = FALSE WHERE is_baseline AND axis = 'policy'`);   // 축마다 부분 고유 인덱스(#96) — 내리고 올리기를 따로, 다른 축은 건드리지 않음
    psql(`UPDATE eval_gate_run SET is_baseline = TRUE WHERE id = ${baseId}`);
    if (gid) psql(`DELETE FROM eval_gate_run WHERE id = ${gid} AND label = 'E2E 반영 확인용'`);
  }
  expect(psql(`SELECT id FROM eval_gate_run WHERE is_baseline AND axis = 'policy'`)).toBe(baseId);
});
