import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { API, adminToken, apiFetch, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(10/30 사용자 "실험이 끝났는데 '실험 중'? 진행 중이 아니라 종결 아닌가" → 개선 후보 한 목록 + 상태 필터):
// ① 화면 단계 = 서버 phase(상태 + 마지막 실험 + 반영 판정) — 측정이 반영 불가면 '실패'로 자동 종결되고(사람 손 없음) 기본 [진행 중]에서 빠져 [실패]에 보인다.
// ② 칩 숫자 = 그 필터에 보이는 줄 수 = API(board) · [진행 중] 숫자 = 맨 위 "결정할 후보".
// 진짜 측정은 돌리지 않는다: 자동 실험이 안 되는 후보 하나에 실패 측정을 붙여 close_failed와 같은 종결을 API로 일으킨다. 끝에 되돌린다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();

async function call(path: string, body?: unknown) {
  const t = await adminToken();
  return apiFetch(`${API}/api${path}`, {
    method: body === undefined ? 'GET' : 'POST', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

type Row = { id: number; key: string; phase: string; status: string; recommend: string };
const isOpen = (c: Row) => ['running', 'error', 'review'].includes(c.phase) || (c.phase === 'proposed' && c.recommend !== 'reject');

test('개선 후보 — 칩 숫자 = 보이는 줄 = API, [진행 중] = 결정할 후보, 반영 불가 측정은 실패로 자동 종결', async ({ page }) => {
  test.setTimeout(120_000);
  const board = await (await call('/eval/improvements/board?axis=policy')).json();
  test.skip(!board.run_id, '진단 없음');
  const c: Row | undefined = board.candidates.find((x: Row & { change: unknown }) => x.status === 'proposed' && !x.change && x.recommend !== 'reject');
  test.skip(!c, '자동 실험이 안 되는 결정 전 후보 없음');
  const base = psql(`SELECT id FROM eval_gate_run WHERE is_baseline AND axis = 'policy'`);
  test.skip(!base, '기준선 없음');
  let gid = '';
  try {
    await loginAdmin(page);
    await goAdmin(page, '평가 게이트');
    const sec = page.getByTestId('gate-in-progress');
    const open = board.candidates.filter(isOpen);
    await expect(sec.getByTestId('board-filter-open')).toContainText(String(open.length));
    await expect(sec.locator('[data-testid^="inprogress-"]')).toHaveCount(open.length);
    await expect(page.getByTestId('card-open')).toContainText(String(open.length));
    await sec.getByTestId('board-filter-all').click();
    await expect(sec.locator('[data-testid^="inprogress-"]')).toHaveCount(board.candidates.length);

    // 실험 시작(승인) → 실패 측정 연결 → 실행기가 끝에 부르는 종결(close_failed)을 [다시 실행] 없이 재현: 재시작 없이 API로는 못 부르므로 DB에 같은 결과를 만든다
    await call(`/eval/candidates/${c!.id}/review`, { status: 'approved' });
    gid = psql(`INSERT INTO eval_gate_run (created_by, label, kind, golden_version, verdict, retrieval, answers, summary, judge_meta)
                SELECT 'e2e', 'E2E 실패 측정', 'gate', golden_version, 'fail', retrieval, NULL, summary, judge_meta
                FROM eval_gate_run WHERE id = ${base} RETURNING id`).split(/\s+/)[0];
    psql(`UPDATE eval_candidate SET measured_gate_run_ids = array_append(coalesce(measured_gate_run_ids, '{}'), ${gid}) WHERE id = ${c!.id}`);
    // 종결 전에도 서버 phase는 이미 '실패'(반영 불가 측정) — 화면이 '실험 중'으로 남지 않는다
    const mid = (await (await call('/eval/improvements/board?axis=policy')).json()).candidates.find((x: Row) => x.id === c!.id);
    expect(mid.phase).toBe('failed');
    psql(`UPDATE eval_candidate SET status = 'failed', review_reason = '자동 종결 — E2E', reviewed_by_user_id = NULL, reviewed_at = NOW() WHERE id = ${c!.id}`);

    await page.reload();
    await goAdmin(page, '평가 게이트');
    await expect(sec.getByTestId(`inprogress-${c!.key}`)).toHaveCount(0);           // 기본 [진행 중]에서 빠짐
    await sec.getByTestId('board-filter-failed').click();
    const line = sec.getByTestId(`inprogress-${c!.key}`);
    await expect(line).toHaveAttribute('data-phase', 'failed');
    await expect(line).toContainText(`측정 #${gid}`);
    await line.click();
    const card = page.getByRole('dialog').getByTestId(`candidate-${c!.key}`);
    await expect(card.getByTestId('cand-status')).toHaveText('실패');
    await expect(card.getByTestId('cand-reason')).toContainText('종결 사유(자동)');
    await expect(card.getByRole('button', { name: '안 하기…' })).toHaveCount(0);   // 이미 종결 — 사람 손 안 씀
    await expect(card.getByRole('button', { name: '운영에 반영했어요…' })).toHaveCount(0);
  } finally {
    psql(`UPDATE eval_candidate SET status = 'approved' WHERE id = ${c?.id ?? 0} AND status = 'failed'`);
    await call(`/eval/candidates/${c?.id ?? 0}/review`, { status: 'proposed' });
    if (gid) {
      psql(`UPDATE eval_candidate SET measured_gate_run_ids = array_remove(measured_gate_run_ids, ${gid}::bigint) WHERE ${gid} = ANY(measured_gate_run_ids)`);
      psql(`DELETE FROM eval_gate_run WHERE id = ${gid}`);
    }
  }
});
