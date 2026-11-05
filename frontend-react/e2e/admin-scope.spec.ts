import { test, expect } from '@playwright/test';
import { goAdmin, gotoAdminUrl, selectDomain, switchScope } from './admin-nav';
import { API, adminToken, apiFetch, loginAdmin } from './auth';

// 관리 범위 나누기(11/03 docs/platform/admin-audience-split-design.md) — [컬렉션 관리 | 플랫폼 관리] 전환, 범위마다 메뉴가 다르고,
// 옛 주소 /admin/<id>는 그 화면이 든 범위에서 그대로 열린다. 두 홈은 카드(큰 숫자 + [자세히]) — 지금 API 숫자만, 쓰기 없음.
const nav = (page: import('@playwright/test').Page) => page.getByRole('navigation', { name: '관리자 메뉴' });
const pressed = (page: import('@playwright/test').Page, scope: string) =>
  nav(page).getByRole('group', { name: '관리 범위' }).getByRole('button', { name: scope, exact: true });

test('범위 전환 — 컬렉션 관리 홈 ↔ 플랫폼 관리 홈, 범위마다 메뉴가 다름', async ({ page }) => {
  await loginAdmin(page);
  // /admin(주소 없음) → 컬렉션 관리 › 홈
  await expect(page).toHaveURL(/\/admin\/home$/);
  await selectDomain(page, '딜리버스');   // 정책 컬렉션 — 정책서 검토 대기 카드가 보이는 종류
  await expect(pressed(page, '컬렉션 관리')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('collection-home')).toBeVisible();
  for (const it of ['정책서', '정정 요청', '용어집', '소스 연결', '컬렉션 정보']) {
    await expect(nav(page).getByRole('button', { name: it, exact: true })).toBeVisible();
  }
  await expect(nav(page).getByRole('button', { name: '평가 게이트', exact: true })).toHaveCount(0);
  await expect(nav(page).getByRole('button', { name: '시스템 정보', exact: true })).toHaveCount(0);   // 준비 중 메뉴는 내림
  // 카드 = 큰 숫자 + [자세히] — 승인 대기는 운영 지식 › 승인 대기 탭으로
  const cards = page.getByTestId('collection-home-cards');
  await expect(cards.getByTestId('card-checks')).toBeVisible();
  // 정책 컬렉션엔 운영 지식 메뉴·승인 대기 카드가 없다(운영 지식은 시스템에만, 11/03)
  await expect(nav(page).getByRole('button', { name: '운영 지식', exact: true })).toHaveCount(0);
  await expect(cards.getByTestId('card-pending')).toHaveCount(0);
  // 지식 공백 = 통계 API의 no_knowledge(서버가 이미 미해결만 센다 — 해결 건을 또 빼지 않는다, 리뷰 11/03)
  const tok = await adminToken();
  const st = await (await apiFetch(`${API}/api/stats/namespace/${encodeURIComponent('딜리버스')}`, { headers: { Authorization: `Bearer ${tok}` } })).json();
  await expect(cards.getByTestId('card-gap-value')).toHaveText(String(st.no_knowledge));
  await expect(cards.getByTestId('card-gap')).toContainText(`미해결 ${st.no_knowledge} · 해결 ${st.filled}`);
  // 시스템 컬렉션 — 운영 지식 메뉴 · 승인 대기 카드가 있고, 카드는 운영 지식 › 승인 대기 탭으로
  await selectDomain(page, '딜리버스 시스템', '딜리버스');
  await expect(nav(page).getByRole('button', { name: '운영 지식', exact: true })).toBeVisible();
  await expect(cards.getByTestId('card-pending-value')).toHaveText(/^\d+/);
  await cards.getByRole('button', { name: '승인 대기 자세히' }).click();
  await expect(page).toHaveURL(/\/admin\/k\.docs\?tab=review$/);
  // 새로고침해도 그대로 — 컬렉션 종류를 확인하기 전에 홈으로 튕기지 않는다(리뷰 M1 11/03)
  await page.reload();
  await expect(page.getByRole('button', { name: '승인 대기', exact: true }).first()).toBeVisible();
  await expect(page).toHaveURL(/\/admin\/k\.docs\?tab=review$/);
  // VOC 이메일은 시스템 컬렉션을 볼 때만 메뉴에 있다(11/05 정책 → 시스템) — 지금 딜리버스 시스템을 보는 중

  await switchScope(page, '플랫폼 관리');
  await expect(page).toHaveURL(/\/admin\/p\.home$/);
  await expect(page.getByTestId('platform-home')).toBeVisible();
  for (const it of ['평가 게이트', 'AI 감사', '사용자 피드백', '컬렉션', '조직·권한', 'VOC 이메일', '설정']) {
    await expect(nav(page).getByRole('button', { name: it, exact: true })).toBeVisible();
  }
  await expect(nav(page).getByRole('button', { name: '운영 지식', exact: true })).toHaveCount(0);
  await expect(nav(page).getByRole('button', { name: '캐시', exact: true })).toHaveCount(0);   // 캐시는 설정 안 탭
  const pc = page.getByTestId('platform-home-cards');
  for (const id of ['card-acc-knowledge', 'card-acc-policy', 'card-confident-wrong', 'card-overrides', 'card-llm', 'card-feedback', 'card-unowned']) {
    await expect(pc.getByTestId(`${id}-value`)).toHaveText(/\d/);   // 숫자가 실제로 읽혔다("—"·"…" 아님)
  }
  await pc.getByRole('button', { name: '담당 없는 컬렉션 자세히' }).click();   // 조직·권한 › 담당 컬렉션 탭
  await expect(page).toHaveURL(/\/admin\/org\?tab=domains$/);
  await expect(page.getByRole('tab', { name: '담당 컬렉션' })).toHaveAttribute('aria-selected', 'true');
  await goAdmin(page, '홈', '플랫폼 관리');
  await pc.getByRole('button', { name: '정답률 · 업무지식 자세히' }).click();
  await expect(page).toHaveURL(/\/admin\/q\.eval$/);
  await expect(pressed(page, '플랫폼 관리')).toHaveAttribute('aria-pressed', 'true');

  await switchScope(page, '컬렉션 관리');
  await expect(page).toHaveURL(/\/admin\/home$/);
});

