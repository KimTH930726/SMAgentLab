import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { API, adminToken } from './auth';

// 이 테스트가 존재하는 이유(2026-10-26 v2.174 C2 재리뷰 [높음]): "다른 컬렉션에서 찾아보기"로 받은 답은 대화를 연 컬렉션(A)이 아니라
// 답한 컬렉션(B) 건이다 — 그 답의 "답변 틀림"은 B로 접수돼야 한다. 소유 확인이 대화 컬렉션만 보던 때는 B로 보낸 신고가 200을 받고도
// 원장에 0건(조용히 버려짐)이었다. 메시지를 직접 만들어 API로만 본다(LLM 0). 만든 대화·원장 줄은 끝에 지운다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();

test('제안으로 받은 답의 "답변 틀림" = 답한 컬렉션 건으로 접수(조용히 버려지지 않음)', async ({ request }) => {
  const [a, b] = psql(`SELECT name FROM ops_namespace WHERE kind = 'domain' AND name NOT LIKE '%test%' AND name NOT LIKE 'zz%' ORDER BY id LIMIT 2`).split('\n');
  test.skip(!a || !b, '정책 컬렉션이 둘 이상 있어야 함');
  const uid = psql(`SELECT id FROM ops_user WHERE username = 'admin'`);
  const conv = psql(`INSERT INTO ops_conversation (namespace_id, title, user_id, agent_type) SELECT id, 'E2E 제안 신고', ${uid}, 'knowledge_rag' `
    + `FROM ops_namespace WHERE name = '${a}' RETURNING id`).split('\n')[0];
  try {
    psql(`INSERT INTO ops_message (conversation_id, role, content) VALUES (${conv}, 'user', 'E2E 제안 질문')`);
    const msg = psql(`INSERT INTO ops_message (conversation_id, role, content, status, metadata) VALUES (${conv}, 'assistant', 'E2E 다른 곳 답', `
      + `'completed', '{"answered_namespace": "${b}"}'::jsonb) RETURNING id`).split('\n')[0];
    const H = { Authorization: `Bearer ${await adminToken()}` };
    const res = await request.post(`${API}/api/feedback`, {
      headers: H, data: { namespace: b, question: 'E2E 제안 질문', answer: 'E2E 다른 곳 답', is_positive: false, message_id: Number(msg) } });
    expect(res.ok()).toBeTruthy();
    const row = psql(`SELECT n.name FROM ops_improvement_item i JOIN ops_namespace n ON n.id = i.namespace_id WHERE i.message_id = ${msg}`);
    expect(row).toBe(b);   // 답한 컬렉션(B)으로 — 0건(조용히 버려짐)도, A도 아님
    // 남의 컬렉션으로는 여전히 막힌다 — 대화(A)도 답한 곳(B)도 아닌 C
    const c = psql(`SELECT name FROM ops_namespace WHERE kind = 'domain' AND name NOT IN ('${a}', '${b}') AND name NOT LIKE 'zz%' ORDER BY id LIMIT 1`);
    if (c) {
      await request.post(`${API}/api/feedback`, {
        headers: H, data: { namespace: c, question: 'x', answer: 'x', is_positive: false, message_id: Number(msg) } });
      expect(psql(`SELECT count(*) FROM ops_improvement_item WHERE message_id = ${msg}`)).toBe('1');
    }
  } finally {
    psql(`DELETE FROM ops_improvement_item WHERE message_id IN (SELECT id FROM ops_message WHERE conversation_id = ${conv})`);
    psql(`DELETE FROM ops_conversation WHERE id = ${conv}`);
  }
});
