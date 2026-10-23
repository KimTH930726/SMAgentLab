import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { API, adminToken, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(2026-10-23, v2.169): 평가 게이트 [검색][답][판정 이력]은 판정 기록(eval_gate_run)을 읽기만 한다 —
// 화면 숫자가 DB에 저장된 기준선·판정과 같은지(재계산·반올림 어긋남 없음), 목록 층(list + navigation)이 하나로 합쳐지는지,
// 판정 상세의 나빠진 문항 수가 저장된 qid 목록과 같은지, 관리자 기준선 지정이 실제로 옮겨 가는지를 실 dev 스택으로 확인한다.
// 기준선을 바꿨으면 끝에 원래대로 되돌린다. 기록이 없으면(새 DB) 건너뛴다 — 기록은 scripts/eval_gate.py --save로만 생긴다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;

test('평가 게이트 — 기준선 숫자·층 합치기·판정 상세·기준선 지정이 기록과 같음', async ({ page }) => {
  test.setTimeout(90_000);
  const total = Number(psql('SELECT COUNT(*) FROM eval_gate_run'));
  test.skip(total === 0, '판정 기록 없음');
  const baseRaw = psql('SELECT id FROM eval_gate_run WHERE is_baseline');
  test.skip(!baseRaw, '기준선 없음 — 화면은 "기준선이 없습니다"');
  const baseId = Number(baseRaw);
  const s = JSON.parse(psql(`SELECT summary::text FROM eval_gate_run WHERE id = ${baseId}`));
  test.skip(!s.retrieval || !s.answers, '기준선에 검색·답 측정이 다 있어야 이 시나리오를 본다');
  // 이름 정확히 같은 줄 — 부분 문자열이면 "A"가 "A 재측정" 줄을 잡는다(리뷰 L7)
  const rowOf = (label: string) => page.getByRole('row').filter({ has: page.getByText(label, { exact: true }) }).first();
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

  // [답] — 정답률·라벨 분포, 목록 층이 한 줄(list·navigation이 따로 나오지 않음)
  await page.getByRole('button', { name: '답', exact: true }).click();
  await expect(page.getByText(`정답률 ${pct(s.answers.accuracy)}`)).toBeVisible();
  await expect(page.getByText(`오답 ${s.answers.labels['오답'] ?? 0}`)).toBeVisible();
  await expect(page.getByRole('row', { name: /^목록 / })).toHaveCount(1);
  await expect(page.getByRole('cell', { name: 'navigation' })).toHaveCount(0);
  const wrong = s.answers.retrieval_ok_answer_wrong.length;
  await expect(qidList('검색은 맞았는데 답이 틀림', wrong)).toBeVisible();

  // [판정 이력] — 줄 수 = 기록 수, 나빠진 문항이 가장 많은 판정을 펼쳐 저장된 qid 수와 비교
  await page.getByRole('button', { name: '판정 이력', exact: true }).click();
  await expect(page.locator('tbody > tr').filter({ hasText: /\d+\. \d+\./ })).toHaveCount(Math.min(total, 50));
  const [gid, label, downN] = psql(`SELECT id, label, jsonb_array_length(answers->'down_qids') FROM eval_gate_run
                                    WHERE kind = 'gate' AND answers IS NOT NULL ORDER BY 3 DESC, id LIMIT 1`).split('|');
  if (gid) {
    await rowOf(label).click();
    await expect(qidList('답 나빠짐', Number(downN))).toBeVisible();
    await expect(page.locator('tbody tbody tr').first()).toContainText('전체')   // 상세 표 첫 줄은 전체(jsonb 키 순서에 안 끌려감)
  }

  // 기준선 지정 — 통과한 다른 판정으로 옮겼다가 되돌린다(관리자만 버튼)
  const other = psql(`SELECT id, label FROM eval_gate_run WHERE NOT is_baseline AND verdict IS DISTINCT FROM 'fail' ORDER BY id DESC LIMIT 1`);
  if (other) {
    const [oid, olabel] = other.split('|');
    try {
      await rowOf(olabel).getByRole('button', { name: '기준선으로' }).click();
      await expect.poll(() => psql('SELECT id FROM eval_gate_run WHERE is_baseline')).toBe(oid);
      // 화면이 다시 불러왔나 — 버튼 글자 "기준선으로"가 아니라 버튼이 사라지고 별 표시가 붙었는지(리뷰 M2: 버튼 글자에 맞아 공짜 통과)
      await expect(rowOf(olabel)).toHaveCount(1)   // 줄을 못 찾으면 아래 "버튼 0개"가 공짜로 통과한다
      await expect(rowOf(olabel).getByRole('button', { name: '기준선으로' })).toHaveCount(0);
      await expect(rowOf(olabel).getByText('기준선', { exact: true })).toBeVisible();
      await page.getByRole('button', { name: '검색', exact: true }).click();
      await expect(page.getByText(`"${olabel}"`, { exact: false })).toBeVisible();   // [검색] 머리말도 새 기준선
      await page.getByRole('button', { name: '판정 이력', exact: true }).click();
      expect(psql('SELECT COUNT(*) FROM eval_gate_run WHERE is_baseline')).toBe('1');
    } finally {
      const t = await adminToken();
      const res = await fetch(`${API}/api/eval/gate-runs/${baseId}/baseline`, { method: 'POST', headers: { Authorization: `Bearer ${t}` } });
      expect(res.ok, '기준선 되돌리기 실패 — 수동으로 되돌릴 것').toBe(true);
    }
    expect(psql('SELECT id FROM eval_gate_run WHERE is_baseline')).toBe(String(baseId));
  }
});
