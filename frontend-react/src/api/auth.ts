import { apiFetch } from './client';
import type { User, LoginResponse, Part } from '../types';

export async function login(username: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

export interface LLMCredentialsTriple {
  client_id: string;
  client_secret: string;
  user_id: string;
}

export async function register(payload: {
  username: string;
  password: string;
  part: string;
  llm_credentials?: LLMCredentialsTriple;
}): Promise<User> {
  return apiFetch<User>('/auth/register', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function refreshAccessToken(refreshToken: string): Promise<{ access_token: string }> {
  return apiFetch<{ access_token: string }>('/auth/refresh', {
    method: 'POST',
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
}

export async function getMe(): Promise<User> {
  return apiFetch<User>('/auth/me');
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return apiFetch<void>('/auth/me/password', {
    method: 'PUT',
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
}

export async function updateLLMCredentials(credentials: LLMCredentialsTriple): Promise<void> {
  return apiFetch<void>('/auth/me/llm-credentials', {
    method: 'PUT',
    body: JSON.stringify({ credentials }),
  });
}

export async function deleteLLMCredentials(): Promise<void> {
  return apiFetch<void>('/auth/me/llm-credentials', { method: 'DELETE' });
}

export async function updateConfluencePAT(pat: string): Promise<void> {
  return apiFetch<void>('/auth/me/confluence-pat', {
    method: 'PUT',
    body: JSON.stringify({ pat }),
  });
}

export async function deleteConfluencePAT(): Promise<void> {
  return apiFetch<void>('/auth/me/confluence-pat', { method: 'DELETE' });
}

export async function getConfluencePATStatus(): Promise<{ has_confluence_pat: boolean }> {
  return apiFetch<{ has_confluence_pat: boolean }>('/auth/me/confluence-pat/status');
}

export async function getParts(): Promise<Part[]> {
  return apiFetch<Part[]>('/auth/parts');
}

export async function getAllParts(): Promise<Part[]> {
  return apiFetch<Part[]>('/auth/parts/all');
}

// Admin
export async function getUsers(): Promise<User[]> {
  return apiFetch<User[]>('/auth/users');
}

export async function updateUser(
  userId: number,
  payload: { role?: string; part?: string; is_active?: boolean },
): Promise<User> {
  return apiFetch<User>(`/auth/users/${userId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function deleteUser(userId: number): Promise<void> {
  return apiFetch<void>(`/auth/users/${userId}`, { method: 'DELETE' });
}

export async function createPart(name: string): Promise<Part> {
  return apiFetch<Part>('/auth/parts', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export async function renamePart(partId: number, name: string): Promise<Part> {
  return apiFetch<Part>(`/auth/parts/${partId}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  });
}

export async function deletePart(partId: number): Promise<void> {
  return apiFetch<void>(`/auth/parts/${partId}`, { method: 'DELETE' });
}
