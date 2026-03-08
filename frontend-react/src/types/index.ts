// Auth types
export interface User {
  id: number;
  username: string;
  role: 'admin' | 'user';
  part: string;
  is_active: boolean;
  has_api_key: boolean;
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  user: User;
}

export interface Part {
  id: number;
  name: string;
  created_at: string;
}

// Namespace types
export interface Namespace {
  name: string;
  description: string;
  created_at: string;
}

export interface NamespaceDetail extends Namespace {
  owner_part?: string | null;
  knowledge_count: number;
  glossary_count: number;
  created_by_username?: string | null;
}

// Knowledge types
export interface KnowledgeItem {
  id: number;
  namespace: string;
  container_name: string;
  target_tables: string[] | null;
  content: string;
  query_template: string | null;
  base_weight: number;
  created_by_part?: string | null;
  created_by_user_id?: number | null;
  created_by_username?: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeCreatePayload {
  namespace: string;
  container_name: string;
  target_tables: string[];
  content: string;
  query_template?: string | null;
  base_weight?: number;
}

export interface KnowledgeUpdatePayload {
  container_name?: string;
  target_tables?: string[];
  content?: string;
  query_template?: string | null;
  base_weight?: number;
}

// Glossary types
export interface GlossaryItem {
  id: number;
  namespace: string;
  term: string;
  description: string;
  created_by_part?: string | null;
  created_by_user_id?: number | null;
  created_by_username?: string | null;
  created_at: string;
}

export interface GlossaryCreatePayload {
  namespace: string;
  term: string;
  description: string;
}

export interface GlossaryUpdatePayload {
  term?: string;
  description?: string;
}

// Chat / Conversation types
export interface Conversation {
  id: number;
  namespace: string;
  title: string;
  trimmed?: boolean;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessage {
  id: number;
  conversation_id: number;
  role: 'user' | 'assistant';
  content: string;
  mapped_term?: string | null;
  results?: KnowledgeResult[] | null;
  status?: string;
  has_feedback?: boolean;
  created_at: string;
}

export interface KnowledgeResult {
  id: number;
  container_name: string;
  target_tables: string[];
  content: string;
  query_template: string | null;
  final_score: number;
  v_score?: number;
  k_score?: number;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  mapped_term?: string | null;
  results?: KnowledgeResult[];
  isStreaming?: boolean;
  question?: string;
  has_feedback?: boolean;
  messageId?: number;
}

// SSE Event types
export interface SSEStatusEvent {
  type: 'status';
  step: 'embedding' | 'context' | 'search' | 'llm';
  message: string;
}

export interface SSEMetaEvent {
  type: 'meta';
  conversation_id: number | null;
  mapped_term: string | null;
  results: KnowledgeResult[];
}

export interface SSETokenEvent {
  type: 'token';
  data: string;
}

export interface SSEDoneEvent {
  type: 'done';
  message_id?: number;
}

export type SSEEvent = SSEStatusEvent | SSEMetaEvent | SSETokenEvent | SSEDoneEvent;

// Chat request
export interface ChatRequest {
  namespace: string;
  question: string;
  wVector?: number;
  wKeyword?: number;
  topK?: number;
  conversationId?: number | null;
  signal?: AbortSignal;
}

// Feedback types
export interface FeedbackPayload {
  namespace: string;
  question: string;
  answer: string;
  knowledge_id?: number | null;
  is_positive: boolean;
  comment?: string | null;
  message_id?: number | null;
}

// Few-shot types
export interface FewshotItem {
  id: number;
  namespace: string;
  question: string;
  answer: string;
  knowledge_id: number | null;
  created_by_part?: string | null;
  created_by_user_id?: number | null;
  created_by_username?: string | null;
  created_at: string;
}

export interface FewshotCreatePayload {
  namespace: string;
  question: string;
  answer: string;
  knowledge_id?: number | null;
}

export interface FewshotUpdatePayload {
  question?: string;
  answer?: string;
}

export interface FewshotSearchResult {
  question: string;
  answer: string;
  similarity: number;
}

export interface FewshotSearchResponse {
  question: string;
  namespace: string;
  fewshots: FewshotSearchResult[];
  prompt_section: string;
}

// Query log item from /stats/namespace/{name}/queries
export type QueryStatus = 'pending' | 'resolved' | 'unresolved';

export interface QueryLog {
  id: number;
  question: string;
  mapped_term: string | null;
  status: QueryStatus;
  created_at: string;
  answer: string | null;
}

// Stats types — matches backend NamespaceDetailStats
export interface NamespaceStats {
  namespace: string;
  total_queries: number;
  resolved: number;
  pending: number;
  unresolved: number;
  term_distribution: Array<{ term: string; total: number; pending: number; unresolved: number }>;
  unresolved_cases: Array<{ id: number; question: string; mapped_term: string | null; created_at: string }>;
}

// matches backend StatsResponse
export interface GlobalStats {
  namespaces: Array<{
    namespace: string;
    total_queries: number;
    resolved: number;
    pending: number;
    unresolved: number;
    positive_feedback: number;
    negative_feedback: number;
    knowledge_count: number;
    glossary_count: number;
  }>;
  unresolved_cases: Array<{ namespace: string; question: string; created_at: string }>;
}

// Debug search types
export interface DebugSearchRequest {
  namespace: string;
  question: string;
  w_vector?: number;
  w_keyword?: number;
  top_k?: number;
}

export interface DebugSearchResult {
  id: number;
  container_name: string;
  target_tables: string[];
  content: string;
  query_template: string | null;
  v_score: number;
  k_score: number;
  final_score: number;
  base_weight: number;
}

export interface DebugGlossaryMatch {
  term: string;
  description: string;
  similarity: number;
}

export interface DebugFewshot {
  question: string;
  answer: string;
  similarity: number;
}

export interface DebugSearchResponse {
  question: string;
  namespace: string;
  enriched_query: string;
  glossary_match: DebugGlossaryMatch | null;
  w_vector: number;
  w_keyword: number;
  fewshots: DebugFewshot[];
  results: DebugSearchResult[];
  context_preview: string;
}
