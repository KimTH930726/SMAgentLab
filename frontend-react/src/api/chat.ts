import { streamSSE, apiFetch } from './client';
import type { SSEEvent, ChatRequest, DebugSearchRequest, DebugSearchResponse } from '../types';

export function streamChat(params: ChatRequest): AsyncGenerator<SSEEvent> {
  const body = {
    namespace: params.namespace,
    question: params.question,
    w_vector: params.wVector,
    w_keyword: params.wKeyword,
    top_k: params.topK,
    conversation_id: params.conversationId ?? null,
    category: params.category ?? null,
  };
  return streamSSE('/chat/stream', body, params.signal);
}

export async function savePartialContent(messageId: number, content: string): Promise<void> {
  await apiFetch('/chat/messages/' + messageId + '/content', {
    method: 'PATCH',
    body: JSON.stringify({ content }),
  });
}

export async function deleteGhostMessage(messageId: number): Promise<void> {
  await apiFetch('/chat/messages/' + messageId, {
    method: 'DELETE',
  });
}

export async function debugSearch(params: DebugSearchRequest): Promise<DebugSearchResponse> {
  try {
    return await apiFetch<DebugSearchResponse>('/chat/debug', {
      method: 'POST',
      body: JSON.stringify({
        namespace: params.namespace,
        question: params.question,
        w_vector: params.w_vector,
        w_keyword: params.w_keyword,
        top_k: params.top_k,
      }),
    });
  } catch (err) {
    console.error('debugSearch error:', err);
    throw err;
  }
}
