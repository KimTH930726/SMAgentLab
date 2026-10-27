import type { ReactNode } from 'react';
import { clsx } from 'clsx';
import { ChevronRight } from 'lucide-react';
import type { NamespaceDetail, Part } from '../../types';
import { effectiveOwnerParts } from '../../utils/domainAccess';
import { isOrgCollection } from '../../utils/collections';

/**
 * 컬렉션 카드·묶음 — 고르는 창의 [업무 | 조직] 두 탭과 관리 화면(컬렉션 › 조직)이 같이 쓴다(10/22 사용자 "통일 규칙", collection-design §15-13).
 * 1순위 "내 것"(mine) = 보라 배경·테두리·굵은 제목 / 2순위 "우리 팀"(team) = 진한 테두리 / 3순위 나머지(other) = 내용 있는 것 먼저·빈 것 흐리게.
 * 슈퍼어드민(파트 없음)은 mine·team이 없다 — 전부 other.
 */
export type Tier = 'mine' | 'team' | 'other';
export const TIER_ORDER: Record<Tier, number> = { mine: 0, team: 1, other: 2 };

export type PendingTarget = 'corrections' | 'checks';

/** 카드 아래 "손볼 것" 한 줄(10/27 사용자 "정정 대기나 원문 확인 있으면 표시" → "투박하다, 누르면 그 화면으로") — 0이면 안 그린다.
 *  onJump가 있으면 누르면 그 화면으로(보는 컬렉션 고르기에서만, 권한 있는 사람만), 없으면 숫자만. */
export function PendingLine({ corrections = 0, sourceChecks = 0, onJump }: {
  corrections?: number; sourceChecks?: number; onJump?: (to: PendingTarget) => void;
}) {
  if (!corrections && !sourceChecks) return null;
  const item = (to: PendingTarget, label: string, n: number, hint: string) => {
    const inner = <><span className="w-1.5 h-1.5 rounded-full bg-amber-500 dark:bg-amber-400" aria-hidden />{label} {n}{onJump && <ChevronRight className="w-3 h-3" aria-hidden />}</>;
    const cls = 'inline-flex items-center gap-1 text-xs text-amber-700 dark:text-amber-300';
    return onJump
      ? <button key={to} type="button" onClick={() => onJump(to)} title={hint} className={clsx(cls, 'hover:underline')}>{inner}</button>
      : <span key={to} title={hint} className={cls}>{inner}</span>;
  };
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 border-t border-slate-700/70 pt-1.5 mt-auto" data-testid="pending-chips">
      {corrections > 0 && item('corrections', '정정 대기', corrections, '처리 전 정정 요청 — 품질 › 정정 요청')}
      {sourceChecks > 0 && item('checks', '원문 확인', sourceChecks, '처리 전 정책서 원문 확인 — 정책서 › 원문 확인')}
    </div>
  );
}

