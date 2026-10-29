import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(10/29 팀 적재 #98): 분류 미리보기는 사람이 확정하기 전 마지막 관문이다 — 검토 필요를 LLM이 고른 곳별로 묶어
// 일괄 분류하고(넣을 곳은 미리 골라 두지 않음 — 사람이 고른다), 보류하고, 필수 표본을 판정해야 [분류 확정 · 적재]가 켜지는지 실 dev 스택으로 본다.
// 컨플·LLM 없이 미리보기 상태 배치를 DB에 직접 만들고(적재는 안 함 — 원문 초안이 없어 확정하지 않는다), 끝나면 지운다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();

test('팀 적재 분류 미리보기 — 묶음 일괄 분류 · 보류 · 표본 판정 뒤에만 확정 가능', async ({ page }) => {
  test.setTimeout(120_000);
  const team = psql(`SELECT t.id FROM ops_team t JOIN ops_namespace n ON n.kind = 'org_space' AND n.name = 'org:team:' || t.id ORDER BY t.id DESC LIMIT 1`);
  test.skip(!team, '팀 공통 조직 컬렉션이 있는 팀이 없음');
  const common = Number(psql(`SELECT id FROM ops_namespace WHERE name = 'org:team:${team}'`));
  const busy = psql(`SELECT count(*) FROM rag_import_batch WHERE team_id = ${team} AND status IN ('previewing','loading')`);
  test.skip(busy !== '0', '그 팀에 진행 중 배치가 있음');
  const bid = Number(psql(`INSERT INTO rag_import_batch (team_id, root_url, root_title, status, sampling_required, total_pages, done_pages, created_by_user_id)
    VALUES (${team}, 'https://confl.example/pages/viewpage.action?pageId=1', 'E2E 팀 적재', 'preview', TRUE, 7, 7, (SELECT id FROM ops_user WHERE username = 'admin'))
    RETURNING id`).split('\n')[0]);
  const page_ = (pid: string, status: string, extra: string) => psql(
    `INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, status, chunk_count, ${extra.split('|')[0]}) VALUES (${bid}, '${pid}', 'E2E ${pid}', '묶음A', 1, '${status}', 1, ${extra.split('|')[1]})`);
  try {
    for (const i of [1, 2, 3]) page_(`a${i}`, 'review', `reason, ai_namespace_id, proposed_namespace_id|'low_confidence', ${common}, ${common}`);   // LLM이 고른 곳 = 팀 공통
    for (const i of [1, 2]) page_(`b${i}`, 'review', `reason, ai_namespace_id, proposed_namespace_id|'low_confidence', NULL, NULL`);              // LLM 판단 못 함
    for (const i of [1, 2]) page_(`c${i}`, 'auto', `ai_namespace_id, final_namespace_id, confident, sample_state|${common}, ${common}, TRUE, 'pending'`);

    await loginAdmin(page);
    await goAdmin(page, '지식', '팀 적재');
    await page.getByRole('button', { name: new RegExp(`^#${bid} `) }).click();
    const confirmBtn = page.getByRole('button', { name: '분류 확정 · 적재' });
    await expect(confirmBtn).toBeDisabled();
    await expect(page.getByText('검토 필요 5건 — 분류하거나 보류')).toBeVisible();

    // 넣을 곳을 미리 골라 두지 않는다 → 고르기 전엔 분류 버튼이 꺼져 있고, 고르면 묶음 전체를 한 번에
    const groupA = page.locator('div.rounded-lg', { has: page.getByRole('button', { name: '미분류 3건 분류' }) });
    await expect(page.getByRole('button', { name: '미분류 3건 분류' })).toBeDisabled();
    await groupA.getByRole('combobox').selectOption(String(common));
    await page.getByRole('button', { name: '미분류 3건 분류' }).click();
    await expect.poll(() => psql(`SELECT count(*) FROM rag_import_page WHERE batch_id = ${bid} AND final_namespace_id = ${common} AND status = 'review'`)).toBe('3');
    // 다른 묶음은 그대로 빈 칸 → 보류
    await expect(page.getByRole('button', { name: '미분류 2건 분류' })).toBeDisabled();
    await page.getByRole('button', { name: '보류', exact: true }).first().click();
    await expect.poll(() => psql(`SELECT count(*) FROM rag_import_page WHERE batch_id = ${bid} AND status = 'held'`)).toBe('2');

    // 필수 표본 2건이 남아 아직 확정 불가 → 표본 검토에서 맞음 두 번
    await expect(page.getByText(/표본 검토 2건 남음/)).toBeVisible();
    await expect(confirmBtn).toBeDisabled();
    await page.getByRole('button', { name: '표본 검토', exact: true }).click();
    for (let i = 0; i < 2; i++) await page.getByRole('button', { name: '맞음', exact: true }).first().click();
    await expect.poll(() => psql(`SELECT count(*) FROM rag_import_page WHERE batch_id = ${bid} AND sample_state = 'ok'`)).toBe('2');
    await expect(confirmBtn).toBeEnabled();
  } finally {
    psql(`DELETE FROM rag_import_batch WHERE id = ${bid}`);
  }
});
