import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';

// 이 테스트가 존재하는 이유(2026-10-01): 근거 정정 흐름은 버튼(근거 카드·답변 아래) → 입력창
// 정정 모드(ChatContainer) → 개선 원장 → 관리자 정정 검토 탭 → 승인 = 버전 교체 → 같은 질문
// 재답변까지 서로 다른 컴포넌트를 건너가는 경로라, 유닛테스트로는 "화면에서 실제로 이어지는가"를
// 못 본다. 핵심 약속("승인 전엔 절대 반영 안 됨, 승인 후엔 반영됨")을 실 dev 스택 그대로 확인한다.
//
// 데이터: 실 지식/정책을 건드리지 않도록 임시 네임스페이스를 만들고 끝나면 통째로 지운다
// (ops_namespace ON DELETE CASCADE). 정책 임포트는 xlsx만 받아서 openpyxl이 있는 backend
// 컨테이너 안에서 시트를 만들어 올린다.

const ADMIN_USER = 'admin';
const ADMIN_PASS = '1111';
const NS = 'zz_e2e_correction';
const API = 'http://localhost:8000';

function psql(sql: string): string {
  return execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
    { encoding: 'utf-8' }).trim();
}

const POLICY_FIXTURE = `
import io, json, os, uuid, urllib.request
from openpyxl import Workbook
wb = Workbook(); ws = wb.active; ws.title = "정책"
ws.append(["No.", "대분류", "정책명", "조건/상세"])
ws.append([1, "테스트", "임시 반품 기한", "반품은 수령 후 7일 이내에 신청할 수 있다."])
buf = io.BytesIO(); wb.save(buf)
b = uuid.uuid4().hex
ns = os.environ["NS"]
body = (f'--{b}\\r\\nContent-Disposition: form-data; name="namespace"\\r\\n\\r\\n{ns}\\r\\n'.encode()
        + f'--{b}\\r\\nContent-Disposition: form-data; name="file"; filename="zz_e2e.xlsx"\\r\\nContent-Type: application/octet-stream\\r\\n\\r\\n'.encode()
        + buf.getvalue() + f"\\r\\n--{b}--\\r\\n".encode())
req = urllib.request.Request("http://localhost:8000/api/policy/import", data=body, method="POST",
    headers={"Content-Type": f"multipart/form-data; boundary={b}", "Authorization": "Bearer " + os.environ["TOK"]})
print(urllib.request.urlopen(req, timeout=300).status)
`;

test.describe.configure({ mode: 'serial' });
test.setTimeout(240_000);

test.beforeAll(async ({ request }) => {
  psql(`DELETE FROM ops_namespace WHERE name='${NS}'`);
  psql(`INSERT INTO ops_namespace (name, description) VALUES ('${NS}', 'e2e correction')`);
  const login = await request.post(`${API}/api/auth/login`, { data: { username: ADMIN_USER, password: ADMIN_PASS } });
  const tok = (await login.json()).access_token as string;
  // 같은 이름으로 다시 만든 파트에 지난 실행의 시맨틱 캐시가 남아 있으면, 캐시 답변이 이미 지운 근거를 가리킨다
  await request.delete(`${API}/api/admin/cache?namespace=${encodeURIComponent(NS)}`, { headers: { Authorization: `Bearer ${tok}` } });
  const k = await request.post(`${API}/api/knowledge`, {
    headers: { Authorization: `Bearer ${tok}` },
    data: { namespace: NS, content: '사내 VPN 접속 비밀번호는 90일마다 변경해야 한다.' },
  });
  expect(k.ok()).toBeTruthy();
  execFileSync('docker', ['exec', '-i', '-e', `NS=${NS}`, '-e', `TOK=${tok}`, 'ops-backend', 'python', '-'],
    { input: POLICY_FIXTURE, encoding: 'utf-8' });
  // 임포트 결과가 검토대기로 들어오면 채팅 근거로 안 잡힐 수 있어 활성화(이 테스트의 대상은 정정 흐름)
  psql(`UPDATE policy_item SET status='active' WHERE namespace_id=(SELECT id FROM ops_namespace WHERE name='${NS}') AND status='pending_review'`);
});

