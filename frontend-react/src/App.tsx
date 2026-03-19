import { useEffect, useRef } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Sidebar } from './components/layout/Sidebar';
import { useAppStore } from './store/useAppStore';
import { useAuthStore } from './store/useAuthStore';
import { useThemeStore } from './store/useThemeStore';
import { getSearchDefaults } from './api/llm';
import Chat from './pages/Chat';
import Admin from './pages/Admin';
import Login from './pages/Login';
import Register from './pages/Register';
import AgentSelect from './pages/AgentSelect';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 30_000 } },
});

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

// Chat은 항상 mount 유지 (스트리밍 중 화면 이동해도 끊기지 않도록)
function ThemeSync() {
  const theme = useThemeStore((s) => s.theme);
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);
  return null;
}

function SearchConfigSync() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const searchConfigLoaded = useAppStore((s) => s.searchConfigLoaded);
  const initSearchConfig = useAppStore((s) => s.initSearchConfig);

  useEffect(() => {
    if (!isAuthenticated || searchConfigLoaded) return;
    getSearchDefaults()
      .then((d) => initSearchConfig({
        wVector: d.default_w_vector,
        wKeyword: d.default_w_keyword,
        topK: d.default_top_k,
      }))
      .catch(() => {/* fallback 유지 */});
  }, [isAuthenticated, searchConfigLoaded, initSearchConfig]);

  return null;
}

function AppContent() {
  const location = useLocation();
  const isAdmin = location.pathname.startsWith('/admin');
  const bumpChatRefresh = useAppStore((s) => s.bumpChatRefresh);
  const selectedAgent = useAppStore((s) => s.selectedAgent);

  // admin → chat 전환 시 메시지 재로드 트리거
  const prevAdminRef = useRef(isAdmin);
  useEffect(() => {
    const wasAdmin = prevAdminRef.current;
    prevAdminRef.current = isAdmin;
    if (wasAdmin && !isAdmin) {
      bumpChatRefresh();
    }
  }, [isAdmin, bumpChatRefresh]);

  // 에이전트 미선택 시 에이전트 선택 화면
  if (!selectedAgent) {
    return <AgentSelect />;
  }

  return (
    <div className="flex h-screen overflow-hidden bg-slate-900">
      <Sidebar />
      <main className="flex-1 overflow-hidden">
        <div className={isAdmin ? 'hidden' : 'h-full'}>
          <Chat />
        </div>
        {isAdmin && <Admin />}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeSync />
      <SearchConfigSync />
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route
            path="/*"
            element={
              <ProtectedRoute>
                <AppContent />
              </ProtectedRoute>
            }
          />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
