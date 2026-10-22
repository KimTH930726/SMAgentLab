import { apiFetch } from './client';
import type { NamespaceDetail } from '../types';

// GET /api/namespaces returns string[]
export async function getNamespaces(): Promise<string[]> {
  try {
    return await apiFetch<string[]>('/namespaces');
  } catch (err) {
    console.error('getNamespaces error:', err);
    throw err;
  }
}

export async function getNamespacesDetail(): Promise<NamespaceDetail[]> {
  try {
    return await apiFetch<NamespaceDetail[]>('/namespaces/detail');
  } catch (err) {
    console.error('getNamespacesDetail error:', err);
    throw err;
  }
}

export async function createNamespace(payload: { name: string; description: string }): Promise<void> {
  try {
    return await apiFetch<void>('/namespaces', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error('createNamespace error:', err);
    throw err;
  }
}

/** 업무 컬렉션 담당 파트 지정(관리자) — null이면 담당 해제(담당이 없으면 관리자만 수정) */
export async function setNamespaceOwner(name: string, ownerPart: string | null): Promise<{ name: string; owner_part: string | null }> {
  return apiFetch(`/namespaces/${encodeURIComponent(name)}/settings`, {
    method: 'PATCH',
    body: JSON.stringify(ownerPart ? { owner_part: ownerPart } : { clear_owner: true }),
  });
}

export async function renameNamespace(oldName: string, newName: string): Promise<{ name: string }> {
  return apiFetch<{ name: string }>(`/namespaces/${encodeURIComponent(oldName)}`, {
    method: 'PATCH',
    body: JSON.stringify({ new_name: newName }),
  });
}

export async function deleteNamespace(name: string): Promise<void> {
  try {
    await apiFetch<void>(`/namespaces/${encodeURIComponent(name)}`, { method: 'DELETE' });
  } catch (err) {
    console.error('deleteNamespace error:', err);
    throw err;
  }
}

// 업무구분 CRUD·AI 추천 API는 10/22 업무구분 폐지로 삭제
