import { useSearchParams } from 'react-router-dom';
import { Network, Users, Briefcase } from 'lucide-react';
import { UserSection, PartSection } from './UserManager';
import { PartDomainSection } from './PartDomainSection';

type SubTab = 'users' | 'teams' | 'domains';

/**
 * 플랫폼 관리 › 조직·권한(10/22, 사용자: "사용자·팀파트·담당 컬렉션을 하나로 묶어 서브탭으로 — 설정처럼").
 * 사용자 / 팀 › 파트(트리, 팀 추가·옮기기) / 담당 컬렉션(팀 › 파트 › 담당 컬렉션). 서브탭 모양은 설정(LLMSettings)과 같게.
 */
export function OrgAccessPanel() {
  // 탭 = 주소의 ?tab(11/03) — 플랫폼 관리 › 홈 [담당 없는 컬렉션] 카드가 담당 컬렉션 탭으로 바로 온다
  const [params, setParams] = useSearchParams();
  const want = params.get('tab');
  const subTab: SubTab = want === 'teams' || want === 'domains' ? want : 'users';
  const setSubTab = (t: SubTab) => setParams((p) => { const n = new URLSearchParams(p); if (t === 'users') n.delete('tab'); else n.set('tab', t); return n; }, { replace: true });
  const tab = (id: SubTab, label: string, Icon: typeof Users) => (
    <button
      type="button"
      role="tab"
      aria-selected={subTab === id}
      onClick={() => setSubTab(id)}
      className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
        subTab === id
          ? 'text-indigo-600 dark:text-indigo-400 border-indigo-500'
          : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600'
      }`}
    >
      <Icon className="w-3.5 h-3.5" aria-hidden="true" />
      {label}
    </button>
  );
  return (
    <div>
      <div role="tablist" aria-label="조직·권한" className="flex gap-1 mb-6 border-b border-slate-700">
        {tab('users', '사용자', Users)}
        {tab('teams', '팀·파트', Network)}
        {tab('domains', '담당 컬렉션', Briefcase)}
      </div>
      {subTab === 'users' && <UserSection />}
      {subTab === 'teams' && <PartSection />}
      {subTab === 'domains' && <PartDomainSection />}
    </div>
  );
}
