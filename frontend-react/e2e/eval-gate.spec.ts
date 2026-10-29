import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { adminToken, API, apiFetch, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(2026-10-23 v2.169 → 10/26 v2.172 카드·층별 막대): 평가 게이트 [검색][답]은 지금 점수(기준선 판정 기록)를 읽기만 한다 —
// 카드 숫자가 DB에 저장된 판정과 같은지, 목록 층(list + navigation)이 한 줄로 합쳐지는지, 맨 위 숫자가 잠근 시험지(사람·실사용 문항)인지,
// 층 줄의 놓침·틀림 수가 서버 층별 문항(/layers)과 같고 그 합이 카드 숫자와 같은지, 층 줄을 누르면 그 문항들이 열리는지를 실 dev 스택으로 확인한다.
// 기록이 없으면(새 DB) 건너뛴다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const LIST = ['list', 'navigation'];

test('평가 게이트 [성적] 검색 성적·답 성적 — 카드 숫자 = DB, 층 합치기, 층별 놓침·틀림 = 서버, 층을 누르면 문항', async ({ page }) => {
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
  await goAdmin(page, '품질', '평가 게이트');

  // [검색] — 카드 3장 = 저장된 Recall·Top-1·놓친 수, 층별 놓침 합 = 놓친 수(서버 규칙이 판정 숫자와 같다)
  await page.getByRole('button', { name: '성적', exact: true }).click();   // 10/28 [검색]·[답] 탭 → [성적] 하나(답 성적 | 검색 성적)
  await page.getByRole('button', { name: '검색 성적', exact: true }).click();
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

  // [답] — 맨 위 = 잠근 시험지(사람·실사용 가중 평균), 전체 정답률, 라벨 분포, 층별 틀림 합 = 서버 틀린 문항 수
  await page.getByRole('button', { name: '답 성적', exact: true }).click();
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

// 10/29 지식(컨플) 축(#96) — 맨 위 [정책서 | 지식(컨플)]. 지식 축은 그 축 기준선의 숫자를 보이고(정책서 기준선과 섞이지 않음),
// 답을 못 낸 문항(게이트웨이 거부 등)은 정답률에서 빼고 따로 보인다. 단계 흐름·틀린 문항·적용 이력은 정책서 축에만.
test('평가 게이트 지식(컨플) 축 — 카드 = 지식 기준선, 답 못 냄 따로, 정책서 전용 칸 숨김, 정책서로 돌아오면 정책서 숫자', async ({ page }) => {
  test.setTimeout(90_000);
  const kid = psql(`SELECT id FROM eval_gate_run WHERE is_baseline AND axis = 'knowledge'`);
  const pid = psql(`SELECT id FROM eval_gate_run WHERE is_baseline AND axis = 'policy'`);
  test.skip(!kid || !pid, '두 축 기준선이 다 있어야 이 시나리오를 본다');
  const ks = JSON.parse(psql(`SELECT summary::text FROM eval_gate_run WHERE id = ${kid}`));
  const ps = JSON.parse(psql(`SELECT summary::text FROM eval_gate_run WHERE id = ${pid}`));
  test.skip(!ks.retrieval || !ks.answers, '지식 기준선에 검색·답 측정이 다 있어야');

  await loginAdmin(page);
  await goAdmin(page, '품질', '평가 게이트');
  await expect(page.getByTestId('flow-검색')).toBeVisible();   // 정책서 축 = 단계 흐름 있음
  await page.getByTestId('axis-knowledge').click();
  await expect(page.getByTestId('flow-검색')).toHaveCount(0);
  await expect(page.getByRole('button', { name: '틀린 문항', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '적용 이력', exact: true })).toHaveCount(0);

  await expect(page.getByText(`측정 #${kid}`)).toBeVisible();
  await expect(page.getByText(pct(ks.answers.accuracy), { exact: false }).first()).toBeVisible();
  const fails: string[] = ks.answers.failures ?? [];
  if (fails.length) await expect(page.getByTestId('answer-failures')).toContainText(`답을 못 냄 ${fails.length}문항`);
  await page.getByRole('button', { name: '검색 성적', exact: true }).click();
  const r = ks.retrieval;
  await expect(page.getByTestId('stat-recall')).toContainText(`${pct(r.recall)}${r.hit}/${r.n}`);

  await page.getByTestId('axis-policy').click();   // 돌아오면 정책서 기준선 숫자(캐시가 축끼리 섞이지 않음)
  await page.getByRole('button', { name: '성적', exact: true }).click();
  await page.getByRole('button', { name: '검색 성적', exact: true }).click();
  await expect(page.getByTestId('stat-recall')).toContainText(`${pct(ps.retrieval.recall)}${ps.retrieval.hit}/${ps.retrieval.n}`);
});
