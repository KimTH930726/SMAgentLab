import { apiFetch } from './client';

export interface LLMConfig {
  /** openai_compat = dev 전용 로컬 LLM(E2E 동안 등, 2026-10-20) — 설정 화면에선 고를 수 없다 */
  provider: 'ollama' | 'inhouse' | 'openai_compat';
  is_runtime_override: boolean;
  is_connected: boolean;
  openai_compat?: { enabled: boolean; model: string };
  ollama: {
    base_url: string;
    model: string;
    timeout: number;
  };
  inhouse: {
    base_url: string;
    agent_code: string;
    agent_id: string;
    conversation_id: string;
    model: string;
    has_credentials: boolean;
    response_mode: string;
    timeout: number;
  };
}

export interface LLMConfigUpdate {
  provider: 'ollama' | 'inhouse';
  ollama_base_url?: string;
  ollama_model?: string;
  ollama_timeout?: number;
  // InHouse (OAuth2 Client Credentials)
  inhouse_llm_base_url?: string;
  inhouse_llm_client_id?: string;
  inhouse_llm_client_secret?: string;
  inhouse_llm_agent_id?: string;
  inhouse_llm_agent_code?: string;
  inhouse_llm_conversation_id?: string;
  inhouse_llm_model?: string;
  inhouse_llm_response_mode?: string;
  inhouse_llm_timeout?: number;
}

export interface LLMTestResult {
  is_connected: boolean;
  provider: string;
  error?: string;
}

export async function getLLMConfig(): Promise<LLMConfig> {
  return await apiFetch<LLMConfig>('/llm/config');
}

export async function updateLLMConfig(payload: LLMConfigUpdate): Promise<LLMConfig> {
  return await apiFetch<LLMConfig>('/llm/config', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function testLLMConnection(payload: Omit<LLMConfigUpdate, 'ollama_timeout' | 'inhouse_llm_timeout'>): Promise<LLMTestResult> {
  return await apiFetch<LLMTestResult>('/llm/test', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ── 검색 임계값 설정 ──

export interface SearchThresholds {
  glossary_min_similarity: number;
  knowledge_min_score: number;
  knowledge_high_score: number;
  knowledge_mid_score: number;
}

export async function getSearchThresholds(): Promise<SearchThresholds> {
  return await apiFetch<SearchThresholds>('/llm/thresholds');
}

export async function updateSearchThresholds(payload: Partial<SearchThresholds>): Promise<SearchThresholds> {
  return await apiFetch<SearchThresholds>('/llm/thresholds', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

// ── 검색 기본값 설정 ──

export interface SearchDefaults {
  default_top_k: number;
  default_w_vector: number;
  default_w_keyword: number;
}

export async function getSearchDefaults(): Promise<SearchDefaults> {
  return await apiFetch<SearchDefaults>('/llm/search-defaults');
}

export async function updateSearchDefaults(payload: Partial<SearchDefaults>): Promise<SearchDefaults> {
  return await apiFetch<SearchDefaults>('/llm/search-defaults', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

// 사내 LLM 사용량(2026-10-12) — 기능별 집계, 읽기 전용
export interface LlmUsageFeature {
  feature: string; calls: number; failed: number; aborted?: number; input_tokens: number | null; output_tokens: number | null;
  with_tokens: number; input_chars: number | null; output_chars: number | null; avg_ms: number | null; last_at: string | null;
}

export async function getLlmUsage(days = 7): Promise<{ days: number; features: LlmUsageFeature[] }> {
  return await apiFetch(`/admin/llm-usage?days=${days}`);
}
