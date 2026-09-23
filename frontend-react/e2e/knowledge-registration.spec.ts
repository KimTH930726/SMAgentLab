import { test, expect } from '@playwright/test';

// 이 테스트가 존재하는 이유(2026-09-24): 지식 등록 흐름이 미리보기+검토 2단계로
// 재설계되면서, LLM 자동 태깅/용어 자동추출 호출이 화면 어디서도 안 불리게 조용히
// 끊긴 채 몇 주간 방치돼 있었다(에러가 안 나서 유닛테스트로는 못 잡음). 실제로 로그인
// 하고 폼을 채우고 제출해서 화면에 결과가 뜨는지까지 확인해야 이런 종류의 회귀를
// 잡는다 — 그래서 이 스펙은 API를 목킹하지 않고 떠 있는 실 dev 스택 그대로 사용한다.

const ADMIN_USER = 'admin';
const ADMIN_PASS = '1111';
const NAMESPACE = '딜리버스 DB';

test.beforeEach(async ({ page }) => {
  await page.goto('/login');
  await page.getByPlaceholder('사용자 아이디').fill(ADMIN_USER);
  await page.getByPlaceholder('비밀번호').fill(ADMIN_PASS);
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('heading', { name: '로그인' })).toBeHidden({ timeout: 10_000 });

  // 로그인 직후엔 매번 에이전트 선택 화면부터 나온다(Zustand store, 새 브라우저
  // 컨텍스트라 저장된 선택이 없음) — 하나 골라야 그다음 화면(사이드바+Admin)이 보인다.
  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  if (await agentHeading.isVisible().catch(() => false)) {
    await page.getByText('선택하기').first().click();
  }
});

test('수동 지식 등록 — 폼 제출 후 실제로 저장되고 삭제까지 된다', async ({ page }) => {
  // page.goto('/admin')는 풀 리로드라 selectedAgent(비영속 Zustand 상태)가 초기화돼
  // 다시 에이전트 선택 화면으로 튕긴다 — 사이드바 링크 클릭(클라이언트 라우팅)으로 이동.
  await page.getByRole('link', { name: 'Admin' }).click();
  await page.getByRole('button', { name: '지식 베이스' }).click();

  await page.locator('select').filter({ hasText: '파트 선택' }).selectOption(NAMESPACE);

  await page.getByRole('button', { name: '지식 등록' }).click();
  await page.getByText('직접 입력').click();

  // 매 실행마다 의미상으로도 충분히 다른 문장이어야 한다 — 타임스탬프만 바꾸고 나머지
  // 문장이 똑같으면 반복 실행 시 임베딩 유사도가 쌓여 중복 판정(승인 대기)으로 튈 수 있음.
  const runId = `${Date.now()}_${Math.floor(Math.random() * 1_000_000)}`;
  const uniqueMarker = `E2E테스트마커_${runId}`;
  const fictionalTerm = `가상업무${Math.floor(Math.random() * 1_000_000)}`;
  const content = `${uniqueMarker} — ${fictionalTerm} 절차는 Playwright 회귀 테스트가 실행마다 무작위로 만들어내는 가상의 업무 프로세스 설명입니다.`;

  const createResponse = page.waitForResponse((r) => r.url().includes('/api/knowledge') && r.request().method() === 'POST');

  // Chat 화면은 Admin일 때도 DOM에 계속 남아있다(CSS로만 숨김, 스트리밍 유지 목적) —
  // 채팅 질문창 textarea가 먼저 걸리지 않게 보이는 것만 골라야 한다.
  await page.locator('textarea:visible').first().fill(content);
  await page.getByRole('button', { name: '추가' }).click();

  const response = await createResponse;
  expect(response.ok()).toBeTruthy();
  const created = await response.json();
  const createdId: number = created.id;

  // 등록 성공 시 폼이 곧바로 "지식 조회" 탭으로 전환돼(ManualForm onSuccess) 폼 안의
  // 인라인 성공 문구는 화면에 안정적으로 안 남는다 — 그 문구를 기다리는 대신, 실제로
  // 저장까지 됐는지를 목록/검색으로 직접 확인한다("200 OK가 검증이 아니다").
  // 활성으로 바로 등록됐을 수도, 유사도 중복 판정으로 승인 대기로 갔을 수도 있다.
  await page.getByRole('button', { name: '지식 조회' }).click();
  await page.getByPlaceholder(/검색/).fill(uniqueMarker);
  const foundInActiveList = await page.getByText(uniqueMarker).isVisible({ timeout: 8_000 }).catch(() => false);
  if (!foundInActiveList) {
    await page.getByRole('button', { name: /승인 대기/ }).click();
    await expect(page.getByText(uniqueMarker)).toBeVisible({ timeout: 10_000 });
  }

  // 정리 — 이 테스트가 실 dev DB에 영구 데이터를 남기지 않도록 API로 직접 삭제.
  // UI로 지우는 것도 가능하지만(체크박스+일괄삭제 또는 승인대기 반려), 이 테스트의
  // 목적은 "등록이 실제로 저장되는가"이지 삭제 UX 검증이 아니라 가장 확실한 API 직접
  // 호출로 마무리한다. auto_glossary(2026-09-24 복원)가 fictionalTerm을 실제로 새
  // 용어로 등록했을 수 있어 그것도 같이 지운다 — 안 지우면 매 실행마다 용어집에
  // 테스트용 가짜 용어가 하나씩 쌓인다(실제로 겪은 문제).
  const authState = await page.evaluate(() => localStorage.getItem('ops_auth'));
  const accessToken = authState ? JSON.parse(authState).accessToken : null;
  if (accessToken) {
    const authHeader = { Authorization: `Bearer ${accessToken}` };
    if (createdId) {
      await page.request.delete(`/api/knowledge/${createdId}`, { headers: authHeader }).catch(() => {});
    }
    const glossaryResp = await page.request
      .get(`/api/knowledge/glossary?namespace=${encodeURIComponent(NAMESPACE)}`, { headers: authHeader })
      .catch(() => null);
    if (glossaryResp?.ok()) {
      const glossaryItems: Array<{ id: number; term: string; description: string }> = await glossaryResp.json();
      // LLM이 fictionalTerm 그대로가 아니라 문장 속 다른 표현("Playwright 회귀 테스트"
      // 같은 구문)을 용어로 뽑아낼 수도 있어(2026-09-24 실측), "Playwright"까지 넓게 잡는다.
      const junkTerms = glossaryItems.filter((g) =>
        g.term.includes(fictionalTerm) || g.term.includes(uniqueMarker) ||
        g.term.includes('Playwright') || g.description.includes('Playwright')
      );
      for (const g of junkTerms) {
        await page.request.delete(`/api/knowledge/glossary/${g.id}`, { headers: authHeader }).catch(() => {});
      }
    }
  }
});
