import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';
import { createSystem, dropSystem } from './v0-fixture';

// 이 테스트가 존재하는 이유(10/29 팀 적재 #98 — 시작은 운영 지식 › 등록 "공간·하위 전체 넣기", 검토는 [적재 검토] 탭): 분류 미리보기는 사람이 확정하기 전
// 마지막 관문이다 — 검토 필요를 대분류별로 묶어 묶음 위치로 한 번에 확정하고(기본 위치가 없으면 미리 채우지 않음), 줄마다 보류하고, 필수 표본을 판정해야
// [분류 확정 · 적재]가 켜지는지 실 dev 스택으로 본다.
// 컨플·LLM 없이 미리보기 상태 배치를 DB에 직접 만들고(적재는 안 함 — 원문 초안이 없어 확정하지 않는다), 끝나면 지운다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
// "AI가 고른 다른 곳" = 임시 시스템 컬렉션(11/02 — 예전엔 팀 공통 조직 컬렉션, 조직 컬렉션 폐지). 테스트마다 만들고 끝나면 지운다.
const SYS = 'E2E적재시스템';

test('팀 적재 분류 미리보기 — 묶음 위치로 확정 · 줄 보류 · 표본 판정 뒤에만 확정 가능', async ({ page }) => {
  test.setTimeout(120_000);
  const team = psql(`SELECT id FROM ops_team ORDER BY id DESC LIMIT 1`);
  test.skip(!team, '팀이 없음');
  const busy = psql(`SELECT count(*) FROM rag_import_batch WHERE team_id = ${team} AND status IN ('previewing','loading')`);
  test.skip(busy !== '0', '그 팀에 진행 중 배치가 있음');
  const bid = Number(psql(`INSERT INTO rag_import_batch (team_id, root_url, root_title, status, sampling_required, total_pages, done_pages, created_by_user_id)
    VALUES (${team}, 'https://confl.example/pages/viewpage.action?pageId=1', 'E2E 팀 적재', 'preview', TRUE, 7, 7, (SELECT id FROM ops_user WHERE username = 'admin'))
    RETURNING id`).split('\n')[0]);
  const page_ = (pid: string, status: string, extra: string) => psql(
    `INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, status, chunk_count, ${extra.split('|')[0]}) VALUES (${bid}, '${pid}', 'E2E ${pid}', '묶음A', 1, '${status}', 1, ${extra.split('|')[1]})`);
  try {
    const common = createSystem(SYS);
    for (const i of [1, 2, 3]) page_(`a${i}`, 'review', `reason, ai_namespace_id, proposed_namespace_id|'low_confidence', ${common}, ${common}`);   // LLM이 고른 곳 = 시스템
    for (const i of [1, 2]) page_(`b${i}`, 'review', `reason, ai_namespace_id, proposed_namespace_id|'low_confidence', NULL, NULL`);              // LLM 판단 못 함
    for (const i of [1, 2]) page_(`c${i}`, 'auto', `ai_namespace_id, final_namespace_id, confident, sample_state|${common}, ${common}, TRUE, 'pending'`);

    await loginAdmin(page);
    // 따로 메뉴(팀 적재)는 없다 — 운영 지식 › [적재 검토] 탭(할 일 수 = 분류 안 한 검토 필요 + 안 본 표본)
    await expect(page.getByRole('navigation', { name: '관리자 메뉴' }).getByRole('button', { name: '팀 적재' })).toHaveCount(0);
    await goAdmin(page, '소스 연결');   // 10/30 운영 지식 [적재 검토] 탭 → 지식 › 소스 연결
    await expect(page.getByLabel('배치')).toBeVisible();
    await expect(page.getByLabel('배치').locator(`option[value="${bid}"]`)).toHaveText(/할 일 1/);   // 연결할 묶음 수(10/30 묶음 단위 연결) — 모두 묶음A
    await page.getByLabel('배치').selectOption(String(bid));   // 배치가 여럿이면 선택 상자(10/30)
    await page.getByTestId('preview-more').click();   // 상세는 [자세히] 아래(10/30 최종 검수)
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
    dropSystem(SYS);
  }
});

