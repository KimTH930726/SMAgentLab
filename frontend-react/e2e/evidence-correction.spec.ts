import { test, expect, type Page } from '@playwright/test';
import { goAdmin, domainSelect } from './admin-nav';
import { createPolicyPart, dropPolicyPart } from './v0-fixture';
import { API, adminToken, loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(2026-10-01): 근거 정정 흐름은 버튼(근거 카드·답변 아래) → 입력창
// 정정 모드(ChatContainer) → 개선 원장 → 관리자 정정 검토 탭 → 승인 = 버전 교체 → 같은 질문
// 재답변까지 서로 다른 컴포넌트를 건너가는 경로라, 유닛테스트로는 "화면에서 실제로 이어지는가"를
// 못 본다. 핵심 약속("승인 전엔 절대 반영 안 됨, 승인 후엔 반영됨")을 실 dev 스택 그대로 확인한다.
//
// 데이터: 실 지식/정책을 건드리지 않도록 임시 정책서 파트(v0 방식 — 원문 통째, 2026-10-20 현행화)를 만들고 끝나면 통째로 지운다
// (ops_namespace ON DELETE CASCADE). 정책서 파트는 채팅 범위가 그 파트뿐이라 실데이터(v0 정책서 14개 파트)가 섞이지 않는다.

const NS = 'zz_e2e_correction';

test.describe.configure({ mode: 'serial' });
// 정정 접수·AI 판정·답변이 모두 LLM을 거친다(E2E는 로컬 LLM — llm-local.setup.ts) — 느린 날을 대비해 넉넉히
test.setTimeout(900_000);

test.beforeAll(async ({ request }) => {
  test.setTimeout(180_000);   // 임시 정책 임베딩 — 백엔드 컨테이너에 새 프로세스로 임베딩 모델을 올려 20~40초
  createPolicyPart(NS, [{ name: '임시 반품 기한', path: ['테스트'], body: '반품은 수령 후 7일 이내에 신청할 수 있다.' }]);
  const tok = await adminToken();
  // 같은 이름으로 다시 만든 파트에 지난 실행의 시맨틱 캐시가 남아 있으면, 캐시 답변이 이미 지운 근거를 가리킨다
  await request.delete(`${API}/api/admin/cache?namespace=${encodeURIComponent(NS)}`, { headers: { Authorization: `Bearer ${tok}` } });
  const k = await request.post(`${API}/api/knowledge`, {
    headers: { Authorization: `Bearer ${tok}` },
    data: { namespace: NS, content: '사내 VPN 접속 비밀번호는 90일마다 변경해야 한다.' },
  });
  expect(k.ok()).toBeTruthy();
});

test.afterAll(async ({ request }) => {
  const tok = await adminToken();
  await request.delete(`${API}/api/admin/cache?namespace=${encodeURIComponent(NS)}`, { headers: { Authorization: `Bearer ${tok}` } });
  dropPolicyPart(NS);
});

async function login(page: Page) {
  await loginAdmin(page, { openAdmin: false });
  await page.locator('select').filter({ hasText: NS }).first().selectOption(NS);
}

/** 질문을 보내고 답변 완료(피드백 버튼 노출)까지 기다린다. 마지막 답변 영역 텍스트를 돌려준다. */
async function ask(page: Page, question: string) {
  const before = await page.getByRole('button', { name: '답변 틀림' }).count();
  await page.locator('textarea:visible').first().fill(question);
  await page.getByTitle('전송 (Ctrl+Enter)').click();
  await expect(page.getByRole('button', { name: '답변 틀림' })).toHaveCount(before + 1, { timeout: 300_000 });
}

async function openCorrectionCard(page: Page, userInput: string) {
  await page.getByRole('link', { name: 'Admin' }).click();
  await domainSelect(page).selectOption(NS);
  await goAdmin(page, '품질', '정정 요청');   // 정정 요청은 품질 메뉴로(10/21)
  const card = page.locator('div.rounded-xl').filter({ hasText: userInput }).first();
  await expect(card).toBeVisible({ timeout: 10_000 });
  return card;
}

// AI가 다른 대상을 골랐으면 담당자가 "정정 대상"에서 바꾼다(바꾼 대상 기준으로 수정안 재생성). AI 판정은 모델마다 흔들린다 —
// 로컬 Qwen이 "근거와 다르다"고 쓰고도 분류는 "근거에 없음"을 고른 적이 있다(10/20). 대상 확인은 실제로도 담당자 몫이라 테스트도 거친다.
async function ensureTarget(page: Page, card: ReturnType<Page['locator']>, targetLabel: string) {
  const targetSelect = card.locator('select').filter({ hasText: '답변 오류' });
  const value = await targetSelect.locator('option', { hasText: targetLabel }).getAttribute('value');
  if (value && (await targetSelect.inputValue()) !== value) {
    const retargeted = page.waitForResponse((r) => /\/api\/corrections\/\d+\/retarget/.test(r.url()), { timeout: 300_000 });
    await targetSelect.selectOption(value);
    expect((await retargeted).ok()).toBeTruthy();
  }
}

/** 승인 전에 대상만 맞춰 두고 채팅으로 돌아간다 — "검토 중" 표시는 대상 근거에 붙는다. */
async function confirmTargetInAdmin(page: Page, userInput: string, targetLabel: string) {
  await ensureTarget(page, await openCorrectionCard(page, userInput), targetLabel);
  await page.getByRole('link', { name: 'Chat' }).click();
}

async function approveInAdmin(page: Page, userInput: string, targetLabel?: string, expectedFix?: string) {
  const card = await openCorrectionCard(page, userInput);
  if (targetLabel) await ensureTarget(page, card, targetLabel);
  const approved = page.waitForResponse((r) => /\/api\/corrections\/\d+\/approve/.test(r.url()));
  // 사람은 고치지 않는다(10/21): 수정안이 있으면 [승인], 없으면 [AI 초안 만들기], 그래도 승인할 수 없으면(초안 실패·【확인 필요】)
  // [다시 쓰기] 모달에 맞는 내용을 한 줄 적어 AI가 다시 쓰게 한 뒤 [승인]
  const approveBtn = card.getByRole('button', { name: '승인', exact: true });
  if (!(await approveBtn.isVisible().catch(() => false))) {
    const make = card.getByRole('button', { name: /AI (초안 만들기|검토 초안)/ });
    if (await make.isVisible().catch(() => false)) {
      await make.click();
      // AI 초안 실패는 카드에 문구가 뜬다 — 5분 기다리지 말고 바로 실패로(리뷰 L10)
      await expect(card.getByRole('button', { name: '다시 쓰기' }).or(card.getByText('AI가 초안을 만들지 못했어요'))).toBeVisible({ timeout: 300_000 });
      await expect(card.getByText('AI가 초안을 만들지 못했어요')).toHaveCount(0);
    }
  }
  if (!(await approveBtn.isVisible().catch(() => false))) {
    await card.getByRole('button', { name: '다시 쓰기' }).click();
    const form = page.locator('form').filter({ has: page.locator('textarea[id^="corr-"]') });
    await form.locator('textarea').fill(expectedFix ?? userInput);
    await form.getByRole('button', { name: '다시 쓰기' }).click();
    // 닫히거나(성공) 오류가 뜰 때까지(실패) 기다리고, 오류면 바로 실패 — 5분을 다 기다리지 않게(리뷰 L10)
    await expect(async () => {
      expect((await form.count()) === 0 || (await form.getByRole('alert').count()) > 0).toBeTruthy();
    }).toPass({ timeout: 300_000 });
    await expect(form.getByRole('alert')).toHaveCount(0);
    await expect(form).toHaveCount(0);
  }
  await expect(approveBtn).toBeVisible({ timeout: 30_000 });
  await approveBtn.click();
  expect((await approved).ok()).toBeTruthy();
  await page.getByRole('link', { name: 'Chat' }).click();
}

// 근거 카드 "이 근거 틀림" 버튼은 2026-10-06 제거 — 신고는 "답변 틀림" 하나, 틀린 근거(여기선 지식 문서)는 AI가 찾는다
test('지식 — "답변 틀림" 한 줄 → AI가 틀린 지식 근거 지목 → 승인 전 원본 유지 + 검토중 → 승인 후 수정 답변', async ({ page }) => {
  await login(page);
  const Q = 'VPN 비밀번호 변경 주기가 어떻게 돼?';
  await ask(page, Q);

  await expect(page.getByRole('button', { name: '이 근거 틀림' })).toHaveCount(0);
  await page.getByRole('button', { name: '답변 틀림' }).last().click();
  await expect(page.getByText('어디가 틀렸나요?')).toBeVisible();
  await page.locator('textarea:visible').first().fill('60일마다 바꿔야 합니다');
  await page.getByTitle('정정 접수 (Ctrl+Enter)').click();
  await expect(page.getByText('정정 신고 접수됨')).toBeVisible({ timeout: 300_000 });
  await confirmTargetInAdmin(page, '60일마다 바꿔야 합니다', 'VPN');

  // 승인 전: 같은 질문 → 여전히 원본(90일) + 근거에 "정정 검토 중"
  await ask(page, Q);
  await expect(page.getByText('정정 검토 중').last()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/90일마다 변경/).last()).toBeVisible();

  await approveInAdmin(page, '60일마다 바꿔야 합니다', undefined, '사내 VPN 접속 비밀번호는 60일마다 변경해야 한다.');

  await ask(page, Q);
  await expect(page.getByText(/60일마다 변경/).last()).toBeVisible({ timeout: 10_000 });
});

