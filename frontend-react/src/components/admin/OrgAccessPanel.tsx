import { useState } from 'react';
import { Network, Users, Briefcase } from 'lucide-react';
import { UserSection, PartSection } from './UserManager';
import { PartDomainSection } from './PartDomainSection';

type SubTab = 'users' | 'teams' | 'domains';

/**
 * 시스템·권한 › 조직·권한(10/22, 사용자: "사용자·팀파트·담당 업무를 하나로 묶어 서브탭으로 — 설정처럼").
 * 사용자 / 팀 › 파트(트리, 팀 추가·옮기기) / 담당 업무(팀 › 파트 › 담당 업무). 서브탭 모양은 설정(LLMSettings)과 같게.
 */
export function OrgAccessPanel() {
  const [subTab, setSubTab] = useState<SubTab>('users');
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
        {tab('domains', '담당 업무', Briefcase)}
      </div>
      {subTab === 'users' && <UserSection />}
      {subTab === 'teams' && <PartSection />}
      {subTab === 'domains' && <PartDomainSection />}
    </div>
  );
}
