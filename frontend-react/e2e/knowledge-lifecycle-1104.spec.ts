import { test, expect, type Page } from '@playwright/test';
import { chooseLocation, goAdmin, selectDomain } from './admin-nav';
import { API, adminToken, apiFetch, loginAdmin } from './auth';
import { createSystem, dropSystem, psql } from './v0-fixture';

// 11/04 지식 수명주기 기능을 사용자 흐름으로 — ① 대량 텍스트 조각 미리보기 "비슷한 지식" 배지(비교·새로 등록·등록 안 함·같은 글)
// ② 채팅 [답변 틀림] 사유 칩 ③ 근거 최신성 배지 ④ 검토 필요 '원문 바뀜' ⑤ 지식 공백 등록 창의 [기존 것에 합치기] ⑥ 승인 대기 [원문 ↗].
// 테스트마다 임시 시스템 컬렉션을 따로 만들고(서로 독립) 끝에 통째로 지운다. 화면 문구가 아니라 DB 값으로 전/후를 본다.
// LLM은 피할 수 없을 때만 — 넣을 곳 판정(①), 채팅 답·정정 분석(②③). E2E는 로컬 LLM(llm-local.setup.ts).

const mkNs = (tag: string) => `E2E수명${tag}_${Date.now()} 시스템`;
const label = (ns: string) => ns.replace(/ 시스템$/, '');
const q = (s: string) => s.replace(/'/g, "''");
const nsId = (ns: string) => `(SELECT id FROM ops_namespace WHERE name = '${q(ns)}')`;

async function addKnowledge(ns: string, content: string, keepNew = true): Promise<{ id: number; pending: boolean }> {
  const r = await apiFetch(`${API}/api/knowledge`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${await adminToken()}` },
    body: JSON.stringify({ namespace: ns, content, keep_new: keepNew }),
  });
  expect(r.status, await r.clone().text()).toBe(201);
  const b = await r.json();
  return { id: b.id as number, pending: !!b.pending_review };
}

/** 붙여넣기 등록(API — 미리보기 → 일괄 등록, 원문 원장 연결)으로 지식을 넣고 입력 순서대로 id. 각 글은 제목 줄(##)로 시작해야 조각이 글마다 나뉜다 */
async function addViaPaste(ns: string, texts: string[]): Promise<number[]> {
  const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${await adminToken()}` };
  const prev = await (await apiFetch(`${API}/api/knowledge/import/text-split/preview`, {
    method: 'POST', headers: H, body: JSON.stringify({ raw_text: texts.join('\n\n'), strategy: 'section', namespace: ns }) })).json();
  expect(prev.count).toBe(texts.length);
  const items = (prev.chunks as string[]).map((c, i) => ({ content: c, heading_path: prev.heading_paths[i], source_document_id: prev.source_document_id }));
  const r = await apiFetch(`${API}/api/knowledge/bulk`, { method: 'POST', headers: H,
    body: JSON.stringify({ namespace: ns, items, source_file: '텍스트 직접입력', source_type: 'paste_split' }) });
  expect(r.status, await r.clone().text()).toBe(201);
  const job = (await r.json()).job_id;
  await expect.poll(async () => (await (await apiFetch(`${API}/api/knowledge/ingestion-jobs/${job}`, { headers: H })).json()).status,
    { timeout: 60_000 }).toBe('completed');
  return (prev.chunks as string[]).map((c) => Number(psql(`SELECT id FROM rag_knowledge WHERE namespace_id = ${nsId(ns)} AND status = 'active' AND content = '${q(c)}'`)));
}

/** 임시 컬렉션 정리 — 이 컬렉션 지식을 가리키는 등록 단계 중복 판단(AI 감사 원장, FK 없음)과 넣을 곳 판단, 그다음 컬렉션 통째(지식·대화·신고·질의 로그·원문 원장은 CASCADE) */
function cleanupNs(ns: string) {
  const id = psql(`SELECT id FROM ops_namespace WHERE name = '${q(ns)}'`);
  if (!id) return;
  psql(`DELETE FROM ops_ai_decision WHERE kind = 'duplicate_check' AND target_ids && ARRAY(SELECT id::bigint FROM rag_knowledge WHERE namespace_id = ${id})`);
  psql(`DELETE FROM ops_ai_decision WHERE kind = 'knowledge_location' AND (final_value->>'namespace_id' = '${id}' OR ai_value->>'namespace_id' = '${id}')`);
  psql(`BEGIN; UPDATE rag_knowledge SET supersedes_id = NULL WHERE namespace_id = ${id}; DELETE FROM ops_namespace WHERE id = ${id}; COMMIT;`);
  dropSystem(ns);
}

async function openKnowledgeAdmin(page: Page, ns: string) {
  await loginAdmin(page, { openAdmin: false });
  await page.getByRole('link', { name: 'Admin' }).click();
  await goAdmin(page, '운영 지식');
  await selectDomain(page, ns, label(ns));
}

/** 질문 → 답변 완료([답변 틀림] 노출)까지 — 실 LLM 답(로컬), 느린 날 대비 넉넉히 */
async function ask(page: Page, question: string) {
  const before = await page.getByRole('button', { name: '답변 틀림' }).count();
  await page.locator('textarea:visible').first().fill(question);
  await page.getByTitle('전송 (Ctrl+Enter)').click();
  await expect(page.getByRole('button', { name: '답변 틀림' })).toHaveCount(before + 1, { timeout: 300_000 });
}

const fmtDay = (s: string) => new Date(s).toLocaleDateString('ko-KR', { year: 'numeric', month: 'numeric', day: 'numeric', timeZone: 'Asia/Seoul' });
/** 조각 행(체크박스가 든 카드) — 배지에서 가장 가까운 rounded-lg 카드 */
const rowOf = (page: Page, testId: string) => page.getByTestId(testId).locator('xpath=ancestor::div[contains(@class,"rounded-lg")][1]');

// ── ① 대량 텍스트 조각 미리보기 — 비슷한 지식 배지 ─────────────────────────────────────────────
test('대량 텍스트 미리보기 — 비슷한 조각 배지 [비교]→새로 등록·등록 안 함, 같은 글은 배지만(사람이 뺌), 무관한 조각은 배지 없음 → 등록 결과 DB', async ({ page }) => {
  test.setTimeout(300_000);
  const NS = mkNs('대량');
  try {
    createSystem(NS);
    const A = '## 정산 배치 재기동\n정산 배치가 멈추면 배치 상태를 확인한 뒤 재기동한다. 재기동 후 정산 파일 건수를 원장과 대조한다.';
    const B = '## 주문 취소 환불 처리\n주문 취소 시 결제 수단별로 환불 처리한다. 카드 결제는 승인 취소, 계좌이체는 환불 계좌로 송금한다.';
    const C = '## 회원 등급 산정\n회원 등급은 최근 6개월 구매 금액으로 매월 1일에 산정한다. 등급별 혜택은 쿠폰과 적립률로 나뉜다.';
    // 기존 지식은 "지난번 붙여넣기"로(원문 원장 연결) — 직접 입력 지식(원문 없음)과의 비교는
    // 아래 별도 테스트(직접 입력 지식도 조각 미리보기 비교 대상)가 본다(11/04 NULL 가드 결함 회귀 방지).
    const [a, b, c] = await addViaPaste(NS, [A, B.replace('처리한다. ', '처리한다.  '), C]);   // B 저장본은 공백 두 칸 — 붙여넣기는 한 칸(공백만 다른 같은 글)
    const countRows = () => psql(`SELECT count(*) FROM rag_knowledge WHERE namespace_id = ${nsId(NS)} AND status IN ('active','pending_review')`);
    expect(countRows()).toBe('3');

    const paste = [
      `${A}\n재기동은 하루 3회까지만 한다.`,                                            // #0 A와 거의 같음 → [새로 등록]
      '## 사내 식당 메뉴 안내\n사내 식당은 매주 월요일에 다음 주 메뉴를 게시판에 올린다. 채식 메뉴는 화요일과 목요일에 있다.',   // #1 무관
      B,                                                                                // #2 B와 공백만 다름 → 같은 글
      `${C}\nVIP 등급은 별도 심사를 거쳐 분기마다 정한다.`,                               // #3 C와 거의 같음 → [등록 안 함]
    ].join('\n\n');

    await openKnowledgeAdmin(page, NS);
    await page.getByRole('button', { name: '지식 등록', exact: true }).click();
    await page.getByText('대량 텍스트', { exact: true }).first().click();
    await page.locator('textarea:visible').first().fill(paste);
    await page.getByRole('button', { name: '청크 검토 & 등록' }).click();
    await expect(page.getByText(/^청크 검토 — /)).toBeVisible({ timeout: 120_000 });   // 제목 2개 이상이면 section(LLM 없음), 아니면 분석
    await chooseLocation(page, NS);

    // 배지 — 넣을 곳이 정해진 뒤 조각마다 같은 규칙으로 확인(임베딩)
    await expect(page.getByTestId('chunk-similar-0')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('chunk-similar-0')).toContainText(`#${a}`);
    await expect(page.getByTestId('chunk-similar-3')).toContainText(`#${c}`);
    await expect(page.getByTestId('chunk-same-2')).toContainText(`같은 글 #${b}`);
    for (const t of ['chunk-similar-1', 'chunk-same-1', 'chunk-decided-1', 'chunk-similar-2']) await expect(page.getByTestId(t)).toHaveCount(0);
    // 같은 글은 배지만 — 체크는 그대로(서버가 같은 원본 안 반복은 건너뛰고 다른 원본은 승인 대기, 11/04 리뷰 H1) · 사람이 뺀다
    await expect(rowOf(page, 'chunk-same-2').locator('input[type=checkbox]')).toBeChecked();
    await rowOf(page, 'chunk-same-2').locator('input[type=checkbox]').uncheck();
    // 요약 = [비교]로 정할 조각만(같은 글 제외) — 2개
    await expect(page.getByTestId('chunk-similar-summary')).toContainText('조각 2개');

    // #0 [비교] → 기존 A와 나란히 → [새로 등록] — 배지가 '새로 등록', 체크 유지
    await page.getByTestId('chunk-similar-0').click();
    let step = page.getByTestId('similar-step');
    await expect(step.getByTestId('similar-existing')).toContainText(`#${a}`);
    await step.getByTestId('similar-new').click();
    await expect(step).toHaveCount(0);
    await expect(page.getByTestId('chunk-decided-0')).toHaveText('새로 등록');
    await expect(rowOf(page, 'chunk-decided-0').locator('input[type=checkbox]')).toBeChecked();

    // #3 [비교] → [등록 안 함] — 배지 '등록 안 함', 체크 해제
    await page.getByTestId('chunk-similar-3').click();
    step = page.getByTestId('similar-step');
    await expect(step.getByTestId('similar-existing')).toContainText(`#${c}`);
    await step.getByTestId('similar-skip').click();
    await expect(step).toHaveCount(0);
    await expect(page.getByTestId('chunk-decided-3')).toHaveText('등록 안 함');
    await expect(rowOf(page, 'chunk-decided-3').locator('input[type=checkbox]')).not.toBeChecked();

    // 등록 안 함·새로 등록 판단은 등록이 끝난 뒤에만 AI 감사 원장에(창을 취소하면 안 남게, 11/04 리뷰 L3) — 등록 전엔 없음
    const decisions = () => psql(`SELECT coalesce(string_agg((target_ids[1])::text || ':' || (final_value->>'action'), ',' ORDER BY target_ids[1]), '') FROM ops_ai_decision
      WHERE kind = 'duplicate_check' AND signals->>'stage' = 'register' AND target_ids && ARRAY[${a}, ${c}]::bigint[]`);
    expect(decisions()).toBe('');

    const posted = page.waitForResponse((r) => r.url().endsWith('/api/knowledge/bulk') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '선택 항목 등록 (2건)' }).click();
    const res = await posted;
    expect(res.status(), await res.text()).toBe(201);
    const job = (await res.json()).job_id;
    const token = await adminToken();
    await expect.poll(async () => (await (await apiFetch(`${API}/api/knowledge/ingestion-jobs/${job}`, { headers: { Authorization: `Bearer ${token}` } })).json()).status,
      { timeout: 60_000 }).toBe('completed');
    await expect.poll(decisions).toBe(`${a}:approve,${c}:reject`);

    // DB — 새로 등록한 #0은 바로 사용 중(승인 대기 아님), 무관한 #1도 사용 중, 같은 글 #2·등록 안 함 #3은 저장 안 됨
    const stat = (like: string) => psql(`SELECT coalesce(string_agg(status, ','), '') FROM rag_knowledge WHERE namespace_id = ${nsId(NS)} AND content LIKE '%${q(like)}%' AND id NOT IN (${a}, ${b}, ${c})`);
    expect(stat('하루 3회까지만')).toBe('active');
    expect(stat('사내 식당')).toBe('active');
    expect(stat('주문 취소 시')).toBe('');
    expect(stat('VIP 등급은 별도 심사')).toBe('');
    expect(countRows()).toBe('5');
    expect(psql(`SELECT count(*) FROM rag_knowledge WHERE namespace_id = ${nsId(NS)} AND status = 'pending_review'`)).toBe('0');
    // 기존 지식은 그대로(합치기를 안 했으니)
    expect(psql(`SELECT content FROM rag_knowledge WHERE id = ${c}`)).toBe(C);
  } finally {
    cleanupNs(NS);
  }
});

