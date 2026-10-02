import { apiFetch } from './client';
import type { NamespaceStats, QueryLog, QueryFilter } from '../types';

export async function getNamespaceStats(namespace: string): Promise<NamespaceStats> {
  try {
    return await apiFetch<NamespaceStats>(`/stats/namespace/${encodeURIComponent(namespace)}`);
  } catch (err) {
    console.error('getNamespaceStats error:', err);
    throw err;
  }
}

export async function getQueryLogs(namespace: string, status?: QueryFilter): Promise<QueryLog[]> {
  try {
    const params = new URLSearchParams();
    if (status !== undefined) params.set('status', status);
    return await apiFetch<QueryLog[]>(`/stats/namespace/${encodeURIComponent(namespace)}/queries?${params}`);
  } catch (err) {
    console.error('getQueryLogs error:', err);
    throw err;
  }
}

export async function deleteQueryLog(id: number): Promise<void> {
  try {
    await apiFetch<void>(`/stats/query-log/${id}`, { method: 'DELETE' });
  } catch (err) {
    console.error('deleteQueryLog error:', err);
    throw err;
  }
}

/** 지식 공백 질의를 등록한 지식으로 메웠다고 기록 — 상태는 공백 그대로, 연결 지식만(메움 실적) */
export async function fillKnowledgeGap(id: number, knowledgeId: number): Promise<void> {
  await apiFetch<void>(`/stats/query-log/${id}/fill`, {
    method: 'PATCH',
    body: JSON.stringify({ knowledge_id: knowledgeId }),
  });
}

export async function bulkDeleteQueryLogs(ids: number[]): Promise<{ deleted: number }> {
  try {
    return await apiFetch<{ deleted: number }>('/stats/query-logs/bulk-delete', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    });
  } catch (err) {
    console.error('bulkDeleteQueryLogs error:', err);
    throw err;
  }
}
