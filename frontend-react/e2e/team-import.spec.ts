import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(10/29 팀 적재 #98 — 시작은 지식 문서 › 등록 "공간·하위 전체 넣기", 검토는 [적재 검토] 탭): 분류 미리보기는 사람이 확정하기 전
// 마지막 관문이다 — 검토 필요를 대분류별로 묶어 묶음 위치로 한 번에 확정하고(기본 위치가 없으면 미리 채우지 않음), 줄마다 보류하고, 필수 표본을 판정해야
// [분류 확정 · 적재]가 켜지는지 실 dev 스택으로 본다.
// 컨플·LLM 없이 미리보기 상태 배치를 DB에 직접 만들고(적재는 안 함 — 원문 초안이 없어 확정하지 않는다), 끝나면 지운다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();

test('팀 적재 분류 미리보기 — 묶음 위치로 확정 · 줄 보류 · 표본 판정 뒤에만 확정 가능', async ({ page }) => {
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
    // 따로 메뉴(팀 적재)는 없다 — 지식 문서 › [적재 검토] 탭(할 일 수 = 분류 안 한 검토 필요 + 안 본 표본)
    await goAdmin(page, '지식', '지식 문서');
    await expect(page.getByRole('navigation', { name: '관리자 메뉴' }).getByRole('button', { name: '팀 적재' })).toHaveCount(0);
    await page.getByTestId('subtab-import').click();
    await expect(page.getByText('적재 배치')).toBeVisible();
    await expect(page.getByRole('button', { name: new RegExp(`^#${bid} .*할 일 7`) })).toBeVisible();   // 검토 필요 5 + 표본 2
    await page.getByRole('button', { name: new RegExp(`^#${bid} `) }).click();
    const confirmBtn = page.getByRole('button', { name: '분류 확정 · 적재' });
    await expect(confirmBtn).toBeDisabled();
    await expect(page.getByText('검토 필요 5건 — 분류하거나 보류')).toBeVisible();

    // 기본 위치가 없으면 미리 채우지 않는다 → 묶음 위치를 고르기 전엔 [확정]이 0건(꺼짐), 고르면 묶음의 5줄이 따라온다
    const group = page.getByTestId('review-group').first();
    await expect(group.getByTestId('review-row')).toHaveCount(0);   // 처음엔 접혀 있음(10/30)
    await group.getByTestId('group-toggle').click();
    await expect(group.getByTestId('group-confirm')).toHaveText('이 묶음 0건 확정');
    await expect(group.getByTestId('group-confirm')).toBeDisabled();
    await group.getByTestId('group-ns').getByRole('combobox').selectOption(String(common));
    await expect(group.getByTestId('group-confirm')).toHaveText('이 묶음 5건 확정');
    for (const r of await group.getByTestId('review-row').all()) await expect(r.getByRole('combobox')).toHaveValue(String(common));   // 일괄 = 모든 줄
    // 확정 전엔 저장 안 됨 — 줄 보류 2건(b1·b2)
    expect(psql(`SELECT count(*) FROM rag_import_page WHERE batch_id = ${bid} AND final_namespace_id IS NOT NULL AND status = 'review'`)).toBe('0');
    for (const pid of ['b1', 'b2']) {
      await group.getByTestId('review-row').filter({ hasText: `E2E ${pid}` }).getByRole('button', { name: '보류', exact: true }).click();
      await expect.poll(() => psql(`SELECT status FROM rag_import_page WHERE batch_id = ${bid} AND page_id = '${pid}'`)).toBe('held');
    }
    await expect(group.getByTestId('group-confirm')).toHaveText('이 묶음 3건 확정');
    await group.getByTestId('group-confirm').click();
    await expect.poll(() => psql(`SELECT count(*) FROM rag_import_page WHERE batch_id = ${bid} AND final_namespace_id = ${common} AND status = 'review'`)).toBe('3');

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

// 공간 기본 위치(10/29 v2.192): 등록 창구 안 "공간·하위 전체 넣기" 선택지 + 미리보기에서 기본 위치를 정하면 사람이 손대지 않은 업무 페이지만
// 기본 위치로(공간 기본값), 조직으로 보인 페이지는 검토 필요로 남고, 사람이 이미 분류한 페이지는 그대로 — DB 값으로 확인한다.
test('공간 기본 위치 — 등록 창구 세 갈래 · 기본 위치로 분류는 손대지 않은 업무 페이지만', async ({ page }) => {
  test.setTimeout(120_000);
  const row = psql(`SELECT t.id || '|' || c.id || '|' || d.id FROM ops_team t
      JOIN ops_namespace c ON c.kind = 'org_space' AND c.name = 'org:team:' || t.id
      JOIN ops_part p ON p.team_id = t.id JOIN ops_namespace_owner o ON o.part_id = p.id JOIN ops_namespace d ON d.id = o.namespace_id AND d.kind = 'domain'
      ORDER BY t.id DESC LIMIT 1`);
  test.skip(!row, '담당 업무가 있는 팀이 없음');
  const [team, common, biz] = row.split('|').map(Number);
  test.skip(psql(`SELECT count(*) FROM rag_import_batch WHERE team_id = ${team} AND status IN ('previewing','loading')`) !== '0', '진행 중 배치 있음');
  const bid = Number(psql(`INSERT INTO rag_import_batch (team_id, root_url, root_title, status, sampling_required, total_pages, done_pages, created_by_user_id)
    VALUES (${team}, 'https://confl.example/pages/viewpage.action?pageId=2', 'E2E 기본 위치', 'preview', TRUE, 5, 5, (SELECT id FROM ops_user WHERE username = 'admin'))
    RETURNING id`).split('\n')[0]);
  const ins = (pid: string, cols: string, vals: string) => psql(
    `INSERT INTO rag_import_page (batch_id, page_id, title, bundle, status, chunk_count, ${cols}) VALUES (${bid}, '${pid}', 'E2E ${pid}', '묶음B', 'review', 1, ${vals})`);
  try {
    for (const i of [1, 2, 3]) ins(`r${i}`, 'reason', `'low_confidence'`);                                          // LLM 판단 못 함
    ins('o1', 'reason, ai_namespace_id', `'low_confidence', ${common}`);                                             // 조직으로 보임
    ins('h1', 'reason, ai_namespace_id, final_namespace_id, decided_by_user_id', `'low_confidence', ${common}, ${common}, (SELECT id FROM ops_user WHERE username = 'admin')`);   // 사람이 이미 분류

    await loginAdmin(page);
    await goAdmin(page, '지식', '지식 문서');
    await page.getByRole('button', { name: '지식 등록', exact: true }).click();
    // 등록 창구 세 갈래 — PAT가 있으면 보인다
    await page.getByText('URL / Confluence', { exact: true }).click();
    await page.getByPlaceholder('https://confl.sinc.co.kr/display/SPACE/페이지제목').fill('https://confl.sinc.co.kr/display/GIFT');
    const space = page.getByRole('radio', { name: /공간·하위 전체 넣기/ });
    if (await space.count()) {
      await space.click();
      await expect(page.getByText('이 공간 기본 위치').first()).toBeVisible();
      await expect(page.getByRole('button', { name: '분류 미리보기 만들기' })).toBeVisible();   // 누르지 않는다(컨플을 받는다)
    }

    await page.getByTestId('subtab-import').click();
    await page.getByRole('button', { name: new RegExp(`^#${bid} `) }).click();
    const ctl = page.locator('div', { has: page.getByRole('button', { name: '기본 위치로 분류' }) }).last();
    await ctl.getByRole('combobox').selectOption(String(biz));
    await ctl.getByRole('button', { name: '기본 위치로 분류' }).click();
    await page.getByRole('button', { name: '확인' }).click();   // 다시 분류 확인
    await expect(page.getByText('다시 분류했어요')).toBeVisible();
    await page.getByRole('button', { name: '확인' }).click();
    const st = (pid: string) => psql(`SELECT status || '|' || coalesce(reason,'') || '|' || coalesce(final_namespace_id::text,'') || '|' || coalesce(assigned_by,'')
      FROM rag_import_page WHERE batch_id = ${bid} AND page_id = '${pid}'`);
    for (const i of [1, 2, 3]) expect(st(`r${i}`)).toBe(`auto||${biz}|space_default`);
    expect(st('o1')).toBe('review|org_like||');
    expect(st('h1')).toBe(`review|low_confidence|${common}|`);   // 사람이 분류한 건 그대로
    expect(psql(`SELECT count(*) FROM rag_import_page WHERE batch_id = ${bid} AND sample_state = 'pending'`)).toBe('3');
    expect(psql(`SELECT default_namespace_id FROM rag_import_batch WHERE id = ${bid}`)).toBe(String(biz));

    // 검토 필요 줄: 묶음 위치는 기본 위치로 미리 채움(o1 = 기본값) · 사람이 분류한 h1은 "분류됨" 그대로 → 확정할 것 1건
    const group = page.getByTestId('review-group').first();
    const o1 = group.getByTestId('review-row').filter({ hasText: 'E2E o1' });
    const h1 = group.getByTestId('review-row').filter({ hasText: 'E2E h1' });
    await group.getByTestId('group-toggle').click();
    await expect(o1.getByText('기본값', { exact: true })).toBeVisible();
    await expect(h1.getByText('분류됨', { exact: true })).toBeVisible();
    await expect(group.getByTestId('group-confirm')).toHaveText('이 묶음 1건 확정');
    // 줄마다 다르면 묶음 위치는 "줄마다 다름"(빈 값) — 고르면 모든 줄이 따라간다(저장된 h1도), 되돌리면 다시 h1은 분류됨
    const groupSel = group.getByTestId('group-ns').getByRole('combobox');
    await expect(groupSel).toHaveValue('');
    await expect(groupSel.locator('option[value=""]')).toHaveText('— 줄마다 다름 —');
    await expect(groupSel.locator('option[value=""]')).toBeDisabled();   // 빈 값은 고를 수 없음(저장 동작 없음)
    await expect(o1.locator(`option[value="${common}"]`)).toContainText('AI 판단');   // 펼친 목록에 AI가 처음 고른 곳 표시
    await expect(o1.locator(`option[value="${biz}"]`)).not.toContainText('AI 판단');
    await groupSel.selectOption(String(biz));
    await expect(h1.getByText('바꿈', { exact: true })).toBeVisible();
    await expect(group.getByTestId('group-confirm')).toHaveText('이 묶음 2건 확정');
    // [AI 판단으로] → 두 줄 다 AI가 고른 팀 공통(h1은 저장값과 같아 분류됨) → 확정 1건(o1) → o1 = 팀 공통, h1 그대로
    await group.getByTestId('group-to-ai').click();
    await expect(group.getByTestId('group-to-ai')).toBeDisabled();   // 이미 AI 판단 그대로
    await expect(groupSel).toHaveValue(String(common));
    await expect(h1.getByText('분류됨', { exact: true })).toBeVisible();
    await expect(o1.getByText('바꿈', { exact: true })).toBeVisible();
    await expect(group.getByTestId('group-confirm')).toHaveText('이 묶음 1건 확정');
    await group.getByTestId('group-confirm').click();
    // 확정 뒤 새 값이 올 때까지 기본값으로 되돌아 보이거나 다시 눌리지 않는다(리뷰 10/30 M1)
    await expect(o1.getByText('분류됨', { exact: true })).toBeVisible();
    await expect(o1.getByRole('combobox')).toHaveValue(String(common));
    await expect(group.getByTestId('group-confirm')).toHaveText('이 묶음 0건 확정');
    await expect(group.getByTestId('group-confirm')).toBeDisabled();
    await expect.poll(() => st('o1')).toBe(`review|org_like|${common}|`);
    expect(st('h1')).toBe(`review|low_confidence|${common}|`);
  } finally {
    psql(`DELETE FROM rag_import_batch WHERE id = ${bid}`);
  }
});
