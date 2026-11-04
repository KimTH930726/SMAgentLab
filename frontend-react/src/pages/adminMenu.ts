/**
 * 관리자 메뉴 — 보는 사람별 두 범위(컬렉션 관리 / 플랫폼 관리, 11/03 docs/platform/admin-audience-split-design.md).
 * 앞선 구조: docs/platform/admin-ia-design.md(10/21 도메인 5묶음), 컬렉션 개편 docs/domains/knowledge/collection-design.md §15(10/22).
 * 사이드바(AdminNav)와 본문(Admin.tsx)이 이 한 목록을 같이 쓴다 — 주소 /admin/<id>(옛 주소 그대로).
 */
import type { User } from '../types';

export type AdminMenuId =
  | 'home' | 'k.docs' | 'k.policy' | 'q.corrections' | 'k.glossary' | 'q.stats' | 'k.sources' | 'c.info'
  | 'p.home' | 'q.eval' | 'q.decisions' | 'q.feedback' | 'domains' | 'systems' | 'org' | 'voc' | 'sys.settings';

/** 관리 범위 — 컬렉션 관리(담당 파트 = 시니어) / 플랫폼 관리(관리자만) */
export type AdminScope = 'collection' | 'platform';
export const ADMIN_SCOPES: { scope: AdminScope; label: string; home: AdminMenuId; hint: string }[] = [
  { scope: 'collection', label: '컬렉션 관리', home: 'home', hint: '보는 컬렉션의 지식·정정·통계 — 담당 파트가 처리' },
  { scope: 'platform', label: '플랫폼 관리', home: 'p.home', hint: 'OpsLens 전체 — 평가 게이트·AI 감사·조직·설정(관리자)' },
];

export interface AdminMenuItem {
  id: AdminMenuId;
  label: string;
  adminOnly?: boolean;
  viewerOk?: boolean;      // 조회 전용(가입 후 승인 대기)에게도 보임 — 나머지는 서버가 403이라 숨긴다
  hint?: string;           // title 툴팁
  scoped?: boolean;        // 고른 컬렉션에 따라 내용이 바뀌는 화면 — 본문 위 경로 표시줄에 컬렉션을 보여 준다
  /** 이 메뉴가 담을 수 있는 컬렉션 종류(scoped만) — 없으면 둘 다. 서버도 그 종류엔 그 데이터를 400으로 막는다(정책 가져오기·VOC·참조데이터).
   *  설정(메뉴 노출)은 이 안에서 숨기기만 한다 — 담을 수 없는 종류에 켤 수는 없다(10/23). */
  kinds?: CollectionKind[];
  /** 메뉴 노출 설정으로 끌 수 없음 — 운영 지식은 숨긴 메뉴 주소의 도착지이자 [지식 등록 →]·[보기 →] 링크가 가는 곳(리뷰 10/23), 홈은 범위의 첫 화면 */
  required?: boolean;
}

/** 보는 컬렉션 종류 — 업무(area=policy) / 시스템(area=system). 둘 다 kind='domain'이라 종류는 상세 목록의 area로 안다(11/02) */
export type CollectionKind = 'domain' | 'system';
export const COLLECTION_KINDS: { kind: CollectionKind; label: string }[] = [
  { kind: 'domain', label: '정책 컬렉션' },
  { kind: 'system', label: '시스템 컬렉션' },
];
/** 메뉴 노출 설정의 범위 키 — 나중에 권한별은 role:<역할>로 같은 모양(api/menu.ts) */
export const kindScope = (kind: CollectionKind) => `kind:${kind}`;

export interface AdminMenuGroup {
  id: string;
  label: string;
  scope: AdminScope;
  items: AdminMenuItem[];  // 하나면 묶음 이름을 누르면 바로 그 화면(아코디언 없음)
}

