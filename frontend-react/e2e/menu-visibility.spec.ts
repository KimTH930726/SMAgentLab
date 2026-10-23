import { test, expect } from '@playwright/test';
import { goAdmin, gotoAdminUrl, selectDomain } from './admin-nav';
import { psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 메뉴 노출(10/23) — 보는 컬렉션 종류마다 왼쪽 메뉴 노출. 조직 컬렉션은 담을 수 없는 메뉴(정책서 등)가 늘 빠지고("—"),
// 설정에서 끈 메뉴도 빠진다. 저장은 DB 값으로 확인(200 OK ≠ 저장됨). 조직 컬렉션 카드를 누르면 보는 컬렉션이 바뀐다.
const KEY = 'menu_visibility';
const saved = () => psql(`SELECT coalesce((SELECT value FROM ops_system_config WHERE key = '${KEY}'), '')`);
let before = '';

test.beforeAll(() => { before = saved(); });
test.afterAll(() => {
  // 원래 값으로 되돌린다(없었으면 지움) — 실사용 설정을 테스트 부산물로 남기지 않는다
  if (before) psql(`UPDATE ops_system_config SET value = $$${before}$$ WHERE key = '${KEY}'`);
  else psql(`DELETE FROM ops_system_config WHERE key = '${KEY}'`);
});

test('메뉴 노출 — 조직 컬렉션에서 통계 끄기 → 조직을 볼 때만 메뉴에서 빠짐 · 담을 수 없는 칸은 "—"', async ({ page }) => {
  psql(`DELETE FROM ops_system_config WHERE key = '${KEY}'`);
  const team = psql(`SELECT t.id || ':' || t.name FROM ops_team t JOIN ops_namespace n ON n.name = 'org:team:' || t.id ORDER BY t.id LIMIT 1`).trim();
  test.skip(!team, '조직 컬렉션이 없음');
  const [tid, tname] = [team.slice(0, team.indexOf(':')), team.slice(team.indexOf(':') + 1)];
  const nav = page.getByRole('navigation', { name: '관리자 메뉴' });

  await loginAdmin(page);
  await goAdmin(page, '시스템·권한', '설정');
  await page.getByRole('button', { name: '메뉴 노출' }).click();
  const box = page.getByTestId('menu-visibility');
  await expect(box.locator('#mv-domain-k\\.policy')).toBeChecked();
  await expect(box.locator('#mv-org_space-k\\.policy')).toHaveCount(0);          // 조직엔 정책서 칸 없음("—")
  await box.locator('#mv-org_space-q\\.stats').uncheck();
  await box.getByRole('button', { name: '저장' }).click();
  await expect(box).toContainText('저장했어요');
  await expect.poll(() => saved()).toContain('"kind:org_space": ["q.stats"]');

  // 업무 컬렉션을 볼 땐 통계가 그대로
  await selectDomain(page, '딜리버스');
  await goAdmin(page, '품질', '통계');
  await expect(page).toHaveURL(/\/admin\/q\.stats$/);

  // 조직 컬렉션 화면에서 팀 공통 카드를 눌러 보는 컬렉션을 바꾼다 → 통계·정책서가 메뉴에서 빠지고 지금 화면(통계)은 지식 문서로
  await goAdmin(page, '컬렉션', '조직 컬렉션');
  const panel = page.getByTestId('org-collections');
  await panel.getByRole('button', { name: new RegExp(`^${tname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`) }).click();
  await panel.getByRole('button', { name: /^팀 공통 · / }).first().click();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('opslens.namespace:admin'))).toBe(`org:team:${tid}`);
  await expect(panel.getByRole('button', { name: /^팀 공통 · .*· 지금/ })).toHaveCount(1);
  await gotoAdminUrl(page, '/admin/q.stats');
  await expect(page).toHaveURL(/\/admin\/k\.docs$/);
  await expect(nav.getByRole('button', { name: '정책서', exact: true })).toHaveCount(0);
  const quality = nav.getByRole('button', { name: '품질', exact: true });
  if ((await quality.getAttribute('aria-expanded')) === 'false') await quality.click();
  await expect(nav.getByRole('button', { name: '통계', exact: true })).toHaveCount(0);
  await expect(nav.getByRole('button', { name: '정정 요청', exact: true })).toBeVisible();
});
