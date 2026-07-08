import { apiFetch } from './client';
import type { Conversation, ConversationMessage } from '../types';

export async function getConversations(namespace: string, agentType: string = 'knowledge_rag'): Promise<Conversation[]> {
  try {
    return await apiFetch<Conversation[]>(
      `/conversations?namespace=${encodeURIComponent(namespace)}&agent_type=${encodeURIComponent(agentType)}`,
    );
  } catch (err) {
    console.error('getConversations error:', err);
    throw err;
  }
}

export async function deleteConversation(id: number): Promise<void> {
  try {
    await apiFetch<void>(`/conversations/${id}`, { method: 'DELETE' });
  } catch (err) {
    console.error('deleteConversation error:', err);
    throw err;
  }
}

export async function getMessages(conversationId: number): Promise<ConversationMessage[]> {
  try {
    return await apiFetch<ConversationMessage[]>(`/conversations/${conversationId}/messages`);
  } catch (err) {
    console.error('getMessages error:', err);
    throw err;
  }
}
