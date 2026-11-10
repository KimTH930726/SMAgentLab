import { test, expect } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { API, adminToken, apiFetch, loginAdmin } from './auth';
import { createSystem, dropSystem, psql } from './v0-fixture';

// 11/04 지식 검색 화면 — 기본 [검색] = 채팅과 같은 검색 결과를 카드로(관련도·위치·채널·채팅 근거 횟수), 필터 옆 건수, [수정]으로 바로 고치기.
// 임베딩이 있어야 검색되므로 시험 지식은 등록 API(POST /api/knowledge)로 넣는다.
const NS = `E2E검색_${Date.now()} 시스템`;

test.afterAll(() => {
  psql(`DELETE FROM rag_knowledge WHERE namespace_id IN (SELECT id FROM ops_namespace WHERE name = '${NS}')`);
  dropSystem(NS);
});

test('지식 검색 — 카드(관련도·채팅 근거) · 필터 건수 · 수정 · 같은 글 사본 · [목록]으로 전체 훑기', async ({ page }) => {
  test.setTimeout(90_000);   // 검색 두 번(임베딩) + 수정 창 두 번 — 기본 30초를 넘긴다(11/10 사본 단계 추가)
  createSystem(NS);
  const token = await adminToken();
  const add = async (content: string) => {
    const r = await apiFetch(`${API}/api/knowledge`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ namespace: NS, content }),
    });
    expect(r.status, await r.clone().text()).toBe(201);
    return (await r.json()).id as number;
  };
  const restart = await add('## 정산 배치 재기동\n정산 배치가 멈추면 상태를 확인한 뒤 재기동한다. 재기동 후 정산 파일 건수를 대조한다.');
  await add('## 쿠폰 발급 오류\n쿠폰 발급이 실패하면 발급 이력에서 오류 코드를 확인하고 운영팀에 알린다.');
  psql(`UPDATE rag_knowledge SET source_type = 'confluence', source_file = '정산 운영 가이드' WHERE id = ${restart}`);

  await loginAdmin(page, { openAdmin: false });
  await page.getByRole('link', { name: 'Admin' }).click();
  await goAdmin(page, '운영 지식');
  await selectDomain(page, NS, NS.replace(/ 시스템$/, ''));

  const mode = page.getByRole('group', { name: '보기 방식' });
  await expect(mode.getByRole('button', { name: '검색', exact: true })).toBeVisible();
  await expect(page.getByText('정산 배치 재기동').first()).toBeVisible();   // 검색어 없으면 목록(훑기)
  await page.getByPlaceholder(/질문하듯 검색/).fill('정산 배치 재기동 순서');
  await page.getByPlaceholder(/질문하듯 검색/).press('Enter');

  const results = page.getByTestId('explore-results');
  await expect(results).toBeVisible();
  const first = results.locator('article').first();
  await expect(first).toHaveAttribute('data-testid', `explore-card-${restart}`);      // 채팅 검색 순서 = 가장 관련 있는 것 먼저
  await expect(page.getByTestId(`explore-card-${restart}-score`)).toHaveText(/^\d+%$/);
  await expect(page.getByTestId(`explore-card-${restart}-used`)).toContainText('채팅 노출 0회');
  await expect(first.locator('mark').first()).toBeVisible();                        // 검색어 둘레 본문 강조

  // 채널 필터 — 건수와 함께, 누르면 그 채널만
  const channel = page.getByTestId('facet-channel');
  await expect(channel.getByRole('button', { name: /컨플루언스 1/ })).toBeVisible();
  await channel.getByRole('button', { name: /컨플루언스 1/ }).click();
  await expect(results.locator('article')).toHaveCount(1);
  await channel.getByRole('button', { name: /컨플루언스 1/ }).click();               // 다시 누르면 해제

  // 카드 전체 = 수정 창(11/04 — [수정] 버튼만 누를 수 있던 것)
  await first.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');

  // 같은 글 사본(11/10 시안 승인) — 다른 페이지에 똑같이 실린 글은 검색에서 한 장 + [같은 글 N곳]으로 사본 목록, [열기] = 그 사본 수정 창
  const copy = psql(`INSERT INTO rag_knowledge (namespace_id, content, embedding, status, heading_path, source_type, source_file, confluence_page_id) `
    + `SELECT namespace_id, replace(content, ' ', '  '), embedding, 'active', '{다른 운영 페이지}', 'confluence', '다른 운영 페이지', 'E2E-COPY' `
    + `FROM rag_knowledge WHERE id = ${restart} RETURNING id`).split('\n')[0];   // 공백만 다른 사본 — 같은 글(content_norm_md5)이지만 글자는 다름
  const explored = page.waitForResponse((r) => r.url().endsWith('/api/knowledge/explore') && r.request().method() === 'POST');
  await page.getByPlaceholder(/질문하듯 검색/).fill('정산 배치 재기동 절차');
  await page.getByRole('button', { name: '검색', exact: true }).last().click();
  const body = await (await explored).json() as { items: { id: number; copies: { id: number }[] }[] };
  expect(body.items.find((x) => x.id === restart)?.copies.map((c) => c.id)).toEqual([Number(copy)]);   // API: 사본 id
  await expect(results.locator(`[data-testid="explore-card-${copy}"]`)).toHaveCount(0);   // 사본은 따로 카드가 안 됨
  const chip = page.getByTestId(`explore-card-${restart}-copies`);
  await expect(chip).toHaveText(/같은 글 1곳/);
  await chip.click();
  await expect(page.getByRole('dialog')).toHaveCount(0);                               // 칩은 카드 수정 창을 안 염
  await expect(page.getByTestId(`explore-card-${restart}-copy-list`)).toContainText(`#${copy}`);
  await expect(page.getByTestId(`explore-card-${restart}-copy-list`)).toContainText('다른 운영 페이지');
  await page.screenshot({ path: 'test-results/explore-copies.png' });
  await page.getByTestId(`explore-copy-open-${copy}`).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  // 수정 창 본문 = 사본 자기 글(공백까지) — 검색된 조각 글로 열면 그대로 저장해도 사본이 바뀌고 "사람이 고침"으로 찍혔다(리뷰 11/10)
  await expect(page.getByRole('dialog').locator('textarea').first()).toHaveValue(psql(`SELECT content FROM rag_knowledge WHERE id = ${copy}`));
  await page.keyboard.press('Escape');

  // [목록] — 전체 훑기(일괄 선택)
  await mode.getByRole('button', { name: '목록', exact: true }).click();
  await expect(page.getByText('전체 선택')).toBeVisible();
  await expect(results).toBeHidden();
  // 11/05: [검색]으로 돌아오면 검색어·결과 그대로(다시 검색 안 해도 카드)
  await mode.getByRole('button', { name: '검색', exact: true }).click();
  await expect(results).toBeVisible();
});
