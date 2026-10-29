import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(10/29 팀 적재 #98 — 지식 문서 › 등록 안 "공간·하위 전체 넣기"): 분류 미리보기는 사람이 확정하기 전 마지막 관문이다 — 검토 필요를 LLM이 고른 곳별로 묶어
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
    // 등록 창구는 하나(10/29) — 지식 문서 › 지식 등록 아래 "적재 배치"에 있다. 따로 메뉴(팀 적재)는 없다
    await goAdmin(page, '지식', '지식 문서');
    await expect(page.getByRole('navigation', { name: '관리자 메뉴' }).getByRole('button', { name: '팀 적재' })).toHaveCount(0);
    await page.getByRole('button', { name: '지식 등록', exact: true }).click();
    await expect(page.getByText('적재 배치')).toBeVisible();
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
  } finally {
    psql(`DELETE FROM rag_import_batch WHERE id = ${bid}`);
  }
});
