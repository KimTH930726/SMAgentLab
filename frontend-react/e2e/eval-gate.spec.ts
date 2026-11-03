import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { adminToken, API, apiFetch, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(2026-10-23 v2.169 → 10/26 v2.172 카드·층별 막대 → 10/29 맨 위 카드 [자세히]): 평가 게이트 검색·답 성적은 지금 점수(기준선 판정 기록)를 읽기만 한다 —
// 카드 숫자가 DB에 저장된 판정과 같은지, 목록 층(list + navigation)이 한 줄로 합쳐지는지, 맨 위 숫자가 잠근 시험지(사람·실사용 문항)인지,
// 층 줄의 놓침·틀림 수가 서버 층별 문항(/layers)과 같고 그 합이 카드 숫자와 같은지, 층 줄을 누르면 그 문항들이 열리는지를 실 dev 스택으로 확인한다.
// 기록이 없으면(새 DB) 건너뛴다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const LIST = ['list', 'navigation'];

test('평가 게이트 카드 [자세히] 검색 성적·답 성적 — 카드 숫자 = DB, 층 합치기, 층별 놓침·틀림 = 서버, 층을 누르면 문항', async ({ page }) => {
  test.setTimeout(120_000);
  const baseRaw = psql(`SELECT id FROM eval_gate_run WHERE is_baseline AND axis = 'policy'`);   // 정책서 축(#96 — 축마다 기준선)
  test.skip(!baseRaw, '기준선 없음');
  const baseId = Number(baseRaw);
  const s = JSON.parse(psql(`SELECT summary::text FROM eval_gate_run WHERE id = ${baseId}`));
  test.skip(!s.retrieval || !s.answers, '지금 점수에 검색·답 측정이 다 있어야 이 시나리오를 본다');
  const t = await adminToken();
  const layers = await (await apiFetch(`${API}/api/eval/gate-runs/${baseId}/layers`, { headers: { Authorization: `Bearer ${t}` } })).json();
  const sum = (m: Record<string, string[]>) => Object.values(m).reduce((a, v) => a + v.length, 0);

  await loginAdmin(page);
  await goAdmin(page, '평가 게이트');

  // 맨 위 카드(10/29 한 템플릿) = 지금 점수 — [자세히]가 옛 [성적] 탭 내용(검색 성적·답 성적)을 연다
  await expect(page.getByTestId('card-total')).toContainText(pct(s.answers.accuracy));
  await expect(page.getByTestId('card-retrieval')).toContainText(`${pct(s.retrieval.recall)}정답을 AI에게 넘김 ${s.retrieval.hit}/${s.retrieval.n}`);
  // [검색] — 카드 3장 = 저장된 Recall·Top-1·놓친 수, 층별 놓침 합 = 놓친 수(서버 규칙이 판정 숫자와 같다)
  await page.getByTestId('card-retrieval').getByRole('button', { name: '자세히 →' }).click();
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
    if (name) await expect(page.getByTestId(`layer-${name}`)).toContainText(`못 넘김 ${qs.length}`)   // 10/27 '놓침' → '못 넘김';
  }
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('gate-scores')).toHaveCount(0);

  // [답] — 잠근 골든셋(사람·실사용 가중 평균)은 맨 위 카드 하나에만(옛 리포트 머리 줄과 중복 없앰), 상세 = 전체 정답률·라벨 분포·층별 틀림
  const locked = ['human', 'real'].map((k) => s.answers.by_type[k]).filter(Boolean);
  if (locked.length) {
    const n = locked.reduce((a: number, x: { n: number }) => a + x.n, 0);
    const acc = locked.reduce((a: number, x: { n: number; accuracy: number }) => a + x.accuracy * x.n, 0) / n;
    await expect(page.getByTestId('card-locked')).toContainText(pct(acc));
  }
  await expect(page.getByTestId('report-locked')).toHaveCount(0);
  await page.getByTestId('card-total').getByRole('button', { name: '자세히 →' }).click();
  if (locked.length) await expect(page.getByTestId('locked-accuracy')).toBeVisible();
  await expect(page.getByRole('dialog').getByText(pct(s.answers.accuracy), { exact: false }).first()).toBeVisible();
  await expect(page.getByText(`오답 ${s.answers.labels['오답'] ?? 0}`)).toBeVisible();
  await expect(page.getByTestId('layer-navigation')).toHaveCount(0);
  const listWrong = LIST.reduce((a, k) => a + (layers.wrong[k]?.length ?? 0), 0);
  if (listWrong) {
    await expect(page.getByTestId('layer-목록')).toContainText(`틀림 ${listWrong}`);
    // 층 줄을 누르면 그 층의 틀린 문항이 열린다(이전/다음으로 그 층 안에서 이동)
    await page.getByTestId('layer-목록').click();
    const dialog = page.getByRole('dialog').last();
    await expect(dialog).toBeVisible();
    if (listWrong > 1) await expect(dialog.getByText(`1 / ${listWrong}`)).toBeVisible();
    await page.keyboard.press('Escape');
  }
  const okWrong = s.answers.retrieval_ok_answer_wrong.length;
  if (okWrong) await expect(page.getByRole('button', { name: /검색은 맞았는데 답이 틀림/ })).toContainText(`검색은 맞았는데 답이 틀림 ${okWrong}문항`);
});

