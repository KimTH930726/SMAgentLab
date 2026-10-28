import { test, expect } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { adminToken, API, apiFetch, loginAdmin } from './auth';
import { psql } from './v0-fixture';

// 이 테스트가 존재하는 이유(10/27 밤 자가 개선 루프 재구성, docs/tech/self-improvement-loop-design.md):
// ① AI 감사 로그는 기록만 — "AI가 틀렸나요?"를 묻지 않고, 번복(사람이 AI 추천을 바꿈)과 그 순간 고른 이유를 보여 준다(사용자 10/27).
// ② 옛 품질 › RAG 파이프라인 메뉴는 평가 게이트로 흡수 — 단계 흐름 요약(누르면 리포트가 그 단계로) + [적용 이력](이력·항목 전체).
// ③ 즉석 질의 → 검색 미리보기 — 채팅과 같은 검색(API와 화면이 같은 판정), [시험지에 추가]가 실사용 층 한 줄로 DB에 남는다.
// 실 dev 스택 그대로. 테스트가 넣은 원장 줄·시험지 줄은 끝에 지운다.

const MARK = 'E2E 감사 로그 확인용';
const made: string[] = [];
const row = (finalSql: string) => {
  const id = psql(`INSERT INTO ops_ai_decision (kind, ai_value, final_value, confident, signals, human_changed, changed_by_user_id, changed_at)
    SELECT 'knowledge_location', jsonb_build_object('namespace_id', (SELECT min(id) FROM ops_namespace WHERE kind = 'domain')),
           ${finalSql}, true, '{"reason": "${MARK}"}'::jsonb, true, u.id, NOW()
    FROM ops_user u WHERE u.username = 'admin' RETURNING id`).split(/\s+/)[0];
  made.push(id);
  return id;
};
const PREVIEW_Q = `E2E미리보기 배송비 기준 ${Date.now()}`;

test.afterAll(() => {
  if (made.length) psql(`DELETE FROM ops_ai_decision WHERE id IN (${made.join(',')}) AND signals->>'reason' = '${MARK}'`);
  psql(`DELETE FROM eval_question_candidate WHERE question = '${PREVIEW_Q}' AND source = 'preview'`);
});

test('AI 감사 로그 — 기록만: 번복과 이유가 보이고, 묻는 버튼은 없고, 번복만(기본)을 끄면 그대로 둔 판단도 보인다', async ({ page }) => {
  test.setTimeout(90_000);
  const over = row(`jsonb_build_object('namespace_id', (SELECT max(id) FROM ops_namespace WHERE kind = 'domain'), 'override_reason', 'context_changed')`);
  const kept = row(`jsonb_build_object('namespace_id', (SELECT min(id) FROM ops_namespace WHERE kind = 'domain'), 'choice', 'accept')`);

  await loginAdmin(page);
  await goAdmin(page, '품질', 'AI 감사 로그');
  await expect(page.getByTestId('override-summary')).toBeVisible();
  for (const k of ['location', 'clarify', 'source', 'suggest']) await expect(page.getByTestId(`decision-card-${k}`)).toBeVisible();

  // 종류 ⓘ = 지금 기준 창(대장의 되묻기 항목 이름)
  const t = await adminToken();
  const techs: { key: string; name: string }[] = await (await apiFetch(`${API}/api/eval/techniques`, { headers: { Authorization: `Bearer ${t}` } })).json();
  await page.getByTestId('decision-detail-clarify').click();
  await expect(page.getByRole('dialog').getByTestId('decision-criteria')).toContainText(techs.find((x) => x.key === 'clarify')!.name);
  await page.keyboard.press('Escape');

  const log = page.getByTestId('audit-log');
  await page.getByTestId('decision-card-location').click();
  const checkbox = log.getByLabel('번복만');
  await expect(checkbox).toBeChecked();   // 기본 = 번복만
  const line = log.getByTestId(`log-${over}`);
  await expect(line).toContainText('번복');
  await expect(line).toContainText('상황이 바뀜');   // 번복 순간 고른 이유(지식 등록) — 감사 로그는 보여 주기만
  await expect(log.getByTestId(`log-${kept}`)).toHaveCount(0);   // 추천 그대로 = 번복 아님
  await expect(page.getByRole('button', { name: 'AI가 틀림' })).toHaveCount(0);   // "AI가 틀렸나요?" 묻는 버튼 없음
  await checkbox.uncheck();
  await expect(log.getByTestId(`log-${kept}`)).toBeVisible();
  await expect(log.getByTestId(`log-${kept}`)).not.toContainText('· 번복');
});

