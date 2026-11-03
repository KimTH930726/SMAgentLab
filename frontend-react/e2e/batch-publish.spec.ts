import { test, expect } from '@playwright/test';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';
import { createSystem, dropSystem, psql } from './v0-fixture';

// 검수 후 게시(11/03 결정 가) — 팀 적재 배치는 적재해도 채팅에 바로 안 나온다. [게시]를 눌러야 조각이 active가 되고 같은 페이지 옛 버전이 내려간다.
// 컨플 없이 "적재 끝·게시 전" 배치(보류 작업 + staging 조각 + 같은 페이지 옛 조각)를 DB에 직접 만들고, 화면에서 게시한 뒤 DB로 본다.
// 검색 확인은 누르지 않는다. [게시]는 자동 용어 추출로 적재 LLM(로컬)을 한 번 부른다 — 만든 용어는 시험 컬렉션을 지울 때 같이 지워진다(FK CASCADE).

const SYS = 'E2E게시시스템';

test('검수 대기 배치 — 카드(조각·중복 의심) → [게시] → 새 조각 active · 옛 버전 내림 · 게시 시각', async ({ page }) => {
  test.setTimeout(120_000);
  const team = psql(`SELECT id FROM ops_team ORDER BY id DESC LIMIT 1`);
  test.skip(!team, '팀이 없음');
  test.skip(psql(`SELECT count(*) FROM rag_import_batch WHERE team_id = ${team} AND status IN ('previewing','loading')`) !== '0', '진행 중 배치 있음');
  const ns = createSystem(SYS);
  // 이 시스템의 담당을 admin 소속과 무관하게 — admin은 관리자라 담당 검사를 통과한다
  const vec = `'[${Array(1024).fill(0).join(',')}]'`;
  const old = psql(`INSERT INTO rag_knowledge (namespace_id, content, embedding, status, confluence_page_id) VALUES (${ns}, 'E2E 옛 버전', ${vec}::vector, 'active', 'e2e-p1') RETURNING id`).split('\n')[0];
  const plan = JSON.stringify({ source_doc_ids: [], partial_doc_ids: [], replacing_pages: ['e2e-p1'], replacing_docs: [], confirm_replace: false, force_pending: false });
  const job = psql(`INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks, created_chunks, held_plan)
    VALUES (${ns}, 'confluence', 'held', 3, 3, '${plan}'::jsonb) RETURNING id`).split('\n')[0];
  for (const [i, st] of [[1, 'staging'], [2, 'staging'], [3, 'staging_review']] as const) {
    psql(`INSERT INTO rag_knowledge (namespace_id, content, embedding, status, confluence_page_id, ingestion_job_id)
      VALUES (${ns}, 'E2E 새 조각 ${i}', ${vec}::vector, '${st}', 'e2e-p1', ${job})`);
  }
  const bid = psql(`INSERT INTO rag_import_batch (team_id, root_url, root_title, status, sampling_required, total_pages, done_pages, created_by_user_id, job_ids, loaded_at)
    VALUES (${team}, 'https://confl.sinc.co.kr/display/E2E', 'E2E 게시', 'loaded', FALSE, 1, 1, (SELECT id FROM ops_user WHERE username = 'admin'), ARRAY[${job}], NOW())
    RETURNING id`).split('\n')[0];
  psql(`INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, status, chunk_count, final_namespace_id) VALUES (${bid}, 'e2e-p1', 'E2E 페이지', '묶음', 1, 'loaded', 3, ${ns})`);
  try {
    await loginAdmin(page);
    await goAdmin(page, '지식', '소스 연결');
    await page.getByLabel('배치').selectOption(bid);
    const review = page.getByTestId('publish-review');
    await expect(review).toBeVisible();
    await expect(review).toContainText('검수 대기');
    await expect(review).toContainText('게시할 조각');
    expect(psql(`SELECT status FROM rag_knowledge WHERE id = ${old}`)).toBe('active');   // 게시 전엔 옛 버전이 그대로 답에 쓰인다

    await review.getByRole('button', { name: '게시', exact: true }).click();
    await page.getByTestId('app-dialog-ok').click();   // 확인 창
    await expect.poll(() => psql(`SELECT published_at IS NOT NULL FROM rag_import_batch WHERE id = ${bid}`), { timeout: 30_000 }).toBe('t');
    expect(psql(`SELECT status FROM rag_knowledge WHERE id = ${old}`)).toBe('deprecated');
    expect(psql(`SELECT string_agg(status, ',' ORDER BY id) FROM rag_knowledge WHERE ingestion_job_id = ${job}`)).toBe('active,active,pending_review');
    expect(psql(`SELECT status FROM rag_ingestion_job WHERE id = ${job}`)).toBe('completed');
  } finally {
    psql(`DELETE FROM rag_import_page WHERE batch_id = ${bid}`);
    psql(`DELETE FROM rag_import_batch WHERE id = ${bid}`);
    psql(`DELETE FROM rag_knowledge WHERE namespace_id = ${ns}`);
    psql(`DELETE FROM rag_ingestion_job WHERE id = ${job}`);
    dropSystem(SYS);
  }
});
