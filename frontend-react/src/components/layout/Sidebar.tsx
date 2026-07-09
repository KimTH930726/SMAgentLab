import { useEffect, useRef, useState, useCallback } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import {
  MessageSquare,
  Settings,
  Plus,
  Trash2,
  ChevronDown,
  ChevronUp,
  Activity,
  LogOut,
  User,
  Shield,
  Cog,
  Eye,
  EyeOff,
  Key,
  Lock,
  Cpu,
  Sun,
  Moon,
  BookOpen,
  Database,
  ArrowLeftRight,
} from 'lucide-react';
import { clsx } from 'clsx';
import { useQuery } from '@tanstack/react-query';
import { useAppStore } from '../../store/useAppStore';
import { useAuthStore } from '../../store/useAuthStore';
import { stopChatStream, clearStreamState, useStreamStore } from '../../store/useStreamStore';
import { getNamespaces, getNamespacesDetail } from '../../api/namespaces';
import { sortNamespacesByUserPart } from '../../utils/sortNamespaces';
import { getConversations, deleteConversation } from '../../api/conversations';
import { healthCheck } from '../../api/client';
import { changePassword, updateLLMCredentials, deleteLLMCredentials, updateConfluencePAT, deleteConfluencePAT } from '../../api/auth';
import { getLLMConfig } from '../../api/llm';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { useThemeStore } from '../../store/useThemeStore';
import logoSvg from '../../assets/logo.svg';

