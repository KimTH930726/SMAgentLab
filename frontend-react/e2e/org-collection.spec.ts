import { test, expect } from '@playwright/test';
import { domainSelect, goAdmin } from './admin-nav';
import { psql } from './v0-fixture';
import { API, adminToken, loginAdmin } from './auth';

// 조직 컬렉션(10/22, collection-design §15-13 · v2.160 #83) — 팀·파트를 만들면 조직 컬렉션이 생기고(DB),
// 고르는 창 [조직] 탭 → 팀 카드 → 파트 카드로 고르면 칩에 파트 이름(id 키가 아니라 화면 이름), 정책서 화면은 "조직 컬렉션엔 없음" 안내.
// 임시 팀·파트는 API로 만들고 끝나면 API로 지운다(지우면 조직 컬렉션도 같이 — 남으면 psql로 정리).
const TEAM = 'E2E조직팀';
const PART = 'E2E조직파트';
const esc = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

async function api(path: string, init: RequestInit = {}) {
  const token = await adminToken();
  return fetch(`${API}${path}`, { ...init, headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(init.headers ?? {}) } });
}

async function cleanup() {
  const partId = psql(`SELECT id FROM ops_part WHERE name = '${PART}'`);
  const teamId = psql(`SELECT id FROM ops_team WHERE name = '${TEAM}'`);
  if (partId) await api(`/api/auth/parts/${partId}`, { method: 'DELETE' });
  if (teamId) await api(`/api/auth/teams/${teamId}`, { method: 'DELETE' });
  // API가 못 지운 경우(중간 실패)도 남기지 않는다 — 조직 컬렉션 먼저(팀 칸 FK), 그다음 파트·팀
  if (partId) psql(`DELETE FROM ops_namespace WHERE name = 'org:part:${partId}' AND kind = 'org_space'`);
  if (teamId) psql(`DELETE FROM ops_namespace WHERE name = 'org:team:${teamId}' AND kind = 'org_space'`);
  psql(`DELETE FROM ops_part WHERE name = '${PART}'`);
  psql(`DELETE FROM ops_team WHERE name = '${TEAM}'`);
}

test.afterEach(cleanup);

test('조직 컬렉션 — 팀·파트 만들면 생김 · 조직 탭 팀 → 파트로 고르면 칩에 파트 이름 · 정책서는 안내', async ({ page }) => {
  await cleanup();
  const team = await api('/api/auth/teams', { method: 'POST', body: JSON.stringify({ name: TEAM }) });
  expect(team.status).toBe(201);
  const teamId = (await team.json()).id as number;
  const part = await api('/api/auth/parts', { method: 'POST', body: JSON.stringify({ name: PART, team_id: teamId }) });
  expect(part.status).toBe(201);
  const partId = (await part.json()).id as number;

  // DB: 팀 공통(owner_team_id = 그 팀) + 파트 컬렉션(담당 = 그 파트 한 줄)
  expect(psql(`SELECT owner_team_id FROM ops_namespace WHERE name = 'org:team:${teamId}' AND kind = 'org_space'`)).toBe(String(teamId));
  expect(psql(`SELECT string_agg(o.part_id::text, ',') FROM ops_namespace n JOIN ops_namespace_owner o ON o.namespace_id = n.id
               WHERE n.name = 'org:part:${partId}' AND n.kind = 'org_space'`)).toBe(String(partId));

  await loginAdmin(page);
  await domainSelect(page).click();
  const title = page.getByRole('heading', { name: '어떤 컬렉션을 볼까요?' });
  await expect(title).toBeVisible();
  await page.getByRole('tab', { name: '조직' }).click();
  await expect(page.getByRole('tab', { name: '조직' })).toHaveAttribute('aria-selected', 'true');
  const panel = page.getByRole('tabpanel');
  await panel.getByRole('button', { name: new RegExp(`^${esc(TEAM)}`) }).click();                    // 1단계: 팀 카드
  await expect(panel.getByRole('navigation', { name: '조직 경로' })).toContainText(TEAM);           // 경로 "조직 › 팀"
  await expect(panel.getByRole('button', { name: new RegExp(`^팀 공통 · ${esc(TEAM)}`) })).toBeVisible();   // 그 팀의 팀 공통 카드
  await panel.getByRole('button', { name: new RegExp(`^${esc(PART)}`) }).click();                    // 2단계: 파트 카드
  await expect(title).toBeHidden();
  await expect(domainSelect(page)).toContainText(PART);                                                // 칩 = 화면 이름(id 키 아님)
  await expect(domainSelect(page)).not.toContainText('org:');
  expect(await page.evaluate(() => {
    const u = JSON.parse(localStorage.getItem('ops_auth') || '{}').user?.username;
    return localStorage.getItem(`opslens.namespace:${u}`);
  })).toBe(`org:part:${partId}`);                                                                      // 기억하는 값은 id 키

  await goAdmin(page, '지식', '정책서');
  await expect(page.getByTestId('org-no-policy')).toHaveText('조직 컬렉션엔 정책·시스템 정보가 없어요');
  await expect(page.getByTestId('admin-crumb')).toContainText(PART);
});
