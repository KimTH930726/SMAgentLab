import { useState, Component, type ReactNode } from 'react';
import { clsx } from 'clsx';
import { Database, BookOpen, BarChart2, Search, Layers, Zap, Settings, Users, Wrench, GitMerge, Network, BookMarked, ListOrdered, Workflow, FileText } from 'lucide-react';
import { NamespaceManager } from '../components/admin/NamespaceManager';
import { KnowledgeTable } from '../components/admin/KnowledgeTable';
import { GlossaryTable } from '../components/admin/GlossaryTable';
import { StatsPanel } from '../components/admin/StatsPanel';
import { DebugPanel } from '../components/admin/DebugPanel';
import { FewshotTable } from '../components/admin/FewshotTable';
import { LLMSettings } from '../components/admin/LLMSettings';
import { UserManager } from '../components/admin/UserManager';
import { McpToolManager } from '../components/admin/McpToolManager';
import { CachePanel } from '../components/admin/CachePanel';
import {
  SqlTargetDbTab,
  SqlSchemaTab,
  SqlErdTab,
  SqlSynonymTab,
  SqlFewshotTab,
  SqlPipelineTab,
  SqlAuditLogTab,
} from '../components/admin/Text2SqlAdmin';
import { useAuthStore } from '../store/useAuthStore';
import { useAppStore } from '../store/useAppStore';

// ── ErrorBoundary ─────────────────────────────────────────────────────────────

class TabErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
          <p className="text-rose-400 font-medium text-sm">탭 렌더링 오류</p>
          <pre className="text-xs text-slate-500 max-w-lg whitespace-pre-wrap">{this.state.error.message}</pre>
          <button className="text-xs text-indigo-400 hover:text-indigo-300" onClick={() => this.setState({ error: null })}>
            다시 시도
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

type TabId =
  // knowledge_rag
  | 'namespaces' | 'knowledge' | 'glossary' | 'fewshots' | 'mcp_tools' | 'debug'
  // text2sql
  | 'sql_db' | 'sql_schema' | 'sql_erd' | 'sql_synonyms' | 'sql_fewshots' | 'sql_pipeline' | 'sql_audit'
  // common
  | 'cache' | 'stats' | 'llm' | 'users';

interface Tab {
  id: TabId;
  label: string;
  icon: React.ReactNode;
  adminOnly?: boolean;
  agentScope?: 'knowledge_rag' | 'text2sql' | 'all';
}

const TABS: Tab[] = [
  // knowledge_rag 전용 탭
  { id: 'namespaces',   label: '기준 정보 관리',    icon: <Layers className="w-4 h-4" />,     agentScope: 'knowledge_rag' },
  { id: 'knowledge',    label: '지식 베이스',        icon: <BookOpen className="w-4 h-4" />,   agentScope: 'knowledge_rag' },
  { id: 'glossary',     label: '용어집',              icon: <Database className="w-4 h-4" />,   agentScope: 'knowledge_rag' },
  { id: 'fewshots',     label: 'Few-shot',            icon: <Zap className="w-4 h-4" />,        agentScope: 'knowledge_rag' },
  { id: 'debug',        label: '파이프라인 디버그',    icon: <Search className="w-4 h-4" />,     agentScope: 'knowledge_rag' },
  // text2sql 전용 탭
  { id: 'sql_db',       label: '대상 DB',             icon: <Database className="w-4 h-4" />,   agentScope: 'text2sql' },
  { id: 'sql_schema',   label: '스키마',               icon: <BookMarked className="w-4 h-4" />, agentScope: 'text2sql' },
  { id: 'sql_erd',      label: 'ERD',                  icon: <Network className="w-4 h-4" />,    agentScope: 'text2sql' },
  { id: 'sql_synonyms', label: '용어 사전',             icon: <GitMerge className="w-4 h-4" />,  agentScope: 'text2sql' },
  { id: 'sql_fewshots', label: 'SQL Few-shot',           icon: <ListOrdered className="w-4 h-4" />, agentScope: 'text2sql' },
  { id: 'mcp_tools',    label: 'MCP 도구',            icon: <Wrench className="w-4 h-4" />,     agentScope: 'all' },
  { id: 'sql_pipeline', label: '파이프라인',             icon: <Workflow className="w-4 h-4" />,  agentScope: 'text2sql' },
  { id: 'sql_audit',    label: '감사 로그',              icon: <FileText className="w-4 h-4" />,  agentScope: 'text2sql' },
  // 공통 탭 (knowledge_rag 전용으로 이동)
  { id: 'cache',        label: '캐시 현황',             icon: <BarChart2 className="w-4 h-4" />, adminOnly: true, agentScope: 'knowledge_rag' },
  { id: 'stats',        label: '통계',                  icon: <BarChart2 className="w-4 h-4" />, agentScope: 'knowledge_rag' },
  { id: 'llm',          label: '시스템 설정',            icon: <Settings className="w-4 h-4" />, agentScope: 'all' },
  { id: 'users',        label: '사용자 관리',            icon: <Users className="w-4 h-4" />,    adminOnly: true, agentScope: 'all' },
];

export default function Admin() {
  const user = useAuthStore((s) => s.user);
  const selectedAgent = useAppStore((s) => s.selectedAgent);
  const isAdmin = user?.role === 'admin';

  const visibleTabs = TABS.filter((tab) => {
    if (tab.adminOnly && !isAdmin) return false;
    if (!tab.agentScope || tab.agentScope === 'all') return true;
    return tab.agentScope === selectedAgent;
  });

  const defaultTab = visibleTabs[0]?.id ?? 'llm';
  const [activeTab, setActiveTab] = useState<TabId>(defaultTab);

  const firstVisibleId = visibleTabs[0]?.id;
  const isCurrentVisible = visibleTabs.some((t) => t.id === activeTab);
  const resolvedTab = isCurrentVisible ? activeTab : (firstVisibleId ?? 'llm');

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
                resolvedTab === tab.id
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
        <TabErrorBoundary key={resolvedTab}>
          {resolvedTab === 'namespaces'   && <NamespaceManager onNavigate={(id) => setActiveTab(id as TabId)} />}
          {resolvedTab === 'knowledge'    && <KnowledgeTable />}
          {resolvedTab === 'glossary'     && <GlossaryTable />}
          {resolvedTab === 'fewshots'     && <FewshotTable />}
          {resolvedTab === 'mcp_tools'    && <McpToolManager />}
          {resolvedTab === 'debug'        && <DebugPanel onNavigate={(id) => setActiveTab(id as TabId)} />}
          {resolvedTab === 'sql_db'       && <SqlTargetDbTab />}
          {resolvedTab === 'sql_schema'   && <SqlSchemaTab />}
          {resolvedTab === 'sql_erd'      && <SqlErdTab />}
          {resolvedTab === 'sql_synonyms' && <SqlSynonymTab />}
          {resolvedTab === 'sql_fewshots' && <SqlFewshotTab />}
          {resolvedTab === 'sql_pipeline' && <SqlPipelineTab />}
          {resolvedTab === 'sql_audit'    && <SqlAuditLogTab />}
          {resolvedTab === 'cache'        && isAdmin && <CachePanel />}
          {resolvedTab === 'stats'        && <StatsPanel />}
          {resolvedTab === 'llm'          && <LLMSettings />}
          {resolvedTab === 'users'        && isAdmin && <UserManager />}
        </TabErrorBoundary>
      </div>
    </div>
  );
}
