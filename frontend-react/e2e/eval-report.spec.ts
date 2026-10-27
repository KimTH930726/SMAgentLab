import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { API, adminToken, apiFetch, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(2026-10-23 v2.170 자가 개선 루프 → 10/26 v2.172 틀린 문항 리포트): 리포트는 진단·후보·적재 검수 기록을 읽기만 하고,
// 사람 결정(나중에·안 하기·결정 취소·운영에 반영했어요)만 상태를 바꾼다. 화면 숫자·줄 수가 서버 리포트와 같은지, 거르기가 서버 꼬리표대로 줄을 줄이는지,
// 문항을 누르면 저장 구조까지 보이는지, 결정 버튼이 실제로 DB 상태를 바꾸는지, [운영에 반영했어요]가 그 측정을 지금 점수(기준선)로 옮기는지를
// 실 dev 스택으로 확인한다. 바꾼 상태·테스트가 만든 측정 줄은 끝에 원래대로 되돌린다. 진단이 없으면 건너뛴다(scripts/eval_improve.py --save로만 생김).

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

type Report = {
  gate_run_id: number; improvement_id: number; score: { locked: number | null; total: number | null };
  failed: { qid: string; storage: string[]; techniques: { id: number }[]; cause_name: string | null; locked: boolean }[]; techniques: { id: number; status: string; n_failed: number }[];
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

test('리포트 — 지금 점수·틀린 문항 수 = 서버, 저장 상태 거르기, 문항 상세의 저장 구조, 측정도 안 되는 문항', async ({ page }) => {
  test.setTimeout(90_000);
  const d = await report();
  test.skip(!d, '진단 없음');
  await loginAdmin(page);
  await goAdmin(page, '품질', '평가 게이트');
  await expect(page.getByTestId('gate-report')).toBeVisible();   // 첫 탭 = 리포트
  if (d!.score.locked != null) await expect(page.getByTestId('report-locked')).toContainText(pct(d!.score.locked));
  // 10/27 좌(고칠 방법 묶음)·우(문항 8개씩 페이지) — 목록 제목의 수 = 서버, 한 페이지 줄 수 = min(8, 수)
  const rows = page.locator('[data-testid^="report-row-"]');
  const title = page.getByTestId('report-list-title');
  await expect(page.getByTestId('report-failed-n')).toContainText(`틀린 문항 ${d!.failed.length}`);
  await expect(title).toHaveText(`틀린 문항 전체 ${d!.failed.length}`);
  await expect(rows).toHaveCount(Math.min(8, d!.failed.length));
  if (d!.failed.length > 8) {
    await expect(page.getByTestId('report-page')).toHaveText(`1 / ${Math.ceil(d!.failed.length / 8)}`);
    await page.getByTestId('report-pager').getByRole('button', { name: '다음 ›' }).click();
    await expect(page.getByTestId('report-page')).toHaveText(`2 / ${Math.ceil(d!.failed.length / 8)}`);
    await expect(rows).toHaveCount(Math.min(8, d!.failed.length - 8));
  }

  // 저장 상태로 거르면 그 꼬리표가 붙은 문항만 — 수 = 서버 꼬리표 수
  const tag = d!.failed.flatMap((r) => r.storage)[0];
  if (tag) {
    const n = d!.failed.filter((r) => r.storage.includes(tag)).length;
    await page.getByLabel('저장 상태로 거르기').selectOption(tag);
    await expect(title).toHaveText(`틀린 문항 전체 ${n} / ${d!.failed.length}`);
    await page.getByLabel('저장 상태로 거르기').selectOption('');
  }

  // 고칠 방법을 고르면 그 방법이 겨냥한 문항만 — 수 = 서버 n_failed, 모든 방법 + "고칠 방법 없음" 수의 합 ≥ 전체(한 문항이 여러 방법에 걸릴 수 있음)
  const t0 = d!.techniques.find((x) => x.n_failed > 0);
  if (t0) {
    await page.getByRole('group', { name: '묶는 기준' }).getByRole('button', { name: '고칠 방법별' }).click();
    await page.getByTestId(`report-tech-${t0.id}`).click();
    await expect(title).toHaveText(`이 방법으로 고칠 문항 ${t0.n_failed}`);
  }
  const none = d!.failed.filter((r) => r.techniques.length === 0 && !r.locked).length;   // 잠근 문항은 진단 안 함 — "고칠 방법 없음"에 안 섞음
  await page.getByTestId('report-nav-none').click();
  await expect(title).toHaveText(`고칠 방법이 없는 문항 ${none}`);
  await page.getByTestId('report-nav-all').click();

  // 문항을 누르면 질문·답·이유 + ⑥ 저장 구조(결과 파일이 서버에 있을 때)
  if (d!.failed.length) {
    await rows.first().click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText(`문항 ${d!.failed[0].qid}`)).toBeVisible();
    await expect(dialog.getByTestId('question-detail')).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText(/저장 구조 —/)).toBeVisible();   // 10/27 ⑤ 번호가 앞에 붙음
    await page.keyboard.press('Escape');
  }

  const unmeasured = d!.unmeasured.reduce((a, u) => a + u.n, 0);
  if (unmeasured) {
    await page.getByTestId('report-unmeasured-open').click();
    await expect(page.getByRole('dialog')).toContainText(`측정도 안 되는 문항 ${unmeasured}`);
    await page.keyboard.press('Escape');
  }
  await expect(page.locator('[data-testid^="report-tech-"]')).toHaveCount(d!.techniques.length);

  // 원인별(기본, 10/27) — 원인을 고르면 수 = 그 원인 문항, 그 원인에 걸린 고칠 방법 카드의 수 = 그 원인 안에서 그 방법이 겨냥한 문항
  await page.getByRole('group', { name: '묶는 기준' }).getByRole('button', { name: '원인별' }).click();
  const byCause = new Map<string, Report['failed']>();
  for (const r of d!.failed) byCause.set(r.cause_name ?? (r.locked ? '잠근 문항(진단 안 함)' : '원인 없음'), [...(byCause.get(r.cause_name ?? (r.locked ? '잠근 문항(진단 안 함)' : '원인 없음')) ?? []), r]);
  const [cname, crows] = [...byCause.entries()].sort((a, b) => b[1].length - a[1].length)[0] ?? [];
  if (cname) {
    await page.getByTestId(`report-cause-${cname}`).click();
    await expect(title).toHaveText(`${cname} ${crows!.length}`);
    const tid = crows!.flatMap((r) => r.techniques.map((t) => t.id))[0];
    if (tid) {
      const n = crows!.filter((r) => r.techniques.some((t) => t.id === tid)).length;
      await expect(page.getByTestId(`report-method-${tid}`)).toContainText(`${n}문항`);
      await page.getByTestId(`report-method-${tid}`).click();
      await expect(title).toContainText(`${cname} · `);
      await expect(title).toContainText(`${n}`);
      await expect(page.getByTestId('report-panel').locator('[data-testid^="candidate-"]')).toHaveCount(1);   // 그 방법의 결정 카드
    }
  }
});

