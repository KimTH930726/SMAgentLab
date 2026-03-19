import { apiFetch } from './client';
import type { AgentInfo } from '../types';

export async function listAgents(): Promise<AgentInfo[]> {
  return apiFetch<AgentInfo[]>('/agents');
}

export async function checkAgentHealth(agentId: string): Promise<{ agent_id: string; healthy: boolean }> {
  return apiFetch<{ agent_id: string; healthy: boolean }>(`/agents/${agentId}/health`);
}
