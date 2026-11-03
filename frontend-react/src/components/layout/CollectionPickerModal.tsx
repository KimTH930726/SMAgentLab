import { useEffect, useMemo, useState } from 'react';
import { clsx } from 'clsx';
import { Modal } from '../ui/Modal';
import type { NamespaceDetail, Part } from '../../types';
import { byContentThenName, isPickableDomain } from '../../utils/selectedDomain';
import { effectiveOwnerParts, ownerNames } from '../../utils/domainAccess';
import { isSystemCollection } from '../../utils/collections';
import { TierCard, TierSection, sortSystems, systemSub, type PendingTarget, type Tier } from './collectionCards';

type Tab = 'policy' | 'system';

/**
 * 컬렉션 고르는 창 — [업무 | 시스템] 탭(10/22, 컬렉션 설계 §15-11·§15-13 · 11/02 조직 → 시스템, taxonomy-standard). 보는 컬렉션 고르기(사이드바)와 지식 등록의 넣을 곳 고르기(10/23 사용자 A안)가
 * 같은 창을 쓴다 — 정렬·모양을 한 곳에서. 타이핑 없이 누르기만.
 * 정책 탭: 내 파트 담당 > 우리 팀(다른 파트) 담당 > 다른 팀·담당 없음. 시스템 탭: 한 줄 목록 — 내 파트 담당 > 내용 있는 것 > 가나다.
 * note: 카드 설명 뒤에 붙일 한 마디(넣을 곳 고르기의 "승인 요청" 등). 테스트용 컬렉션은 안 보인다.
 * onJump: 카드 아래 "정정 대기 n ›"·"원문 확인 n ›"를 누르면 그 화면으로(보는 컬렉션 고르기만 넘김, 권한은 canJump). 없으면 숫자만(넣을 곳 고르기).
 */
