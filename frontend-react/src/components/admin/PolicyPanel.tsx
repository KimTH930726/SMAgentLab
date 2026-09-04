import { useState } from 'react';
import { clsx } from 'clsx';
import { List, FileWarning, FlaskConical } from 'lucide-react';
import { PolicyItemBrowser } from './PolicyItemBrowser';
import { PolicyUnresolvedReport } from './PolicyUnresolvedReport';
import { PolicyLab } from './PolicyLab';

/**
 * 정책서 관련 화면 3개를 하나의 "정책" 대분류 탭 아래 서브탭으로 묶는다(2026-09-04) —
 * VocEmailPanel.tsx의 서브탭 패턴과 동일. 이전엔 Admin.tsx에 3개가 평평하게 나열돼 있었다.
 */
type PolicySubTab = 'items' | 'unresolved' | 'lab';

const SUB_TABS: { id: PolicySubTab; label: string; icon: React.ReactNode }[] = [
  { id: 'items', label: '항목 브라우저', icon: <List className="w-4 h-4" /> },
  { id: 'unresolved', label: '미분류', icon: <FileWarning className="w-4 h-4" /> },
  { id: 'lab', label: '저장소 실험실', icon: <FlaskConical className="w-4 h-4" /> },
];

export function PolicyPanel() {
  const [subTab, setSubTab] = useState<PolicySubTab>('items');

  return (
    <div className="space-y-4">
      <div className="border-b border-slate-700">
        <div className="flex gap-1">
          {SUB_TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setSubTab(tab.id)}
              className={clsx(
                'flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors',
                subTab === tab.id
                  ? 'text-indigo-400 border-indigo-500'
                  : 'text-slate-400 border-transparent hover:text-slate-200 hover:border-slate-600',
              )}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {subTab === 'items' && <PolicyItemBrowser />}
      {subTab === 'unresolved' && <PolicyUnresolvedReport />}
      {subTab === 'lab' && <PolicyLab />}
    </div>
  );
}
