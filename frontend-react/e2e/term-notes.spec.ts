import { test, expect } from '@playwright/test';
import { adminToken, API, apiFetch, loginAdmin } from './auth';
import { selectDomain } from './admin-nav';
import { psql } from './v0-fixture';

// 팀 간 용어 참고(10/29, 목업 검수 "좋은데") — 질문에 나온 용어를 다른 영역 용어집이 다른 뜻으로 쓰면 답 아래 한 줄. 답·근거는 그대로(용어집 조회만).
// 실 dev 스택 그대로(LLM 1회 — E2E는 로컬 LLM). 다시 열어도(대화 기록) 그대로 보이는지까지. 데이터에 그런 용어가 없으면 건너뛴다.
const NS = '제휴서비스';
const TERM = '픽업';
const Q = `${TERM} 주문 처리 순서 알려줘`;

// 시험이 만든 대화는 지운다(사이드바에 시험 대화가 쌓이지 않게) — 시험 시작 뒤 생긴, 첫 질문이 이 시험 질문인 대화만
let before = 0;
test.beforeAll(() => { before = Number(psql('SELECT coalesce(max(id), 0) FROM ops_conversation')); });
test.afterAll(async () => {
  const ids = psql(`SELECT coalesce(string_agg(c.id::text, ','), '') FROM ops_conversation c WHERE c.id > ${before}
    AND (SELECT m.content FROM ops_message m WHERE m.conversation_id = c.id AND m.role = 'user' ORDER BY m.id LIMIT 1) = '${Q}'`);
  if (!ids) return;
  const t = await adminToken();
  for (const id of ids.split(',')) await apiFetch(`${API}/api/conversations/${id}`, { method: 'DELETE', headers: { Authorization: `Bearer ${t}` } });
  // 질의 기록은 대화를 지워도 남는다(통계·지식 공백 집계용) — 이 시험 질문 줄 중 메시지가 지워진 것만
  psql(`DELETE FROM ops_query_log q WHERE q.question = '${Q}' AND NOT EXISTS (SELECT 1 FROM ops_message m WHERE m.id = q.message_id)`);
});

test('다른 팀이 다른 뜻으로 쓰는 용어 → 답 아래 참고(팀 이름 = 용어집), 대화를 다시 열어도 그대로', async ({ page }) => {
  test.setTimeout(240_000);
  // 이 영역 뜻과 설명이 다른 다른 영역들(서버 규칙과 같은 기준: 같은 용어·설명 다름 — 최대 2개 보임)
  const others = psql(`SELECT string_agg(n.name, ',' ORDER BY n.name) FROM rag_glossary g JOIN ops_namespace n ON n.id = g.namespace_id
    WHERE g.term = '${TERM}' AND n.name <> '${NS}' AND coalesce(g.description, '') <> ''
      AND g.description IS DISTINCT FROM (SELECT g2.description FROM rag_glossary g2 JOIN ops_namespace n2 ON n2.id = g2.namespace_id
                                         WHERE n2.name = '${NS}' AND g2.term = '${TERM}' LIMIT 1)`);
  const mine = psql(`SELECT count(*) FROM rag_glossary g JOIN ops_namespace n ON n.id = g.namespace_id WHERE n.name = '${NS}' AND g.term = '${TERM}'`);
  test.skip(!others || mine === '0', '이 영역·다른 영역 둘 다에 다른 뜻으로 있는 용어가 없음');
  const areas = others.split(',').slice(0, 2);

  await loginAdmin(page, { openAdmin: false });
  const authState = await page.evaluate(() => localStorage.getItem('ops_auth'));
  const auth = { Authorization: `Bearer ${authState ? JSON.parse(authState).accessToken : ''}` };
  await page.request.delete(`/api/admin/cache?namespace=${encodeURIComponent(NS)}`, { headers: auth });
  await selectDomain(page, NS);
  await page.locator('textarea:visible').first().fill(Q);
  await page.getByTitle('전송 (Ctrl+Enter)').click();

  const note = page.getByTestId('term-notes').last();
  await expect(note).toBeVisible({ timeout: 180_000 });
  await expect(note).toContainText(`다른 팀에서는 '${TERM}'`);
  // 근거 카드는 평소 접힘(10/30) — "근거 N건 ▸"를 눌러야 펼쳐진다
  const toggle = page.getByTestId('evidence-toggle').last();
  if (await toggle.count()) {
    await expect(page.getByText(/검색된 문서 \d+건|정책 근거 \d+건/)).toHaveCount(0);
    await toggle.click();
    await expect(page.getByText(/검색된 문서 \d+건|정책 근거 \d+건/).first()).toBeVisible();
  }
  // 화면 = 서버가 저장한 참고(메시지 metadata) — 서버 규칙(하위 영역·정규화 비교·정책 파트 제외)을 시험에서 다시 짜지 않는다(리뷰 L4)
  const saved = JSON.parse(psql(`SELECT coalesce(m.metadata->'term_notes', '[]')::text FROM ops_message m JOIN ops_conversation c ON c.id = m.conversation_id
    WHERE c.id > ${before} AND m.role = 'assistant' ORDER BY m.id DESC LIMIT 1`)) as { term: string; others: { area: string }[] }[];
  const savedAreas = saved.find((x) => x.term === TERM)?.others.map((o) => o.area) ?? [];
  expect(savedAreas.length).toBeGreaterThan(0);
  for (const a of savedAreas) await expect(note).toContainText(a);
  expect(savedAreas.every((a) => areas.includes(a) || others.split(',').includes(a))).toBe(true);   // 다른 영역 용어집에 실제로 있는 팀만

  // 대화 기록(메시지 metadata.term_notes)으로 다시 그려도 그대로 — 새로고침하면 빈 채팅이라 사이드바에서 그 대화를 다시 연다
  await page.reload();
  await selectDomain(page, NS);
  await page.getByText(Q, { exact: false }).first().click();
  await expect(page.getByTestId('term-notes').last()).toContainText(`다른 팀에서는 '${TERM}'`, { timeout: 30_000 });
});
