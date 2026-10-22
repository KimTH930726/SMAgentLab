import { useMemo, useState } from 'react';
import { ArrowLeftRight } from 'lucide-react';
import { clsx } from 'clsx';
import { Modal } from '../ui/Modal';
import type { NamespaceDetail, Part } from '../../types';
import { byContentThenName, isPickableDomain } from '../../utils/selectedDomain';
import { effectiveOwnerParts, ownerNames } from '../../utils/domainAccess';
import { collectionLabel, isOrgCollection } from '../../utils/collections';
import { OrgCollectionSections, OrgTeamSections, TierCard, TierSection, orgSub, orgTeams, teamSub, type OrgTeam, type Tier } from './collectionCards';

type Tab = 'business' | 'org';

/**
 * 보는 컬렉션 고르기(10/22, 컬렉션 설계 §15-11·§15-13) — 사이드바 칩 + 고르는 창 [업무 | 조직] 탭. 타이핑 없이 누르기만(사용자 10/22 "주관식 별로").
 * 업무 탭: 내 파트 담당 > 우리 팀(다른 파트) 담당 > 다른 팀·담당 없음. 담당 팀은 파트 목록(/auth/parts의 team)으로 안다.
 * 조직 탭: 1단계 팀 카드(내 팀 먼저) → 2단계 그 팀의 팀 공통 + 파트 카드(내 파트 먼저). 두 탭 모두 같은 카드·묶음(collectionCards).
 * 조직 컬렉션을 고르면 지식 문서·용어집·채팅이 그 컬렉션 하나(정책서·시스템 정보는 "없음" 안내). 테스트용 컬렉션은 안 보인다.
 */
