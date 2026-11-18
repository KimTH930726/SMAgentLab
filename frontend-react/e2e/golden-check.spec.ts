import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';
import { createSystem, dropSystem, psql } from './v0-fixture';

// 게시 전 골든셋 확인(11/17) — 검수 대기 배치에서 [골든셋 확인]을 누르면 이 컬렉션 골든셋 문항을 지금 게시본 vs 게시했다면으로 비교하고,
// 나빠짐·정답 사라짐이 있으면 [게시]가 [그래도 게시]로 바뀐다(막지는 않음 — 사람이 정한다). 정답 페이지를 다른 내용으로 바꾸는 배치를
// DB에 직접 만들어 "정답 사라짐 1"을 본다. 게시는 확인 창에서 취소 — 배치는 그대로 검수 대기. LLM 0(임베딩만).

const SYS = 'E2E골든확인시스템';
const hash = (line: string) => createHash('md5').update(line).digest('hex').slice(0, 12);   // trustkit line_hash와 같은 식

test('검수 대기 배치 — [골든셋 확인] → 정답 사라짐 카드 · 리포트 → [그래도 게시] 확인 창(취소하면 그대로)', async ({ page }) => {
  test.setTimeout(120_000);
  const team = psql(`SELECT id FROM ops_team ORDER BY id DESC LIMIT 1`);
  test.skip(!team, '팀이 없음');
  test.skip(psql(`SELECT count(*) FROM rag_import_batch WHERE team_id = ${team} AND status IN ('previewing','loading')`) !== '0', '진행 중 배치 있음');
  const tag = String(Date.now());
  const question = `E2E 골든확인 질문 ${tag}?`;
  const goldLines = [`E2E 골든확인 정답 고유 첫 줄 ${tag}`, `E2E 골든확인 정답 고유 둘째 줄 ${tag}`];
  const ns = createSystem(SYS);
  const vec = `'[1${',0'.repeat(1023)}]'`;
  let bid = '';
  let job = '';
  try {
    psql(`INSERT INTO rag_knowledge (namespace_id, content, embedding, status, confluence_page_id) VALUES (${ns}, '${goldLines.join('\n')}', ${vec}::vector, 'active', 'e2e-g1')`);
    const plan = JSON.stringify({ source_doc_ids: [], partial_doc_ids: [], replacing_pages: ['e2e-g1'], replacing_docs: [], confirm_replace: false, force_pending: false });
    job = psql(`INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks, created_chunks, held_plan)
      VALUES (${ns}, 'confluence', 'held', 1, 1, '${plan}'::jsonb) RETURNING id`).split('\n')[0];
    psql(`INSERT INTO rag_knowledge (namespace_id, content, embedding, status, confluence_page_id, ingestion_job_id)
      VALUES (${ns}, 'E2E 골든확인 완전히 바뀐 내용 ${tag}', ${vec}::vector, 'staging', 'e2e-g1', ${job})`);
    bid = psql(`INSERT INTO rag_import_batch (team_id, root_url, root_title, status, sampling_required, total_pages, done_pages, created_by_user_id, job_ids, loaded_at)
      VALUES (${team}, 'https://confl.sinc.co.kr/display/E2E', 'E2E 골든확인', 'loaded', FALSE, 1, 1, (SELECT id FROM ops_user WHERE username = 'admin'), ARRAY[${job}], NOW())
      RETURNING id`).split('\n')[0];
    psql(`INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, status, chunk_count, final_namespace_id) VALUES (${bid}, 'e2e-g1', 'E2E 페이지', '묶음', 1, 'loaded', 1, ${ns})`);
    const gold = JSON.stringify({ kind: 'knowledge', anchors: [{ page: 'e2e-g1', lines: goldLines.map(hash), need: 1 }] });
    psql(`INSERT INTO eval_question_candidate (namespace_id, question, gold, status, source, decided_at) VALUES (${ns}, '${question}', '${gold}'::jsonb, 'added', 'no_knowledge', NOW())`);

    await loginAdmin(page);
    await goAdmin(page, '소스 연결');
    await page.getByLabel('배치').selectOption(bid);
    const review = page.getByTestId('publish-review');
    const card = review.getByTestId('golden-check-card');
    await expect(card).toContainText('아직 안 함');
    await expect(review.getByRole('button', { name: '게시', exact: true })).toBeVisible();

    await review.getByTestId('golden-check-run').click();
    await expect(card).toContainText('나빠짐 0 · 정답 사라짐 1 / 1문항', { timeout: 60_000 });   // 정답이 사라진 문항은 나빠짐과 겹쳐 세지 않는다
    await card.click();
    const report = page.getByTestId('golden-check-report');
    await expect(report).toContainText(question);
    await expect(report).toContainText('정답 사라짐');
    await expect(report).toContainText('1위 → 10위 밖');
    await page.keyboard.press('Escape');

    const publish = review.getByTestId('publish-submit');
    await expect(publish).toHaveText('그래도 게시');
    await publish.click();
    await expect(page.getByTestId('app-dialog-message')).toContainText('정답 사라짐 1');
    await page.getByTestId('app-dialog-cancel').click();
    expect(psql(`SELECT published_at IS NULL FROM rag_import_batch WHERE id = ${bid}`)).toBe('t');   // 취소 → 그대로 검수 대기
    expect(psql(`SELECT (golden_check->>'gold_lost') || '|' || (golden_check->>'items') FROM rag_import_batch WHERE id = ${bid}`)).toBe('1|1');
  } finally {
    psql(`DELETE FROM eval_question_candidate WHERE namespace_id = ${ns}`);
    if (bid) {
      psql(`DELETE FROM rag_import_page WHERE batch_id = ${bid}`);
      psql(`DELETE FROM rag_import_batch WHERE id = ${bid}`);
    }
    psql(`DELETE FROM rag_knowledge WHERE namespace_id = ${ns}`);
    if (job) psql(`DELETE FROM rag_ingestion_job WHERE id = ${job}`);
    dropSystem(SYS);
  }
});
