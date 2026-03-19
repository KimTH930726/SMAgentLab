import { create } from 'zustand';
import type { Conversation } from '../types';

interface SearchConfig {
  wVector: number;
  wKeyword: number;
  topK: number;
}

// 백엔드 fetch 전 임시 fallback (API 응답으로 즉시 덮어씀)
const FALLBACK_SEARCH_CONFIG: SearchConfig = { wVector: 0.7, wKeyword: 0.3, topK: 3 };

const LS_KEY = 'ops_search_config';

function loadPersonalConfig(): Partial<SearchConfig> | null {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? (JSON.parse(raw) as Partial<SearchConfig>) : null;
  } catch {
    return null;
  }
}

function savePersonalConfig(cfg: SearchConfig) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(cfg));
  } catch {
    // ignore
  }
}

export type AgentType = 'knowledge_rag' | 'text2sql';

interface AppState {
  selectedAgent: AgentType | null;
  setSelectedAgent: (agent: AgentType | null) => void;
  mcpEnabled: boolean;
  setMcpEnabled: (v: boolean) => void;
  namespace: string;
  setNamespace: (ns: string) => void;
  conversationId: number | null;
  setConversationId: (id: number | null) => void;
  conversations: Conversation[];
  setConversations: (convs: Conversation[]) => void;
  searchConfig: SearchConfig;
  setSearchConfig: (cfg: Partial<SearchConfig>) => void;
  searchConfigLoaded: boolean;
  initSearchConfig: (cfg: SearchConfig) => void;
  chatRefreshKey: number;
  bumpChatRefresh: () => void;
  category: string;
  setCategory: (cat: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  selectedAgent: null,
  setSelectedAgent: (selectedAgent) => set({ selectedAgent, conversationId: null, conversations: [] }),
  mcpEnabled: false,
  setMcpEnabled: (mcpEnabled) => set({ mcpEnabled }),
  namespace: '',
  setNamespace: (namespace) => set({ namespace, conversationId: null, conversations: [], category: '' }),
  conversationId: null,
  setConversationId: (conversationId) =>
    set((state) => (state.conversationId === conversationId ? state : { conversationId })),
  conversations: [],
  setConversations: (conversations) => set({ conversations }),
  searchConfig: FALLBACK_SEARCH_CONFIG,
  setSearchConfig: (cfg: Partial<SearchConfig>) =>
    set((state) => {
      const next = { ...state.searchConfig, ...cfg };
      savePersonalConfig(next);
      return { searchConfig: next };
    }),
  searchConfigLoaded: false,
  initSearchConfig: (cfg) => {
    // 개인 설정이 있으면 우선 적용 (어드민 기본값보다 개인 설정 우선)
    const personal = loadPersonalConfig();
    const merged = personal ? { ...cfg, ...personal } : cfg;
    set({ searchConfig: merged, searchConfigLoaded: true });
  },
  chatRefreshKey: 0,
  bumpChatRefresh: () => set((state) => ({ chatRefreshKey: state.chatRefreshKey + 1 })),
  category: '',
  setCategory: (category) => set({ category }),
}));
