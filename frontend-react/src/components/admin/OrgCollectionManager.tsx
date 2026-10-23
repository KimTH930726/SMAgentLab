import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getParts } from '../../api/auth';
import { useNamespaceAccess } from '../../utils/useNamespaceAccess';
import { canEditDomain, effectiveOwnerParts } from '../../utils/domainAccess';
import { OrgCollectionSections, OrgTeamSections, TierCard, orgTeams, teamSub, type OrgTeam, type Tier } from '../layout/collectionCards';
import type { NamespaceDetail } from '../../types';
import { CollectionLinks } from './CollectionLinks';

/**
 * 컬렉션 › 조직 컬렉션(10/22, collection-design §3·§15-8·§15-9) — 고르는 창 조직 탭과 같은 팀 → (팀 공통 + 파트) 카드, 개수 보기용.
 * 조직 컬렉션은 팀·파트를 만들면 생기고 지우면 같이 지워진다(지식·용어가 있으면 거부) — 여기서 만들거나 이름을 바꾸지 않는다.
 * [지식 등록 →](쓸 수 있을 때) · [보기 →](항상) = 그 컬렉션을 고르고 지식 문서 › 등록 탭 / 목록으로(넣는 곳은 [지식 등록] 한 곳, §15-9).
 * 카드 본문을 누르면 보는 컬렉션만 바꾼다(업무 컬렉션 표의 줄과 같게, 10/23) — 지금 보는 카드는 "· 지금" + 테두리.
 */
export function OrgCollectionManager({ onRegister }: { onRegister: (tab: 'ingest' | 'list', into?: string) => void }) {
  const { nsDetails, selectedNs, setSelectedNs, user } = useNamespaceAccess();
  const { data: parts = [] } = useQuery({ queryKey: ['parts-public'], queryFn: getParts, staleTime: 300_000 });
  const userPart = user?.part ?? null;
  const teams = useMemo(() => orgTeams(nsDetails, parts, userPart), [nsDetails, parts, userPart]);
  const [teamKey, setTeamKey] = useState<string | null>(null);
  const openTeam = teams.find((t) => String(t.id) === teamKey) ?? null;

  const go = (d: NamespaceDetail, tab: 'ingest' | 'list') => {
    setSelectedNs(d.name);
    onRegister(tab, d.name);
  };

  if (teams.length === 0) {
    return <p className="text-sm text-slate-400 py-10 text-center">조직 컬렉션이 아직 없어요 — 조직·권한에서 팀·파트를 만들면 생겨요.</p>;
  }

  const teamCard = (t: OrgTeam) => (
    <TierCard key={String(t.id)} tier={t.tier} title={t.name} empty={t.content === 0} sub={teamSub(t)} onClick={() => setTeamKey(String(t.id))} />
  );

  const collectionCard = (d: NamespaceDetail, tier: Tier) => {
    const editable = canEditDomain(user, effectiveOwnerParts(d));
    return (
      <TierCard key={d.name} tier={tier} title={d.display_name ?? d.name} empty={d.knowledge_count + d.glossary_count === 0}
        sub={`지식 문서 ${d.knowledge_count} · 용어 ${d.glossary_count}`}
        // 카드를 누르면 보는 컬렉션을 이것으로 — 업무 컬렉션 표의 줄 누르기와 같게(10/23 사용자)
        current={d.name === selectedNs} onClick={() => setSelectedNs(d.name)}
        action={<CollectionLinks editable={editable} onGo={(tab) => go(d, tab)} />} />
    );
  };

  return (
    <div className="space-y-5" data-testid="org-collections">
      {openTeam ? (
        <OrgCollectionSections team={openTeam} userPart={userPart} onBack={() => setTeamKey(null)} card={collectionCard} />
      ) : (
        <>
          <p className="text-xs text-slate-500" title="팀 공통 = 그 팀 모든 파트가 쓰기, 파트 = 그 파트만. 조회는 누구나">팀을 누르면 팀 공통 · 파트 컬렉션이 보여요.</p>
          <OrgTeamSections teams={teams} card={teamCard} />
        </>
      )}
    </div>
  );
}
