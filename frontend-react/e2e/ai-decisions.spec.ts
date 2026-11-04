import { test, expect } from '@playwright/test';
import { goAdmin, selectDomain } from './admin-nav';
import { adminToken, API, apiFetch, loginAdmin } from './auth';
import { psql } from './v0-fixture';

// 이 테스트가 존재하는 이유(10/27 밤 자가 개선 루프 재구성, docs/domains/quality/self-improvement-loop-design.md):
// ① AI 감사 로그는 기록만 — "AI가 틀렸나요?"를 묻지 않고, 번복(사람이 AI 추천을 바꿈)과 그 순간 고른 이유를 보여 준다(사용자 10/27).
// ② 옛 품질 › RAG 파이프라인 메뉴는 평가 게이트로 흡수 — 단계 줄(누르면 ① 개선할 곳이 그 단계로) + ③ 기법 대장(항목 전체·이력).
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

test('AI 감사 리포트 — 사람 검토 결과(수용·번복·규칙 적용…)가 보이고, 번복률은 사람 검토로만, 로그는 검토 결과로 거르고, 표본 감사가 남는다', async ({ page }) => {
  test.setTimeout(90_000);
  const over = row(`jsonb_build_object('namespace_id', (SELECT max(id) FROM ops_namespace WHERE kind = 'domain'), 'override_reason', 'context_changed')`);
  const kept = row(`jsonb_build_object('namespace_id', (SELECT min(id) FROM ops_namespace WHERE kind = 'domain'), 'choice', 'accept')`);
  const t = await adminToken();
  const h = { headers: { Authorization: `Bearer ${t}` } };

  await loginAdmin(page);
  await goAdmin(page, 'AI 감사 로그');
  const report = page.getByTestId('audit-report');
  await expect(report).toBeVisible();
  // 화면 숫자 = API(사람 검토 = 수용 + 번복, 번복률 분모)
  const ov: { kinds: { kind: string; decided: number; overrides: number; review: Record<string, number>; admin_reviewed: boolean }[] } =
    await (await apiFetch(`${API}/api/eval/overrides?days=30`, h)).json();
  const admin = ov.kinds.filter((k) => k.admin_reviewed);   // 요약 = 관리자 검토 종류만(되묻기·제안은 사용자 선택)
  const reviewed = admin.reduce((a, k) => a + k.review.accept + k.review.override, 0);
  expect(reviewed).toBe(admin.reduce((a, k) => a + k.decided, 0));
  await expect(page.getByTestId('report-reviewed')).toHaveText(String(reviewed));
  for (const k of ['knowledge_location', 'chat_clarify', 'source_check']) await expect(page.getByTestId(`report-row-${k}`)).toBeVisible();
  await expect(page.getByTestId('report-findings')).toBeVisible();

  // 판단 기준(ⓘ) = 대장의 되묻기 항목
  const techs: { key: string; name: string; rules?: string[] }[] = await (await apiFetch(`${API}/api/eval/techniques`, h)).json();
  await page.getByRole('button', { name: '되묻기 판단 기준' }).click();
  const crit = page.getByRole('dialog').getByTestId('decision-criteria');
  await expect(crit).toContainText(techs.find((x) => x.key === 'clarify')!.name);
  await expect(crit.getByTestId('criteria-example').first()).toBeVisible();
  for (const r of techs.find((x) => x.key === 'clarify')!.rules!) await expect(crit).toContainText(r);
  await page.keyboard.press('Escape');

  // 감사 로그 — 기본 = 번복, 행마다 검토 결과 배지 · 번복 사유(업계 용어)
  const log = page.getByTestId('audit-log');
  await page.getByTestId('report-row-knowledge_location').getByRole('button', { name: '등록 위치', exact: true }).click();
  const chips = log.getByTestId('audit-review');
  await expect(chips.getByRole('button', { name: /^번복/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(log.getByTestId(`log-${over}-review`)).toHaveText('번복');
  await expect(log.getByTestId(`log-${over}`)).toContainText('맥락 변화');
  await expect(log.getByTestId(`log-${kept}`)).toHaveCount(0);
  await chips.getByRole('button', { name: /^수용/ }).click();
  await expect(log.getByTestId(`log-${kept}-review`)).toHaveText('수용');
  await expect(log.getByTestId(`log-${over}`)).toHaveCount(0);

  // 표본 감사 — 창이 열리고, 수용 판단에 매긴 결과가 로그에 남는다(실데이터를 건드리지 않게 테스트 줄만 API로)
  await page.getByTestId('spot-open').click();
  await expect(page.getByTestId('spot-modal')).toBeVisible();
  await page.keyboard.press('Escape');
  const r1 = await apiFetch(`${API}/api/eval/decisions/${kept}/spot-check`, { method: 'POST', headers: { ...h.headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ result: 'overridden', reason: 'wrong' }) });
  expect(r1.status).toBe(200);
  const r2 = await apiFetch(`${API}/api/eval/decisions/${over}/spot-check`, { method: 'POST', headers: { ...h.headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ result: 'accepted' }) });
  expect(r2.status).toBe(404);   // 번복은 감사 대상 아님
  await page.reload();
  await page.getByTestId('report-row-knowledge_location').getByRole('button', { name: '등록 위치', exact: true }).click();
  await page.getByTestId('audit-log').getByTestId('audit-review').getByRole('button', { name: /^수용/ }).click();
  await expect(page.getByTestId('audit-log').getByTestId(`log-${kept}`)).toContainText('감사: 번복 · AI 오류');
});

test('평가 게이트 — 단계 줄 = 원인 수(누르면 그 단계 원인만), 적용 이력 최근 5줄·전체 = API, 항목 전체 = 대장(옛 RAG 파이프라인 흡수)', async ({ page }) => {
  test.setTimeout(90_000);
  const t = await adminToken();
  const h = { headers: { Authorization: `Bearer ${t}` } };
  const techs: { key: string; stage: string; flow: string; state: string }[] = await (await apiFetch(`${API}/api/eval/techniques`, h)).json();
  const history: { stage: string }[] = await (await apiFetch(`${API}/api/eval/history`, h)).json();
  await loginAdmin(page);
  const nav = page.getByRole('navigation', { name: '관리자 메뉴' });
  await nav.waitFor();
  await expect(nav.getByRole('button', { name: 'RAG 파이프라인', exact: true })).toHaveCount(0);   // 메뉴는 내렸다
  await goAdmin(page, '평가 게이트');

  for (const s of ['적재', '검색', '근거 판정', '답 모양', '생성']) await expect(page.getByTestId(`flow-${s}`)).toBeVisible();
  // 적재 칸 = 적재 검사 후보가 걸린 틀린 문항 수(다른 칸과 겹칠 수 있어 "겹침") — 누르면 ① 개선할 곳의 적재 검사 줄과 같은 수(10/29)
  const rep: { failed: { cause: string | null; techniques: { id: number }[] }[]; techniques: { id: number; cause: string }[] } | null =
    await (await apiFetch(`${API}/api/eval/report`, h)).json();
  if (rep) {
    const ing = new Set(rep.techniques.filter((x) => x.cause === 'ingest').map((x) => x.id));
    const n = rep.failed.filter((r) => r.techniques.some((x) => ing.has(x.id))).length;
    await expect(page.getByTestId('flow-적재')).toContainText(new RegExp(`적재\\s*${n}`));
    await page.getByTestId('flow-적재').click();
    await expect(page.getByTestId('stage-filter')).toContainText('적재 단계만');
    if (ing.size) await expect(page.getByTestId('report-cause-ingest').locator('> button')).toContainText(String(n));
    await page.getByTestId('flow-적재').click();   // 다시 누르면 전체
    const nRetr = rep.failed.filter((r) => r.cause === 'retrieval_miss').length;   // 검색 칸 = 검색 실패 원인 문항 수
    await expect(page.getByTestId('flow-검색')).toContainText(new RegExp(`검색\\s*${nRetr}`));
  }
  await page.getByTestId('flow-검색').click();
  await expect(page.getByTestId('flow-검색')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('stage-filter')).toContainText('검색 단계만');
  await expect(page.locator('[data-testid="report-cause-wrong_with_context"]')).toHaveCount(0);   // 다른 단계 원인은 숨김
  await page.getByTestId('flow-검색').click();
  await expect(page.getByTestId('stage-filter')).toHaveCount(0);

  // ③ 기법 대장(11/03 흐름도) — 펼치면 흐름도: 세 흐름(채팅·적재·평가) 칸을 다 합치면 대장 전체(빠진 기법 0), 칸 상태 = API 상태.
  // 그 아래 [이력] = 최근 5줄 + 전체 = API
  await page.getByTestId('gate-history-section').getByRole('button', { name: /기법 대장/ }).click();
  const flowEl = page.getByTestId('technique-flow');
  let seen = 0;
  for (const tab of ['채팅 흐름', '적재 흐름', '평가·개선']) {
    await flowEl.getByRole('tab', { name: tab }).click();
    seen += await flowEl.locator('[data-testid^="flow-tech-"]').count();
  }
  expect(seen).toBe(techs.length);
  await flowEl.getByRole('tab', { name: '채팅 흐름' }).click();
  for (const t of techs.filter((x: { flow: string }) => x.flow === 'chat')) {
    await expect(flowEl.getByTestId(`flow-tech-${t.key}`)).toHaveAttribute('data-state', t.state);
  }
  await flowEl.getByTestId('flow-tech-chat_prompt').click();
  await expect(page.getByTestId('technique-flow-panel')).toContainText('필수');
  await page.keyboard.press('Escape');   // 상세는 모달(11/03) — 닫아야 아래를 누를 수 있다
  await expect(page.getByTestId('technique-flow-panel')).toBeHidden();
  await expect(page.getByTestId('gate-history')).toBeVisible();
  await expect(page.getByTestId('history-row')).toHaveCount(Math.min(5, history.length));
  if (history.length > 5) {
    await page.getByTestId('history-more').click();
    await expect(page.getByTestId('history-row')).toHaveCount(history.length);
  }
  await page.getByRole('button', { name: /^항목 전체/ }).click();
  await page.locator('[data-testid^="technique-row-"]').first().click();
  await expect(page.getByRole('dialog')).toContainText('하는 일');
});

test('AI가 본 근거(지식 상세) — 이 지식의 순위 표시 · 화면 판정 = API(채팅과 같은 검색) · 맞는 답 표시 → 시험지에 넣기 = 실사용 층 한 줄', async ({ page }) => {
  test.setTimeout(150_000);
  const NS = '딜리버스';
  const t = await adminToken();
  const H = { Authorization: `Bearer ${t}`, 'Content-Type': 'application/json' };
  // 이미 있는 사용 중 지식 하나(새로 만들면 중복 판정으로 승인 대기가 되거나 자동 용어 추출이 용어집에 시험 용어를 남긴다 — 10/28 리뷰)
  const [kid, head] = psql(`SELECT id || '|' || left(regexp_replace(content, '\s+', ' ', 'g'), 12) FROM rag_knowledge
    WHERE status = 'active' AND namespace_id = (SELECT id FROM ops_namespace WHERE name = '${NS}') ORDER BY id LIMIT 1`).split('|');
  test.skip(!kid, '딜리버스에 사용 중 지식 없음');
  const created = { id: Number(kid) };
  {
    const api = await (await apiFetch(`${API}/api/eval/preview`, { method: 'POST', headers: H, body: JSON.stringify({ namespace: NS, question: PREVIEW_Q }) })).json();
    expect(api.verdict).toBeTruthy();
    await loginAdmin(page);
    await nav(page, NS);
    await page.getByPlaceholder(/검색/).fill(head);
    await page.getByText(head).first().click();
    await page.getByTestId('evidence-open').click();
    const dlg = page.getByRole('dialog').last();
    await dlg.getByLabel('질문').fill(PREVIEW_Q);
    await dlg.getByRole('button', { name: '근거 보기' }).click();
    await expect(dlg.getByTestId('preview-verdict')).toContainText(api.verdict.name);
    const mine = api.items.find((x: { kind: string; id?: number }) => x.kind === 'knowledge' && x.id === created.id);
    await expect(dlg.getByTestId('evidence-mine')).toContainText(mine ? `지식 ${mine.rank}위` : '안 잡혀요');
    const policies = dlg.getByTestId('preview-item-policy');
    await expect(policies).toHaveCount(api.items.filter((x: { kind: string }) => x.kind === 'policy').length);
    test.skip(api.items.every((x: { kind: string }) => x.kind !== 'policy'), '정책이 안 걸리는 데이터 — 시험지 넣기는 확인 못 함');

    const before = Number(psql(`SELECT count(*) FROM eval_question_candidate WHERE question = '${PREVIEW_Q}'`));
    await policies.first().getByLabel('이게 맞는 답').check();
    await dlg.getByTestId('preview-add').click();
    await expect(page.getByTestId('app-dialog')).toContainText('골든셋에 추가했어요');
    await page.getByTestId('app-dialog-ok').click();
    const want = api.items.find((x: { kind: string }) => x.kind === 'policy').logical_id;
    expect(psql(`SELECT count(*) || '|' || max(status) || '|' || max(source) || '|' || max(gold->>'logical_id') FROM eval_question_candidate WHERE question = '${PREVIEW_Q}'`))
      .toBe(`${before + 1}|added|preview|${want}`);
    await dlg.getByTestId('preview-add').click();   // 두 번 눌러도 한 줄
    await expect(page.getByTestId('app-dialog')).toContainText('이미 골든셋에');
    await page.getByTestId('app-dialog-ok').click();
    expect(psql(`SELECT count(*) FROM eval_question_candidate WHERE question = '${PREVIEW_Q}'`)).toBe(String(before + 1));
  }
});

/** 운영 지식 화면으로 — 보는 컬렉션을 고른 뒤 */
async function nav(page: import('@playwright/test').Page, ns: string) {
  await goAdmin(page, '운영 지식');
  await selectDomain(page, ns);
}