export function Sidebar() {
  const location = useLocation();
  const isChatPage = location.pathname === '/';

  const { namespace, setNamespace, conversationId, setConversationId, conversations, setConversations } = useAppStore();
  const selectedAgent = useAppStore((s) => s.selectedAgent);
  const setSelectedAgent = useAppStore((s) => s.setSelectedAgent);
  const chatRefreshKey = useAppStore((s) => s.chatRefreshKey);
  const searchConfig = useAppStore((s) => s.searchConfig);
  const setSearchConfig = useAppStore((s) => s.setSearchConfig);
  const user = useAuthStore((s) => s.user);
  const { data: namespaces = [] } = useQuery({
    queryKey: ['namespaces'],
    queryFn: getNamespaces,
    staleTime: 30_000,
  });
  const { data: nsDetails = [] } = useQuery({
    queryKey: ['namespaces-detail'],
    queryFn: getNamespacesDetail,
    staleTime: 30_000,
  });
  const sortedNamespaces = sortNamespacesByUserPart(namespaces, user?.part, nsDetails);
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const [showSearchConfig, setShowSearchConfig] = useState(false);
  const [loadingConvs, setLoadingConvs] = useState(false);

  // LLM config — display current model
  const { data: llmConfig } = useQuery({
    queryKey: ['llm-config'],
    queryFn: getLLMConfig,
    staleTime: 30_000,
    refetchOnMount: 'always',
  });
  const currentModel = llmConfig
    ? llmConfig.provider === 'ollama'
      ? llmConfig.ollama.model
      : llmConfig.inhouse.model || 'Agent 기본'
    : null;

  // Health check on mount and every 30s
  useEffect(() => {
    const check = async () => {
      const ok = await healthCheck();
      setBackendOk(ok);
    };
    check();
    const interval = setInterval(check, 30_000);
    return () => clearInterval(interval);
  }, []);

  // 초기 또는 사용자 변경 시 내 파트 소유 namespace 자동 선택
  // localStorage에 이전 세션의 namespace가 남아있어도 올바른 파트 namespace로 교체
  const lastHandledUserPartRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (sortedNamespaces.length === 0) return;

    const currentPart = user?.part ?? null;

    // namespace 미설정 → 정렬된 첫 번째 선택
    if (!namespace) {
      setNamespace(sortedNamespaces[0]);
      return;
    }

    // nsDetails 미로드 → 대기
    if (nsDetails.length === 0) return;

    // 이미 이 파트 처리 완료 → 사용자가 수동 선택한 거 존중
    if (lastHandledUserPartRef.current === currentPart) return;
    lastHandledUserPartRef.current = currentPart;

    // 내 파트 소유 namespace 목록 확인
    if (currentPart) {
      const myNsNames = new Set(nsDetails.filter((n) => n.owner_part === currentPart).map((n) => n.name));
      // 현재 선택된 namespace가 내 파트 소유가 아니고, 내 파트 소유 namespace가 존재하면 → 교체
      if (myNsNames.size > 0 && !myNsNames.has(namespace)) {
        setNamespace(sortedNamespaces[0]);
      }
    }
  }, [namespace, sortedNamespaces, nsDetails, user?.part, setNamespace]);

  // Load conversations when namespace changes (only on chat page)
  const refreshConversations = useCallback(async () => {
    if (!namespace || !isChatPage) return;
    setLoadingConvs(true);
    try {
      const data = await getConversations(namespace, selectedAgent ?? 'knowledge_rag');
      setConversations(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingConvs(false);
    }
  }, [namespace, isChatPage, selectedAgent, setConversations]);

  // Refresh on namespace or page change
  useEffect(() => {
    refreshConversations();
  }, [refreshConversations]);

  // Also refresh when conversationId changes (new conv created, or user navigated)
  // Skip refresh during streaming — conversation should appear only after answer completes
  const streamActive = useStreamStore((s) => s.active);
  const prevConvIdRef = useRef(conversationId);
  useEffect(() => {
    if (prevConvIdRef.current !== conversationId) {
      prevConvIdRef.current = conversationId;
      if (!streamActive) {
        refreshConversations();
      }
    }
  }, [conversationId, refreshConversations, streamActive]);

  // Admin → Chat 전환 시 bumpChatRefresh 신호로도 대화목록 갱신
  const prevChatRefreshKeyRef = useRef(chatRefreshKey);
  useEffect(() => {
    if (prevChatRefreshKeyRef.current === chatRefreshKey) return;
    prevChatRefreshKeyRef.current = chatRefreshKey;
    refreshConversations();
  }, [chatRefreshKey, refreshConversations]);

  // Refresh conversation list when stream finishes (new conversation now has answer)
  const prevStreamActiveRef = useRef(streamActive);
  useEffect(() => {
    if (prevStreamActiveRef.current && !streamActive) {
      refreshConversations();
    }
    prevStreamActiveRef.current = streamActive;
  }, [streamActive, refreshConversations]);

  const handleDeleteConversation = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    try {
      await deleteConversation(id);
      setConversations(conversations.filter((c) => c.id !== id));
      if (conversationId === id) setConversationId(null);
    } catch (err) {
      console.error(err);
    }
  };

  const handleNewChat = () => {
    if (useStreamStore.getState().active) {
      stopChatStream();
    }
    clearStreamState();
    setConversationId(null);
  };

  return (
    <aside className="w-64 flex-shrink-0 bg-slate-800 border-r border-slate-700 flex flex-col h-full">
      {/* Logo */}
      <div className="px-5 py-4 border-b border-slate-700 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <img src={logoSvg} alt="logo" className="w-7 h-7" />
          <span className="font-semibold text-slate-100 text-sm">Ops-Navigator</span>
        </div>
        {/* Backend health indicator */}
        {backendOk === null && (
          <div className="p-1 rounded-lg cursor-default" title="백엔드 상태 확인 중...">
            <Activity className="w-4 h-4 text-slate-500 animate-pulse" />
          </div>
        )}
        {backendOk === true && (
          <div className="p-1.5 rounded-lg hover:bg-slate-700/50 cursor-default transition-colors" title="백엔드 서버 정상 연결됨">
            <span className="block w-2 h-2 rounded-full bg-emerald-500" />
          </div>
        )}
        {backendOk === false && (
          <div className="p-1.5 rounded-lg hover:bg-slate-700/50 cursor-default transition-colors" title="백엔드 서버 연결 실패 — 서버가 중지되었거나 네트워크 문제">
            <span className="block w-2 h-2 rounded-full bg-rose-500" />
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav className="px-3 py-3 border-b border-slate-700 flex gap-1">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            clsx(
              'flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
              isActive
                ? 'bg-indigo-600 text-white'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700',
            )
          }
        >
          <MessageSquare className="w-4 h-4" />
          Chat
        </NavLink>
        <NavLink
          to="/admin"
          className={({ isActive }) =>
            clsx(
              'flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors',
              isActive
                ? 'bg-indigo-600 text-white'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700',
            )
          }
        >
          <Settings className="w-4 h-4" />
          Admin
        </NavLink>
      </nav>

      {/* Active agent indicator + change button */}
      {selectedAgent && (
        <div className="px-3 py-2 border-b border-slate-700 flex items-center gap-2">
          <div className={`flex items-center gap-1.5 flex-1 min-w-0 px-2 py-1 rounded-lg text-xs font-medium ${
            selectedAgent === 'knowledge_rag'
              ? 'bg-indigo-500/10 text-indigo-400'
              : 'bg-emerald-500/10 text-emerald-400'
          }`}>
            {selectedAgent === 'knowledge_rag'
              ? <BookOpen className="w-3 h-3 flex-shrink-0" />
              : <Database className="w-3 h-3 flex-shrink-0" />}
            <span className="truncate">
              {selectedAgent === 'knowledge_rag' ? '지식베이스 AI' : 'Text-to-SQL'}
            </span>
          </div>
          <button
            onClick={() => setSelectedAgent(null)}
            className="p-1.5 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-slate-700 transition-colors flex-shrink-0"
            title="에이전트 변경"
          >
            <ArrowLeftRight className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Current LLM model indicator */}
      {currentModel && (
        <div className="px-4 py-1.5 border-b border-slate-700 flex items-center gap-1.5">
          <Cpu className="w-3 h-3 text-slate-500 flex-shrink-0" />
          <span className="text-[11px] text-slate-500 truncate" title={currentModel}>
            <span className="text-slate-500">LLM Model :</span> {currentModel}
          </span>
        </div>
      )}

      {/* Namespace + Category selector — Chat only */}
      {isChatPage && (
        <div className="px-3 py-3 border-b border-slate-700 space-y-2">
          <div>
            <label className="text-xs font-medium text-slate-500 uppercase tracking-wider block mb-1.5">
              파트
            </label>
            {namespaces.length === 0 ? (
              <div className="text-xs text-slate-500 px-1">파트 없음</div>
            ) : (
              <select
                value={namespace}
                onChange={(e) => setNamespace(e.target.value)}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-500 cursor-pointer"
              >
                {sortedNamespaces.map((ns) => (
                  <option key={ns} value={ns}>{ns}</option>
                ))}
              </select>
            )}
          </div>
        </div>
      )}

      {/* Chat-only section */}
      {isChatPage && (
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* New chat button */}
          <div className="px-3 py-2 border-b border-slate-700">
            <button
              onClick={handleNewChat}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm font-medium rounded-lg transition-colors"
            >
              <Plus className="w-4 h-4" />
              새 대화
            </button>
          </div>

          {/* Conversation list */}
          <div className="flex-1 overflow-y-auto px-2 py-2">
            {loadingConvs && (
              <div className="text-xs text-slate-500 text-center py-4 animate-pulse">
                대화 목록 로딩 중...
              </div>
            )}
            {!loadingConvs && conversations.length === 0 && (
              <div className="text-xs text-slate-500 text-center py-4">
                대화 없음
              </div>
            )}
            {!loadingConvs &&
              conversations.map((conv) => (
                <div
                  key={conv.id}
                  onClick={() => setConversationId(conv.id)}
                  className={clsx(
                    'group flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer transition-colors mb-0.5',
                    conversationId === conv.id
                      ? 'bg-indigo-600/20 border border-indigo-600/40'
                      : 'hover:bg-slate-700',
                  )}
                >
                  <MessageSquare className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
                  <span className="flex-1 text-xs text-slate-300 truncate">{conv.title}</span>
                  <button
                    onClick={(e) => handleDeleteConversation(e, conv.id)}
                    className="opacity-0 group-hover:opacity-100 p-1 rounded text-slate-500 hover:text-rose-400 transition-all"
                    title="대화 삭제"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              ))}
          </div>

          {/* Search config collapsible */}
          <div className="border-t border-slate-700">
            <button
              onClick={() => setShowSearchConfig((p) => !p)}
              className="w-full flex items-center justify-between px-4 py-2.5 text-xs font-medium text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 transition-colors"
            >
              <span className="flex items-center gap-1.5">
                <Settings className="w-3.5 h-3.5" />
                검색 설정
              </span>
              {showSearchConfig ? (
                <ChevronUp className="w-3.5 h-3.5" />
              ) : (
                <ChevronDown className="w-3.5 h-3.5" />
              )}
            </button>
            {showSearchConfig && (
              <div className="px-4 pb-3 space-y-3 bg-slate-900/50">
                <div>
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>의미 중심</span>
                    <span className="text-indigo-400 font-mono">{searchConfig.wVector.toFixed(1)}</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.1}
                    value={searchConfig.wVector}
                    onChange={(e) => {
                      const v = parseFloat(e.target.value);
                      setSearchConfig({ wVector: v, wKeyword: parseFloat((1 - v).toFixed(1)) });
                    }}
                    className="w-full accent-indigo-500"
                  />
                </div>
                <div>
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>키워드 중심</span>
                    <span className="text-indigo-400 font-mono">{searchConfig.wKeyword.toFixed(1)}</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.1}
                    value={searchConfig.wKeyword}
                    onChange={(e) => {
                      const v = parseFloat(e.target.value);
                      setSearchConfig({ wKeyword: v, wVector: parseFloat((1 - v).toFixed(1)) });
                    }}
                    className="w-full accent-indigo-500"
                  />
                </div>
                <div>
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>검색 결과 수 (Top-K)</span>
                    <span className="text-indigo-400 font-mono">{searchConfig.topK}</span>
                  </div>
                  <input
                    type="range"
                    min={1}
                    max={10}
                    step={1}
                    value={searchConfig.topK}
                    onChange={(e) => setSearchConfig({ topK: parseInt(e.target.value, 10) })}
                    className="w-full accent-indigo-500"
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* If admin page, show nothing extra */}
      {!isChatPage && <div className="flex-1" />}

      {/* Theme toggle */}
      <ThemeToggle />

      {/* User info + Logout */}
      <UserSection />
    </aside>
  );
}

function ThemeToggle() {
  const { theme, toggleTheme } = useThemeStore();
  return (
    <div className="border-t border-slate-700/50 px-3 py-2">
      <button
        onClick={toggleTheme}
        className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 transition-colors"
        title={theme === 'dark' ? '라이트 모드로 전환' : '다크 모드로 전환'}
      >
        {theme === 'dark' ? (
          <Sun className="w-3.5 h-3.5" />
        ) : (
          <Moon className="w-3.5 h-3.5" />
        )}
        {theme === 'dark' ? '라이트 모드' : '다크 모드'}
      </button>
    </div>
  );
}

function UserSection() {
  const user = useAuthStore((s) => s.user);
  const updateUser = useAuthStore((s) => s.updateUser);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();
  const [showSettings, setShowSettings] = useState(false);

  const { setSelectedAgent } = useAppStore();

  const handleLogout = () => {
    setSelectedAgent(null);
    logout();
    navigate('/login', { replace: true });
  };

  if (!user) return null;

  return (
    <>
      <div className="border-t border-slate-700/50 px-3 py-3">
        <div className="flex items-center gap-2 px-2">
          <div className="w-7 h-7 rounded-full bg-slate-700 flex items-center justify-center flex-shrink-0">
            {user.role === 'admin' ? (
              <Shield className="w-3.5 h-3.5 text-indigo-400" />
            ) : (
              <User className="w-3.5 h-3.5 text-slate-400" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-slate-200 truncate">{user.username}</p>
            <p className="text-[10px] text-slate-500 truncate">{user.part}</p>
          </div>
          <button
            onClick={() => setShowSettings(true)}
            className="p-1.5 rounded-lg text-slate-500 hover:text-indigo-400 hover:bg-slate-700 transition-colors"
            title="계정 설정"
          >
            <Cog className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={handleLogout}
            className="p-1.5 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-slate-700 transition-colors"
            title="로그아웃"
          >
            <LogOut className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      <AccountSettingsModal
        isOpen={showSettings}
        onClose={() => setShowSettings(false)}
        user={user}
        onUserUpdate={(partial) => updateUser({ ...user, ...partial })}
      />
    </>
  );
}

// ── 계정 설정 모달 ─────────────────────────────────────────────────────────

interface AccountSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  user: { username: string; part: string; role: string; has_llm_credentials: boolean; has_confluence_pat?: boolean };
  onUserUpdate: (u: Partial<{ has_llm_credentials: boolean; has_confluence_pat: boolean }>) => void;
}

function AccountSettingsModal({ isOpen, onClose, user, onUserUpdate }: AccountSettingsModalProps) {
  // Password change
  const [currentPw, setCurrentPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [pwLoading, setPwLoading] = useState(false);
  const [pwMsg, setPwMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null);

  // LLM 자격증명 트리플 (DevX OAuth2)
  const [credClientId, setCredClientId] = useState('');
  const [credClientSecret, setCredClientSecret] = useState('');
  const [credUserId, setCredUserId] = useState('');
  const [showCredSecret, setShowCredSecret] = useState(false);
  const [credLoading, setCredLoading] = useState(false);
  const [credMsg, setCredMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null);

  // Confluence PAT
  const [pat, setPat] = useState('');
  const [showPat, setShowPat] = useState(false);
  const [patLoading, setPatLoading] = useState(false);
  const [patMsg, setPatMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null);

  // Reset on close
  useEffect(() => {
    if (!isOpen) {
      setCurrentPw(''); setNewPw(''); setConfirmPw('');
      setShowPw(false); setPwMsg(null);
      setCredClientId(''); setCredClientSecret(''); setCredUserId('');
      setShowCredSecret(false); setCredMsg(null);
      setPat(''); setShowPat(false); setPatMsg(null);
    }
  }, [isOpen]);

  const handleChangePassword = async () => {
    setPwMsg(null);
    if (!currentPw || !newPw) { setPwMsg({ type: 'err', text: '모든 필드를 입력해주세요.' }); return; }
    if (newPw.length < 4) { setPwMsg({ type: 'err', text: '새 비밀번호는 4자 이상이어야 합니다.' }); return; }
    if (newPw !== confirmPw) { setPwMsg({ type: 'err', text: '새 비밀번호가 일치하지 않습니다.' }); return; }

    setPwLoading(true);
    try {
      await changePassword(currentPw, newPw);
      setPwMsg({ type: 'ok', text: '비밀번호가 변경되었습니다.' });
      setCurrentPw(''); setNewPw(''); setConfirmPw('');
    } catch (err) {
      setPwMsg({ type: 'err', text: err instanceof Error ? err.message : '비밀번호 변경 실패' });
    } finally {
      setPwLoading(false);
    }
  };

  const handleUpdateCredentials = async () => {
    setCredMsg(null);
    if (!credClientId.trim() || !credClientSecret.trim() || !credUserId.trim()) {
      setCredMsg({ type: 'err', text: 'Client ID / Client Secret / User ID 모두 입력해주세요.' });
      return;
    }
    setCredLoading(true);
    try {
      await updateLLMCredentials({
        client_id: credClientId.trim(),
        client_secret: credClientSecret.trim(),
        user_id: credUserId.trim(),
      });
      setCredMsg({ type: 'ok', text: '자격증명이 등록되었습니다.' });
      setCredClientId(''); setCredClientSecret(''); setCredUserId('');
      onUserUpdate({ has_llm_credentials: true });
    } catch (err) {
      setCredMsg({ type: 'err', text: err instanceof Error ? err.message : '자격증명 등록 실패' });
    } finally {
      setCredLoading(false);
    }
  };

  const handleDeleteCredentials = async () => {
    setCredMsg(null);
    setCredLoading(true);
    try {
      await deleteLLMCredentials();
      setCredMsg({ type: 'ok', text: '자격증명이 삭제되었습니다. (이후 팀 공통 자격증명 사용)' });
      onUserUpdate({ has_llm_credentials: false });
    } catch (err) {
      setCredMsg({ type: 'err', text: err instanceof Error ? err.message : '자격증명 삭제 실패' });
    } finally {
      setCredLoading(false);
    }
  };

  const handleUpdatePat = async () => {
    setPatMsg(null);
    if (!pat.trim()) { setPatMsg({ type: 'err', text: 'PAT를 입력해주세요.' }); return; }
    setPatLoading(true);
    try {
      await updateConfluencePAT(pat.trim());
      setPatMsg({ type: 'ok', text: 'Confluence PAT가 등록되었습니다.' });
      setPat('');
      onUserUpdate({ has_confluence_pat: true });
    } catch (err) {
      setPatMsg({ type: 'err', text: err instanceof Error ? err.message : 'PAT 등록 실패' });
    } finally {
      setPatLoading(false);
    }
  };

  const handleDeletePat = async () => {
    setPatMsg(null);
    setPatLoading(true);
    try {
      await deleteConfluencePAT();
      setPatMsg({ type: 'ok', text: 'Confluence PAT가 삭제되었습니다.' });
      onUserUpdate({ has_confluence_pat: false });
    } catch (err) {
      setPatMsg({ type: 'err', text: err instanceof Error ? err.message : 'PAT 삭제 실패' });
    } finally {
      setPatLoading(false);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="계정 설정">
      <div className="space-y-6">
        {/* Account Info */}
        <div className="bg-slate-900/50 rounded-lg p-4 border border-slate-700/50">
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <span className="text-slate-500 text-xs">아이디</span>
              <p className="text-slate-200 font-medium">{user.username}</p>
            </div>
            <div>
              <span className="text-slate-500 text-xs">파트</span>
              <p className="text-slate-200 font-medium">{user.part}</p>
            </div>
            <div>
              <span className="text-slate-500 text-xs">역할</span>
              <p className="text-slate-200 font-medium">{user.role === 'admin' ? '슈퍼어드민' : '일반 사용자'}</p>
            </div>
            <div>
              <span className="text-slate-500 text-xs">LLM 자격증명</span>
              <p className={clsx('font-medium', user.has_llm_credentials ? 'text-emerald-400' : 'text-slate-500')}>
                {user.has_llm_credentials ? '본인 키 사용' : '팀 공통 키 사용'}
              </p>
            </div>
            <div>
              <span className="text-slate-500 text-xs">Confluence PAT</span>
              <p className={clsx('font-medium', user.has_confluence_pat ? 'text-emerald-400' : 'text-slate-500')}>
                {user.has_confluence_pat ? '등록됨' : '미등록'}
              </p>
            </div>
          </div>
        </div>

        {/* Password Change */}
        <div>
          <h4 className="text-sm font-semibold text-slate-200 mb-3 flex items-center gap-2">
            <Lock className="w-4 h-4 text-slate-400" />
            비밀번호 변경
          </h4>
          <div className="space-y-2.5">
            <div className="relative">
              <input
                type={showPw ? 'text' : 'password'}
                value={currentPw}
                onChange={(e) => setCurrentPw(e.target.value)}
                placeholder="현재 비밀번호"
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 pr-10 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              />
              <button
                type="button"
                onClick={() => setShowPw((v) => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              >
                {showPw ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>
            <input
              type={showPw ? 'text' : 'password'}
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              placeholder="새 비밀번호 (4자 이상)"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
            />
            <input
              type={showPw ? 'text' : 'password'}
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              placeholder="새 비밀번호 확인"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
              onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && handleChangePassword()}
            />
            {pwMsg && (
              <p className={clsx('text-xs px-2', pwMsg.type === 'ok' ? 'text-emerald-400' : 'text-rose-400')}>
                {pwMsg.text}
              </p>
            )}
            <Button size="sm" onClick={handleChangePassword} loading={pwLoading} className="w-full">
              비밀번호 변경
            </Button>
          </div>
        </div>

        {/* LLM 자격증명 트리플 */}
        <div>
          <h4 className="text-sm font-semibold text-slate-200 mb-3 flex items-center gap-2">
            <Key className="w-4 h-4 text-slate-400" />
            사내 LLM 자격증명 <span className="text-xs text-slate-500 font-normal">(선택 — 미등록 시 팀 공통)</span>
          </h4>
          <div className="space-y-2.5">
            <input
              type="text"
              value={credClientId}
              onChange={(e) => setCredClientId(e.target.value)}
              placeholder="Client ID (예: usr-...)"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono"
            />
            <div className="relative">
              <input
                type={showCredSecret ? 'text' : 'password'}
                value={credClientSecret}
                onChange={(e) => setCredClientSecret(e.target.value)}
                placeholder="Client Secret"
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 pr-10 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono"
              />
              <button
                type="button"
                onClick={() => setShowCredSecret((v) => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              >
                {showCredSecret ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>
            <input
              type="text"
              value={credUserId}
              onChange={(e) => setCredUserId(e.target.value)}
              placeholder="User ID (예: 20251105_...)"
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono"
            />
            <p className="text-[10px] text-slate-500">DevX OAuth2 Client Credentials 트리플. 등록 시 본인 키로, 미등록 시 .env 팀 공통 키로 동작합니다. (Fernet 암호화 저장)</p>
            {credMsg && (
              <p className={clsx('text-xs px-2', credMsg.type === 'ok' ? 'text-emerald-400' : 'text-rose-400')}>
                {credMsg.text}
              </p>
            )}
            <div className="flex gap-2">
              <Button size="sm" onClick={handleUpdateCredentials} loading={credLoading} className="flex-1">
                {user.has_llm_credentials ? '자격증명 변경' : '자격증명 등록'}
              </Button>
              {user.has_llm_credentials && (
                <Button size="sm" variant="ghost" onClick={handleDeleteCredentials} loading={credLoading}>
                  삭제
                </Button>
              )}
            </div>
          </div>
        </div>

        {/* Confluence PAT */}
        <div>
          <h4 className="text-sm font-semibold text-slate-200 mb-3 flex items-center gap-2">
            <Key className="w-4 h-4 text-slate-400" />
            Confluence PAT
          </h4>
          <div className="space-y-2.5">
            <div className="relative">
              <input
                type={showPat ? 'text' : 'password'}
                value={pat}
                onChange={(e) => setPat(e.target.value)}
                placeholder={user.has_confluence_pat ? '새 PAT로 교체' : 'Personal Access Token 입력'}
                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 pr-10 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                onKeyDown={(e) => e.key === 'Enter' && !e.nativeEvent.isComposing && handleUpdatePat()}
              />
              <button
                type="button"
                onClick={() => setShowPat((v) => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              >
                {showPat ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>
            <p className="text-[10px] text-slate-500">Confluence 프로필 → Personal Access Token에서 발급. 암호화되어 저장됩니다.</p>
            {patMsg && (
              <p className={clsx('text-xs px-2', patMsg.type === 'ok' ? 'text-emerald-400' : 'text-rose-400')}>
                {patMsg.text}
              </p>
            )}
            <div className="flex gap-2">
              <Button size="sm" onClick={handleUpdatePat} loading={patLoading} className="flex-1">
                {user.has_confluence_pat ? 'PAT 변경' : 'PAT 등록'}
              </Button>
              {user.has_confluence_pat && (
                <Button size="sm" variant="ghost" onClick={handleDeletePat} loading={patLoading}
                  className="text-rose-400 hover:text-rose-300 border border-rose-500/30 hover:border-rose-400/50">
                  삭제
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
}
