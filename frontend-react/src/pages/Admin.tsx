import { useState } from 'react';
import { clsx } from 'clsx';
import { Database, BookOpen, BarChart2, Search, Layers, Zap, Settings, Users } from 'lucide-react';
import { NamespaceManager } from '../components/admin/NamespaceManager';
import { KnowledgeTable } from '../components/admin/KnowledgeTable';
import { GlossaryTable } from '../components/admin/GlossaryTable';
import { StatsPanel } from '../components/admin/StatsPanel';
import { DebugPanel } from '../components/admin/DebugPanel';
import { FewshotTable } from '../components/admin/FewshotTable';
import { LLMSettings } from '../components/admin/LLMSettings';
import { UserManager } from '../components/admin/UserManager';
import { useAuthStore } from '../store/useAuthStore';

type TabId = 'namespaces' | 'knowledge' | 'glossary' | 'fewshots' | 'stats' | 'debug' | 'llm' | 'users';

interface Tab {
  id: TabId;
  label: string;
  icon: React.ReactNode;
  adminOnly?: boolean;
}

const TABS: Tab[] = [
  { id: 'namespaces', label: '파트 DB 관리', icon: <Layers className="w-4 h-4" /> },
  { id: 'knowledge', label: '지식 베이스', icon: <BookOpen className="w-4 h-4" /> },
  { id: 'glossary', label: '용어집', icon: <Database className="w-4 h-4" /> },
  { id: 'fewshots', label: 'Few-shot', icon: <Zap className="w-4 h-4" /> },
  { id: 'stats', label: '통계', icon: <BarChart2 className="w-4 h-4" /> },
  { id: 'debug', label: '파이프라인 디버그', icon: <Search className="w-4 h-4" /> },
  { id: 'llm', label: '시스템 설정', icon: <Settings className="w-4 h-4" /> },
  { id: 'users', label: '사용자 관리', icon: <Users className="w-4 h-4" />, adminOnly: true },
];

export default function Admin() {
  const [activeTab, setActiveTab] = useState<TabId>('namespaces');
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';

  const visibleTabs = TABS.filter((tab) => !tab.adminOnly || isAdmin);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Tab bar */}
      <div className="flex-shrink-0 border-b border-slate-700 bg-slate-800 px-6">
        <div className="flex gap-1 overflow-x-auto">
          {visibleTabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={clsx(
                'flex items-center gap-2 px-4 py-3.5 text-sm font-medium whitespace-nowrap border-b-2 transition-colors',
                activeTab === tab.id
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

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-6">
        {activeTab === 'namespaces' && <NamespaceManager onNavigate={setActiveTab} />}
        {activeTab === 'knowledge' && <KnowledgeTable />}
        {activeTab === 'glossary' && <GlossaryTable />}
        {activeTab === 'fewshots' && <FewshotTable />}
        {activeTab === 'stats' && <StatsPanel />}
        {activeTab === 'debug' && <DebugPanel onNavigate={setActiveTab} />}
        {activeTab === 'llm' && <LLMSettings />}
        {activeTab === 'users' && isAdmin && <UserManager />}
      </div>
    </div>
  );
}