export function TierCard({ tier, title, sub, empty, current, onClick, action, pending, testid }: {
  tier: Tier;
  title: string;
  sub: ReactNode;
  empty: boolean;
  current?: boolean;
  onClick?: () => void;
  action?: ReactNode;      // 카드 아래 버튼(관리 화면 [지식 등록 →]) — 있으면 카드 자체는 버튼이 아니다(버튼 안 버튼 금지)
  // 카드 맨 아래 "손볼 것" 줄 — 건수가 있을 때만. 바로가기(onJump)가 있으면 그 줄에 버튼이 생겨 카드 전체를 버튼으로 두지 않는다(버튼 안 버튼 금지),
  // 없으면 줄은 카드 버튼 안의 글자(넣을 곳 고르기 — 카드 어디를 눌러도 선택되게, 리뷰 10/27)
  pending?: { corrections?: number; sourceChecks?: number; onJump?: (to: PendingTarget) => void };
  testid?: string;
}) {
  const hasPending = !!(pending && (pending.corrections || pending.sourceChecks));
  const footer = hasPending ? <PendingLine {...pending} /> : null;
  const jumpable = hasPending && !!pending?.onJump;
  const cls = clsx(
    'text-left min-h-[56px] px-3 py-2 rounded-lg border transition-colors',   // 같은 줄 카드는 grid가 높이를 맞춘다(아래 줄은 mt-auto로 바닥에)
    tier === 'mine' ? 'bg-indigo-50 dark:bg-indigo-950/40' : 'bg-slate-900',
    onClick && !action && (tier === 'mine' ? 'hover:bg-indigo-100 dark:hover:bg-indigo-900/40' : 'hover:bg-slate-800'),
    current ? 'border-indigo-500 dark:border-indigo-400 ring-1 ring-indigo-500 dark:ring-indigo-400'
      : tier === 'mine' ? 'border-indigo-300 dark:border-indigo-700' : tier === 'team' ? 'border-slate-600' : 'border-slate-700',
  );
  const body = (
    <>
      <span className={clsx('block text-sm', tier === 'mine' ? 'font-semibold' : 'font-medium', empty ? 'text-slate-500' : 'text-slate-100')}>
        {title}{current && <span className="ml-1 text-xs text-indigo-600 dark:text-indigo-300">· 지금</span>}
      </span>
      <span className="block text-xs text-slate-500">{sub}</span>
    </>
  );
  if (onClick && !action && !jumpable) {
    return (
      <button type="button" onClick={onClick} aria-current={current ? 'true' : undefined} className={clsx(cls, 'flex flex-col gap-1')} data-testid={testid}>
        {body}{footer}
      </button>
    );
  }
  if (onClick) {
    // 아래에 버튼이 있는 카드(관리 화면 [지식 등록 →], 손볼 것 바로가기) — 제목·설명 부분만 버튼, 아래는 따로(버튼 안 버튼 금지)
    return (
      <div className={clsx(cls, 'flex flex-col gap-1', !current && 'hover:border-slate-500')} data-testid={testid}>
        <button type="button" onClick={onClick} aria-current={current ? 'true' : undefined} title="이 컬렉션을 보는 컬렉션으로"
          className="text-left rounded focus:outline-none focus-visible:ring-1 focus-visible:ring-indigo-500 dark:focus-visible:ring-indigo-400">{body}</button>
        {action && <div className="mt-1">{action}</div>}
        {footer}
      </div>
    );
  }
  return <div className={clsx(cls, 'flex flex-col gap-1')} data-testid={testid}>{body}{action && <div className="mt-1">{action}</div>}{footer}</div>;
}

export function TierSection({ tier, title, children, count }: { tier: Tier; title: string; children: ReactNode; count: number }) {
  if (count === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className={clsx('text-xs', tier === 'mine' ? 'font-medium text-indigo-600 dark:text-indigo-300' : 'text-slate-500')}>{title}</h3>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">{children}</div>
    </section>
  );
}

// ── 조직 컬렉션 묶기: 팀 → (팀 공통 + 파트) ────────────────────────────────────

export const orgContent = (d: NamespaceDetail) => d.knowledge_count + d.glossary_count;
export const orgSub = (d: NamespaceDetail) =>
  orgContent(d) === 0 ? '아직 비어 있음' : `지식 ${d.knowledge_count} · 용어 ${d.glossary_count}`;

export interface OrgTeam {
  id: number | null;          // 팀 없는 파트 묶음은 null
  name: string;
  collections: NamespaceDetail[];
  content: number;
  tier: Tier;                 // 내 팀 = mine, 나머지 = other
}

const NO_TEAM = '팀 없음';

/** 조직 컬렉션을 팀별로 — 내 팀 먼저(보라), 나머지는 내용 있는 팀 먼저 · 가나다. 팀 공통이 없어도 파트가 있으면 팀이 보인다. */
export function orgTeams(details: NamespaceDetail[], parts: Part[], userPart?: string | null): OrgTeam[] {
  const myTeamId = userPart ? parts.find((p) => p.name === userPart)?.team_id ?? null : null;
  const by = new Map<number | null, OrgTeam>();
  for (const d of details.filter(isOrgCollection)) {
    const id = d.team_id ?? null;
    const t = by.get(id) ?? { id, name: d.team ?? NO_TEAM, collections: [], content: 0, tier: (id !== null && id === myTeamId ? 'mine' : 'other') as Tier };
    t.collections.push(d);
    t.content += orgContent(d);
    by.set(id, t);
  }
  return [...by.values()].sort((a, b) =>
    TIER_ORDER[a.tier] - TIER_ORDER[b.tier] || Number(b.content > 0) - Number(a.content > 0) || a.name.localeCompare(b.name, 'ko'));
}