export function DomainPicker({ value, onChange, details, parts, userPart }: {
  value: string;
  onChange: (name: string) => void;
  details: NamespaceDetail[];
  parts: Part[];
  userPart?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>('business');
  const [teamKey, setTeamKey] = useState<string | null>(null);   // 조직 탭 2단계에서 연 팀(null = 1단계 팀 카드)
  const { groups, myTeam } = useMemo(() => {
    const teamOf = new Map(parts.map((p) => [p.name, p.team ?? null]));
    const myTeam = userPart ? teamOf.get(userPart) ?? null : null;
    // 담당 N개(10/22): 내 파트가 담당 중 하나면 '내 담당', 담당 중 하나라도 우리 팀 파트면 '우리 팀'
    const tier = (d: NamespaceDetail): Tier => {
      const owners = effectiveOwnerParts(d).map((o) => o.name);
      if (userPart && owners.includes(userPart)) return 'mine';
      if (myTeam && owners.some((o) => teamOf.get(o) === myTeam)) return 'team';
      return 'other';
    };
    const pickable = details.filter(isPickableDomain);
    const by = (t: Tier) => pickable.filter((d) => tier(d) === t);
    return {
      myTeam,
      groups: {
        mine: by('mine').sort(byContentThenName),
        team: by('team').sort(byContentThenName),
        other: by('other').sort(byContentThenName),
      },
    };
  }, [details, parts, userPart]);
  const teams = useMemo(() => orgTeams(details, parts, userPart), [details, parts, userPart]);
  const keyOf = (t: OrgTeam) => String(t.id);
  const openTeam = teams.find((t) => keyOf(t) === teamKey) ?? null;

  const openPicker = () => {
    // 지금 고른 게 조직 컬렉션이면 조직 탭의 그 팀으로 연다
    const cur = details.find((d) => d.name === value);
    const org = isOrgCollection(cur);
    setTab(org ? 'org' : 'business');
    setTeamKey(org && cur ? String(cur.team_id ?? null) : null);
    setOpen(true);
  };
  const choose = (name: string) => {
    onChange(name);
    setOpen(false);
  };

  const businessCard = (d: NamespaceDetail, t: Tier) => {
    const empty = (d.policy_count ?? 0) + d.knowledge_count === 0;
    const ownerName = ownerNames(effectiveOwnerParts(d));
    return (
      <TierCard key={d.name} tier={t} title={d.name} empty={empty} current={d.name === value} onClick={() => choose(d.name)}
        sub={<>{empty ? '아직 비어 있음' : `정책 ${d.policy_count ?? 0} · 지식 ${d.knowledge_count}`}{t === 'team' && ownerName && ` · ${ownerName}`}</>} />
    );
  };
  const onlyOthers = groups.mine.length === 0 && groups.team.length === 0;

  const orgPanel = () => {
    if (teams.length === 0) return <p className="text-sm text-slate-500 py-6 text-center">조직 컬렉션이 아직 없어요.</p>;
    if (!openTeam) {
      return (
        <OrgTeamSections teams={teams} card={(t) => (
          <TierCard key={keyOf(t)} tier={t.tier} title={t.name} empty={t.content === 0} sub={teamSub(t)}
            current={t.collections.some((d) => d.name === value)} onClick={() => setTeamKey(keyOf(t))} />
        )} />
      );
    }
    return (
      <OrgCollectionSections team={openTeam} userPart={userPart} onBack={() => setTeamKey(null)} card={(d, tier) => (
        <TierCard key={d.name} tier={tier} title={d.display_name ?? d.name} empty={d.knowledge_count + d.glossary_count === 0}
          sub={orgSub(d)} current={d.name === value} onClick={() => choose(d.name)} />
      )} />
    );
  };

  const tabBtn = (id: Tab, label: string) => (
    <button key={id} type="button" role="tab" id={`picker-tab-${id}`} aria-selected={tab === id} aria-controls={`picker-panel-${id}`}
      onClick={() => { setTab(id); if (id === 'org') setTeamKey(null); }}
      className={clsx('px-4 py-2 text-sm font-medium border-b-2 transition-colors',
        tab === id ? 'text-indigo-600 border-indigo-500 dark:text-indigo-400 dark:border-indigo-400' : 'text-slate-400 border-transparent hover:text-slate-200')}>
      {label}
    </button>
  );

  return (
    <>
      <span id="sidebar-domain-label" className="text-xs font-medium text-slate-500 block mb-1.5">보는 컬렉션</span>
      <button
        type="button"
        id="sidebar-domain"
        aria-labelledby="sidebar-domain-label sidebar-domain"
        aria-haspopup="dialog"
        onClick={openPicker}
        title="다른 컬렉션으로 바꾸기"
        className="w-full flex items-center justify-between gap-2 bg-slate-900 border border-indigo-500 dark:border-indigo-400 rounded-lg px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 transition-colors"
      >
        <span className="truncate font-medium">{collectionLabel(value, details) || '컬렉션 없음'}</span>
        <ArrowLeftRight className="w-3.5 h-3.5 flex-shrink-0 text-indigo-500 dark:text-indigo-300" aria-hidden="true" />
      </button>

      <Modal isOpen={open} onClose={() => setOpen(false)} title="어떤 컬렉션을 볼까요?" maxWidth="max-w-2xl">
        <div className="space-y-4">
          <div role="tablist" aria-label="컬렉션 종류" className="flex gap-1 border-b border-slate-700">
            {tabBtn('business', '업무')}
            {tabBtn('org', '조직')}
          </div>
          {tab === 'business' ? (
            <div role="tabpanel" id="picker-panel-business" aria-labelledby="picker-tab-business" className="space-y-5">
              <TierSection tier="mine" title={`내 파트 담당 · ${userPart ?? ''}`} count={groups.mine.length}>{groups.mine.map((d) => businessCard(d, 'mine'))}</TierSection>
              <TierSection tier="team" title={`우리 팀 담당${myTeam ? ` · ${myTeam}` : ''}`} count={groups.team.length}>{groups.team.map((d) => businessCard(d, 'team'))}</TierSection>
              <TierSection tier="other" title={onlyOthers ? `업무 컬렉션 ${groups.other.length}개` : '다른 팀 · 담당 없음'} count={groups.other.length}>
                {groups.other.map((d) => businessCard(d, 'other'))}
              </TierSection>
            </div>
          ) : (
            <div role="tabpanel" id="picker-panel-org" aria-labelledby="picker-tab-org" className="space-y-5"
              title="팀·파트의 노하우 — 지식 문서·용어만 담는다">
              {orgPanel()}
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