// 11/04 E2E에서 찾은 결함 — 붙여넣기 조각(원문 원장 id가 실림)과 직접 입력 지식(source_document_id NULL)을 비교하면
// find_similar_active_knowledge의 "옛 버전 빼기" 조건 `NOT (status='active' AND source_document_id = ANY(...))`가 NULL이 돼
// 원문 없는 지식이 후보에서 통째로 빠졌다(배지 없음 + 일괄 등록도 승인 대기로 안 감) — retrieval.py IS NOT NULL 가드로 고침, 회귀 방지. LLM 0.
test('직접 입력 지식도 조각 미리보기·일괄 등록의 비슷한 지식 비교 대상(원문 원장 없는 지식이 빠지지 않음)', async () => {
  test.setTimeout(180_000);
  const NS = mkNs('널');
  try {
    createSystem(NS);
    const A = '## 정산 배치 재기동\n정산 배치가 멈추면 배치 상태를 확인한 뒤 재기동한다. 재기동 후 정산 파일 건수를 원장과 대조한다.';
    const a = (await addKnowledge(NS, A)).id;   // 직접 입력 — source_document_id NULL
    expect(psql(`SELECT source_document_id IS NULL FROM rag_knowledge WHERE id = ${a}`)).toBe('t');
    const H = { 'Content-Type': 'application/json', Authorization: `Bearer ${await adminToken()}` };
    const near = `${A}\n재기동은 하루 3회까지만 한다.`;
    const prev = await (await apiFetch(`${API}/api/knowledge/import/text-split/preview`, {
      method: 'POST', headers: H, body: JSON.stringify({ raw_text: `${near}\n\n## 사내 식당\n사내 식당 메뉴는 월요일에 올린다.`, strategy: 'section', namespace: NS }) })).json();
    const sim = await (await apiFetch(`${API}/api/knowledge/similar/batch`, { method: 'POST', headers: H,
      body: JSON.stringify({ namespace: NS, items: (prev.chunks as string[]).map((c) => ({ content: c, source_document_id: prev.source_document_id })) }) })).json();
    // 같은 조각을 원문 id 없이 물으면 A가 나온다(대조군) — 차이는 원문 id 하나뿐
    const simNoDoc = await (await apiFetch(`${API}/api/knowledge/similar/batch`, { method: 'POST', headers: H,
      body: JSON.stringify({ namespace: NS, items: (prev.chunks as string[]).map((c) => ({ content: c })) }) })).json();
    expect(simNoDoc[0].matches.map((m: { id: number }) => m.id)).toContain(a);
    expect.soft(sim[0].matches.map((m: { id: number }) => m.id), '조각 미리보기 비교에서 직접 입력 지식이 빠짐').toContain(a);
    // 일괄 등록도 같은 조건을 쓴다 — 원문 id가 실린 거의 같은 조각이 승인 대기로 가야 한다(고치기 전엔 바로 사용 중)
    const items = (prev.chunks as string[]).map((c, i) => ({ content: c, heading_path: prev.heading_paths[i], source_document_id: prev.source_document_id }));
    const r = await apiFetch(`${API}/api/knowledge/bulk`, { method: 'POST', headers: H,
      body: JSON.stringify({ namespace: NS, items, source_file: '텍스트 직접입력', source_type: 'paste_split' }) });
    expect(r.status).toBe(201);
    const job = (await r.json()).job_id;
    await expect.poll(async () => (await (await apiFetch(`${API}/api/knowledge/ingestion-jobs/${job}`, { headers: H })).json()).status,
      { timeout: 60_000 }).toBe('completed');
    expect.soft(psql(`SELECT status FROM rag_knowledge WHERE namespace_id = ${nsId(NS)} AND id <> ${a} AND content LIKE '%하루 3회까지만%'`),
      '일괄 등록 중복 판정에서 직접 입력 지식이 빠짐').toBe('pending_review');
  } finally {
    cleanupNs(NS);
  }
});

