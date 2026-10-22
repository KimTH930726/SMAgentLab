import { apiFetch } from './client';
import type { User, LoginResponse, Part, Team } from '../types';

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

/** 지금 로그인한 사용자(역할·소속)를 서버에서 다시 읽는다 — 승인·소속 변경이 다시 로그인 없이 화면에 반영되게 */
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

export async function createPart(name: string, teamId?: number): Promise<Part> {
  return apiFetch<Part>('/auth/parts', {
    method: 'POST',
    body: JSON.stringify({ name, team_id: teamId ?? null }),
  });
}

/** 파트를 다른 팀으로(관리자) — 사람·담당 업무는 파트에 붙어 같이 간다 */
export async function setPartTeam(partId: number, teamId: number): Promise<Part> {
  return apiFetch<Part>(`/auth/parts/${partId}/team`, { method: 'PUT', body: JSON.stringify({ team_id: teamId }) });
}

// 팀(조직: 팀 > 파트, 10/22)
export async function getTeams(): Promise<Team[]> {
  return apiFetch<Team[]>('/auth/teams');
}

export async function createTeam(name: string): Promise<Team> {
  return apiFetch<Team>('/auth/teams', { method: 'POST', body: JSON.stringify({ name }) });
}

export async function renameTeam(teamId: number, name: string): Promise<Team> {
  return apiFetch<Team>(`/auth/teams/${teamId}`, { method: 'PATCH', body: JSON.stringify({ name }) });
}

/** 파트가 남아 있으면 409(파트를 먼저 옮겨야 함) */
export async function deleteTeam(teamId: number): Promise<void> {
  return apiFetch<void>(`/auth/teams/${teamId}`, { method: 'DELETE' });
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

/** 파트의 담당 업무를 통째로 저장(관리자) — 한 트랜잭션, 없는 영역이 하나라도 있으면 아무것도 안 바뀜 */
export async function setPartDomains(partId: number, domains: string[]): Promise<{ part_id: number; domains: string[] }> {
  return apiFetch(`/auth/parts/${partId}/domains`, { method: 'PUT', body: JSON.stringify({ domains }) });
}
