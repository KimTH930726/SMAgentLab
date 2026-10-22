import { test, expect } from '@playwright/test';
import { API, adminToken, loginAdmin } from './auth';
import { selectDomain } from './admin-nav';

// 근거만 보여 주기(2026-10-20) — AI가 답을 못 낼 때(LLM 연결 실패) 일반 답 모양의 실패 문구 대신 경고 + 찾은 정책 근거를 보여 준다.
// 예전엔 정책 근거를 실패 문구와 겹치는 것으로 골라 사실상 늘 사라졌다. LLM 장애는 없는 Ollama 주소로 잠깐 전환해 만들고,
// 끝나면 E2E 공통 설정(로컬 LLM — llm-local.setup.ts)으로 되돌린다.
const NS = '스타벅스카드';   // v0 정책서 파트 — 정책 검색 범위가 이 파트뿐

test('LLM 장애 → 경고 + 정책 근거 카드(근거만 보여 주기), 피드백 버튼 없음', async ({ page, request }) => {
  test.setTimeout(180_000);
  // 게이트웨이 모드엔 E2E 공통 설정이 없어, 끝나고 되돌리면 관리자가 화면에서 건 설정까지 지운다(리뷰 L1)
  test.skip(process.env.E2E_LLM === 'gateway', '게이트웨이 모드에선 LLM 전환 테스트를 하지 않음');
  const tok = await adminToken();
  const auth = { Authorization: `Bearer ${tok}` };
  const before = await (await request.get(`${API}/api/llm/config`, { headers: auth })).json();
  await request.delete(`${API}/api/admin/cache?namespace=${encodeURIComponent(NS)}`, { headers: auth });
  const dead = await request.put(`${API}/api/llm/config`, { headers: auth, data: { provider: 'ollama', ollama_base_url: 'http://127.0.0.1:9' } });
  expect(dead.ok()).toBeTruthy();
  try {
    await loginAdmin(page, { openAdmin: false });
    await selectDomain(page, NS);

    await page.locator('textarea:visible').first().fill('카드 환불 수수료 기준');
    await page.getByTitle('전송 (Ctrl+Enter)').click();
    await expect(page.getByTestId('degraded-answer')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('degraded-answer')).toContainText('AI 답변을 지금 만들 수 없습니다');
    await expect(page.getByText(/정책 근거 \d+건/).last()).toBeVisible();
    await expect(page.getByRole('button', { name: '답변 틀림' })).toHaveCount(0);   // 답이 아니니 피드백 없음

    // 저장된 메시지에도 표시가 남아 다시 열었을 때 같은 모양(새로고침은 새 대화로 시작해 화면으로는 못 본다 — API로 확인)
    const convs = await (await request.get(`${API}/api/conversations?namespace=${encodeURIComponent(NS)}`, { headers: auth })).json();
    const msgs = await (await request.get(`${API}/api/conversations/${convs[0].id}/messages`, { headers: auth })).json();
    const last = msgs[msgs.length - 1];
    expect(last.status).toBe('failed');
    expect(last.metadata?.degraded).toBe(true);
    expect((last.metadata?.policy_citations ?? []).length).toBeGreaterThan(0);
  } finally {
    // 공통 설정이 로컬 LLM으로 바꿔 둔 상태면 그대로, 아니면 .env로
    if (before.provider === 'openai_compat') await request.put(`${API}/api/llm/config`, { headers: auth, data: { provider: 'openai_compat' } });
    else await request.delete(`${API}/api/llm/config`, { headers: auth });
  }
});
