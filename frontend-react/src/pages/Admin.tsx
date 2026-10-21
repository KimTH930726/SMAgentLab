import { Component, useEffect, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { NamespaceManager } from '../components/admin/NamespaceManager';
import { KnowledgeTable } from '../components/admin/KnowledgeTable';
import { GlossaryTable } from '../components/admin/GlossaryTable';
import { StatsPanel } from '../components/admin/StatsPanel';
import { EvaluationGate } from '../components/admin/EvaluationGate';
import { LLMSettings } from '../components/admin/LLMSettings';
import { UserSection, PartSection } from '../components/admin/UserManager';
import { PartDomainSection } from '../components/admin/PartDomainSection';
import { CachePanel } from '../components/admin/CachePanel';
import { VocEmailPanel } from '../components/admin/VocEmailPanel';
import { PolicyPanel } from '../components/admin/PolicyPanel';
import { CorrectionReviewTab } from '../components/admin/CorrectionReviewTab';
import { useAuthStore } from '../store/useAuthStore';
import { useNamespaceAccess } from '../utils/useNamespaceAccess';
import { AdminCrumb } from '../components/layout/AdminCrumb';
import { adminPath, resolveAdminMenu, visibleAdminMenu, type AdminMenuId } from './adminMenu';

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
          <p className="text-rose-600 dark:text-rose-400 font-medium text-sm">탭 렌더링 오류</p>
          <pre className="text-xs text-slate-500 max-w-lg whitespace-pre-wrap">{this.state.error.message}</pre>
          <button className="text-xs text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300" onClick={() => this.setState({ error: null })}>
            다시 시도
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

// 정정 요청(품질) — 지식·정책 공통이라 지식 탭에서 뺐다(10/21). 처리는 그 업무 영역 담당 파트 + 관리자
function CorrectionsPanel() {
  const { selectedNs, canModifyNs } = useNamespaceAccess();
  if (!canModifyNs) {
    return <p className="text-sm text-slate-400 py-6 text-center">이 업무 영역의 정정 요청은 담당 파트와 관리자만 처리할 수 있어요.</p>;
  }
  return <CorrectionReviewTab namespace={selectedNs} />;
}

function NotReady({ what }: { what: string }) {
  return <p className="text-sm text-slate-400 py-10 text-center">{what} 화면은 준비 중이에요.</p>;
}

export default function Admin() {
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';
  const groups = visibleAdminMenu(user);
  const current = resolveAdminMenu(location.pathname, groups);
  const go = (id: AdminMenuId) => navigate(adminPath(id));
  // 없는 메뉴·볼 수 없는 메뉴 주소로 들어오면 실제로 보여 주는 메뉴 주소로 바로잡는다(주소창과 화면이 어긋나지 않게)
  useEffect(() => {
    if (location.pathname !== adminPath(current)) navigate(adminPath(current), { replace: true });
  }, [location.pathname, current, navigate]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {user?.role === 'viewer' && (
        <div className="flex-shrink-0 px-6 py-2 text-xs bg-amber-50 text-amber-800 border-b border-amber-200 dark:bg-amber-900/30 dark:text-amber-200 dark:border-amber-700/40"
          title="사용자 관리에서 관리자가 승인하면 소속 파트가 담당하는 업무 영역의 관리 화면을 쓸 수 있습니다.">
          승인 대기 중이에요 — 관리자가 승인하면 관리 화면을 쓸 수 있어요. 채팅은 지금도 쓸 수 있어요.
        </div>
      )}
      <AdminCrumb current={current} />
      <div className="flex-1 overflow-y-auto p-6">
        <TabErrorBoundary key={current}>
          {current === 'domains'       && <NamespaceManager onNavigate={(to) => go(to === 'glossary' ? 'k.glossary' : to === 'policy' ? 'k.policy' : 'k.docs')} />}
          {current === 'k.policy'      && <PolicyPanel />}
          {current === 'k.docs'        && <KnowledgeTable />}
          {current === 'k.glossary'    && <GlossaryTable />}
          {current === 'k.system'      && <NotReady what="시스템 정보(DB 칼럼·공통코드)" />}
          {current === 'org.users'     && isAdmin && <UserSection />}
          {current === 'org.parts'     && isAdmin && <PartSection />}
          {current === 'org.domains'   && isAdmin && <PartDomainSection />}
          {current === 'q.stats'       && <StatsPanel onOpenCorrections={() => go('q.corrections')} />}
          {current === 'q.corrections' && <CorrectionsPanel />}
          {current === 'q.eval'        && <EvaluationGate />}
          {current === 'voc'           && <VocEmailPanel />}
          {current === 'sys.settings'  && <LLMSettings />}
          {current === 'sys.cache'     && isAdmin && <CachePanel />}
        </TabErrorBoundary>
      </div>
    </div>
  );
}
