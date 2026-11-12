// 시나리오 리허설(C-4, 11/12) — 시연 녹화 흐름을 영상 없이 사본 화면에서 돌려 장면마다 통과/실패만 낸다.
// 언제: 폐쇄망 반입 · PoC · 큰 머지 전. 먼저 scripts/rehearsal/up.sh(사본), 끝나면 down.sh.
// 사용: node scripts/rehearsal/run.mjs [scenario.json]
// 출력: 장면별 표(콘솔) + scripts/rehearsal/out/result.json · 실패 장면 스크린샷. 답 원문은 어디에도 찍지 않는다(구조·개수만).
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(HERE, '../../frontend-react/package.json'));
const { chromium } = require('playwright');

const SC = JSON.parse(fs.readFileSync(process.argv[2] || path.join(HERE, 'scenario.json'), 'utf8'));
const FE = `http://127.0.0.1:${process.env.REHEARSAL_FRONTEND_PORT || 8502}`;
const API = `http://127.0.0.1:${process.env.REHEARSAL_BACKEND_PORT || 8001}`;
const ADMIN_PW = process.env.REHEARSAL_ADMIN_PASSWORD || '1111';
const PW = 'rehearsal1234!';
const DB = 'opsdb_rehearsal';
const LONG = 600_000;
const OUT = path.join(HERE, 'out');
fs.mkdirSync(OUT, { recursive: true });

const psql = (sql) => execFileSync('docker', ['exec', 'ops-postgres', 'psql', '-U', 'ops', '-d', DB, '-Atc', sql],
  { encoding: 'utf8', env: { ...process.env, MSYS_NO_PATHCONV: '1' } }).trim();

// ── 안전장치: 사본에만 돈다. 부르는 포트가 사본 컨테이너의 포트이고, 그 컨테이너가 사본 DB에 붙어 있는지 docker로 확인
// (예전엔 psql로 사본 DB 이름만 물어 늘 통과했다 — 8001에 다른 백엔드가 떠 있으면 원본에 쓸 수 있었음, 리뷰 11/12)
const inspect = (name, fmt) => execFileSync('docker', ['inspect', '-f', fmt, name], { encoding: 'utf8', env: { ...process.env, MSYS_NO_PATHCONV: '1' } }).trim();
function hostPort(name, inner) {
  const b = JSON.parse(inspect(name, '{{json .HostConfig.PortBindings}}'))[`${inner}/tcp`];
  return b?.[0]?.HostPort;
}
const beEnv = inspect('ops-backend-rehearsal', '{{range .Config.Env}}{{println .}}{{end}}').split('\n');
const beDb = (beEnv.find((l) => l.startsWith('DATABASE_URL=')) || '').split('/').pop();
if (beDb !== DB) throw new Error('사본 백엔드가 사본 DB에 붙어 있지 않음 — 중단');
if (hostPort('ops-backend-rehearsal', 8000) !== new URL(API).port) throw new Error(`${API}는 사본 백엔드 포트가 아님 — 중단`);
if (hostPort('ops-frontend-rehearsal', 80) !== new URL(FE).port) throw new Error(`${FE}는 사본 화면 포트가 아님 — 중단`);
const health = await fetch(`${API}/health`).catch(() => null);
if (!health?.ok) throw new Error(`사본 백엔드(${API})가 안 떠 있음 — scripts/rehearsal/up.sh 먼저`);

async function api(p, init = {}, token) {
  const r = await fetch(`${API}${p}`, { ...init, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init.headers || {}) } });
  const body = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, body };
}
const adminTok = (await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username: 'admin', password: ADMIN_PW }) })).body.access_token;
if (!adminTok) throw new Error('사본 관리자 로그인 실패(REHEARSAL_ADMIN_PASSWORD)');

// ── 리허설 사용자(사본 DB에만) — 있으면 그대로
for (const u of Object.values(SC.users)) {
  const r = await api('/api/auth/register', { method: 'POST', body: JSON.stringify({ username: u.name, password: PW, part: u.part }) });
  if (r.ok) {   // 가입은 viewer로 시작 — 신고·승인을 하려면 user여야 한다
    const put = await api(`/api/auth/users/${r.body.id}`, { method: 'PUT', body: JSON.stringify({ role: 'user' }) }, adminTok);
    if (!put.ok) throw new Error(`사용자 ${u.name} 역할 변경 실패 ${put.status}`);
  }
  // 400·409는 "이미 있음"일 수도, 검증 실패일 수도 — 로그인이 되고 역할이 user인지로 가린다(리뷰 11/12)
  const li = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username: u.name, password: PW }) });
  if (!li.ok) throw new Error(`사용자 ${u.name} 준비 실패(등록 ${r.status} · 로그인 ${li.status})`);
  if (li.body.user?.role !== 'user') throw new Error(`사용자 ${u.name} 역할이 user 아님(${li.body.user?.role})`);
}

