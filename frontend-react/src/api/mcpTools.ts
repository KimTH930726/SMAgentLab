import { apiFetch } from './client';
import type { McpTool, McpToolCreatePayload, McpToolUpdatePayload } from '../types';

export async function listMcpTools(namespace: string, agentType: string = 'knowledge_rag'): Promise<McpTool[]> {
  return apiFetch<McpTool[]>(`/mcp-tools?namespace=${encodeURIComponent(namespace)}&agent_type=${encodeURIComponent(agentType)}`);
}

export async function createMcpTool(payload: McpToolCreatePayload): Promise<McpTool> {
  return apiFetch<McpTool>('/mcp-tools', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateMcpTool(toolId: number, payload: McpToolUpdatePayload): Promise<McpTool> {
  return apiFetch<McpTool>(`/mcp-tools/${toolId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function toggleMcpTool(toolId: number, isActive: boolean): Promise<McpTool> {
  return apiFetch<McpTool>(`/mcp-tools/${toolId}/toggle`, {
    method: 'PATCH',
    body: JSON.stringify({ is_active: isActive }),
  });
}

export async function deleteMcpTool(toolId: number): Promise<void> {
  await apiFetch(`/mcp-tools/${toolId}`, { method: 'DELETE' });
}

export interface McpToolTestResult {
  status: string;
  request: { method: string; url: string; headers: Record<string, string>; params: Record<string, string> };
  response?: {
    status_code: number;
    headers: Record<string, string>;
    body: string;
    truncated: boolean;
    elapsed_ms: number;
    size_bytes: number;
  };
  error?: string;
  elapsed_ms?: number;
}

export async function testMcpTool(toolId: number, params: Record<string, unknown> = {}): Promise<McpToolTestResult> {
  return apiFetch<McpToolTestResult>(`/mcp-tools/${toolId}/test`, {
    method: 'POST',
    body: JSON.stringify({ params }),
  });
}

export async function autocompleteMcpTool(namespace: string, rawText: string): Promise<{
  status: string;
  tool?: Record<string, unknown>;
  message?: string;
  raw?: string;
}> {
  return apiFetch('/mcp-tools/autocomplete', {
    method: 'POST',
    body: JSON.stringify({ namespace, raw_text: rawText }),
  });
}

export interface McpToolLog {
  id: number;
  tool_id: number | null;
  tool_name: string;
  username: string | null;
  user_id: number | null;
  namespace_id: number;
  conversation_id: number | null;
  params: Record<string, unknown>;
  response_status: number | null;
  response_kb: number | null;
  duration_ms: number | null;
  error: string | null;
  called_at: string;
  request_url: string | null;
  http_method: string | null;
}

export interface McpToolLogsResponse {
  items: McpToolLog[];
  total: number;
  page: number;
  page_size: number;
}

export interface McpToolLogStats {
  tool_id: number | null;
  tool_name: string;
  total_calls: number;
  success_calls: number;
  avg_duration_ms: number | null;
  last_called_at: string | null;
  status_dist: Record<string, number>;
}

export async function listMcpToolLogs(
  namespace: string,
  toolId?: number,
  fromDt?: string,
  toDt?: string,
  page = 1,
  pageSize = 20,
): Promise<McpToolLogsResponse> {
  const params = new URLSearchParams({ namespace, page: String(page), page_size: String(pageSize) });
  if (toolId !== undefined) params.set('tool_id', String(toolId));
  if (fromDt) params.set('from_dt', fromDt);
  if (toDt) params.set('to_dt', toDt);
  return apiFetch<McpToolLogsResponse>(`/mcp-tools/logs?${params.toString()}`);
}

export async function getMcpToolLogStats(
  namespace: string,
  fromDt?: string,
  toDt?: string,
): Promise<McpToolLogStats[]> {
  const params = new URLSearchParams({ namespace });
  if (fromDt) params.set('from_dt', fromDt);
  if (toDt) params.set('to_dt', toDt);
  return apiFetch<McpToolLogStats[]>(`/mcp-tools/logs/stats?${params.toString()}`);
}
