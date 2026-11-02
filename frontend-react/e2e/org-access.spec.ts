import { test, expect } from '@playwright/test';
import { goAdmin } from './admin-nav';
import { psql } from './v0-fixture';
import { loginAdmin } from './auth';

// 시스템·권한 › 조직·권한(10/22) — 서브탭 [사용자|팀·파트|담당 컬렉션], 팀 › 파트 트리에서 팀 만들기 → 그 팀 아래 파트 → 파트가 있는 팀은 삭제가 막힌다.
// 화면 문구가 아니라 DB 값으로 전/후를 본다. 임시 팀·파트는 E2E 접두사로 만들고 끝나면 지운다.
const TEAM = 'E2E팀';
const PART = 'E2E팀파트';
const cleanup = () => {
  psql(`DELETE FROM ops_part WHERE name = '${PART}'`);
  psql(`DELETE FROM ops_team WHERE name = '${TEAM}'`);
};

test.afterEach(cleanup);

test('조직·권한 — 팀 만들기 · 팀 아래 파트 · 파트 있는 팀 삭제 막힘 · 담당 컬렉션 트리', async ({ page }) => {
  cleanup();
  await loginAdmin(page);
  await goAdmin(page, '시스템·권한', '조직·권한');
  await page.getByRole('tab', { name: '팀·파트' }).click();

  // ① 팀 만들기
  await page.getByRole('main').getByRole('button', { name: '팀', exact: true }).click();
  await page.locator('#team-name').fill(TEAM);
  await page.getByRole('button', { name: '저장' }).click();
  await expect.poll(() => psql(`SELECT count(*) FROM ops_team WHERE name = '${TEAM}'`)).toBe('1');
  const teamSection = page.getByRole('region', { name: `${TEAM} 팀` });
  await expect(teamSection).toBeVisible();

  // ② 그 팀 아래 파트 만들기 → DB에서 팀이 맞는지
  await teamSection.getByRole('button', { name: '파트', exact: true }).click();
  await page.getByPlaceholder(/예: 딜리버스/).fill(PART);
  await page.getByRole('button', { name: '생성' }).click();
  await expect.poll(() => psql(`SELECT t.name FROM ops_part p JOIN ops_team t ON t.id = p.team_id WHERE p.name = '${PART}'`)).toBe(TEAM);
  await expect(teamSection.getByRole('button', { name: PART, exact: true })).toBeVisible();

  // ③ 파트가 있는 팀 삭제 → 막힘(오류 창), 팀·파트 그대로
  await teamSection.getByRole('button', { name: `${TEAM} 팀 삭제` }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: '삭제' }).click();
  await expect(page.getByText('파트를 다른 팀으로 먼저 옮겨')).toBeVisible();   // 오류 창(showError)
  await page.getByRole('button', { name: '확인', exact: true }).click();
  expect(psql(`SELECT count(*) FROM ops_team WHERE name = '${TEAM}'`)).toBe('1');
  expect(psql(`SELECT count(*) FROM ops_part p JOIN ops_team t ON t.id = p.team_id WHERE p.name = '${PART}' AND t.name = '${TEAM}'`)).toBe('1');

  // ③-1 조직 컬렉션 폐지(11/02) — 팀·파트를 만들어도 컬렉션이 안 생기고(DB), 고르는 창엔 [정책 | 시스템]뿐
  // 이 테스트가 만든 팀·파트를 가리키는 컬렉션이 없다(전역 0건은 #106이 내용 때문에 남긴 행에 깨진다, 리뷰 L4)
  expect(psql(`SELECT count(*) FROM ops_namespace_owner o JOIN ops_part p ON p.id = o.part_id WHERE p.name = '${PART}'`)).toBe('0');
  await page.locator('#sidebar-domain').click();
  await expect(page.getByRole('tab', { name: '시스템' })).toBeVisible();
  await expect(page.getByRole('tab', { name: '조직' })).toHaveCount(0);
  await page.getByRole('tab', { name: '시스템' }).click();
  await expect(page.locator('#picker-panel-system').getByRole('button', { name: new RegExp(`^(팀 공통 · )?${TEAM}`) })).toHaveCount(0);
  await page.keyboard.press('Escape');

  // ④ 담당 컬렉션 탭 — 팀 줄 아래 파트(트리)
  await page.getByRole('tab', { name: '담당 컬렉션' }).click();
  await expect(page.getByRole('cell', { name: TEAM, exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: PART, exact: true })).toBeVisible();
});

test('관리자 메뉴 — 지금 화면이 든 묶음도 접힌다', async ({ page }) => {
  await loginAdmin(page);
  await goAdmin(page, '지식', '운영 지식');
  const nav = page.getByRole('navigation', { name: '관리자 메뉴' });
  const header = nav.getByRole('button', { name: '지식', exact: true });
  await expect(header).toHaveAttribute('aria-expanded', 'true');
  await header.click();
  await expect(header).toHaveAttribute('aria-expanded', 'false');
  await expect(nav.getByRole('button', { name: '정책서', exact: true })).toHaveCount(0);
  await header.click();
  await expect(header).toHaveAttribute('aria-expanded', 'true');
});