// ── ② 채팅 [답변 틀림] 사유 칩 ───────────────────────────────────────────────────────────────
test('채팅 [답변 틀림] — 사유 칩 4개, [내용이 낡음]만 고르고 한 줄 비워 접수 → user_reason=stale·user_input=칩 이름 → 정정 요청 카드에 신고 사유', async ({ page }) => {
  test.setTimeout(900_000);
  const NS = mkNs('사유');
  try {
    createSystem(NS);
    await addKnowledge(NS, '## 사내 VPN 비밀번호\n사내 VPN 접속 비밀번호는 90일마다 변경해야 한다. 변경은 계정 포털에서 한다.');
    await loginAdmin(page, { openAdmin: false });
    await selectDomain(page, NS, label(NS));
    await ask(page, 'VPN 비밀번호 변경 주기가 어떻게 돼?');

    await page.getByRole('button', { name: '답변 틀림' }).last().click();
    const chips = page.getByTestId('correction-reasons');
    await expect(chips.getByRole('button')).toHaveText(['내용이 낡음', '다른 시스템 얘기', '단계·조건 빠짐', '근거를 못 찾음']);
    const send = page.getByTitle('정정 접수 (Ctrl+Enter)');
    await expect(send).toBeDisabled();                                   // 칩도 한 줄도 없으면 못 보냄
    await chips.getByRole('button', { name: '내용이 낡음' }).click();
    await expect(chips.getByRole('button', { name: '내용이 낡음' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('textarea:visible').first()).toHaveValue('');
    await expect(send).toBeEnabled();                                    // 칩만으로 보낼 수 있음
    await send.click();
    await expect(page.getByText('정정 신고 접수됨')).toBeVisible({ timeout: 300_000 });   // AI 분석(로컬 LLM)

    const row = psql(`SELECT id || '|' || user_reason || '|' || user_input FROM ops_improvement_item WHERE namespace_id = ${nsId(NS)} AND user_reason IS NOT NULL`);
    const [id, reason, input] = row.split('|');
    expect(reason).toBe('stale');
    expect(input).toBe('내용이 낡음');
    expect(psql(`SELECT count(*) FROM ops_improvement_item WHERE namespace_id = ${nsId(NS)} AND user_reason IS NOT NULL`)).toBe('1');

    await page.getByRole('link', { name: 'Admin' }).click();
    await goAdmin(page, '정정 요청');
    await selectDomain(page, NS, label(NS));
    await expect(page.getByTestId(`correction-reason-${id}`)).toContainText('내용이 낡음', { timeout: 15_000 });
  } finally {
    cleanupNs(NS);   // 신고 줄·대화는 컬렉션 CASCADE로 함께 지워짐
  }
});

// ── ③ 근거 최신성 배지 ───────────────────────────────────────────────────────────────────────
test('채팅 근거 최신성 — [근거 ▸] 카드마다 배지(검토 기한 지남 · 검토 날짜 · 수정 날짜), /freshness 값과 일치', async ({ page }) => {
  test.setTimeout(600_000);
  const NS = mkNs('최신');
  try {
    createSystem(NS);
    const k1 = (await addKnowledge(NS, '## 택배 송장 재발행 방법\n택배 송장 재발행은 주문 관리 화면의 송장 탭에서 재발행 버튼으로 한다.')).id;
    const k2 = (await addKnowledge(NS, '## 택배 송장 재발행 제한\n택배 송장 재발행은 주문 한 건당 하루 2회까지만 할 수 있다.')).id;
    const k3 = (await addKnowledge(NS, '## 택배 송장 재발행 후 처리\n택배 송장을 재발행하면 기존 송장 번호는 자동으로 취소된다.')).id;
    // k1 = 등록 100일 전(기본 주기 90일 지남) · k2 = 오늘 검토 · k3 = 그대로(마지막 수정·등록 날짜)
    psql(`UPDATE rag_knowledge SET created_at = NOW() - interval '100 days', reviewed_at = NULL, content_edited_at = NULL WHERE id = ${k1}`);
    psql(`UPDATE rag_knowledge SET reviewed_at = NOW() WHERE id = ${k2}`);

    const token = await adminToken();
    const fr = await (await apiFetch(`${API}/api/knowledge/freshness`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify({ ids: [k1, k2, k3] }),
    })).json() as Record<string, { last_at: string; reviewed: boolean; overdue: boolean; interval_days: number | null }>;
    expect([fr[k1].overdue, fr[k2].overdue, fr[k3].overdue]).toEqual([true, false, false]);
    expect([fr[k1].reviewed, fr[k2].reviewed, fr[k3].reviewed]).toEqual([false, true, false]);
    expect(fr[k1].interval_days).toBe(90);
    const expected = (id: number) => (fr[id].overdue ? '검토 기한 지남' : `${fr[id].reviewed ? '검토' : '수정'} ${fmtDay(fr[id].last_at)}`);

    await loginAdmin(page, { openAdmin: false });
    await selectDomain(page, NS, label(NS));
    await ask(page, '택배 송장 재발행은 어떻게 하고 제한은 뭐야?');
    await page.getByRole('button', { name: /근거 \d+건/ }).last().click();
    const badges = page.locator('[data-testid^="freshness-"]');
    await expect(badges.first()).toBeVisible({ timeout: 15_000 });
    const ids = (await badges.evaluateAll((els) => els.map((e) => Number(e.getAttribute('data-testid')!.replace('freshness-', '')))));
    expect(ids).toContain(k1);   // 기한 지난 근거가 실제로 카드에 나와야 이 테스트가 뜻이 있다
    for (const id of ids) {
      expect([k1, k2, k3]).toContain(id);   // 임시 컬렉션 밖 지식이 섞이지 않음
      await expect(page.getByTestId(`freshness-${id}`)).toHaveText(expected(id));
    }
    await expect(page.getByTestId(`freshness-${k1}`)).toHaveText('검토 기한 지남');
  } finally {
    cleanupNs(NS);
  }
});

// ── ④ 검토 필요 '원문 바뀜' ─────────────────────────────────────────────────────────────────
// 실 동기화 연결은 건드리지 않는다 — 실행기가 집지 않는 status='stopped' 임시 연결 줄을 하나 만들어 충돌 페이지를 넣고, 끝에 지운다.
test('검토 필요 — 동기화 충돌(retry_pages why=conflict) 페이지는 기한 전이어도 맨 위 "원문 바뀜" · 정리 후 사라짐', async ({ page }) => {
  test.setTimeout(120_000);
  const NS = mkNs('원문');
  const PID = `e2e${Date.now()}`;
  const ROOT = `e2e://review-conflict/${PID}`;
  const connCount = psql(`SELECT count(*) FROM rag_source_connection`);
  try {
    createSystem(NS);
    const k = (await addKnowledge(NS, '## 재고 실사 절차\n재고 실사는 분기 마지막 주에 하고 차이는 재고 조정 화면에 기록한다.')).id;
    const other = (await addKnowledge(NS, '## 입고 검수 기준\n입고 검수는 수량과 유통기한을 확인하고 불량은 반품 처리한다.')).id;
    psql(`UPDATE rag_knowledge SET confluence_page_id = '${PID}' WHERE id = ${k}`);
    psql(`INSERT INTO rag_source_connection (team_id, root_url, title, status, retry_pages) VALUES (-1, '${ROOT}', 'e2e', 'stopped',
          '[{"page_id": "${PID}", "title": "e2e", "why": "conflict"}]'::jsonb)`);

    const token = await adminToken();
    const due = async () => (await (await apiFetch(`${API}/api/knowledge/review/due?namespace=${encodeURIComponent(NS)}`,
      { headers: { Authorization: `Bearer ${token}` } })).json()) as { due: Array<{ key: string; ids: number[]; source_changed: boolean; overdue_days: number | null }> };
    const d = await due();
    expect(d.due.map((x) => x.key)).toEqual([`p:${PID}`]);   // 기한 전인 다른 지식은 안 나옴
    expect(d.due[0]).toMatchObject({ ids: [k], source_changed: true, overdue_days: null });

    await openKnowledgeAdmin(page, NS);
    await page.getByTestId('tab-review-due').click();
    await expect(page.getByTestId(`review-due-p:${PID}`)).toContainText('원문 바뀜');
    await expect(page.getByTestId(`review-due-k:${other}`)).toHaveCount(0);

    psql(`DELETE FROM rag_source_connection WHERE root_url = '${ROOT}'`);
    expect((await due()).due).toEqual([]);   // 충돌이 풀리면(원복) 목록에서 빠짐
  } finally {
    psql(`DELETE FROM rag_source_connection WHERE root_url = '${ROOT}'`);
    expect(psql(`SELECT count(*) FROM rag_source_connection`)).toBe(connCount);
    cleanupNs(NS);
  }
});

// ── ⑤ 지식 공백 등록 창 — [기존 것에 합치기] ─────────────────────────────────────────────────
test('통계 지식 공백 [지식 등록] — 비슷한 지식이 있으면 비교 창 → [기존 것에 합치기] 저장 → 공백 질의 resolved_knowledge_id = 기존 지식', async ({ page }) => {
  test.setTimeout(180_000);
  const NS = mkNs('공백');
  const BASE = '## 정산 배치 재기동\n정산 배치가 멈추면 배치 상태를 확인한 뒤 재기동한다. 재기동 후 정산 파일 건수를 원장과 대조한다.';
  const QUESTION = `E2E 공백 질의 ${Date.now()} — 정산 배치 재기동은 하루 몇 번까지 돼?`;
  try {
    createSystem(NS);
    const base = (await addKnowledge(NS, BASE)).id;
    const log = psql(`INSERT INTO ops_query_log (question, answer, status, namespace_id) VALUES ('${q(QUESTION)}', '관련 지식을 찾지 못했습니다.', 'no_knowledge', ${nsId(NS)}) RETURNING id`).split(/\s+/)[0];
    const rows = () => psql(`SELECT count(*) FROM rag_knowledge WHERE namespace_id = ${nsId(NS)}`);
    expect(rows()).toBe('1');

    await loginAdmin(page, { openAdmin: false });
    await page.getByRole('link', { name: 'Admin' }).click();
    await goAdmin(page, '통계');
    await selectDomain(page, NS, label(NS));
    await page.locator('main').locator('button').filter({ has: page.getByText('지식 공백', { exact: true }) }).first().click();
    await expect(page.getByText('지식 공백 질의 (1건)')).toBeVisible();
    await page.getByText(QUESTION).first().click();
    await page.getByRole('button', { name: '지식 등록', exact: true }).click();
    const dialog = page.getByRole('dialog').filter({ hasText: '원본 질문' });
    await expect(dialog).toContainText(QUESTION);
    await dialog.locator('textarea').fill(`${BASE}\n재기동은 하루 3회까지만 한다.`);
    await dialog.getByRole('button', { name: '지식 등록', exact: true }).click();

    const step = page.getByTestId('similar-step');
    await expect(step.getByTestId('similar-existing')).toContainText(`#${base}`, { timeout: 30_000 });
    await step.getByTestId('similar-merge-open').click();
    await expect(page.getByLabel('합친 내용')).toHaveValue(/하루 3회까지만/);
    await step.getByTestId('similar-merge-save').click();
    await expect(page.getByText('지식 공백 질의 (0건)')).toBeVisible({ timeout: 30_000 });   // 메운 공백은 목록에서 빠짐

    await expect.poll(() => psql(`SELECT resolved_knowledge_id FROM ops_query_log WHERE id = ${log}`)).toBe(String(base));
    expect(psql(`SELECT resolved_at IS NOT NULL FROM ops_query_log WHERE id = ${log}`)).toBe('t');
    expect(rows()).toBe('1');   // 새 조각 없이
    expect(psql(`SELECT content LIKE '%하루 3회까지만%' FROM rag_knowledge WHERE id = ${base}`)).toBe('t');
    await expect.poll(() => psql(`SELECT final_value->>'action' FROM ops_ai_decision WHERE kind = 'duplicate_check' AND signals->>'stage' = 'register' AND target_ids && ARRAY[${base}]::bigint[] ORDER BY id DESC LIMIT 1`)).toBe('merge');

    // 화면 — 저장 뒤 비교 창이 실제로 없어지고, 화면이 다시 눌려야 한다(투명하게 남은 창 껍데기가 덮고 있지 않은지)
    await page.getByRole('dialog', { name: /지식 공백 질의/ }).getByRole('button', { name: '닫기' }).click({ timeout: 5_000 });
    await expect(page.getByText('지식 공백 질의 (0건)')).toHaveCount(0);
    await expect(step).toHaveCount(0);
  } finally {
    cleanupNs(NS);   // 질의 로그는 컬렉션 CASCADE
  }
});

// ── ⑥ 승인 대기 비교 창 [원문 ↗] ─────────────────────────────────────────────────────────────
test('승인 대기 비교 창 — 컨플 페이지가 있는 조각이면 [신규 원문 ↗]·[기존 #id 원문 ↗] 링크(pageId)', async ({ page }) => {
  test.setTimeout(120_000);
  const NS = mkNs('원문링크');
  const host = psql(`SELECT substring(root_url from '^(https?://[^/]+)') FROM rag_import_batch WHERE root_url ~ '^https?://' ORDER BY id DESC LIMIT 1`);
  test.skip(!host, '컨플 적재 배치(rag_import_batch.root_url)가 없어 원문 주소를 만들 수 없음');
  const P_NEW = `e2e${Date.now()}1`;
  const P_OLD = `e2e${Date.now()}2`;
  try {
    createSystem(NS);
    const BASE = '## 정산 배치 재기동\n정산 배치가 멈추면 배치 상태를 확인한 뒤 재기동한다. 재기동 후 정산 파일 건수를 원장과 대조한다.';
    const old = (await addKnowledge(NS, BASE)).id;
    const fresh = await addKnowledge(NS, `${BASE}\n재기동은 하루 3회까지만 한다.`, false);   // 중복 의심 → 승인 대기(비교 대상 = old)
    expect(fresh.pending).toBe(true);
    expect(psql(`SELECT matched_knowledge_id FROM rag_knowledge_duplicate_match WHERE new_knowledge_id = ${fresh.id}`)).toBe(String(old));
    psql(`UPDATE rag_knowledge SET confluence_page_id = '${P_NEW}' WHERE id = ${fresh.id}`);
    psql(`UPDATE rag_knowledge SET confluence_page_id = '${P_OLD}' WHERE id = ${old}`);

    await openKnowledgeAdmin(page, NS);
    await page.getByRole('button', { name: /^승인 대기/ }).click();
    await page.getByTestId('review-row').filter({ hasText: '하루 3회까지만' }).click();
    const links = page.getByTestId('review-page-links');
    await expect(links).toBeVisible({ timeout: 15_000 });
    await expect(links.getByRole('link', { name: '신규 원문 ↗' })).toHaveAttribute('href', `${host}/pages/viewpage.action?pageId=${P_NEW}`);
    await expect(links.getByRole('link', { name: `기존 #${old} 원문 ↗` })).toHaveAttribute('href', `${host}/pages/viewpage.action?pageId=${P_OLD}`);
    await expect(links.getByRole('link').first()).toHaveAttribute('target', '_blank');

    // 페이지 id가 없으면 링크 칸 자체가 없음
    await page.keyboard.press('Escape');
    psql(`UPDATE rag_knowledge SET confluence_page_id = NULL WHERE id IN (${fresh.id}, ${old})`);
    await page.reload();
    await page.getByRole('button', { name: /^승인 대기/ }).click();
    await page.getByTestId('review-row').filter({ hasText: '하루 3회까지만' }).click();
    await expect(page.getByTestId('review-diff-targets')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('review-page-links')).toHaveCount(0);
  } finally {
    cleanupNs(NS);
  }
});
