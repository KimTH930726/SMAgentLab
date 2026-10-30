import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { API, adminToken, apiFetch, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(10/30 실험 실행기, 사용자 "실험해 보기를 누르면 실제로 돌리고 측정까지 — 결과를 보고 운영 반영을 정한다"):
// 후보 카드가 실험 상태를 그대로 보이는지 — 자동 실험이 안 되는 후보는 이유("개발 작업 필요"), 도는 중이면 단계·n/m, 실패면 이유 + [다시 돌리기].
// 진짜 측정(몇 분~몇 시간)은 돌리지 않는다: 자동 실험이 안 되는 후보만 승인하고, 진행·실패 상태는 작업 줄을 직접 넣어 본다. 끝에 되돌린다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();

async function call(path: string, body?: unknown) {
  const t = await adminToken();
  return apiFetch(`${API}/api${path}`, {
    method: body === undefined ? 'GET' : 'POST', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

test('실험 상태 — 자동 실험 불가 이유 · 도는 중(단계·n/m) · 실패(이유 + 다시 실행)', async ({ page }) => {
  test.setTimeout(90_000);
  const imp = await (await call('/eval/improvements/latest?axis=policy')).json();
  test.skip(!imp, '진단 없음');
  const c = imp.candidates.find((x: { status: string; change: unknown; recommend: string }) => x.status === 'proposed' && !x.change && x.recommend !== 'reject');
  test.skip(!c, '자동 실험이 안 되는 결정 전 후보 없음');
  try {
    const r = await (await call(`/eval/candidates/${c.id}/review`, { status: 'approved' })).json();
    expect(r.experiment).toBeNull();                                         // 바꿀 것이 글뿐 — 작업을 만들지 않는다
    expect(r.experiment_note).toContain('개발 작업');
    expect(psql(`SELECT count(*) FROM eval_experiment_job WHERE candidate_id = ${c.id}`)).toBe('0');
    expect((await call(`/eval/candidates/${c.id}/experiment`, {})).status).toBe(409);   // 다시 돌리기도 같은 이유로 막힘

    await loginAdmin(page);
    await goAdmin(page, '품질', '평가 게이트');
    const open = async () => {
      await page.getByTestId('gate-in-progress').getByTestId(`inprogress-${c.key}`).click();
      return page.getByRole('dialog').getByTestId(`candidate-${c.key}`);
    };
    let card = await open();
    await expect(card.getByTestId('cand-exp-manual')).toContainText('자동 실험 불가');

    // 도는 중 — 단계·진행(화면은 5초마다 다시 불러옴)
    const jid = psql(`INSERT INTO eval_experiment_job (candidate_id, status, stage, done, total, started_at)
                      VALUES (${c.id}, 'running', '2/3 바꾼 설정 검색', 40, 88, NOW()) RETURNING id`).split(/\s+/)[0];
    await page.keyboard.press('Escape');
    await page.reload();
    await goAdmin(page, '품질', '평가 게이트');
    card = await open();
    await expect(card.getByTestId('cand-exp-running')).toContainText('실험 돌리는 중 — 2/3 바꾼 설정 검색');
    await expect(card.getByTestId('cand-exp-running')).toContainText('40/88');
    // 실패 — 이유 + 다시 돌리기
    psql(`UPDATE eval_experiment_job SET status = 'failed', error = '바꾼 설정 검색 실패(종료 코드 1): 테스트', finished_at = NOW() WHERE id = ${jid}`);
    await expect(card.getByTestId('cand-exp-failed')).toContainText('테스트', { timeout: 15_000 });   // 도는 중이라 다시 불러오다 실패를 봄
    await expect(card.getByRole('button', { name: '다시 실행' })).toHaveCount(0);   // 자동 실험이 안 되는 후보 — 다시 실행 없음(누르면 409였다, 리뷰 L9)
  } finally {
    psql(`DELETE FROM eval_experiment_job WHERE candidate_id = ${c.id}`);
    await call(`/eval/candidates/${c.id}/review`, { status: 'proposed' });
  }
});
