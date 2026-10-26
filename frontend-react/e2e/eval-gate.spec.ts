import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { API, adminToken, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(2026-10-23 v2.169, 10/26 v2.171 개편): 평가 게이트 [검색][답][판정 이력]은 판정 기록(eval_gate_run)을 읽기만 한다 —
// 화면 숫자가 DB에 저장된 기준선·판정과 같은지, 목록 층(list + navigation)이 하나로 합쳐지는지, 맨 위 숫자가 잠근 시험지(사람·실사용 문항)인지,
// 판정 상세의 나빠진 문항 수가 저장된 qid 목록과 같은지, 문항을 누르면 질문·답·이유가 열리는지, "이걸 비교 기준으로"가 확인 창을 거쳐
// 실제로 옮겨 가는지를 실 dev 스택으로 확인한다. 기준선을 바꿨으면 끝에 원래대로 되돌린다. 기록이 없으면(새 DB) 건너뛴다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

test('평가 게이트 — 기준선 숫자·잠근 시험지·층 합치기·판정 상세·문항 상세·비교 기준 바꾸기', async ({ page }) => {
  test.setTimeout(120_000);
  const total = Number(psql('SELECT COUNT(*) FROM eval_gate_run'));
  test.skip(total === 0, '판정 기록 없음');
  const baseRaw = psql('SELECT id FROM eval_gate_run WHERE is_baseline');
  test.skip(!baseRaw, '기준선 없음 — 화면은 "기준선이 없습니다"');
  const baseId = Number(baseRaw);
  const s = JSON.parse(psql(`SELECT summary::text FROM eval_gate_run WHERE id = ${baseId}`));
  test.skip(!s.retrieval || !s.answers, '기준선에 검색·답 측정이 다 있어야 이 시나리오를 본다');
  const runs = page.locator('[data-testid^="gate-run-"]');
  // 이름 정확히 같은 줄 — 부분 문자열이면 "A"가 "A 재측정" 줄을 잡는다(리뷰 L7)
  const rowOf = (label: string) => runs.filter({ has: page.getByText(label, { exact: true }) }).first();
  // 0문항이면 버튼이 아니라 글자(QidList) — 데이터에 따라 잘못 실패하지 않게(리뷰 M3)
  const qidList = (title: string, n: number) =>
    n ? page.getByRole('button', { name: `${title}: ${n}문항` }) : page.getByText(`${title}: 0문항`, { exact: true });

  await loginAdmin(page);
  await goAdmin(page, '품질', '평가 게이트');

  // [검색] — 기준선 Recall·Top-1, 목록 층 = list(+navigation) 합
  const r = s.retrieval;
  await expect(page.getByText(`${pct(r.recall)} (${r.hit}/${r.n})`)).toBeVisible();
  await expect(page.getByText(`${pct(r.top1_rate)} (${r.top1}/${r.n})`)).toBeVisible();
  const listN = ['list', 'navigation'].reduce((a, k) => a + (r.by_type[k]?.n ?? 0), 0);
  if (listN) await expect(page.getByRole('row', { name: new RegExp(`^목록 ${listN} `) })).toBeVisible();

  // [답] — 맨 위 = 잠근 시험지(사람·실사용 문항 가중 평균), 그다음 전체 정답률·라벨 분포
  await page.getByRole('button', { name: '답', exact: true }).click();
  const locked = ['human', 'real'].map((t) => s.answers.by_type[t]).filter(Boolean);
  if (locked.length) {
    const n = locked.reduce((a: number, x: { n: number }) => a + x.n, 0);
    const acc = locked.reduce((a: number, x: { n: number; accuracy: number }) => a + x.accuracy * x.n, 0) / n;
    await expect(page.getByTestId('locked-accuracy')).toContainText(`사람이 쓴 질문 ${pct(acc)}`);
  }
  await expect(page.getByText(`전체 정답률 ${pct(s.answers.accuracy)}`)).toBeVisible();
  await expect(page.getByText(`오답 ${s.answers.labels['오답'] ?? 0}`)).toBeVisible();
  await expect(page.getByRole('row', { name: /^목록 / })).toHaveCount(1);
  await expect(page.getByRole('cell', { name: 'navigation' })).toHaveCount(0);
  const wrong = s.answers.retrieval_ok_answer_wrong.length;
  await expect(qidList('검색은 맞았는데 답이 틀림', wrong)).toBeVisible();

  // [판정 이력] — 맨 위 "지금 비교 기준", 줄 수 = 기록 수, 나빠진 문항이 가장 많은 판정을 펼쳐 저장된 qid 수와 비교 + 문항 상세
  await page.getByRole('button', { name: '판정 이력', exact: true }).click();
  const baseLabel = psql(`SELECT label FROM eval_gate_run WHERE id = ${baseId}`);
  await expect(page.getByText('지금 비교 기준:')).toBeVisible();
  await expect(page.getByText(baseLabel, { exact: true }).first()).toBeVisible();
  await expect(runs).toHaveCount(Math.min(total, 50));
  const [gid, label, downN] = psql(`SELECT id, label, jsonb_array_length(answers->'down_qids') FROM eval_gate_run
                                    WHERE kind = 'gate' AND answers IS NOT NULL ORDER BY 3 DESC, id LIMIT 1`).split('|');
  if (gid) {
    await rowOf(label).getByRole('button').first().click();
    await expect(qidList('나빠진 문항', Number(downN))).toBeVisible();
    await expect(rowOf(label).locator('tbody tr').first()).toContainText('전체')   // 상세 표 첫 줄은 전체(jsonb 키 순서에 안 끌려감)
    if (Number(downN) > 0) {
      // 문항 번호를 누르면 질문·답·이유(결과 파일에서) — 파일이 서버에 있으면 6단계가 보이고, 없으면 그 이유가 보인다
      await qidList('나빠진 문항', Number(downN)).click();
      const first = psql(`SELECT answers->'down_qids'->>0 FROM eval_gate_run WHERE id = ${gid}`);
      await page.getByRole('button', { name: first, exact: true }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog.getByText(`문항 ${first}`)).toBeVisible();
      // 결과 파일이 서버에 있으면 반드시 6단계가 보여야 한다 — "또는 오류 문구"로 두면 파일이 있어도 공짜 통과(리뷰 L6)
      const ansFile = psql(`SELECT summary->'inputs'->>'answers_after' FROM eval_gate_run WHERE id = ${gid}`);
      const hasFile = !!ansFile && execFileSync('docker', ['exec', 'ops-backend', 'find', '/app/eval_out', '-name', ansFile],
        { encoding: 'utf-8' }).trim() !== '';   // 셸을 거치지 않는다(이름에 따옴표가 있어도 안전)
      if (hasFile) {
        await expect(dialog.getByTestId('question-detail')).toBeVisible({ timeout: 15_000 });
        await expect(dialog.getByText('질문', { exact: true })).toBeVisible();
      } else {
        await expect(dialog.getByText(/결과 파일/)).toBeVisible({ timeout: 15_000 });
      }
      await page.keyboard.press('Escape');
    }
  }

  // "이걸 비교 기준으로" — 확인 창 → 통과한 다른 판정으로 옮겼다가 되돌린다(관리자만)
  const other = psql(`SELECT id, label FROM eval_gate_run WHERE NOT is_baseline AND verdict IS DISTINCT FROM 'fail' ORDER BY id DESC LIMIT 1`);
  if (other) {
    const [oid, olabel] = other.split('|');
    try {
      await expect(rowOf(olabel)).toHaveCount(1);   // 줄을 못 찾으면 아래 확인이 공짜로 통과한다
      await rowOf(olabel).getByRole('button').first().click();
      await rowOf(olabel).getByRole('button', { name: '이걸 비교 기준으로' }).click();
      await page.getByRole('dialog').getByRole('button', { name: '이걸 비교 기준으로' }).click();   // 확인 창
      await expect.poll(() => psql('SELECT id FROM eval_gate_run WHERE is_baseline')).toBe(oid);
      await expect(rowOf(olabel).getByRole('button', { name: '이걸 비교 기준으로' })).toHaveCount(0);
      await expect(rowOf(olabel).getByText('비교 기준', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: '검색', exact: true }).click();
      await expect(page.getByText(`"${olabel}"`, { exact: false })).toBeVisible();   // [검색] 머리말도 새 기준선
      expect(psql('SELECT COUNT(*) FROM eval_gate_run WHERE is_baseline')).toBe('1');
    } finally {
      const t = await adminToken();
      const res = await fetch(`${API}/api/eval/gate-runs/${baseId}/baseline`, { method: 'POST', headers: { Authorization: `Bearer ${t}` } });
      expect(res.ok, '기준선 되돌리기 실패 — 수동으로 되돌릴 것').toBe(true);
    }
    expect(psql('SELECT id FROM eval_gate_run WHERE is_baseline')).toBe(String(baseId));
  }
});
