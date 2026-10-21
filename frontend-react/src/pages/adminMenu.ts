/**
 * 관리자 메뉴 — 도메인 경계(DDD)대로 6묶음, 순서 = 일하는 흐름(업무 영역을 고르면 → 그 영역의 지식). 설계·검수: docs/tech/admin-ia-design.md (2026-10-21).
 * 사이드바(AdminNav, 왼쪽 아코디언)와 본문(Admin.tsx)이 이 한 목록을 같이 쓴다 — 주소 /admin/<id>.
 */
import type { User } from '../types';

export type AdminMenuId =
  | 'domains'
  | 'k.policy' | 'k.docs' | 'k.glossary' | 'k.system'
  | 'org.users' | 'org.parts' | 'org.domains'
  | 'q.stats' | 'q.corrections' | 'q.eval'
  | 'voc'
  | 'sys.settings' | 'sys.cache';

export interface AdminMenuItem {
  id: AdminMenuId;
  label: string;
  adminOnly?: boolean;
  viewerOk?: boolean;      // 조회 전용(가입 후 승인 대기)에게도 보임 — 나머지는 서버가 403이라 숨긴다
  hint?: string;           // title 툴팁
  scoped?: boolean;        // 고른 업무 영역에 따라 내용이 바뀌는 화면 — 본문 위 경로 표시줄에 업무 영역을 보여 준다
}

export interface AdminMenuGroup {
  id: string;
  label: string;
  items: AdminMenuItem[];  // 하나면 묶음 이름을 누르면 바로 그 화면(아코디언 없음)
}

export const ADMIN_MENU: AdminMenuGroup[] = [
  { id: 'domains', label: '업무 영역', items: [
    { id: 'domains', label: '업무 영역', hint: '업무 영역 목록 — 여기서 고른 영역이 지식 화면의 기본 선택으로 이어진다' },
  ] },
  { id: 'knowledge', label: '지식', items: [
    { id: 'k.policy', label: '정책서', scoped: true },
    { id: 'k.docs', label: '지식 문서', hint: '컨플루언스 등에서 가져온 업무 지식', scoped: true },
    { id: 'k.glossary', label: '용어집', scoped: true },
    { id: 'k.system', label: '시스템 정보', hint: 'DB 칼럼·공통코드 — 준비 중', scoped: true },
  ] },
  { id: 'org', label: '조직', items: [
    { id: 'org.parts', label: '팀·파트', adminOnly: true, hint: '팀 > 파트 — 파트를 누르면 사람·담당 업무 영역을 한눈에' },
    { id: 'org.users', label: '사용자', adminOnly: true },
    { id: 'org.domains', label: '담당 업무 영역', adminOnly: true, hint: '파트가 맡은 업무 영역 — 그 파트 사람이 수정할 수 있다' },
  ] },
  { id: 'quality', label: '품질', items: [
    { id: 'q.stats', label: '통계', scoped: true },
    { id: 'q.corrections', label: '정정 요청', hint: '채팅 "답변 틀림"과 평가 게이트 "이상해요"가 모이는 곳 — 승인해야 검색에 반영', scoped: true },
    { id: 'q.eval', label: '평가 게이트' },
  ] },
  { id: 'voc', label: 'VOC 이메일', items: [
    { id: 'voc', label: 'VOC 이메일', scoped: true },
  ] },
  { id: 'system', label: '시스템', items: [
    { id: 'sys.settings', label: '설정', viewerOk: true, hint: 'LLM · 프롬프트 · 검색 기준값' },
    { id: 'sys.cache', label: '캐시', adminOnly: true, scoped: true },
  ] },
];

/** 이 사용자에게 보이는 메뉴(빈 묶음은 뺀다). */
export function visibleAdminMenu(user: Pick<User, 'role'> | null | undefined): AdminMenuGroup[] {
  const isAdmin = user?.role === 'admin';
  const isViewer = user?.role === 'viewer';
  return ADMIN_MENU
    .map((g) => ({ ...g, items: g.items.filter((it) => (!it.adminOnly || isAdmin) && (!isViewer || it.viewerOk)) }))
    .filter((g) => g.items.length > 0);
}

/** 주소에서 메뉴 id — 없거나 볼 수 없는 메뉴면 첫 메뉴. */
export function resolveAdminMenu(pathname: string, groups: AdminMenuGroup[]): AdminMenuId {
  const want = decodeURIComponent(pathname.replace(/^\/admin\/?/, '').split('/')[0] || '');
  const all = groups.flatMap((g) => g.items.map((it) => it.id));
  return (all.find((id) => id === want) ?? all[0] ?? 'sys.settings') as AdminMenuId;
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