/** 한 팀 안의 카드 등급 — 내 파트 = mine, 내 팀의 팀 공통·같은 팀 다른 파트 = team, 그 밖 = other */
export function orgTier(d: NamespaceDetail, team: OrgTeam, userPart?: string | null): Tier {
  const isTeamCommon = d.owner_team_id != null;
  if (!isTeamCommon && userPart && effectiveOwnerParts(d).some((o) => o.name === userPart)) return 'mine';
  return team.tier === 'mine' ? 'team' : 'other';
}

/** 팀 안 순서 — 등급 → 팀 공통 먼저 → 내용 있는 것 먼저 → 가나다 */
export function sortOrgCollections(items: NamespaceDetail[], team: OrgTeam, userPart?: string | null): NamespaceDetail[] {
  return [...items].sort((a, b) =>
    TIER_ORDER[orgTier(a, team, userPart)] - TIER_ORDER[orgTier(b, team, userPart)]
    || Number(b.owner_team_id != null) - Number(a.owner_team_id != null)
    || Number(orgContent(b) > 0) - Number(orgContent(a) > 0)
    || (a.display_name ?? a.name).localeCompare(b.display_name ?? b.name, 'ko'));
}

export const teamSub = (t: OrgTeam) =>
  t.content === 0 ? `컬렉션 ${t.collections.length}개 · 아직 비어 있음` : `컬렉션 ${t.collections.length}개 · 지식·용어 ${t.content}`;

/** 조직 1단계 — 팀 카드 묶음(내 팀 / 다른 팀). 카드 모양은 부르는 쪽이(고르는 창 = 누르면 2단계, 관리 화면도 같음) */
export function OrgTeamSections({ teams, card }: { teams: OrgTeam[]; card: (t: OrgTeam) => ReactNode }) {
  const mine = teams.filter((t) => t.tier === 'mine');
  const rest = teams.filter((t) => t.tier !== 'mine');
  return (
    <>
      <TierSection tier="mine" title="내 팀" count={mine.length}>{mine.map(card)}</TierSection>
      <TierSection tier="other" title={mine.length ? '다른 팀' : `팀 ${rest.length}개`} count={rest.length}>{rest.map(card)}</TierSection>
    </>
  );
}

/** 조직 2단계 — "조직 › 팀" 경로 + 그 팀의 팀 공통·파트 카드 묶음(내 파트 / 우리 팀 / 나머지) */
export function OrgCollectionSections({ team, userPart, onBack, card }: {
  team: OrgTeam;
  userPart?: string | null;
  onBack: () => void;
  card: (d: NamespaceDetail, tier: Tier) => ReactNode;
}) {
  const items = sortOrgCollections(team.collections, team, userPart);
  const of = (tier: Tier) => items.filter((d) => orgTier(d, team, userPart) === tier);
  const list = (tier: Tier) => of(tier).map((d) => card(d, tier));
  return (
    <>
      <nav aria-label="조직 경로" className="flex items-center gap-1 text-sm">
        <button type="button" onClick={onBack} className="text-indigo-600 dark:text-indigo-300 hover:underline">조직</button>
        <ChevronRight className="w-3.5 h-3.5 text-slate-500" aria-hidden="true" />
        <span className="text-slate-200 font-medium">{team.name}</span>
      </nav>
      <TierSection tier="mine" title={`내 파트 · ${userPart ?? ''}`} count={of('mine').length}>{list('mine')}</TierSection>
      <TierSection tier="team" title={`우리 팀 · ${team.name}`} count={of('team').length}>{list('team')}</TierSection>
      <TierSection tier="other" title={`${team.name} 컬렉션 ${of('other').length}개`} count={of('other').length}>{list('other')}</TierSection>
    </>
  );
}
