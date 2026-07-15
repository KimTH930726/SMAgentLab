import { apiFetch } from './client';
import type { GlobalStats, NamespaceStats, QueryLog, QueryStatus } from '../types';

export async function getStats(): Promise<GlobalStats> {
  try {
    return await apiFetch<GlobalStats>('/stats');
  } catch (err) {
    console.error('getStats error:', err);
    throw err;
  }
}

export async function getNamespaceStats(namespace: string): Promise<NamespaceStats> {
  try {
    return await apiFetch<NamespaceStats>(`/stats/namespace/${encodeURIComponent(namespace)}`);
  } catch (err) {
    console.error('getNamespaceStats error:', err);
    throw err;
  }
}

export async function getQueryLogs(namespace: string, status?: QueryStatus): Promise<QueryLog[]> {
  try {
    const params = new URLSearchParams();
    if (status !== undefined) params.set('status', status);
    return await apiFetch<QueryLog[]>(`/stats/namespace/${encodeURIComponent(namespace)}/queries?${params}`);
  } catch (err) {
    console.error('getQueryLogs error:', err);
    throw err;
  }
}

export async function resolveQueryLog(id: number): Promise<{ status: string; pending_review?: boolean }> {
  try {
    return await apiFetch(`/stats/query-log/${id}/resolve`, { method: 'PATCH' });
  } catch (err) {
    console.error('resolveQueryLog error:', err);
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

export async function markQueryLogResolved(id: number, knowledgeId?: number | null): Promise<void> {
  await apiFetch<void>(`/stats/query-log/${id}/mark-resolved`, {
    method: 'PATCH',
    body: JSON.stringify({ knowledge_id: knowledgeId ?? null }),
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
