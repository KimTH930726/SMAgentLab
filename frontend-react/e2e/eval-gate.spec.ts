import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { API, adminToken, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(2026-10-23 v2.169 → 10/26 v2.172 카드·층별 막대): 평가 게이트 [검색][답]은 지금 점수(기준선 판정 기록)를 읽기만 한다 —
// 카드 숫자가 DB에 저장된 판정과 같은지, 목록 층(list + navigation)이 한 줄로 합쳐지는지, 맨 위 숫자가 잠근 시험지(사람·실사용 문항)인지,
// 층 줄의 놓침·틀림 수가 서버 층별 문항(/layers)과 같고 그 합이 카드 숫자와 같은지, 층 줄을 누르면 그 문항들이 열리는지를 실 dev 스택으로 확인한다.
// 기록이 없으면(새 DB) 건너뛴다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const LIST = ['list', 'navigation'];

test('평가 게이트 [검색][답] — 카드 숫자 = DB, 층 합치기, 층별 놓침·틀림 = 서버, 층을 누르면 문항', async ({ page }) => {
  test.setTimeout(120_000);
  const baseRaw = psql('SELECT id FROM eval_gate_run WHERE is_baseline');
  test.skip(!baseRaw, '기준선 없음');
  const baseId = Number(baseRaw);
  const s = JSON.parse(psql(`SELECT summary::text FROM eval_gate_run WHERE id = ${baseId}`));
  test.skip(!s.retrieval || !s.answers, '지금 점수에 검색·답 측정이 다 있어야 이 시나리오를 본다');
  const t = await adminToken();
  const layers = await (await fetch(`${API}/api/eval/gate-runs/${baseId}/layers`, { headers: { Authorization: `Bearer ${t}` } })).json();
  const sum = (m: Record<string, string[]>) => Object.values(m).reduce((a, v) => a + v.length, 0);

  await loginAdmin(page);
  await goAdmin(page, '품질', '평가 게이트');

  // [검색] — 카드 3장 = 저장된 Recall·Top-1·놓친 수, 층별 놓침 합 = 놓친 수(서버 규칙이 판정 숫자와 같다)
  await page.getByRole('button', { name: '검색', exact: true }).click();
  const r = s.retrieval;
  await expect(page.getByTestId('stat-recall')).toContainText(`${pct(r.recall)}${r.hit}/${r.n}`);
  await expect(page.getByTestId('stat-top1')).toContainText(`${pct(r.top1_rate)}${r.top1}/${r.n}`);
  await expect(page.getByTestId('stat-missed')).toContainText(`${r.n - r.hit}문항`);
  expect(sum(layers.missed)).toBe(r.n - r.hit);
  if (LIST.some((k) => r.by_type[k])) await expect(page.getByTestId('layer-목록')).toHaveCount(1);
  await expect(page.getByTestId('layer-navigation')).toHaveCount(0);
  for (const [type, qs] of Object.entries(layers.missed as Record<string, string[]>)) {
    if (LIST.includes(type)) continue;   // 목록은 두 type을 합친 줄 — 아래 답 쪽에서 합으로 본다
    const name = { fact: '사실', condition: '조건', human: '사람 문항', cross: '영역 넘김', abstain: '답 없음', real: '실사용', ref: '참고표', ref_clarify: '참고표 되묻기', overview: '개요' }[type];
    if (name) await expect(page.getByTestId(`layer-${name}`)).toContainText(`놓침 ${qs.length}`);
  }

  // [답] — 맨 위 = 잠근 시험지(사람·실사용 가중 평균), 전체 정답률, 라벨 분포, 층별 틀림 합 = 서버 틀린 문항 수
  await page.getByRole('button', { name: '답', exact: true }).click();
  const locked = ['human', 'real'].map((k) => s.answers.by_type[k]).filter(Boolean);
  if (locked.length) {
    const n = locked.reduce((a: number, x: { n: number }) => a + x.n, 0);
    const acc = locked.reduce((a: number, x: { n: number; accuracy: number }) => a + x.accuracy * x.n, 0) / n;
    await expect(page.getByTestId('locked-accuracy')).toContainText(pct(acc));
  }
  await expect(page.getByText(pct(s.answers.accuracy), { exact: false }).first()).toBeVisible();
  await expect(page.getByText(`오답 ${s.answers.labels['오답'] ?? 0}`)).toBeVisible();
  await expect(page.getByTestId('layer-navigation')).toHaveCount(0);
  const listWrong = LIST.reduce((a, k) => a + (layers.wrong[k]?.length ?? 0), 0);
  if (listWrong) {
    await expect(page.getByTestId('layer-목록')).toContainText(`틀림 ${listWrong}`);
    // 층 줄을 누르면 그 층의 틀린 문항이 열린다(이전/다음으로 그 층 안에서 이동)
    await page.getByTestId('layer-목록').click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    if (listWrong > 1) await expect(dialog.getByText(`1 / ${listWrong}`)).toBeVisible();
    await page.keyboard.press('Escape');
  }
  const okWrong = s.answers.retrieval_ok_answer_wrong.length;
  if (okWrong) await expect(page.getByRole('button', { name: /검색은 맞았는데 답이 틀림/ })).toContainText(`검색은 맞았는데 답이 틀림 ${okWrong}문항`);
});
