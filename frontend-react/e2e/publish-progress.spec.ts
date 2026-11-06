import { test, expect } from '@playwright/test';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';
import { createSystem, dropSystem, psql } from './v0-fixture';

// 진행 표시(11/05 태훈 "멈춘 것처럼 보인다 — 진행률 UI는 있어야지") — 게시·검색 확인은 뒤에서 돌고, 화면은 서버 진행 기록을 다시 읽어 막대로.
// 게시가 도는 중인 배치(보류 작업 3 중 1 끝남 · 게시 중 표시 · 검색 확인 3/10 진행 중)를 DB에 직접 만들고 화면 막대를 본다.
// 그다음 서버가 끝낸 것처럼(게시 시각) 바꾸면 화면이 스스로 다시 읽어 "게시했어요" 안내를 띄우는지. LLM 호출 없음.

const SYS = 'E2E진행시스템';

test('게시 중 · 검색 확인 중 — 진행 막대(끝남/전체) → 끝나면 결과 안내', async ({ page }) => {
  test.setTimeout(60_000);
  const team = psql(`SELECT id FROM ops_team ORDER BY id DESC LIMIT 1`);
  test.skip(!team, '팀이 없음');
  const ns = createSystem(SYS);
  const jobs = [0, 1, 2].map((i) => psql(`INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks, held_plan)
    VALUES (${ns}, 'confluence', '${i === 0 ? 'completed' : 'held'}', 1, '{}'::jsonb) RETURNING id`).split('\n')[0]);
  const pc = JSON.stringify({ checked_at: new Date(Date.now() - 20_000).toISOString(), running: true, asked: 3, found: 3, failed: 0, total: 10, misses: [], items: [] });
  const bid = psql(`INSERT INTO rag_import_batch (team_id, root_url, root_title, status, sampling_required, total_pages, done_pages, created_by_user_id, job_ids,
      loaded_at, publishing_at, publish_check)
    VALUES (${team}, 'https://confl.sinc.co.kr/display/E2EP', 'E2E 진행', 'loaded', FALSE, 3, 3, (SELECT id FROM ops_user WHERE username = 'admin'),
      ARRAY[${jobs.join(',')}], NOW(), NOW() - INTERVAL '30 seconds', '${pc}'::jsonb) RETURNING id`).split('\n')[0];
  try {
    await loginAdmin(page);
    await goAdmin(page, '소스 연결');
    await page.getByLabel('배치').selectOption(bid);
    const bars = page.getByTestId('progress');
    await expect(bars.filter({ hasText: '게시 중' })).toContainText('1 / 3페이지');
    await expect(bars.filter({ hasText: '게시 중' })).toContainText('경과');
    await expect(bars.filter({ hasText: '검색 확인 중' })).toContainText('3 / 10표본');
    await expect(page.getByRole('button', { name: '게시 중…' })).toBeDisabled();

    // 서버가 끝낸 것처럼 — 화면이 2초마다 다시 읽다가 게시 중 → 끝으로 바뀌는 순간 안내
    psql(`UPDATE rag_ingestion_job SET status = 'completed' WHERE id = ANY(ARRAY[${jobs.join(',')}])`);
    psql(`UPDATE rag_import_batch SET publishing_at = NULL, published_at = NOW(), publish_check = publish_check - 'running' WHERE id = ${bid}`);
    await expect(page.getByText(/게시했어요/)).toBeVisible({ timeout: 15_000 });
    await expect(bars).toHaveCount(0);
  } finally {
    psql(`DELETE FROM rag_import_batch WHERE id = ${bid}`);
    psql(`DELETE FROM rag_ingestion_job WHERE id = ANY(ARRAY[${jobs.join(',')}])`);
    dropSystem(SYS);
  }
});