// ── 화면 도우미(시연 녹화 스크립트와 같은 선택자)
async function login(page, user) {
  await page.goto(`${FE}/login`);
  await page.getByPlaceholder('사용자 아이디').fill(user);
  await page.getByPlaceholder('비밀번호').fill(PW);
  await page.getByRole('button', { name: '로그인' }).click();
  await page.getByRole('heading', { name: '로그인' }).waitFor({ state: 'hidden' });
}
async function setDomain(page, ns, label) {
  await page.evaluate((n) => { const a = JSON.parse(localStorage.getItem('ops_auth') || '{}'); localStorage.setItem(`opslens.namespace:${a.user?.username}`, n); }, ns);
  await page.reload();
  await page.locator('#sidebar-domain', { hasText: label }).waitFor();
}
async function newChat(page) { await page.getByRole('button', { name: '새 대화' }).click(); await page.waitForTimeout(800); }
const fbCount = (page) => page.getByRole('button', { name: '답변 틀림' }).count();
async function waitAnswer(page, before) {
  await page.waitForFunction((n) => [...document.querySelectorAll('button')].filter((b) => b.textContent?.trim() === '답변 틀림').length > n, before, { timeout: LONG });
  await page.waitForTimeout(600);
}
async function ask(page, q) {
  const ta = page.locator('textarea:visible').first();
  await ta.fill(q);
  const n = await fbCount(page);
  await page.getByTitle('전송 (Ctrl+Enter)').click();
  return n;
}
/** 마지막 답 블록의 구조(원문은 밖으로 안 냄) */
async function lastAnswer(page) {
  const blk = page.locator('[data-testid="evidence-toggle"]').last().locator('xpath=ancestor::div[contains(@class,"flex-1")][1]');
  const text = await blk.innerText();
  return {
    blk, text,
    noKnowledge: /관련 지식을 찾지 못했습니다/.test(text),
    vague: /명시되어 있지 않|찾지 못했습니다/.test(text),
    docChips: await blk.getByTestId('cite-doc').count(),
    unknownChips: await blk.getByTestId('cite-unknown').count(),
    kindChips: await blk.getByTestId('cite-kind').count(),
  };
}
async function openEvidence(page) {
  await page.getByTestId('evidence-toggle').last().click();
  await page.waitForTimeout(800);
}
async function clearCache(ns) {
  return (await api(`/api/admin/cache?namespace=${encodeURIComponent(ns)}`, { method: 'DELETE' }, adminTok)).status;
}

// ── 장면 실행기: 실패해도 다음 장면은 돈다(앞 장면에 기대는 장면은 스스로 건너뜀)
const rows = [];
async function scene(id, name, fn, { needs } = {}) {
  const t0 = Date.now();
  const row = { id, name, ok: false, sec: 0, note: '' };
  if (needs && !rows.find((r) => r.id === needs)?.ok) { row.note = `건너뜀(${needs} 실패)`; rows.push(row); console.log(`- ${id} ${name}: ${row.note}`); return; }
  try { const r = (await fn()) || {}; row.ok = r.ok !== false; row.note = r.note || ''; }
  catch (e) { row.note = (e.message || String(e)).split('\n')[0].slice(0, 160); }
  row.sec = Math.round((Date.now() - t0) / 1000);
  if (!row.ok) for (const c of browser.contexts()) for (const [i, p] of c.pages().entries()) await p.screenshot({ path: path.join(OUT, `fail_${id}_${i}.png`) }).catch(() => {});
  rows.push(row);
  console.log(`${row.ok ? '✓' : '✗'} ${id} ${name} (${row.sec}s) ${row.note}`);
}

