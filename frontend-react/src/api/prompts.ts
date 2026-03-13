import { apiFetch } from './client';

export interface Prompt {
  id: number;
  func_key: string;
  func_name: string;
  content: string;
  description: string;
  updated_at: string;
}

export interface PromptUpdate {
  func_name?: string;
  content?: string;
  description?: string;
}

export async function listPrompts(): Promise<Prompt[]> {
  return apiFetch<Prompt[]>('/prompts');
}

export async function getPrompt(funcKey: string): Promise<Prompt> {
  return apiFetch<Prompt>(`/prompts/${encodeURIComponent(funcKey)}`);
}

export async function updatePrompt(promptId: number, payload: PromptUpdate): Promise<Prompt> {
  return apiFetch<Prompt>(`/prompts/${promptId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}