// 공간 기본 위치(10/29 v2.192): 등록 창구 안 "공간·하위 전체 넣기" 선택지 + 미리보기에서 기본 위치를 정하면 사람이 손대지 않은 업무 페이지만
// 기본 위치로(공간 기본값), AI가 확신하는데 기본 위치와 다른 곳(시스템)은 검토 필요로 남고, 사람이 이미 분류한 페이지는 그대로 — DB 값으로 확인한다.
// (예전 "조직으로 보임" 줄은 조직 컬렉션 폐지(11/02)로 "기본 위치와 다름" 줄로 바꿨다)
test('공간 기본 위치 — 등록 창구 세 갈래 · 기본 위치로 분류는 손대지 않은 업무 페이지만', async ({ page }) => {
  test.setTimeout(120_000);
  const row = psql(`SELECT t.id || '|' || d.id FROM ops_team t
      JOIN ops_part p ON p.team_id = t.id JOIN ops_namespace_owner o ON o.part_id = p.id
      JOIN ops_namespace d ON d.id = o.namespace_id AND d.kind = 'domain' AND d.area = 'system' AND d.parent_id IS NULL   -- 운영 지식은 시스템에만(11/02)
      ORDER BY t.id DESC LIMIT 1`);
  test.skip(!row, '담당 컬렉션이 있는 팀이 없음');
  const [team, biz] = row.split('|').map(Number);
  test.skip(psql(`SELECT count(*) FROM rag_import_batch WHERE team_id = ${team} AND status IN ('previewing','loading')`) !== '0', '진행 중 배치 있음');
  const bid = Number(psql(`INSERT INTO rag_import_batch (team_id, root_url, root_title, status, sampling_required, total_pages, done_pages, created_by_user_id)
    VALUES (${team}, 'https://confl.example/pages/viewpage.action?pageId=2', 'E2E 기본 위치', 'preview', TRUE, 5, 5, (SELECT id FROM ops_user WHERE username = 'admin'))
    RETURNING id`).split('\n')[0]);
  const ins = (pid: string, cols: string, vals: string) => psql(
    `INSERT INTO rag_import_page (batch_id, page_id, title, bundle, status, chunk_count, ${cols}) VALUES (${bid}, '${pid}', 'E2E ${pid}', '묶음B', 'review', 1, ${vals})`);
  try {
    const common = createSystem(SYS);
    for (const i of [1, 2, 3]) ins(`r${i}`, 'reason', `'low_confidence'`);                                          // LLM 판단 못 함
    ins('o1', 'reason, ai_namespace_id, confident', `'low_confidence', ${common}, TRUE`);                             // AI 확신 = 시스템(기본 위치와 다름)
    ins('h1', 'reason, ai_namespace_id, final_namespace_id, decided_by_user_id', `'low_confidence', ${common}, ${common}, (SELECT id FROM ops_user WHERE username = 'admin')`);   // 사람이 이미 분류

    await loginAdmin(page);
    await goAdmin(page, '운영 지식');
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

    await goAdmin(page, '소스 연결');
    await page.getByLabel('배치').selectOption(String(bid));   // 배치가 여럿이면 선택 상자(10/30)
    await page.getByTestId('preview-more').click();   // 상세는 [자세히] 아래(10/30 최종 검수)
    const ctl = page.locator('div', { has: page.getByRole('button', { name: '기본 위치로 분류' }) }).last();
    await ctl.getByRole('combobox').selectOption(String(biz));
    await ctl.getByRole('button', { name: '기본 위치로 분류' }).click();
    await page.getByRole('button', { name: '확인' }).click();   // 다시 분류 확인
    await expect(page.getByText('다시 분류했어요')).toBeVisible();
    await page.getByRole('button', { name: '확인' }).click();
    const st = (pid: string) => psql(`SELECT status || '|' || coalesce(reason,'') || '|' || coalesce(final_namespace_id::text,'') || '|' || coalesce(assigned_by,'')
      FROM rag_import_page WHERE batch_id = ${bid} AND page_id = '${pid}'`);
    for (const i of [1, 2, 3]) expect(st(`r${i}`)).toBe(`auto||${biz}|space_default`);
    expect(st('o1')).toBe('review|default_conflict||');
    expect(st('h1')).toBe(`review|low_confidence|${common}|`);   // 사람이 분류한 건 그대로
    expect(psql(`SELECT count(*) FROM rag_import_page WHERE batch_id = ${bid} AND sample_state = 'pending'`)).toBe('3');
    expect(psql(`SELECT default_namespace_id FROM rag_import_batch WHERE id = ${bid}`)).toBe(String(biz));

    // 검토 필요 줄: 묶음 위치는 기본 위치로 미리 채움(o1 = 기본값) · 사람이 분류한 h1은 "분류됨" 그대로 → 확정할 것 1건
    const group = page.getByTestId('review-group').first();
    const o1 = group.getByTestId('review-row').filter({ hasText: 'E2E o1' });
    const h1 = group.getByTestId('review-row').filter({ hasText: 'E2E h1' });
    await group.getByTestId('group-toggle').click();
    await expect(o1.getByText('기본값 · 확정 전', { exact: true })).toBeVisible();
    await expect(h1.getByText('저장됨', { exact: true })).toBeVisible();
    await expect(group.getByTestId('group-confirm')).toHaveText('이 묶음 1건 확정');
    // 줄마다 다르면 묶음 위치는 "줄마다 다름"(빈 값) — 고르면 모든 줄이 따라간다(저장된 h1도), 되돌리면 다시 h1은 분류됨
    const groupSel = group.getByTestId('group-ns').getByRole('combobox');
    await expect(groupSel).toHaveValue('');
    await expect(groupSel.locator('option[value=""]')).toHaveText('— 줄마다 다름 —');
    await expect(groupSel.locator('option[value=""]')).toBeDisabled();   // 빈 값은 고를 수 없음(저장 동작 없음)
    await expect(o1.locator(`option[value="${common}"]`)).toContainText('AI 판단');   // 펼친 목록에 AI가 처음 고른 곳 표시
    await expect(o1.locator(`option[value="${biz}"]`)).not.toContainText('AI 판단');
    await groupSel.selectOption(String(biz));
    await expect(h1.getByText('확정 전', { exact: true })).toBeVisible();
    await expect(group.getByTestId('group-confirm')).toHaveText('이 묶음 2건 확정');
    // [AI 판단으로] → 두 줄 다 AI가 고른 시스템(h1은 저장값과 같아 분류됨) → 확정 1건(o1) → o1 = 시스템, h1 그대로
    await group.getByTestId('group-to-ai').click();
    await expect(group.getByTestId('group-to-ai')).toBeDisabled();   // 이미 AI 판단 그대로
    await expect(groupSel).toHaveValue(String(common));
    await expect(h1.getByText('저장됨', { exact: true })).toBeVisible();
    await expect(o1.getByText('확정 전', { exact: true })).toBeVisible();
    await expect(group.getByTestId('group-confirm')).toHaveText('이 묶음 1건 확정');
    await group.getByTestId('group-confirm').click();
    // 확정 뒤 새 값이 올 때까지 기본값으로 되돌아 보이거나 다시 눌리지 않는다(리뷰 10/30 M1)
    await expect(o1.getByText('저장됨', { exact: true })).toBeVisible();
    await expect(o1.getByRole('combobox')).toHaveValue(String(common));
    await expect(group.getByTestId('group-confirm')).toHaveText('이 묶음 0건 확정');
    await expect(group.getByTestId('group-confirm')).toBeDisabled();
    await expect.poll(() => st('o1')).toBe(`review|default_conflict|${common}|`);
    expect(st('h1')).toBe(`review|low_confidence|${common}|`);
  } finally {
    psql(`DELETE FROM rag_import_batch WHERE id = ${bid}`);
    dropSystem(SYS);
  }
});


