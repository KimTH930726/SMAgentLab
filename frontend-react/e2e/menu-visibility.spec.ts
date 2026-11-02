import { test, expect } from '@playwright/test';
import { goAdmin, gotoAdminUrl, selectDomain } from './admin-nav';
import { createSystem, dropSystem, psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 메뉴 노출(10/23) — 보는 컬렉션 종류(업무 / 시스템, 11/02)마다 왼쪽 메뉴 노출. 시스템 컬렉션은 담을 수 없는 메뉴(정책서·VOC)가 늘 빠지고("—"),
// 설정에서 끈 메뉴도 빠진다. 저장은 DB 값으로 확인(200 OK ≠ 저장됨). 시스템 컬렉션 표의 줄을 누르면 보는 컬렉션이 바뀐다.
const SYS = 'E2E메뉴시스템';
const KEY = 'menu_visibility';
const saved = () => psql(`SELECT coalesce((SELECT value FROM ops_system_config WHERE key = '${KEY}'), '')`);
let before = '';

test.beforeAll(() => { before = saved(); });
test.afterEach(() => dropSystem(SYS));
test.afterAll(() => {
  // 원래 값으로 되돌린다(없었으면 지움) — 실사용 설정을 테스트 부산물로 남기지 않는다
  if (before) psql(`UPDATE ops_system_config SET value = $$${before}$$ WHERE key = '${KEY}'`);
  else psql(`DELETE FROM ops_system_config WHERE key = '${KEY}'`);
});

test('메뉴 노출 — 시스템 컬렉션에서 통계 끄기 → 시스템을 볼 때만 메뉴에서 빠짐 · 담을 수 없는 칸은 "—"', async ({ page }) => {
  psql(`DELETE FROM ops_system_config WHERE key = '${KEY}'`);
  createSystem(SYS);
  const nav = page.getByRole('navigation', { name: '관리자 메뉴' });

  await loginAdmin(page);
  await goAdmin(page, '시스템·권한', '설정');
  await page.getByRole('button', { name: '메뉴 노출' }).click();
  const box = page.getByTestId('menu-visibility');
  await expect(box.locator('#mv-domain-k\\.policy')).toBeChecked();
  await expect(box.locator('#mv-system-k\\.policy')).toHaveCount(0);          // 시스템엔 정책서 칸 없음("—")
  await expect(box.locator('#mv-system-voc')).toHaveCount(0);                   // VOC도
  await expect(box.locator('#mv-system-k\\.system')).toBeChecked();           // 시스템 정보는 둘 다
  await box.locator('#mv-system-q\\.stats').uncheck();
  await box.getByRole('button', { name: '저장' }).click();
  await expect(box).toContainText('저장했어요');
  await expect.poll(() => saved()).toContain('"kind:system": ["q.stats"]');

  // 정책 컬렉션을 볼 땐 통계가 그대로
  await selectDomain(page, '딜리버스');
  await goAdmin(page, '품질', '통계');
  await expect(page).toHaveURL(/\/admin\/q\.stats$/);

  // 시스템 컬렉션 표에서 줄을 눌러 보는 컬렉션을 바꾼다 → 통계·정책서가 메뉴에서 빠지고 지금 화면(통계)은 운영 지식으로
  await goAdmin(page, '컬렉션', '시스템 컬렉션');
  const row = page.getByTestId('system-collections').getByRole('row').filter({ hasText: SYS });
  await row.getByText(SYS, { exact: true }).click();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('opslens.namespace:admin'))).toBe(SYS);
  await expect(row).toHaveAttribute('aria-current', 'true');
  await gotoAdminUrl(page, '/admin/q.stats');
  await expect(page).toHaveURL(/\/admin\/k\.docs$/);
  await expect(nav.getByRole('button', { name: '정책서', exact: true })).toHaveCount(0);
  const quality = nav.getByRole('button', { name: '품질', exact: true });
  if ((await quality.getAttribute('aria-expanded')) === 'false') await quality.click();
  await expect(nav.getByRole('button', { name: '통계', exact: true })).toHaveCount(0);
  await expect(nav.getByRole('button', { name: '정정 요청', exact: true })).toBeVisible();
});
