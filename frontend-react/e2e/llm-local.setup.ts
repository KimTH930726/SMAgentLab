import type { FullConfig } from '@playwright/test';
import { API, adminToken } from './auth';

// E2E는 채팅·정정 시나리오에서 LLM을 실제로 부른다 — 기본은 사내 게이트웨이라 토큰을 쓴다(2026-10-20 실측 42회·약 9.9만 토큰).
// 그래서 E2E 동안만 로컬 LLM(OpenAI 호환, 백엔드 OPENAI_COMPAT_BASE_URL)으로 전환하고 끝나면 원래 LLM으로 되돌린다.
// 로컬 LLM을 못 쓰는 환경이면 조용히 게이트웨이로 돌지 않고 멈춘다 — 게이트웨이로 돌리려면 E2E_LLM=gateway.
//
// 되돌리기 = 화면 설정 해제(DELETE /api/llm/config → .env 설정, 재시작과 같음). 관리자가 화면에서 바꿔 둔 설정(모델·자격증명 등)이
// 있으면 그것도 지워지므로(자격증명은 다시 읽을 수 없다, 리뷰 10/20 M2) 그런 상태면 시작하지 않는다.

async function resetToEnv(token: string) {
  const r = await fetch(`${API}/api/llm/config`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) throw new Error(`LLM 되돌리기 실패: ${r.status} ${await r.text()}`);
}

async function setProvider(token: string, provider: string) {
  const r = await fetch(`${API}/api/llm/config`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ provider }),
  });
  if (!r.ok) throw new Error(`LLM 전환 실패(${provider}): ${r.status} ${await r.text()}`);
  return r.json();
}

export default async function globalSetup(_config: FullConfig) {
  if (process.env.E2E_LLM === 'gateway') return;
  const token = await adminToken();
  const before = await (await fetch(`${API}/api/llm/config`, { headers: { Authorization: `Bearer ${token}` } })).json();
  if (!before.openai_compat?.enabled) {
    throw new Error('로컬 LLM(OPENAI_COMPAT_BASE_URL)이 설정되지 않음 — 게이트웨이로 돌리려면 E2E_LLM=gateway');
  }
  // 지난 실행이 되돌리기 전에 끊겨 이미 로컬이면 그건 E2E가 건 설정 — 해제하면 된다(리뷰 L7)
  if (before.is_runtime_override && before.provider !== 'openai_compat') {
    throw new Error('관리자 화면에서 바꾼 LLM 설정이 걸려 있음 — E2E가 되돌리면 그 설정이 사라진다. 백엔드 재시작(.env 값)으로 정리 후 실행');
  }
  const now = await setProvider(token, 'openai_compat');
  if (!now.is_connected) {
    // 바꾼 채로 멈추면 채팅 전체가 죽은 로컬 LLM을 보게 된다(10/20 실제로 겪음) — 먼저 되돌리고 멈춘다
    await resetToEnv(token);
    throw new Error('로컬 LLM에 연결되지 않음 — 원래 LLM으로 되돌림');
  }
  // 모델은 서버가 올린 것을 자동으로 쓴다(공용 샌드박스라 자주 바뀜) — 결과가 모델마다 다를 수 있어 어느 모델로 돌았는지 남긴다
  // 기록용일 뿐이라 실패해도 준비를 막지 않는다 — 여기서 멈추면 되돌리기가 등록되지 않아 백엔드가 로컬 LLM에 남는다(리뷰 M2)
  try {
    const url = `${process.env.E2E_LOCAL_LLM_URL ?? 'http://10.21.37.34:8000/v1'}/models`;
    const models = await (await fetch(url, { signal: AbortSignal.timeout(5000) })).json();
    console.log(`[E2E] 로컬 LLM 모델: ${models?.data?.[0]?.id ?? '알 수 없음'}`);
  } catch {
    console.log('[E2E] 로컬 LLM 모델: 확인 못 함(기록만, 진행)');
  }
  return async () => {   // globalTeardown(Playwright 1.63은 globalSetup이 돌려준 함수를 끝에 실행) — 실패해도 백엔드 재시작이면 .env 값
    await resetToEnv(await adminToken());
  };
}