// 11/03 설계 §3·§4 — 화면 내용은 그대로, 자리만 옮겼다. 시스템 정보(준비 중)는 내림, 캐시는 설정 안 탭(옛 주소 sys.cache → 설정 › 캐시).
// 플랫폼 관리는 관리자만 — 단 설정(viewerOk)은 누구나 보던 그대로라, 관리자가 아니면 컬렉션 관리 메뉴 끝에 붙는다(visibleAdminMenu).
const one = (scope: AdminScope, it: AdminMenuItem): AdminMenuGroup => ({ id: it.id, label: it.label, scope, items: [it] });
export const ADMIN_MENU: AdminMenuGroup[] = [
  one('collection', { id: 'home', label: '홈', hint: '보는 컬렉션의 할 일 — 정정 요청 · 승인 대기 · 적재 검토 · 지식 공백', scoped: true, required: true }),
  // 운영 지식은 시스템 컬렉션에만(11/02 D) — 정책 컬렉션에선 메뉴를 안 보인다(빈 목록 막다른 길, 11/03 태훈·기능 리뷰 #4)
  one('collection', { id: 'k.docs', label: '운영 지식', hint: '시스템 운영·개발 지식 — 컨플·매뉴얼·장애 대응(정책서는 따로)', scoped: true, required: true, kinds: ['system'] }),
  one('collection', { id: 'k.policy', label: '정책서', scoped: true, kinds: ['domain'] }),
  one('collection', { id: 'q.corrections', label: '정정 요청', hint: '채팅 "답변 틀림"·평가 게이트 "이상해요"·정책서 수정 제안이 모이는 곳 — 승인해야 검색에 반영', scoped: true }),
  one('collection', { id: 'k.glossary', label: '용어집', scoped: true }),
  one('collection', { id: 'q.stats', label: '통계', scoped: true }),
  one('collection', { id: 'k.sources', label: '소스 연결', hint: '컨플 공간 연결 — 묶음 연결 · 동기화 · 적재 이력(팀 단위, 보는 컬렉션과 무관)' }),
  one('collection', { id: 'c.info', label: '컬렉션 정보', hint: '담당 파트 · 관련 정책/시스템 · 코드(수정은 관리자 — 플랫폼 관리 › 컬렉션)', scoped: true }),
  one('platform', { id: 'p.home', label: '홈', adminOnly: true, hint: 'OpsLens 상태 — 정답률 · 단정 오답 · AI 판단 · LLM 호출 · 피드백 · 담당 없는 컬렉션' }),
  one('platform', { id: 'q.eval', label: '평가 게이트', adminOnly: true, hint: '정답률 → 왜 틀렸나 → 개선 후보 → 사람 검수' }),
  one('platform', { id: 'q.decisions', label: 'AI 감사', adminOnly: true, hint: 'AI 판단마다 사람 검토 결과(수용·번복) — 리포트 · 표본 감사 · 감사 로그' }),
  one('platform', { id: 'q.feedback', label: '사용자 피드백', adminOnly: true, hint: '오류 신고 · 기능 제안([피드백 보내기]) + 자동 감지(채팅 장애) — 선정·완료·중복·기각' }),
  { id: 'collections', label: '컬렉션', scope: 'platform', items: [
    { id: 'domains', label: '정책 컬렉션', adminOnly: true, hint: '업무별 지식을 두는 곳 — 담당 파트·건수' },
    { id: 'systems', label: '시스템 컬렉션', adminOnly: true, hint: 'IT 시스템별 지식(운영·장애·연동) — 담당 파트 · 관련 정책' },
  ] },
  one('platform', { id: 'org', label: '조직·권한', adminOnly: true, hint: '사용자 · 팀 › 파트 · 담당 컬렉션(서브탭)' }),
  one('platform', { id: 'voc', label: 'VOC 이메일', adminOnly: true, scoped: true, kinds: ['domain'] }),
  one('platform', { id: 'sys.settings', label: '설정', viewerOk: true, hint: 'LLM · 프롬프트 · 검색 기준값 · 메뉴 노출 · 캐시' }),
];

/** 고른 컬렉션 종류에서 이 메뉴를 담을 수 있나(설정과 무관한 상한). */
export const supportsKind = (it: AdminMenuItem, kind: CollectionKind) => !it.scoped || !it.kinds || it.kinds.includes(kind);

/** 이 사용자에게 보이는 메뉴(빈 묶음은 뺀다). ctx가 있으면 고른 컬렉션 종류에 맞춰 — 담을 수 없는 메뉴 + 설정에서 숨긴 메뉴를 뺀다(10/23).
 *  관리자가 아니면 범위가 하나(컬렉션 관리)뿐 — 플랫폼 쪽에서 남는 메뉴(설정)는 컬렉션 관리로 옮겨 붙인다. */
