import { test, expect } from '@playwright/test';
import { domainSelect, goAdmin, gotoAdminUrl } from './admin-nav';
import { dropSystem, psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 시스템 컬렉션(11/02, docs/domains/knowledge/taxonomy-standard.md — 옛 조직 컬렉션 자리) — 컬렉션 › 시스템 컬렉션에서 [시스템 추가]하면
// DB에 kind='domain' · area='system' · 최상위로 생기고, 관련 정책을 고르면 ops_link 'supports'로 저장된다(200 OK ≠ 저장됨 — DB로 확인).
// 고르는 창 [시스템] 탭에서 고르면 칩에 그 이름, 정책 탭·정책 컬렉션 표엔 안 보이고, 정책서·VOC 메뉴는 숨김(주소로 와도 운영 지식으로).
const SYS = 'E2E시스템컬렉션';
const SYS_KEY = `${SYS} 시스템`;   // 키 이름(만들 때 " 시스템"이 붙는다, 11/02)
const esc = (t: string) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const SYS2 = 'E2E시스템컬렉션2';
test.afterEach(() => { dropSystem(SYS); dropSystem(SYS_KEY); dropSystem(`${SYS2} 시스템`); });

test('시스템 컬렉션 — 추가 · 관련 정책 저장 · 고르는 창 [시스템] 탭 · 정책서·VOC 메뉴 숨김', async ({ page }) => {
  dropSystem(SYS);
  dropSystem(SYS_KEY);
  const biz = psql(`SELECT name FROM ops_namespace WHERE kind = 'domain' AND area = 'policy' AND parent_id IS NULL ORDER BY id LIMIT 1`);
  test.skip(!biz, '정책 컬렉션이 없음');

  await loginAdmin(page);
  await goAdmin(page, '컬렉션', '시스템 컬렉션');
  const panel = page.getByTestId('system-collections');

  // ① 시스템 추가 → DB: 업무 영역과 같은 kind, 구분값 system, 상위 없음
  await panel.getByRole('button', { name: '시스템 추가' }).click();
  await page.locator('#sys-name').fill(SYS);
  await page.locator('#sys-desc').fill('e2e');
  await page.getByRole('button', { name: '추가', exact: true }).click();
  await expect.poll(() => psql(`SELECT kind || '|' || area || '|' || coalesce(parent_id::text, '') FROM ops_namespace WHERE name = '${SYS_KEY}'`))
    .toBe('domain|system|');
  const row = panel.getByRole('row').filter({ hasText: SYS });
  await expect(row).toContainText('없음(관리자만)');   // 담당은 조직·권한 › 담당 컬렉션에서

  // ② 관련 정책 — 안 바꾸면 저장 꺼짐 → 하나 골라 저장 → DB(ops_link) · 표
  await row.getByRole('button', { name: `${SYS} 관련 정책 수정` }).click();
  const form = page.locator('form').filter({ has: page.getByTestId('supports-list') });
  await expect(form.getByRole('button', { name: '저장' })).toBeDisabled();
  await form.getByLabel(biz, { exact: true }).check();
  await form.getByRole('button', { name: '저장' }).click();
  await expect(form).toHaveCount(0);
  await expect.poll(() => psql(`SELECT coalesce(string_agg(b.name, ','), '') FROM ops_link l
      JOIN ops_namespace s ON s.id::text = l.from_key JOIN ops_namespace b ON b.id::text = l.to_key
      WHERE l.relation = 'supports' AND l.status = 'active' AND s.name = '${SYS_KEY}'`)).toBe(biz);
  await expect(row).toContainText(biz);

  // ③ 정책 컬렉션 표엔 시스템 줄이 없다 — 관련 정책으로 이은 정책 줄엔 "시스템 · …"이 붙고(11/02), 연결 없는 정책엔 "관련 시스템 없음"
  await goAdmin(page, '컬렉션', '정책 컬렉션');
  await expect(page.locator('tbody tr td:first-child div.font-medium').filter({ hasText: new RegExp(`^${SYS}`) })).toHaveCount(0);
  const bizRow = page.getByRole('row').filter({ has: page.locator('div.font-medium', { hasText: new RegExp(`^${biz}(POL-|$)`) }) });
  await expect(bizRow).toContainText(new RegExp(`시스템 · (.+ · )?${SYS}`));   // 여러 시스템이면 " · "로 이어 붙음

  // ④ 고르는 창 — 정책 탭엔 없고 [시스템] 탭에서 고른다, [조직] 탭은 없음
  await domainSelect(page).click();
  const title = page.getByRole('heading', { name: '어떤 컬렉션을 볼까요?' });
  await expect(title).toBeVisible();
  await expect(page.getByRole('tab', { name: '조직' })).toHaveCount(0);
  await expect(page.locator('#picker-panel-policy').getByRole('button', { name: new RegExp(`^${esc(SYS)}`) })).toHaveCount(0);
  await page.getByRole('tab', { name: '시스템' }).click();
  await expect(page.getByRole('tab', { name: '시스템' })).toHaveAttribute('aria-selected', 'true');
  await page.locator('#picker-panel-system').getByRole('button', { name: new RegExp(`^${esc(SYS)}`) }).click();
  await expect(title).toBeHidden();
  await expect(domainSelect(page)).toContainText(SYS);
  expect(await page.evaluate(() => {
    const u = JSON.parse(localStorage.getItem('ops_auth') || '{}').user?.username;
    return (v => { try { return v && v.startsWith('{') ? JSON.parse(v).name : v; } catch { return v; } })(localStorage.getItem(`opslens.namespace:${u}`));   // 저장 값 = {id, name}(키 전환 2단계)
  })).toBe(SYS_KEY);
  // 다시 열면 지금 고른 시스템이 있는 [시스템] 탭으로
  await domainSelect(page).click();
  await expect(page.getByRole('tab', { name: '시스템' })).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('Escape');

  // ⑤ 시스템을 보는 중엔 정책서·VOC가 왼쪽 메뉴에 없다(시스템 정보는 있음) — 주소로 와도 운영 지식으로
  const nav = page.getByRole('navigation', { name: '관리자 메뉴' });
  await goAdmin(page, '지식', '운영 지식');
  await expect(nav.getByRole('button', { name: '시스템 정보', exact: true })).toBeVisible();
  for (const hidden of ['정책서', 'VOC 이메일']) {
    await expect(nav.getByRole('button', { name: hidden, exact: true })).toHaveCount(0);
  }
  await gotoAdminUrl(page, '/admin/k.policy');
  await expect(page).toHaveURL(/\/admin\/k\.docs$/);
  await expect(page.getByTestId('admin-crumb')).toContainText(SYS);

  // ⑥ 옛 조직 컬렉션 주소는 시스템 컬렉션으로
  await gotoAdminUrl(page, '/admin/org.collections');
  await expect(page).toHaveURL(/\/admin\/systems$/);
});


test('시스템 컬렉션 — 이름 수정(화면 이름으로 고치면 키 끝 " 시스템" 유지) · 코드는 그대로 · 삭제', async ({ page }) => {
  const key = `${SYS2} 시스템`;
  dropSystem(key);
  psql(`INSERT INTO ops_namespace (name, description, area, code) VALUES ('${key}', 'e2e', 'system', 'SYS-ZQ')`);
  await loginAdmin(page);
  await goAdmin(page, '컬렉션', '시스템 컬렉션');
  const panel = page.getByTestId('system-collections');
  await expect(panel.getByRole('row').filter({ hasText: SYS2 })).toContainText('SYS-ZQ');
  // 이름 수정 — 창엔 화면 이름(뗀 것), 저장은 키로
  await panel.getByRole('button', { name: `${SYS2} 이름 수정` }).click();
  await expect(page.locator('#ns-rename')).toHaveValue(SYS2);
  await page.locator('#ns-rename').fill(`${SYS2}x`);
  await page.getByRole('button', { name: '저장', exact: true }).click();
  await expect.poll(() => psql(`SELECT name || '|' || code FROM ops_namespace WHERE code = 'SYS-ZQ'`)).toBe(`${SYS2}x 시스템|SYS-ZQ`);
  // 삭제
  await panel.getByRole('button', { name: `${SYS2}x 삭제` }).click();
  await page.getByRole('button', { name: '삭제', exact: true }).click();
  await expect.poll(() => psql(`SELECT count(*) FROM ops_namespace WHERE code = 'SYS-ZQ'`)).toBe('0');
  dropSystem(`${SYS2}x 시스템`);
});