// 묶음 단위 연결(10/30 사용자 "업계 수집기처럼 — 사람은 가장 단순한 화면에서 단순한 검수만"): 묶음(1단계 상위 페이지)마다 컬렉션 하나,
// 성격이 다른 하위 갈래는 예외 줄. [연결 · 적재] = 규칙 저장 + 전 페이지 정함 + 적재(표본·페이지 검토 없음). 적재가 실제로 돌지 않게 응답만 확인하고 끝에 지운다.
test('묶음 연결 — 묶음 줄·하위 예외 미리 채움, 연결하면 규칙대로 전 페이지가 정해지고 적재 시작', async ({ page }) => {
  test.setTimeout(120_000);
  const row = psql(`SELECT t.id || '|' || d.id FROM ops_team t
      JOIN ops_part p ON p.team_id = t.id JOIN ops_namespace_owner o ON o.part_id = p.id
      JOIN ops_namespace d ON d.id = o.namespace_id AND d.kind = 'domain' AND d.area = 'system' AND d.parent_id IS NULL   -- 운영 지식은 시스템에만(11/02)
      ORDER BY t.id DESC LIMIT 1`);
  test.skip(!row, '담당 컬렉션이 있는 팀이 없음');
  const [team, biz] = row.split('|').map(Number);
  test.skip(psql(`SELECT count(*) FROM rag_import_batch WHERE team_id = ${team} AND status IN ('previewing','loading')`) !== '0', '진행 중 배치 있음');
  const bid = Number(psql(`INSERT INTO rag_import_batch (team_id, root_url, root_title, status, sampling_required, total_pages, done_pages, created_by_user_id, default_namespace_id)
    VALUES (${team}, 'https://confl.example/pages/viewpage.action?pageId=4', 'E2E 묶음 연결', 'preview', TRUE, 18, 18, (SELECT id FROM ops_user WHERE username = 'admin'), ${biz})
    RETURNING id`).split(/\s+/)[0]);
  // 묶음 A: 업무 4 + 하위 갈래 "A-도구"(시스템 3, 이웃 비교 있음) → 예외 / 묶음 B: 업무 2
  // 묶음 C: 업무 4 + 갈래 "C-이력"(AI만 시스템 3, 이웃 비교 없음 — 첫 적재처럼) → 예외로 띄우지 않음(11/02)
  const ins = (pid: string, bundle: string, depth: number, path: string, status: string, fin: number | null, ai: number | null = fin, nb = false) => psql(
    `INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, path, status, chunk_count, final_namespace_id, ai_namespace_id, reason, signals)
     VALUES (${bid}, '${pid}', '${pid}', '${bundle}', ${depth}, '${path}', '${status}', 1, ${fin ?? 'NULL'}, ${ai ?? 'NULL'}, ${status === 'review' ? "'low_confidence'" : 'NULL'},
             ${nb ? `'{"neighbors": [[${ai}, 0.8]]}'` : "'{}'"})`);
  const st = (pid: string) => psql(`SELECT status || '|' || coalesce(final_namespace_id::text,'') FROM rag_import_page WHERE batch_id = ${bid} AND page_id = '${pid}'`);
  try {
    const common = createSystem(SYS);
    ins('A', 'A', 1, '', 'auto', biz);
    for (const i of [1, 2, 3]) ins(`a${i}`, 'A', 2, 'A', 'auto', biz);
    ins('A-도구', 'A', 2, 'A', 'review', common, common, true);
    for (const i of [1, 2]) ins(`t${i}`, 'A', 3, 'A › A-도구', 'review', common, common, true);
    ins('B', 'B', 1, '', 'auto', biz);
    ins('b1', 'B', 2, 'B', 'review', null);                          // 미분류 — 묶음 값으로 따라감
    for (const i of [1, 2, 3, 4]) ins(`c${i}`, 'C', i === 1 ? 1 : 2, i === 1 ? '' : 'C', 'auto', biz);
    ins('C-이력', 'C', 2, 'C', 'review', null, common);
    for (const i of [1, 2]) ins(`h${i}`, 'C', 3, 'C › C-이력', 'review', null, common);
    // 묶음 D: AI가 "대상 아님"(행정·친목 — IT 운영 지식 아님)으로 본 페이지가 절반 이상 → 제외로 미리 채움(11/02)
    for (const i of [1, 2]) psql(`INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, path, status, chunk_count, reason, signals)
      VALUES (${bid}, 'd${i}', 'd${i}', 'D', ${i}, '${i === 1 ? '' : 'D'}', 'review', 1, 'low_confidence', '{"doc_type": "대상 아님"}')`);
    await loginAdmin(page);
    await goAdmin(page, '운영 지식');
    await goAdmin(page, '소스 연결');
    await page.getByLabel('배치').selectOption(String(bid));
    const bc = page.getByTestId('bundle-connect');
    await expect(bc.getByTestId('bundle-row')).toHaveCount(4);
    await expect(bc.getByTestId('bundle-row').filter({ hasText: '제외 제안' })).toContainText('운영 지식 아님 2/2');
    await expect(bc.getByLabel('D 컬렉션')).toHaveValue('-1');   // 제외
    await expect(bc).not.toContainText('표본');
    // 이견 폴더는 접혀 있다(하위 메뉴처럼 보이지 않게, 11/02) — A만 있음: C-이력은 이웃 비교 없는 AI 판단뿐이라 띄우지 않음
    await expect(bc.getByTestId('bundle-group')).toHaveCount(0);
    await expect(bc.getByTestId('bundle-groups-toggle')).toHaveCount(1);
    await expect(bc.getByTestId('bundle-groups-toggle')).toContainText('AI 이견 폴더 1');
    await bc.getByTestId('bundle-groups-toggle').click();
    await expect(bc.getByTestId('bundle-group')).toHaveCount(1);       // A-도구: AI가 3/3 다르게 봄(> 40%, 3쪽 이상) — 갈래 예외 후보
    await expect(bc.getByTestId('bundle-group')).toContainText('A-도구');
    await expect(bc.getByTestId('bundle-groups')).toContainText('예외 지정(선택)');
    await expect(bc.getByLabel('A 컬렉션')).toHaveValue(String(biz));
    await expect(bc.getByLabel('A-도구 컬렉션')).toHaveValue(String(biz));   // AI 값이 아니라 묶음 값으로 채움(AI "다른 곳" 31%만 맞음)
    await bc.getByLabel('A-도구 컬렉션').selectOption(String(common));   // 사람이 갈래만 바꿈
    await bc.getByTestId('bundle-groups-toggle').click();              // 접어도 지정한 예외가 보인다(리뷰 L2)
    await expect(bc.getByTestId('bundle-groups-toggle')).toContainText('예외 지정 1');
    await bc.getByTestId('bundle-detail-toggle').first().click();
    await expect(bc.getByTestId('bundle-detail')).toContainText('대표 페이지');
    await expect(bc.getByLabel('B 컬렉션')).toHaveValue(String(biz));
    // 적재 요청은 가로채 실제 적재(원장·판단 기록)를 돌리지 않는다 — 연결(규칙·페이지 값)만 DB로 확인
    let confirmed = false;
    await page.route(`**/api/knowledge/team-import/${bid}/confirm`, (r) => { confirmed = true; return r.fulfill({ status: 202, contentType: 'application/json', body: '{"status":"loading"}' }); });
    const res = page.waitForResponse((r) => r.url().includes(`/api/knowledge/team-import/${bid}/connect`));
    await bc.getByTestId('connect-submit').click();
    await page.getByRole('button', { name: '확인' }).click();
    expect((await res).status()).toBe(200);
    await expect.poll(() => confirmed).toBe(true);                    // 버튼 한 번 = 연결 → 적재
    for (const pid of ['A', 'a1', 'a2', 'a3', 'B', 'b1', 'c1', 'C-이력', 'h1', 'h2']) expect(st(pid).split('|')[1]).toBe(String(biz));
    for (const pid of ['A-도구', 't1', 't2']) expect(st(pid).split('|')[1]).toBe(String(common));
    expect(psql(`SELECT count(*) FROM rag_import_rule WHERE team_id = ${team} AND root_url LIKE '%pageId=4'`)).toBe('5');   // 묶음 4 + 예외 1
    expect(psql(`SELECT count(*) FROM rag_import_page WHERE batch_id = ${bid} AND page_id IN ('d1', 'd2') AND status = 'excluded'`)).toBe('2');
    // 연결하면 동기화 대상(연결된 공간)이 한 줄 — 담당 = 연결한 사람(10/30 동기화)
    expect(psql(`SELECT count(*) || '|' || max(status) FROM rag_source_connection WHERE team_id = ${team} AND root_url LIKE '%pageId=4'`)).toBe('1|ok');
    await page.reload();
    await expect(page.getByTestId('source-connections').getByTestId('connection-row').filter({ hasText: 'E2E 묶음 연결' })).toContainText('아직 안 돎');
  } finally {
    psql(`DELETE FROM rag_import_rule WHERE team_id = ${team} AND root_url LIKE '%pageId=4'`);
    psql(`DELETE FROM rag_source_connection WHERE team_id = ${team} AND root_url LIKE '%pageId=4'`);
    psql(`DELETE FROM rag_import_batch WHERE id = ${bid}`);
    dropSystem(SYS);
  }
});
