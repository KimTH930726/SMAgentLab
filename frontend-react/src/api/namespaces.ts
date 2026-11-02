import { apiFetch } from './client';
import type { NamespaceDetail, OwnerPart } from '../types';

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

/** area: 'system' = 시스템 컬렉션(최상위만 — 상위 없음, 11/02). 없으면 정책 컬렉션 */
export async function createNamespace(payload: { name: string; description: string; area?: 'system'; code?: string }): Promise<void> {
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

/** 시스템이 관련된 정책 컬렉션 — 통째로 바꾼다(관리자만, 모르는 이름·시스템 이름이면 400, 11/02) */
export async function setNamespaceSupports(name: string, policies: string[]): Promise<unknown> {
  return apiFetch<unknown>(`/namespaces/${encodeURIComponent(name)}/supports`, {
    method: 'PUT',
    body: JSON.stringify({ policies }),
  });
}

// 업무구분 CRUD·AI 추천 API는 10/22 업무구분 폐지로 삭제
