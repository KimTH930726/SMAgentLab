import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { goAdmin } from './admin-nav';
import { loginAdmin } from './auth';

// 이 테스트가 존재하는 이유(11/16 골든셋 자동화 2단계, 시안 A): 평가 게이트의 [골든셋 후보] — 채팅 신호(답변 오류 판정·지식 없음 거절) 중
// 정답을 안 고른 것을 사람이 골라야만 골든셋에 들어간다. 카드 → 목록 → [정답 = 근거] / [모른다가 정답]을 누르면 그 줄이 목록에서 사라지고
// DB 골든셋에 정확히 한 줄(정답 지식은 닻으로, 모른다는 abstain)이 생기는지 실 dev 스택으로 본다. 시험 데이터는 직접 넣고 끝나면 지운다.

const psql = (sql: string) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', 'opsdb', '-tA', '-c', sql],
  { encoding: 'utf-8' }).trim();
const NS = `zz_e2e_골든후보_${Date.now()} 시스템`;
const VEC = `('[' || array_to_string(array_fill(0::float, ARRAY[1024]), ',') || ']')::vector`;

test('골든셋 후보 — 카드 → 목록 → 정답 고르기·모른다가 정답 → 목록에서 사라지고 골든셋 한 줄', async ({ page }) => {
  test.setTimeout(120_000);
  const tag = String(Date.now());
  const qAE = `zz_e2e 답변오류 질문 ${tag}?`;
  const qNK = `zz_e2e 지식없음 질문 ${tag}?`;
  let conv = '';
  let mid = '';
  const ns = Number(psql(`INSERT INTO ops_namespace (name, kind, area) VALUES ('${NS}', 'domain', 'system') RETURNING id`).split('\n')[0]);
  try {
    const k = Number(psql(`INSERT INTO rag_knowledge (namespace_id, content, embedding, status) VALUES (${ns}, 'zz_e2e 정답 조각 고유 줄 ${tag}', ${VEC}, 'active') RETURNING id`).split('\n')[0]);
    conv = psql(`INSERT INTO ops_conversation DEFAULT VALUES RETURNING id`).split('\n')[0];
    mid = psql(`INSERT INTO ops_message (conversation_id, role, content) VALUES (${conv}, 'assistant', '답') RETURNING id`).split('\n')[0];
    psql(`INSERT INTO ops_query_log (question, status, namespace_id, message_id) VALUES ('${qAE}', 'pending', ${ns}, ${mid})`);
    const cands = JSON.stringify([{ key: `k-${k}`, target_type: 'knowledge', target_id: k, target_sub_id: null, label: `문서 #${k}` }]);
    const ae = psql(`INSERT INTO ops_improvement_item (kind, source, namespace_id, message_id, target_type, original, candidates, ai_verdict)
      VALUES ('answer_quality', 'chat_answer_auto', ${ns}, ${mid}, 'answer', '{}'::jsonb, '${cands}'::jsonb, '{"reason": "근거는 맞는데 답이 잘못 읽음"}'::jsonb) RETURNING id`).split('\n')[0];
    const nk = psql(`INSERT INTO ops_query_log (question, status, namespace_id) VALUES ('${qNK}', 'no_knowledge', ${ns}) RETURNING id`).split('\n')[0];

    await loginAdmin(page);
    await goAdmin(page, '평가 게이트');
    await page.getByTestId('axis-knowledge').click();
    const card = page.getByTestId('card-golden');
    await expect(card).toBeVisible();
    await card.getByRole('button', { name: '보기 →' }).click();
    const modal = page.getByTestId('golden-candidates');
    const rowAE = modal.getByTestId(`golden-row-answer_error-${ae}`);
    const rowNK = modal.getByTestId(`golden-row-no_knowledge-${nk}`);
    await expect(rowAE).toContainText(qAE);
    await expect(rowAE).toContainText('근거는 맞는데 답이 잘못 읽음');
    await expect(rowNK).toContainText(qNK);
    // 채널 칩 — 지식 없음만 보면 답변 오류 줄은 안 보인다
    await modal.getByTestId('golden-chip-no_knowledge').click();
    await expect(rowAE).toHaveCount(0);
    await expect(rowNK).toBeVisible();
    await modal.getByTestId('golden-chip-all').click();

    await rowAE.getByRole('button', { name: `정답 = 문서 #${k}` }).click();
    await expect(rowAE).toHaveCount(0);
    await rowNK.getByRole('button', { name: '모른다가 정답' }).click();
    await expect(rowNK).toHaveCount(0);

    const rows = psql(`SELECT source || '|' || status || '|' || (gold->>'kind') || '|' || coalesce(jsonb_array_length(gold->'anchors'), 0)
                       FROM eval_question_candidate WHERE namespace_id = ${ns} ORDER BY id`).split('\n');
    expect(rows).toEqual(['answer_error|added|knowledge|1', 'no_knowledge|added|abstain|0']);
  } finally {
    psql(`DELETE FROM eval_question_candidate WHERE namespace_id = ${ns}`);
    psql(`DELETE FROM ops_improvement_item WHERE namespace_id = ${ns}`);
    psql(`DELETE FROM ops_query_log WHERE namespace_id = ${ns}`);
    psql(`DELETE FROM rag_knowledge WHERE namespace_id = ${ns}`);
    if (mid) psql(`DELETE FROM ops_message WHERE id = ${mid}`);
    if (conv) psql(`DELETE FROM ops_conversation WHERE id = ${conv}`);
    psql(`DELETE FROM ops_namespace WHERE id = ${ns}`);
  }
});