test('정책 — "답변 틀림" 한 줄 → AI 분석 → (대상 확인) 승인 후 수정 답변', async ({ page }) => {
  await login(page);
  const Q = '반품 신청은 수령 후 며칠 이내에 해야 해?';
  await ask(page, Q);

  await page.getByRole('button', { name: '답변 틀림' }).last().click();
  await expect(page.getByText('어디가 틀렸나요?')).toBeVisible();  // 근거를 고르지 않는다 — AI가 찾음
  // 취소해도 다시 열 수 있어야 한다(👎 신호는 이미 보냈으니 "의견 덧붙이기"로)
  await page.locator('textarea:visible').first().press('Escape');
  await expect(page.getByText('어디가 틀렸나요?')).toBeHidden();
  await page.getByRole('button', { name: '의견 덧붙이기' }).last().click();
  await expect(page.getByText('어디가 틀렸나요?')).toBeVisible();
  await page.locator('textarea:visible').first().fill('14일 이내로 바뀌었습니다');
  await page.getByTitle('정정 접수 (Ctrl+Enter)').click();
  await expect(page.getByText('정정 신고 접수됨')).toBeVisible({ timeout: 300_000 });
  await expect(page.getByText('AI 분석')).toBeVisible();  // 어느 근거·왜·무엇이 틀렸는지 사용자에게도 보여줌
  // 의견까지 접수했으면 '의견 덧붙이기'는 사라진다(이미 신고했는데 또 하라는 듯 보이지 않게)
  await expect(page.getByRole('button', { name: '의견 덧붙이기' })).toHaveCount(0);

  await confirmTargetInAdmin(page, '14일 이내로 바뀌었습니다', '임시 반품 기한');
  await ask(page, Q);
  await expect(page.getByText('정정 검토 중').last()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/7일 이내/).last()).toBeVisible();

  await approveInAdmin(page, '14일 이내로 바뀌었습니다', '임시 반품 기한', '반품은 수령 후 14일 이내에 신청할 수 있다.');

  await ask(page, Q);
  await expect(page.getByText(/14일 이내/).last()).toBeVisible({ timeout: 10_000 });
});
