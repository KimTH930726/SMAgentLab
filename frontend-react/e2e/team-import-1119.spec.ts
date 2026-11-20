import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(11/19 최종 적재에서 찾은 결함):
// ③ 교체 확인 — 같은 페이지 기존 조각에 사람 손(지움·반려)이 있으면 예전엔 적재 단계에서 조용히 "실패"가 되고 다시 할 길이 없었다(홈페이지 451 중 403).
//    이제 확정 전에 [교체 허용]·[이 페이지들 빼기]를 고르고, 허용하면 그때 넣을 곳과 함께 기록된다.
// ④ 읽기 제한 이어받기 — 같은 팀이 같은 공간을 '읽기 제한 페이지도 넣기'로 적재까지 끝냈으면 새 적재 폼이 켜진 채로 출처를 보여 준다.
// 컨플·LLM 없이 DB에 시험 배치·원문·조각을 만들고(적재는 안 함), 끝나면 지운다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
const one = (sql: string) => psql(sql).split('\n')[0];

test('교체 확인 — 사람이 지운 조각이 있는 페이지는 확정 전에 막고, [교체 허용]하면 넣을 곳과 함께 기록', async ({ page }) => {
  test.setTimeout(120_000);
  const row = one(`SELECT t.id || '|' || d.id FROM ops_team t JOIN ops_part p ON p.team_id = t.id JOIN ops_namespace_owner o ON o.part_id = p.id
      JOIN ops_namespace d ON d.id = o.namespace_id AND d.kind = 'domain' AND d.area = 'system' AND d.parent_id IS NULL ORDER BY t.id DESC LIMIT 1`);
  test.skip(!row, '담당 시스템 컬렉션이 있는 팀이 없음');
  const [team, ns] = row.split('|').map(Number);
  test.skip(psql(`SELECT count(*) FROM rag_import_batch WHERE team_id = ${team} AND status IN ('previewing','loading')`) !== '0', '진행 중 배치 있음');
  const pid = 'e2e-replace-1119';
  const doc = Number(one(`INSERT INTO rag_source_document (namespace_id, source_type, source_ref, title, raw_content, raw_format, content_hash, status, activated_at)
      VALUES (${ns}, 'confluence', '${pid}', 'E2E 교체', '<p>x</p>', 'html', md5(random()::text), 'active', NOW() - interval '1 day') RETURNING id`));
  const k = Number(one(`INSERT INTO rag_knowledge (namespace_id, content, status, source_document_id, confluence_page_id, updated_at)
      VALUES (${ns}, 'E2E 사람이 반려한 조각', 'rejected', ${doc}, '${pid}', NOW()) RETURNING id`));
  const bid = Number(one(`INSERT INTO rag_import_batch (team_id, root_url, root_title, status, sampling_required, total_pages, done_pages, created_by_user_id, default_namespace_id)
      VALUES (${team}, 'https://confl.example/pages/viewpage.action?pageId=1119', 'E2E 교체 확인', 'preview', FALSE, 1, 1,
              (SELECT id FROM ops_user WHERE username = 'admin'), ${ns}) RETURNING id`));
  psql(`INSERT INTO rag_import_page (batch_id, page_id, title, bundle, status, chunk_count, final_namespace_id, assigned_by)
        VALUES (${bid}, '${pid}', 'E2E 교체 페이지', '묶음R', 'auto', 1, ${ns}, 'space_default')`);
  try {
    await loginAdmin(page);
    await goAdmin(page, '소스 연결');
    await page.getByLabel('배치').selectOption(String(bid));
    const box = page.getByTestId('replace-confirm');
    await expect(box).toContainText('사람이 고친·지운 조각이 있는 페이지 1개');
    await box.getByRole('button', { name: '교체 허용' }).click();
    await page.getByRole('button', { name: '확인' }).click();
    await expect(box).toHaveCount(0);
    expect(one(`SELECT (signals->>'replace_ok_ns')::int FROM rag_import_page WHERE batch_id = ${bid}`)).toBe(String(ns));
  } finally {
    psql(`DELETE FROM rag_import_batch WHERE id = ${bid}`);
    psql(`DELETE FROM rag_knowledge WHERE id = ${k}`);
    psql(`DELETE FROM rag_source_document WHERE id = ${doc}`);
  }
});

test('읽기 제한 이어받기 — 같은 공간에 켜고 적재한 결정이 있으면 새 적재 폼이 켜진 채로 출처를 보인다', async ({ page }) => {
  test.setTimeout(120_000);
  const team = Number(one(`SELECT id FROM ops_team ORDER BY id DESC LIMIT 1`));
  const tname = one(`SELECT name FROM ops_team WHERE id = ${team}`);
  const ns = Number(one(`SELECT id FROM ops_namespace WHERE area = 'system' AND parent_id IS NULL ORDER BY id LIMIT 1`));
  const key = 'E2EKEY1119';
  const bid = Number(one(`INSERT INTO rag_import_batch (team_id, root_url, root_title, status, total_pages, done_pages, created_by_user_id, include_restricted_at, include_restricted_by)
      VALUES (${team}, 'https://confl.example/x/e2e-restrict', 'E2E 제한', 'loaded', 1, 1, (SELECT id FROM ops_user WHERE username = 'admin'), NOW(),
              (SELECT id FROM ops_user WHERE username = 'admin')) RETURNING id`));
  const doc = Number(one(`INSERT INTO rag_source_document (namespace_id, source_type, source_ref, title, raw_content, raw_format, content_hash, status, media)
      VALUES (${ns}, 'confluence', 'e2e-r-1119', 't', '<p>x</p>', 'html', md5(random()::text), 'superseded', '{"page": {"space_key": "${key}"}}'::jsonb) RETURNING id`));
  psql(`INSERT INTO rag_import_page (batch_id, page_id, status, source_document_id) VALUES (${bid}, 'e2e-r-1119', 'loaded', ${doc})`);
  try {
    await loginAdmin(page);
    await goAdmin(page, '운영 지식');
    await page.getByRole('button', { name: '지식 등록', exact: true }).click();
    await page.getByText('URL / Confluence', { exact: true }).click();
    await page.getByPlaceholder('https://confl.sinc.co.kr/display/SPACE/페이지제목').fill(`https://confl.sinc.co.kr/display/${key}`);
    const space = page.getByRole('radio', { name: /공간·하위 전체 넣기/ });
    test.skip(!(await space.count()), 'PAT 없음 — 공간 넣기 선택지가 안 보임');
    await space.click();
    await page.getByLabel('팀').selectOption({ label: tname });
    await expect(page.getByTestId('import-restricted-inherit')).toContainText(`이 공간(${key})`);
    await expect(page.getByTestId('import-restricted-inherit')).toContainText(`#${bid}`);
    await expect(page.getByTestId('import-include-restricted')).toBeChecked();   // 누르지 않는다(컨플을 받는다)
  } finally {
    psql(`DELETE FROM rag_import_batch WHERE id = ${bid}`);
    psql(`DELETE FROM rag_source_document WHERE id = ${doc}`);
  }
});