test('옛 주소 — 그 화면이 든 범위에서 열림 · 캐시는 설정 › 캐시 · 시스템 정보는 운영 지식으로', async ({ page }) => {
  await loginAdmin(page);
  await gotoAdminUrl(page, '/admin/q.eval');
  await expect(page).toHaveURL(/\/admin\/q\.eval$/);
  await expect(pressed(page, '플랫폼 관리')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('admin-crumb')).toContainText('플랫폼 관리');

  await gotoAdminUrl(page, '/admin/q.corrections');
  await expect(pressed(page, '컬렉션 관리')).toHaveAttribute('aria-pressed', 'true');

  await gotoAdminUrl(page, '/admin/sys.cache');
  await expect(page).toHaveURL(/\/admin\/sys\.settings\?tab=cache$/);
  await expect(page.getByRole('button', { name: '캐시', exact: true })).toBeVisible();
  await expect(page.getByText('Semantic Cache', { exact: false }).first()).toBeVisible();

  // 정책 컬렉션을 보는 중이면 운영 지식이 없어 홈으로, 시스템 컬렉션이면 운영 지식으로(11/03)
  await gotoAdminUrl(page, '/admin/k.system');
  await expect(page).toHaveURL(/\/admin\/(k\.docs|home)$/);

  // 메뉴로 가도 같은 화면(goAdmin이 범위를 바꿔 준다)
  await goAdmin(page, '사용자 피드백');
  await expect(page).toHaveURL(/\/admin\/q\.feedback$/);
  await goAdmin(page, '홈', '컬렉션 관리');
  await expect(page).toHaveURL(/\/admin\/home$/);
});

test('컬렉션 정보 — 보는 컬렉션 한 줄 읽기 전용(만들기·수정·삭제 없음) · 수정은 플랫폼 관리 › 컬렉션', async ({ page }) => {
  await loginAdmin(page);
  await selectDomain(page, '딜리버스');
  await goAdmin(page, '컬렉션 정보');
  const info = page.getByTestId('collection-info');
  await expect(info.getByTestId('info-종류')).toHaveText('정책 컬렉션');
  await expect(info.getByTestId('info-담당 파트')).not.toBeEmpty();
  await expect(page.getByRole('button', { name: /만들기|이름 수정|삭제/ })).toHaveCount(0);
  await page.getByTestId('collection-info-manage').getByRole('button').click();
  await expect(page).toHaveURL(/\/admin\/domains$/);
  await expect(pressed(page, '플랫폼 관리')).toHaveAttribute('aria-pressed', 'true');
});

test('비관리자 — 범위 전환 없음 · 플랫폼 관리 주소로 오면 안내 뒤 컬렉션 관리 홈 · 설정은 그대로', async ({ page }) => {
  await loginAdmin(page);
  // 화면 쪽 역할만 바꿔 본다(서버 계정·DB는 그대로) — 메뉴·주소 처리는 프론트가 역할로 정한다. 앱이 다시 읽는 /auth/me도 같은 역할로
  await page.route('**/api/auth/me', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    await route.fulfill({ response: res, json: { ...body, role: 'user' } });
  });
  await page.evaluate(() => {
    const a = JSON.parse(localStorage.getItem('ops_auth') || '{}');
    a.user.role = 'user';
    localStorage.setItem('ops_auth', JSON.stringify(a));
  });
  await gotoAdminUrl(page, '/admin/q.eval');
  const dlg = page.getByTestId('app-dialog');
  await expect(dlg).toContainText('플랫폼 관리(관리자 전용)');
  await expect(page).toHaveURL(/\/admin\/home$/);
  await dlg.getByRole('button').first().click();
  await expect(nav(page).getByRole('group', { name: '관리 범위' })).toHaveCount(0);
  await expect(nav(page).getByRole('button', { name: '설정', exact: true })).toBeVisible();
  await expect(nav(page).getByRole('button', { name: '평가 게이트', exact: true })).toHaveCount(0);
  // 설정의 관리자 탭(?tab=cache)은 검색 설정으로
  await gotoAdminUrl(page, '/admin/sys.settings?tab=cache');
  await expect(page.getByRole('button', { name: '캐시', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '검색 설정' })).toBeVisible();
});
