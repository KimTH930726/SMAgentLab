import { Component, useEffect, type ReactNode } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { NamespaceManager } from '../components/admin/NamespaceManager';
import { SystemCollectionManager } from '../components/admin/SystemCollectionManager';
import { KnowledgeTable } from '../components/admin/KnowledgeTable';
import { DataSources } from '../components/admin/TeamImportPanel';
import { GlossaryTable } from '../components/admin/GlossaryTable';
import { StatsPanel } from '../components/admin/StatsPanel';
import { EvaluationGate } from '../components/admin/EvaluationGate';
import { AiDecisions } from '../components/admin/AiDecisions';
import { LLMSettings } from '../components/admin/LLMSettings';
import { OrgAccessPanel } from '../components/admin/OrgAccessPanel';
import { VocEmailPanel } from '../components/admin/VocEmailPanel';
import { PolicyPanel } from '../components/admin/policy/PolicyPanel';
import { CorrectionReviewTab } from '../components/admin/CorrectionReviewTab';
import { UserFeedbackPanel } from '../components/feedback/UserFeedback';
import { useAuthStore } from '../store/useAuthStore';
import { useNamespaceAccess } from '../utils/useNamespaceAccess';
import { AdminCrumb } from '../components/layout/AdminCrumb';
import { adminPath, aliasTab, blockedPlatform, resolveAdminMenu, type AdminMenuId } from './adminMenu';
import { CollectionHome, CollectionInfo, PlatformHome } from '../components/admin/AdminHome';
import { showAlert } from '../store/useDialogStore';
import { useAdminMenu } from '../utils/useAdminMenu';

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

// 정정 요청(품질) — 지식·정책 공통이라 지식 탭에서 뺐다(10/21). 처리는 그 정책 컬렉션 담당 파트 + 관리자
// ?focus=N — 정책서 화면의 "정정 #N 검토 중 → 정정 요청에서 보기"가 그 카드로 바로 오게(10/21)
function CorrectionsPanel() {
  const { selectedNs, canModifyNs } = useNamespaceAccess();
  const [params] = useSearchParams();
  const focus = Number(params.get('focus')) || null;
  if (!canModifyNs) {
    return <p className="text-sm text-slate-400 py-6 text-center">이 정책 컬렉션의 정정 요청은 담당 파트와 관리자만 처리할 수 있어요.</p>;
  }
  return <CorrectionReviewTab namespace={selectedNs} focusId={focus} />;
}


export default function Admin() {
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === 'admin';
  // 보는 컬렉션이 시스템이면 정책서·VOC는 메뉴에서 빠지고(담을 수 없음), 그 주소로 오면 아래 바로잡기가 운영 지식으로 옮긴다(10/23, 11/02)
  const { groups, kindKnown } = useAdminMenu();
  const current = resolveAdminMenu(location.pathname, groups);
  const go = (id: AdminMenuId) => navigate(adminPath(id));
  // 컬렉션 현황 화면(업무·시스템)의 [지식 등록 →]·[보기 →] — 컬렉션은 링크가 먼저 골라 둔다.
  // 등록은 넣을 곳을 그 컬렉션으로 정해 둔다(?into= — AI는 검사만, E2 collection-design §15-6)
  const toDocs = (tab: 'ingest' | 'list', into?: string) =>
    navigate(tab === 'ingest' ? `${adminPath('k.docs')}?tab=ingest${into ? `&into=${encodeURIComponent(into)}` : ''}` : adminPath('k.docs'));
  // 없는 메뉴·볼 수 없는 메뉴 주소로 들어오면 실제로 보여 주는 메뉴 주소로 바로잡는다(주소창과 화면이 어긋나지 않게)
  // 옛 캐시 주소(/admin/sys.cache)는 설정 › 캐시 탭으로(11/03 캐시를 설정 안으로)
  useEffect(() => {
    if (!kindKnown) return;   // 컬렉션 종류를 확인하기 전엔 그대로(시스템에만 있는 메뉴 주소가 홈으로 튕기지 않게 — 리뷰 M1)
    const tab = aliasTab(location.pathname);
    // 관리자 전용(플랫폼 관리) 화면 주소로 온 비관리자 — 조용히 바꾸지 않고 알린 뒤 컬렉션 관리 홈으로(리뷰 11/03)
    if (blockedPlatform(location.pathname, groups)) void showAlert('플랫폼 관리(관리자 전용) 화면이에요. 볼 수 있는 화면으로 이동합니다.', { title: '접근 권한 없음' });   // 조회 전용은 홈이 없어 설정으로 간다(재리뷰 L2)
    if (location.pathname !== adminPath(current)) navigate(tab ? `${adminPath(current)}?tab=${tab}` : adminPath(current), { replace: true });
  }, [location.pathname, current, navigate, groups, kindKnown]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {user?.role === 'viewer' && (
        <div className="flex-shrink-0 px-6 py-2 text-xs bg-amber-50 text-amber-800 border-b border-amber-200 dark:bg-amber-900/30 dark:text-amber-200 dark:border-amber-700/40"
          title="사용자 관리에서 관리자가 승인하면 소속 파트가 담당하는 정책 컬렉션의 관리 화면을 쓸 수 있습니다.">
          승인 대기 중이에요 — 관리자가 승인하면 관리 화면을 쓸 수 있어요. 채팅은 지금도 쓸 수 있어요.
        </div>
      )}
      <AdminCrumb current={current} />
      <div className="flex-1 overflow-y-auto p-6">
        <TabErrorBoundary key={current}>
          {current === 'home'          && <CollectionHome onGo={(to) => navigate(to)} />}
          {current === 'p.home'        && isAdmin && <PlatformHome onGo={(to) => navigate(to)} />}
          {current === 'c.info'        && <CollectionInfo onGo={(to) => navigate(to)} />}
          {current === 'domains'       && <NamespaceManager onNavigate={(to) => go(to === 'glossary' ? 'k.glossary' : to === 'policy' ? 'k.policy' : 'k.docs')} onRegister={toDocs} />}
          {current === 'systems'       && <SystemCollectionManager onNavigate={(to) => go(to === 'glossary' ? 'k.glossary' : 'k.docs')} onRegister={toDocs} />}
          {current === 'k.policy'      && <PolicyPanel onOpenCorrection={(id) => navigate(`${adminPath('q.corrections')}?focus=${id}`)} onOpenCorrections={() => go('q.corrections')} />}
          {current === 'k.docs'        && <KnowledgeTable />}
          {current === 'k.sources'     && <DataSources />}
          {current === 'k.glossary'    && <GlossaryTable />}
          {current === 'org'           && isAdmin && <OrgAccessPanel />}
          {current === 'q.stats'       && <StatsPanel onOpenCorrections={() => go('q.corrections')} />}
          {current === 'q.corrections' && <CorrectionsPanel />}
          {current === 'q.feedback'    && <UserFeedbackPanel />}
          {current === 'q.eval'        && <EvaluationGate />}
          {current === 'q.decisions'   && isAdmin && <AiDecisions />}
          {current === 'voc'           && <VocEmailPanel />}
          {current === 'sys.settings'  && <LLMSettings />}
        </TabErrorBoundary>
      </div>
    </div>
  );
}