test('리포트 고칠 방법 — 나중에·결정 취소가 DB 상태를 바꿈, 안 하기는 이유 필수, 기법 설명이 펼쳐짐', async ({ page }) => {
  test.setTimeout(90_000);
  const d = await report();
  test.skip(!d, '진단 없음');
  const target = d!.techniques.find((x) => x.status === 'proposed');
  test.skip(!target, '결정 전 후보 없음');
  const cid = String(target!.id);
  await loginAdmin(page);
  await goAdmin(page, '품질', '평가 게이트');
  await page.getByRole('group', { name: '묶는 기준' }).getByRole('button', { name: '고칠 방법별' }).click();
  await page.getByTestId(`report-tech-${cid}`).click();   // 고르면 오른쪽에 결정 카드
  const row = page.getByTestId('report-panel');
  try {
    await expect(row.getByLabel('진행 단계')).toContainText('제안');
    await expect(row.getByRole('button', { name: '실험해 보기' })).toBeVisible();
    await row.getByRole('button', { name: '안 하기…' }).click();
    await expect(page.getByRole('button', { name: '안 하기', exact: true })).toBeDisabled();   // 이유 없이 못 함
    await page.keyboard.press('Escape');
    await row.getByRole('button', { name: '나중에…' }).click();
    await page.getByRole('button', { name: '나중에', exact: true }).click();
    await expect.poll(() => psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`)).toBe('held');
    await expect(page.getByTestId(`report-tech-${cid}`)).toContainText('나중에');   // 왼쪽 목록의 상태 글자
    await row.getByRole('button', { name: '결정 취소' }).click();
    await expect.poll(() => psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`)).toBe('proposed');
    expect(psql(`SELECT reviewed_at IS NULL FROM eval_candidate WHERE id = ${cid}`)).toBe('t');
    const info = row.getByText('이 기법은 무엇인가', { exact: false });
    if (await info.count()) {
      await info.click();
      await expect(row.locator('[data-testid^="technique-"]')).toContainText('왜 있나');
    }
  } finally {
    if (psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`) !== 'proposed') await review(cid, 'proposed');
  }
});

test('[운영에 반영했어요] — 통과한 측정을 고르면 후보 = 운영 반영, 그 측정이 지금 점수(기준선)가 됨', async ({ page }) => {
  test.setTimeout(90_000);
  const d = await report();
  test.skip(!d, '진단 없음');
  const target = d!.techniques.find((x) => x.status === 'proposed');
  test.skip(!target, '결정 전 후보 없음');
  const baseId = psql('SELECT id FROM eval_gate_run WHERE is_baseline');
  test.skip(!baseId, '기준선 없음');
  const cid = String(target!.id);
  let gid = '';
  try {
    await review(cid, 'approved');   // 실험 시작 — 반영할 측정은 이 뒤에 잰 것이어야 한다
    // 테스트용 통과 측정 한 줄(지금 점수를 그대로 복사, 실험 시작 뒤 시각) — 끝에 지운다
    gid = psql(`INSERT INTO eval_gate_run (created_by, label, kind, golden_version, verdict, retrieval, answers, summary, judge_meta)
                SELECT 'e2e', 'E2E 반영 확인용', 'gate', golden_version, 'pass', retrieval, answers, summary, judge_meta
                FROM eval_gate_run WHERE id = ${baseId} RETURNING id`).split(/\s+/)[0];
    // 이 후보를 잰 측정으로 연결(eval_gate.py --candidate와 같은 칸) — 남의 측정은 반영할 수 없다(리뷰 M3)
    psql(`UPDATE eval_candidate SET measured_gate_run_ids = array_append(coalesce(measured_gate_run_ids, '{}'), ${gid}) WHERE id = ${cid}`);
    await loginAdmin(page);
    await goAdmin(page, '품질', '평가 게이트');
    await page.getByRole('group', { name: '묶는 기준' }).getByRole('button', { name: '고칠 방법별' }).click();
    await page.getByTestId(`report-tech-${cid}`).click();
    const row = page.getByTestId('report-panel');
    await row.getByRole('button', { name: '운영에 반영했어요…' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel(new RegExp(`#${gid} E2E 반영 확인용`)).check();
    await dialog.getByRole('button', { name: '반영 완료로 기록' }).click();
    await expect.poll(() => psql(`SELECT status FROM eval_candidate WHERE id = ${cid}`)).toBe('adopted');
    expect(psql(`SELECT adopted_gate_run_id FROM eval_candidate WHERE id = ${cid}`)).toBe(gid);
    expect(psql('SELECT id FROM eval_gate_run WHERE is_baseline')).toBe(gid);   // 반영한 측정 = 지금 점수
    expect(psql('SELECT COUNT(*) FROM eval_gate_run WHERE is_baseline')).toBe('1');
  } finally {
    // 운영 반영은 화면에서 되돌릴 수 없어(판정 기록과 묶임) 테스트가 만든 상태만 직접 되돌린다 — 후보 → 결정 전, 기준선 → 원래, 테스트 측정 줄 삭제
    psql(`UPDATE eval_candidate SET status = 'proposed', review_reason = NULL, reviewed_by_user_id = NULL, reviewed_at = NULL,
          adopted_gate_run_id = NULL${gid ? `, measured_gate_run_ids = array_remove(measured_gate_run_ids, ${gid})` : ''} WHERE id = ${cid}`);
    psql('UPDATE eval_gate_run SET is_baseline = FALSE WHERE is_baseline');   // 부분 고유 인덱스 — 내리고 올리기를 따로
    psql(`UPDATE eval_gate_run SET is_baseline = TRUE WHERE id = ${baseId}`);
    if (gid) psql(`DELETE FROM eval_gate_run WHERE id = ${gid} AND label = 'E2E 반영 확인용'`);
  }
  expect(psql('SELECT id FROM eval_gate_run WHERE is_baseline')).toBe(baseId);
});
