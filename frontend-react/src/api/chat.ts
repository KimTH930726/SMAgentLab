import { streamSSE, apiFetch } from './client';
import type { SSEEvent, ChatRequest } from '../types';

export function streamChat(params: ChatRequest): AsyncGenerator<SSEEvent> {
  const body: Record<string, unknown> = {
    namespace: params.namespace,
    question: params.question,
    agent_type: params.agentType ?? 'knowledge_rag',
    w_vector: params.wVector,
    w_keyword: params.wKeyword,
    top_k: params.topK,
    conversation_id: params.conversationId ?? null,
    clarify_scope: params.clarifyScope ?? null,
    suggest_of: params.suggestOf ?? null,
  };
  return streamSSE('/chat/stream', body, params.signal);
}