// 10/29 한 템플릿 — 축 [정책서 | 업무지식]은 데이터만 바꾼다: 두 축 다 같은 칸(카드 · 단계 줄 · ① 개선할 곳 · ② 진행 중인 개선 · ③ 적용 이력).
// 지식 축 카드는 그 축 기준선 숫자(정책서와 섞이지 않음), 답을 못 낸 문항은 정답률에서 빼고 따로, 잠근 골든셋이 없는 축엔 "사람이 쓴 질문" 카드가 없다.
test('평가 게이트 두 축 같은 템플릿 — 카드 = 그 축 기준선, 답 못 냄 따로, 정책서로 돌아오면 정책서 숫자', async ({ page }) => {
  test.setTimeout(90_000);
  const kid = psql(`SELECT id FROM eval_gate_run WHERE is_baseline AND axis = 'knowledge'`);
  const pid = psql(`SELECT id FROM eval_gate_run WHERE is_baseline AND axis = 'policy'`);
  test.skip(!kid || !pid, '두 축 기준선이 다 있어야 이 시나리오를 본다');
  const ks = JSON.parse(psql(`SELECT summary::text FROM eval_gate_run WHERE id = ${kid}`));
  const ps = JSON.parse(psql(`SELECT summary::text FROM eval_gate_run WHERE id = ${pid}`));
  test.skip(!ks.retrieval || !ks.answers, '지식 기준선에 검색·답 측정이 다 있어야');
  const SECTIONS = ['gate-summary', 'gate-flow', 'gate-report', 'gate-in-progress', 'gate-history-section'];

  await loginAdmin(page);
  await goAdmin(page, '평가 게이트');
  for (const id of SECTIONS) await expect(page.getByTestId(id)).toBeVisible();
  await page.getByTestId('axis-knowledge').click();
  await expect(page.getByTestId('gate-axis-knowledge')).toBeVisible();
  for (const id of SECTIONS) await expect(page.getByTestId(id)).toBeVisible();   // 지식 축도 같은 칸(숨기지 않음)
  await expect(page.getByTestId('flow-검색')).toBeVisible();

  await expect(page.getByTestId('card-total')).toContainText(pct(ks.answers.accuracy));
  const fails: string[] = ks.answers.failures ?? [];
  if (fails.length) await expect(page.getByTestId('card-total')).toContainText(`답 못 냄 ${fails.length}(뺌)`);
  const kLocked = ['human', 'real'].some((k) => ks.answers.by_type[k]);
  await expect(page.getByTestId('card-locked')).toHaveCount(kLocked ? 1 : 0);
  await page.getByTestId('card-total').getByRole('button', { name: '자세히 →' }).click();
  await expect(page.getByRole('dialog').getByText(`측정 #${kid}`)).toBeVisible();
  if (fails.length) await expect(page.getByTestId('answer-failures')).toContainText(`답을 못 냄 ${fails.length}문항`);
  await page.keyboard.press('Escape');
  const r = ks.retrieval;
  await page.getByTestId('card-retrieval').getByRole('button', { name: '자세히 →' }).click();
  await expect(page.getByTestId('stat-recall')).toContainText(`${pct(r.recall)}${r.hit}/${r.n}`);
  await page.keyboard.press('Escape');

  await page.getByTestId('axis-policy').click();   // 돌아오면 정책서 기준선 숫자(캐시가 축끼리 섞이지 않음)
  await expect(page.getByTestId('card-retrieval')).toContainText(`${pct(ps.retrieval.recall)}정답을 AI에게 넘김 ${ps.retrieval.hit}/${ps.retrieval.n}`);
  await page.getByTestId('card-retrieval').getByRole('button', { name: '자세히 →' }).click();
  await expect(page.getByTestId('stat-recall')).toContainText(`${pct(ps.retrieval.recall)}${ps.retrieval.hit}/${ps.retrieval.n}`);
});