const browser = await chromium.launch();
const t0 = Date.now();
try {
  const uctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const U = await uctx.newPage();
  await login(U, SC.users.user.name);

  await scene('1', '정책 질문 — 답·정책 근거', async () => {
    const s = SC.policy;
    await setDomain(U, s.ns, s.label); await newChat(U);
    await waitAnswer(U, await ask(U, s.q));
    const a = await lastAnswer(U);
    await openEvidence(U);
    const policyEv = await U.locator('[data-ev="p"]').count();
    const ok = !a.noKnowledge && policyEv > 0 && a.unknownChips === 0;
    return { ok, note: `정책 근거 ${policyEv} · 문서 칩 ${a.docChips} · 없는 번호 ${a.unknownChips}` };
  });

  await scene('2', '되묻기 — 선택지 → 답', async () => {
    const s = SC.clarify;
    await setDomain(U, s.ns, s.label); await newChat(U);
    await ask(U, s.q);
    const opts = U.getByTestId('clarify-options').last();
    await opts.waitFor({ timeout: LONG });
    const nOpt = await opts.getByRole('button').count();
    const opt = opts.getByRole('button', { name: new RegExp(s.option) }).first();
    if (!(await opt.count())) return { ok: false, note: `선택지 ${nOpt}개 중 "${s.option}" 없음` };
    const n = await fbCount(U);
    await opt.click();
    await waitAnswer(U, n);
    const a = await lastAnswer(U);
    return { ok: !a.noKnowledge, note: `선택지 ${nOpt} · 문서 칩 ${a.docChips}` };
  });

  await scene('3', '정책+시스템 섞인 질문', async () => {
    const s = SC.mixed;
    await setDomain(U, s.ns, s.label);
    let a, retries = 0;
    for (; retries < 3; retries++) {   // 로컬 LLM 흔들림 — 캐시 비우고 새 대화로 최대 2번 더(시연 스크립트와 같은 규칙)
      if (retries) await clearCache(s.ns);
      await newChat(U);
      await waitAnswer(U, await ask(U, s.q));
      a = await lastAnswer(U);
      if (!a.vague) break;
    }
    await openEvidence(U);
    const policyEv = await U.locator('[data-ev="p"]').count();
    const docEv = await U.locator('[data-ev^="k-"]').count();
    return { ok: !a.vague && policyEv > 0 && docEv > 0, note: `다시 ${Math.min(retries, 2)} · 정책 근거 ${policyEv} · 문서 근거 ${docEv}` };
  });

  await scene('4', '운영 지식 질문 — 근거 칩·대상 근거', async () => {
    const s = SC.ops;
    await setDomain(U, s.ns, s.label); await newChat(U);
    await waitAnswer(U, await ask(U, s.q));
    const a = await lastAnswer(U);
    await openEvidence(U);
    const hasTarget = s.target_id ? await U.locator(`[data-ev="k-${s.target_id}"]`).count() : 1;
    return { ok: !a.noKnowledge && a.docChips > 0 && hasTarget > 0, note: `문서 칩 ${a.docChips} · 대상 근거 ${s.target_id ? (hasTarget ? '있음' : '없음') : '-'}` };
  });

  await scene('5', '답변 틀림 → 정정 신고', async () => {
    await U.getByRole('button', { name: '답변 틀림' }).last().click();
    await U.getByTestId('correction-reasons').getByRole('button', { name: '내용이 낡음' }).click();
    await U.locator('textarea:visible').last().fill(SC.ops.fix);
    await U.getByTitle('정정 접수 (Ctrl+Enter)').click();
    await U.getByText('정정 신고 접수됨').waitFor({ timeout: LONG });
  }, { needs: '4' });

  await scene('6', '담당자 승인', async () => {
    const octx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
    const O = await octx.newPage();
    try {
      await login(O, SC.users.owner.name);
      await setDomain(O, SC.ops.ns, SC.ops.label);
      await O.getByRole('link', { name: 'Admin' }).click();
      const nav = O.getByRole('navigation', { name: '관리자 메뉴' });
      await nav.getByRole('button', { name: '정정 요청', exact: true }).click();
      const card = O.locator('div.rounded-xl').filter({ hasText: SC.ops.fix }).first();
      await card.waitFor({ timeout: 60_000 });
      let note = '';
      const sel = card.locator('select').filter({ hasText: '답변 오류' });
      if (SC.ops.target_id && (await sel.count()) && await sel.first().isVisible().catch(() => false)) {
        const val = await sel.locator('option', { hasText: String(SC.ops.target_id) }).first().getAttribute('value', { timeout: 3000 }).catch(() => null);
        if (val && (await sel.inputValue()) !== val) {
          const [rt] = await Promise.all([O.waitForResponse((r) => /\/api\/corrections\/\d+\/retarget/.test(r.url()), { timeout: LONG }), sel.selectOption(val)]);
          if (!rt.ok()) return { ok: false, note: `대상 변경 실패 ${rt.status()}` };
          note = 'AI 대상 → 지정 대상으로 바꿈 · ';
        }
      }
      const approve = card.getByRole('button', { name: '승인', exact: true });
      if (!(await approve.isVisible().catch(() => false))) {
        const make = card.getByRole('button', { name: /AI (초안 만들기|검토 초안)/ });
        if (await make.isVisible().catch(() => false)) {
          await make.click();
          await approve.or(card.getByRole('button', { name: '다시 쓰기' })).first().waitFor({ timeout: LONG });
        }
      }
      if (!(await approve.isVisible().catch(() => false))) return { ok: false, note: note + '승인 버튼 없음' };
      const done = O.waitForResponse((r) => /\/api\/corrections\/\d+\/approve/.test(r.url()), { timeout: LONG });
      await approve.click();
      const res = await done;
      return { ok: res.ok(), note: note + `승인 ${res.status()}` };
    } catch (e) {
      await O.screenshot({ path: path.join(OUT, 'fail_6.png') }).catch(() => {});
      throw e;
    } finally { await octx.close(); }
  }, { needs: '5' });

  await scene('7', '다시 질문 — 정정 반영', async () => {
    await U.reload();
    await U.locator('#sidebar-domain', { hasText: SC.ops.label }).waitFor();
    await newChat(U);
    await U.getByText(/내 정정 신고 \d+건 처리됨/).waitFor({ timeout: 30_000 });
    await waitAnswer(U, await ask(U, SC.ops.q));
    const a = await lastAnswer(U);
    const reflected = SC.ops.expect_after ? a.text.includes(SC.ops.expect_after) : true;
    return { ok: !a.noKnowledge && reflected, note: `처리 알림 있음 · 정정 값 ${reflected ? '답에 있음' : '답에 없음'}` };
  }, { needs: '6' });

  await uctx.close();

  await scene('R', '지식 등록 — 넣을 곳·비슷한 지식', async () => {
    const s = SC.register;
    // 기준 지식 — 지정이 없으면 등록할 컬렉션 안에서 고른다. 다른 컬렉션 글이면 v2.231 "거의 같은 글 우선"이 넣을 곳을 그쪽으로 바꿔
    // 버튼이 [승인 요청]이 되고 비슷한 지식 단계가 안 나온다(11/12 첫 실행 실패 — 딜리버스 #8을 홈페이지 담당자가 등록)
    const nsLit = s.ns.replace(/'/g, "''");
    const base = s.base_knowledge_id
      ? psql(`select content from rag_knowledge where id = ${Number(s.base_knowledge_id)} and status = 'active'`)
      : psql(`select content from rag_knowledge where status = 'active' and namespace_id = (select id from ops_namespace where name = '${nsLit}')
          and length(content) between 150 and 600 order by id limit 1`);
    if (!base) return { ok: false, note: `기준 지식 없음(scenario.json register)` };
    const before = Number(psql(`select count(*) from rag_knowledge`));
    const rctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
    const R = await rctx.newPage();
    try {
      await login(R, SC.users.owner.name);
      await setDomain(R, s.ns, s.label);
      await R.getByRole('link', { name: 'Admin' }).click();
      const nav = R.getByRole('navigation', { name: '관리자 메뉴' });
      await nav.getByRole('button', { name: '운영 지식', exact: true }).click();
      await R.getByRole('button', { name: '지식 등록', exact: true }).click();
      await R.getByText('직접 입력').click();
      await R.locator('textarea:visible').first().fill(base + s.extra);
      await R.getByRole('button', { name: '넣을 곳 확인' }).click();
      await R.waitForFunction(() => [...document.querySelectorAll('button')].some((b) => ['등록', '승인 요청'].includes(b.textContent?.trim() ?? '') && !b.disabled), null, { timeout: LONG });
      if (await R.getByRole('button', { name: '승인 요청', exact: true }).isVisible().catch(() => false))
        return { ok: false, note: '넣을 곳이 다른 담당 컬렉션으로 잡혀 [승인 요청] — 기준 지식을 등록 컬렉션 안에서(scenario.json)' };
      await R.getByRole('button', { name: '등록', exact: true }).click();
      const step = R.getByTestId('similar-step');
      await step.waitFor({ timeout: 60_000 });
      await step.getByTestId('similar-skip').click();
      await R.waitForTimeout(1500);
      const after = Number(psql(`select count(*) from rag_knowledge`));
      return { ok: after === before, note: `비슷한 지식 창 뜸 · [등록 안 함] 뒤 지식 수 ${before}→${after}` };
    } catch (e) {
      await R.screenshot({ path: path.join(OUT, 'fail_R.png') }).catch(() => {});   // 창을 닫기 전에(장면 실행기 스크린샷은 닫힌 뒤라 못 찍음)
      throw e;
    } finally { await rctx.close(); }
  });
} finally {
  await browser.close();
}

const pass = rows.filter((r) => r.ok).length;
const result = { at: new Date().toISOString(), frontend: FE, total: rows.length, pass, sec: Math.round((Date.now() - t0) / 1000), rows };
fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify(result, null, 1));
console.log(`\n리허설 ${pass}/${rows.length} 통과 · ${result.sec}s · ${path.join(OUT, 'result.json')}`);
process.exitCode = pass === rows.length ? 0 : 1;