test('평가 게이트 — 단계 = 공통 필터(탭 유지·이력·항목·성적이 그 단계로), 적용 이력 줄 수 = API, 항목 전체 = 대장(옛 RAG 파이프라인 흡수)', async ({ page }) => {
  test.setTimeout(90_000);
  const t = await adminToken();
  const h = { headers: { Authorization: `Bearer ${t}` } };
  const techs: { key: string; stage: string }[] = await (await apiFetch(`${API}/api/eval/techniques`, h)).json();
  const history: { stage: string }[] = await (await apiFetch(`${API}/api/eval/history`, h)).json();
  await loginAdmin(page);
  const nav = page.getByRole('navigation', { name: '관리자 메뉴' });
  await nav.waitFor();
  await expect(nav.getByRole('button', { name: 'RAG 파이프라인', exact: true })).toHaveCount(0);   // 메뉴는 내렸다
  await goAdmin(page, '품질', '평가 게이트');

  for (const s of ['적재', '검색', '근거 판정', '답 모양', '생성']) await expect(page.getByTestId(`flow-${s}`)).toBeVisible();
  // 단계 = 공통 필터(10/28) — 고른 단계가 탭을 바꿔도 유지되고, 단계를 눌러도 보던 탭에 머문다
  await page.getByTestId('flow-검색').click();
  await expect(page.getByTestId('flow-검색')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('stage-filter')).toContainText('검색 단계');
  await page.getByRole('button', { name: '적용 이력' }).click();
  await expect(page.getByTestId('gate-history')).toBeVisible();
  const nHist = history.filter((x) => x.stage === '검색').length;
  expect(nHist).toBeGreaterThan(0);   // 0건이면 거르기를 못 본다(대장 근거에 검색 단계 기록이 늘 있음)
  await expect(page.getByTestId('history-row')).toHaveCount(nHist);
  await page.getByRole('button', { name: /^항목 전체/ }).click();
  await expect(page.locator('[data-testid^="technique-row-"]')).toHaveCount(techs.filter((x) => x.stage === '검색').length);
  await page.getByRole('button', { name: '성적', exact: true }).click();
  await expect(page.getByTestId('gate-scores')).toHaveAttribute('data-which', 'retrieval');   // 검색 단계 = 검색 성적(측정 데이터와 무관한 표지)
  await page.getByTestId('flow-생성').click();
  await expect(page.getByTestId('gate-scores')).toHaveAttribute('data-which', 'answers');   // 단계를 눌러도 성적 탭 그대로, 생성 = 답 성적
  await page.getByTestId('stage-filter').getByRole('button', { name: '전체 단계' }).click();
  await expect(page.getByTestId('stage-filter')).toHaveCount(0);

  await page.getByRole('button', { name: '적용 이력' }).click();
  await expect(page.getByTestId('history-row')).toHaveCount(history.length);
  await page.getByRole('button', { name: /^항목 전체/ }).click();
  await expect(page.locator('[data-testid^="technique-row-"]')).toHaveCount(techs.length);   // 단계 밖으로 빠진 항목 0
  await page.locator('[data-testid^="technique-row-"]').first().click();
  await expect(page.getByRole('dialog')).toContainText('하는 일');
});

test('검색 미리보기 — 화면의 판정 = API(채팅과 같은 검색), 정답 정책을 골라 시험지에 추가하면 실사용 층 한 줄', async ({ page }) => {
  test.setTimeout(120_000);
  const NS = '딜리버스';
  const t = await adminToken();
  const api = await (await apiFetch(`${API}/api/eval/preview`, {
    method: 'POST', headers: { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ namespace: NS, question: PREVIEW_Q }),
  })).json();
  await loginAdmin(page);
  await goAdmin(page, '품질', '평가 게이트');
  await selectDomain(page, NS);
  await page.getByRole('button', { name: '검색 미리보기' }).click();
  await page.getByLabel('질문').fill(PREVIEW_Q);
  await page.getByRole('button', { name: '채팅처럼 검색' }).click();
  await expect(page.getByTestId('preview-verdict')).toContainText(api.verdict.name);
  const policies = page.getByTestId('preview-item-policy');
  await expect(policies).toHaveCount(api.items.filter((x: { kind: string }) => x.kind === 'policy').length);
  test.skip(api.items.every((x: { kind: string }) => x.kind !== 'policy'), '정책이 안 걸리는 데이터 — 추가는 확인 못 함');

  const before = Number(psql(`SELECT count(*) FROM eval_question_candidate WHERE question = '${PREVIEW_Q}'`));
  await policies.first().getByLabel('정답').check();
  await page.getByTestId('preview-add').click();
  await expect(page.getByTestId('app-dialog')).toContainText('시험지에 넣었어요');
  await page.getByTestId('app-dialog-ok').click();
  const want = api.items.find((x: { kind: string }) => x.kind === 'policy').logical_id;
  expect(psql(`SELECT count(*) || '|' || max(status) || '|' || max(source) || '|' || max(gold->>'logical_id') FROM eval_question_candidate WHERE question = '${PREVIEW_Q}'`))
    .toBe(`${before + 1}|added|preview|${want}`);
  // 두 번 눌러도 한 줄(같은 컬렉션·같은 질문)
  await page.getByTestId('preview-add').click();
  await expect(page.getByTestId('app-dialog')).toContainText('이미 시험지에');
  await page.getByTestId('app-dialog-ok').click();
  expect(psql(`SELECT count(*) FROM eval_question_candidate WHERE question = '${PREVIEW_Q}'`)).toBe(String(before + 1));
});
