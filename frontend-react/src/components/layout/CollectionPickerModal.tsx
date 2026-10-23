import { useEffect, useMemo, useState } from 'react';
import { clsx } from 'clsx';
import { Modal } from '../ui/Modal';
import type { NamespaceDetail, Part } from '../../types';
import { byContentThenName, isPickableDomain } from '../../utils/selectedDomain';
import { effectiveOwnerParts, ownerNames } from '../../utils/domainAccess';
import { isOrgCollection } from '../../utils/collections';
import { OrgCollectionSections, OrgTeamSections, TierCard, TierSection, orgSub, orgTeams, teamSub, type OrgTeam, type Tier } from './collectionCards';

type Tab = 'business' | 'org';

/**
 * 컬렉션 고르는 창 — [업무 | 조직] 탭(10/22, 컬렉션 설계 §15-11·§15-13). 보는 컬렉션 고르기(사이드바)와 지식 등록의 넣을 곳 고르기(10/23 사용자 A안)가
 * 같은 창을 쓴다 — 정렬·모양을 한 곳에서. 타이핑 없이 누르기만.
 * 업무 탭: 내 파트 담당 > 우리 팀(다른 파트) 담당 > 다른 팀·담당 없음. 조직 탭: 팀 카드(내 팀 먼저) → 그 팀의 팀 공통 + 파트(내 파트 먼저).
 * note: 카드 설명 뒤에 붙일 한 마디(넣을 곳 고르기의 "승인 요청" 등). 테스트용 컬렉션은 안 보인다.
 */
export function CollectionPickerModal({ isOpen, onClose, title, value, onChoose, details, parts, userPart, note }: {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  value: string;
  onChoose: (name: string) => void;
  details: NamespaceDetail[];
  parts: Part[];
  userPart?: string | null;
  note?: (d: NamespaceDetail) => string | undefined;
}) {
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

  // 열 때: 지금 고른 게 조직 컬렉션이면 조직 탭의 그 팀으로
  useEffect(() => {
    if (!isOpen) return;
    const cur = details.find((d) => d.name === value);
    const org = isOrgCollection(cur);
    setTab(org ? 'org' : 'business');
    setTeamKey(org && cur ? String(cur.team_id ?? null) : null);
  }, [isOpen]);   // eslint-disable-line react-hooks/exhaustive-deps

  const choose = (name: string) => {
    onChoose(name);
    onClose();
  };
  const withNote = (d: NamespaceDetail, base: React.ReactNode) => {
    const n = note?.(d);
    return n ? <>{base} · <span className="text-amber-700 dark:text-amber-300">{n}</span></> : base;
  };

  const businessCard = (d: NamespaceDetail, t: Tier) => {
    const empty = (d.policy_count ?? 0) + d.knowledge_count === 0;
    const ownerName = ownerNames(effectiveOwnerParts(d));
    return (
      <TierCard key={d.name} tier={t} title={d.name} empty={empty} current={d.name === value} onClick={() => choose(d.name)}
        sub={withNote(d, <>{empty ? '아직 비어 있음' : `정책 ${d.policy_count ?? 0} · 지식 ${d.knowledge_count}`}{t === 'team' && ownerName && ` · ${ownerName}`}</>)} />
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
          sub={withNote(d, orgSub(d))} current={d.name === value} onClick={() => choose(d.name)} />
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
    <Modal isOpen={isOpen} onClose={onClose} title={title} maxWidth="max-w-2xl">
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
  );
}