test.afterAll(async ({ request }) => {
  const login = await request.post(`${API}/api/auth/login`, { data: { username: ADMIN_USER, password: ADMIN_PASS } });
  const tok = (await login.json()).access_token as string;
  await request.delete(`${API}/api/admin/cache?namespace=${encodeURIComponent(NS)}`, { headers: { Authorization: `Bearer ${tok}` } });
  const ns = `(SELECT id FROM ops_namespace WHERE name='${NS}')`;
  psql(`DELETE FROM ops_improvement_item WHERE namespace_id=${ns}`);
  psql(`UPDATE policy_item SET supersedes_id=NULL WHERE namespace_id=${ns}`);
  psql(`UPDATE rag_knowledge SET supersedes_id=NULL WHERE namespace_id=${ns}`);
  psql(`DELETE FROM ops_namespace WHERE name='${NS}'`);
});

async function login(page: Page) {
  await page.goto('/login');
  await page.getByPlaceholder('사용자 아이디').fill(ADMIN_USER);
  await page.getByPlaceholder('비밀번호').fill(ADMIN_PASS);
  await page.getByRole('button', { name: '로그인' }).click();
  await expect(page.getByRole('heading', { name: '로그인' })).toBeHidden({ timeout: 10_000 });
  const agentHeading = page.getByRole('heading', { name: '에이전트 선택' });
  if (await agentHeading.isVisible().catch(() => false)) {
    await page.getByText('선택하기').first().click();
  }
  await page.locator('select').filter({ hasText: NS }).first().selectOption(NS);
}

/** 질문을 보내고 답변 완료(피드백 버튼 노출)까지 기다린다. 마지막 답변 영역 텍스트를 돌려준다. */
async function ask(page: Page, question: string) {
  const before = await page.getByRole('button', { name: '답변 틀림' }).count();
  await page.locator('textarea:visible').first().fill(question);
  await page.getByTitle('전송 (Ctrl+Enter)').click();
  await expect(page.getByRole('button', { name: '답변 틀림' })).toHaveCount(before + 1, { timeout: 120_000 });
}

async function approveInAdmin(page: Page, userInput: string, targetLabel?: string, expectedFix?: string) {
  await page.getByRole('link', { name: 'Admin' }).click();
  await page.getByRole('button', { name: '지식 베이스' }).click();
  await page.locator('select').filter({ hasText: '파트 선택' }).selectOption(NS);
  await page.getByRole('button', { name: /정정 검토/ }).click();
  const card = page.locator('div.rounded-xl').filter({ hasText: userInput }).first();
  await expect(card).toBeVisible({ timeout: 10_000 });
  if (targetLabel) {
    // AI가 다른 대상을 골랐으면 담당자가 "정정 대상"에서 바꾼다(바꾼 대상 기준으로 수정안 재생성)
    const targetSelect = card.locator('select').filter({ hasText: '답변 오류' });
    const value = await targetSelect.locator('option', { hasText: targetLabel }).getAttribute('value');
    if (value && (await targetSelect.inputValue()) !== value) {
      const retargeted = page.waitForResponse((r) => /\/api\/corrections\/\d+\/retarget/.test(r.url()), { timeout: 120_000 });
      await targetSelect.selectOption(value);
      expect((await retargeted).ok()).toBeTruthy();
    }
  }
  const approved = page.waitForResponse((r) => /\/api\/corrections\/\d+\/approve/.test(r.url()));
  // 사용자 입력으로 정리된 수정안이 있으면 [AI 수정안으로 대체], 초안이 없으면(게이트웨이 장애 등) [직접 수정] → 반영
  const replace = card.getByRole('button', { name: 'AI 수정안으로 대체' });
  if (await replace.isVisible().catch(() => false)) {
    await replace.click();
  } else {
    await card.getByRole('button', { name: '직접 수정' }).click();
    for (const ta of await card.locator('textarea').all()) await ta.fill(expectedFix ?? userInput);
    await card.getByRole('button', { name: '수정 내용으로 반영' }).click();
  }
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
  await expect(page.getByText('정정 신고 접수됨')).toBeVisible({ timeout: 120_000 });

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
  await expect(page.getByText('정정 신고 접수됨')).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText('AI 분석')).toBeVisible();  // 어느 근거·왜·무엇이 틀렸는지 사용자에게도 보여줌
  // 의견까지 접수했으면 '의견 덧붙이기'는 사라진다(이미 신고했는데 또 하라는 듯 보이지 않게)
  await expect(page.getByRole('button', { name: '의견 덧붙이기' })).toHaveCount(0);

  await ask(page, Q);
  await expect(page.getByText('정정 검토 중').last()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/7일 이내/).last()).toBeVisible();

  await approveInAdmin(page, '14일 이내로 바뀌었습니다', '임시 반품 기한', '반품은 수령 후 14일 이내에 신청할 수 있다.');

  await ask(page, Q);
  await expect(page.getByText(/14일 이내/).last()).toBeVisible({ timeout: 10_000 });
});