export function CollectionPickerModal({ isOpen, onClose, title, value, onChoose, details, parts, userPart, note, onJump, canJump, only }: {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  value: string;
  onChoose: (name: string) => void;
  details: NamespaceDetail[];
  parts: Part[];
  userPart?: string | null;
  note?: (d: NamespaceDetail) => string | undefined;
  onJump?: (name: string, to: PendingTarget) => void;
  canJump?: (d: NamespaceDetail) => boolean;
  /** 'system' = 시스템 탭만(운영 지식 넣을 곳 고르기 — 정책 컬렉션엔 넣지 않는다) */
  only?: 'system';
}) {
  const [tab, setTab] = useState<Tab>('system');   // 메인은 시스템(11/03) — 정책 컬렉션을 보고 있을 때만 정책 탭으로 연다
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
    const pickable = details.filter((d) => isPickableDomain(d) && !isSystemCollection(d));
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
  const systems = useMemo(() => sortSystems(details.filter(isSystemCollection), userPart), [details, userPart]);
  const isMineSystem = (d: NamespaceDetail) => !!userPart && effectiveOwnerParts(d).some((o) => o.name === userPart);

  // 열 때: 지금 고른 게 정책 컬렉션일 때만 정책 탭, 그 밖엔 시스템 탭
  useEffect(() => {
    if (!isOpen) return;
    const current = details.find((d) => d.name === value);
    setTab(only === 'system' || !current || isSystemCollection(current) ? 'system' : 'policy');
  }, [isOpen]);   // eslint-disable-line react-hooks/exhaustive-deps

  const choose = (name: string) => {
    onChoose(name);
    onClose();
  };
  const withNote = (d: NamespaceDetail, base: React.ReactNode) => {
    const n = note?.(d);
    return n ? <>{base} · <span className="text-amber-700 dark:text-amber-300">{n}</span></> : base;
  };

  const pending = (d: NamespaceDetail) => ({
    corrections: d.pending_corrections, sourceChecks: d.pending_source_checks,
    onJump: onJump && (canJump?.(d) ?? true) ? (to: PendingTarget) => { onClose(); onJump(d.name, to); } : undefined,
  });
  const policyCard = (d: NamespaceDetail, t: Tier) => {
    const empty = (d.policy_count ?? 0) + d.knowledge_count === 0;
    const ownerName = ownerNames(effectiveOwnerParts(d));
    return (
      <TierCard key={d.name} tier={t} title={d.name} empty={empty} current={d.name === value} onClick={() => choose(d.name)}
        testid={`collection-card-${d.name}`} pending={pending(d)}
        sub={withNote(d, <>{empty ? '아직 비어 있음' : `정책 ${d.policy_count ?? 0} · 지식 ${d.knowledge_count}`}{t === 'team' && ownerName && ` · ${ownerName}`}</>)} />
    );
  };
  const onlyOthers = groups.mine.length === 0 && groups.team.length === 0;

  const systemCard = (d: NamespaceDetail, t: Tier) => (
    <TierCard key={d.name} tier={t} title={d.display_name || d.name} empty={d.knowledge_count + d.glossary_count === 0}
      sub={withNote(d, systemSub(d))} current={d.name === value} onClick={() => choose(d.name)}
      testid={`collection-card-${d.name}`} pending={pending(d)} />
  );
  const systemPanel = () => {
    if (systems.length === 0) return <p className="text-sm text-slate-500 py-6 text-center">시스템 컬렉션이 아직 없어요.</p>;
    const mine = systems.filter(isMineSystem);
    const rest = systems.filter((d) => !isMineSystem(d));
    return (
      <>
        <TierSection tier="mine" title={`내 파트 담당 · ${userPart ?? ''}`} count={mine.length}>{mine.map((d) => systemCard(d, 'mine'))}</TierSection>
        <TierSection tier="other" title={mine.length ? '다른 시스템' : `시스템 컬렉션 ${rest.length}개`} count={rest.length}>{rest.map((d) => systemCard(d, 'other'))}</TierSection>
      </>
    );
  };

  const tabBtn = (id: Tab, label: string) => (
    <button key={id} type="button" role="tab" id={`picker-tab-${id}`} aria-selected={tab === id} aria-controls={`picker-panel-${id}`}
      onClick={() => setTab(id)}
      className={clsx('px-4 py-2 text-sm font-medium border-b-2 transition-colors',
        tab === id ? 'text-indigo-600 border-indigo-500 dark:text-indigo-400 dark:border-indigo-400' : 'text-slate-400 border-transparent hover:text-slate-200')}>
      {label}
    </button>
  );

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={title} maxWidth="max-w-2xl">
      <div className="space-y-4">
        {!only && (
          <div role="tablist" aria-label="컬렉션 종류" className="flex gap-1 border-b border-slate-700">
            {tabBtn('system', '시스템')}
            {tabBtn('policy', '정책')}
          </div>
        )}
        {tab === 'policy' ? (
          <div role="tabpanel" id="picker-panel-policy" aria-labelledby="picker-tab-policy" className="space-y-5">
            <TierSection tier="mine" title={`내 파트 담당 · ${userPart ?? ''}`} count={groups.mine.length}>{groups.mine.map((d) => policyCard(d, 'mine'))}</TierSection>
            <TierSection tier="team" title={`우리 팀 담당${myTeam ? ` · ${myTeam}` : ''}`} count={groups.team.length}>{groups.team.map((d) => policyCard(d, 'team'))}</TierSection>
            <TierSection tier="other" title={onlyOthers ? `정책 컬렉션 ${groups.other.length}개` : '다른 팀 · 담당 없음'} count={groups.other.length}>
              {groups.other.map((d) => policyCard(d, 'other'))}
            </TierSection>
          </div>
        ) : (
          <div role="tabpanel" id="picker-panel-system" aria-labelledby="picker-tab-system" className="space-y-5"
            title="IT 시스템별 지식 — 운영·장애 대응·연동 사양">
            {systemPanel()}
          </div>
        )}
      </div>
    </Modal>
  );
}