export function visibleAdminMenu(user: Pick<User, 'role'> | null | undefined,
                                 ctx?: { kind: CollectionKind; hidden?: Record<string, string[]> }): AdminMenuGroup[] {
  const isAdmin = user?.role === 'admin';
  const isViewer = user?.role === 'viewer';
  const hidden = new Set(ctx ? ctx.hidden?.[kindScope(ctx.kind)] ?? [] : []);
  return ADMIN_MENU
    .map((g) => ({ ...g, scope: isAdmin ? g.scope : 'collection' as AdminScope,
      items: g.items.filter((it) => (!it.adminOnly || isAdmin) && (!isViewer || it.viewerOk)
        && (!ctx || !it.scoped || (supportsKind(it, ctx.kind) && (it.required || !hidden.has(it.id))))) }))
    .filter((g) => g.items.length > 0);
}

/** 옛 주소 → 지금 메뉴(링크·E2E가 안 깨지게). 캐시는 설정 안 탭이라 Admin이 ?tab=cache를 붙인다 */
const ALIAS: Record<string, AdminMenuId> = { 'org.collections': 'systems', 'sys.cache': 'sys.settings' };

/** 주소에서 메뉴 id — 주소가 없으면 홈, 없거나 볼 수 없는 메뉴면 운영 지식(숨긴 메뉴의 도착지), 그것도 못 보면 보이는 첫 메뉴. */
export function resolveAdminMenu(pathname: string, groups: AdminMenuGroup[]): AdminMenuId {
  const raw = decodeURIComponent(pathname.replace(/^\/admin\/?/, '').split('/')[0] || '');
  const want = ALIAS[raw] ?? raw;
  const all = groups.flatMap((g) => g.items.map((it) => it.id));
  const first = all[0] ?? 'sys.settings';
  if (!raw) return all.includes('home') ? 'home' : first;
  const hit = all.find((id) => id === want);
  if (hit) return hit;
  // 관리자 전용(플랫폼 관리) 화면을 못 보는 사람 → 컬렉션 관리 홈(Admin이 안내를 띄운다)
  // 컬렉션 목록(플랫폼 관리)을 못 보는 사람의 옛 주소 → 컬렉션 관리 › 컬렉션 정보(읽기) — 막힘 안내보다 먼저(같은 내용을 읽기로 볼 수 있다, 재리뷰 L1)
  if ((want === 'domains' || want === 'systems') && all.includes('c.info')) return 'c.info';
  if (blockedPlatform(pathname, groups) && all.includes('home')) return 'home';
  // 숨긴 메뉴의 도착지 = 운영 지식, 정책 컬렉션이라 운영 지식이 없으면 홈
  return all.includes('k.docs') ? 'k.docs' : all.includes('home') ? 'home' : first;
}

/** 이 주소가 플랫폼 관리 화면인데 이 사람에게 안 보이나(비관리자의 깊은 링크) */
export function blockedPlatform(pathname: string, groups: AdminMenuGroup[]): boolean {
  const raw = decodeURIComponent(pathname.replace(/^\/admin\/?/, '').split('/')[0] || '');
  const id = (ALIAS[raw] ?? raw) as AdminMenuId;
  const found = findAdminItem(id);
  // 플랫폼 관리 범위가 아예 없는 사람(비관리자)만 — 관리자가 시스템 컬렉션을 보며 VOC 주소로 온 경우는 기존대로 운영 지식으로
  if (id === 'domains' || id === 'systems') return false;   // 컬렉션 목록은 컬렉션 정보(읽기)로 바로 — 막힘 안내 없이
  return !!found && found.group.scope === 'platform' && !groups.some((g) => g.scope === 'platform')
    && !groups.some((g) => g.items.some((it) => it.id === id));
}

/** 옛 주소가 설정 안 탭을 가리키면 그 탭 — /admin/sys.cache → 설정 › 캐시 */
export function aliasTab(pathname: string): string | null {
  return /^\/admin\/sys\.cache(\/|$)/.test(pathname) ? 'cache' : null;
}

export const adminPath = (id: AdminMenuId) => `/admin/${id}`;

/** 메뉴 id → (묶음 이름, 항목) — 경로 표시줄용 */
export function findAdminItem(id: AdminMenuId): { group: AdminMenuGroup; item: AdminMenuItem } | null {
  for (const group of ADMIN_MENU) {
    const item = group.items.find((it) => it.id === id);
    if (item) return { group, item };
  }
  return null;
}

/** 이 메뉴가 든 범위(메뉴 정의 기준) */
export const scopeOf = (id: AdminMenuId): AdminScope => findAdminItem(id)?.group.scope ?? 'collection';
