import { apiFetch } from './client';
import type {
  SqlTargetDb, SqlRelation, SqlSynonym, SqlFewshot, SqlPipelineStage, SqlAuditLog, SqlCacheEntry,
} from '../types';

// Backend prefix is /api/text2sql — client's apiFetch strips /api prefix already
const ns = (namespace: string) => `/text2sql/namespaces/${encodeURIComponent(namespace)}`;

// ── Target DB ─────────────────────────────────────────────────────────────────

export async function getTargetDb(namespace: string): Promise<SqlTargetDb | null> {
  try { return await apiFetch<SqlTargetDb>(`${ns(namespace)}/target-db`); }
  catch { return null; }
}

export async function upsertTargetDb(namespace: string, data: SqlTargetDb): Promise<void> {
  await apiFetch(`${ns(namespace)}/target-db`, { method: 'PUT', body: JSON.stringify(data) });
}

export async function testTargetDb(namespace: string, data: SqlTargetDb): Promise<{ ok: boolean; message: string }> {
  return apiFetch(`${ns(namespace)}/target-db/test`, { method: 'POST', body: JSON.stringify(data) });
}

export async function listTargetSchemas(namespace: string): Promise<{ schemas: string[] }> {
  return apiFetch(`${ns(namespace)}/target-db/schemas`, { method: 'POST' });
}

export interface ScanReport {
  tables_added: number;
  tables_removed: number;
  columns_added: number;
  columns_removed: number;
  columns_updated: number;
  columns_skipped: number;
  embeddings_created: number;
  orphan_synonyms_deleted: number;
  orphan_synonyms_warn: Array<{ id: number; term: string; target: string }>;
  changed_tables: string[];
}

export async function scanSchema(namespace: string): Promise<ScanReport> {
  return apiFetch(`${ns(namespace)}/target-db/scan`, { method: 'POST' });
}

// ── Schema ────────────────────────────────────────────────────────────────────

export interface SchemaTableWithCols {
  id: number;
  table_name: string;
  description: string;
  is_selected: boolean;
  pos_x: number;
  pos_y: number;
  columns: Array<{
    id: number;
    table_id: number;
    name: string;
    data_type: string;
    description: string;
    is_pk: boolean;
    fk_reference: string | null;
  }>;
}

export async function getFullSchema(namespace: string): Promise<SchemaTableWithCols[]> {
  return apiFetch<SchemaTableWithCols[]>(`${ns(namespace)}/schema`);
}

export async function updateSchemaTableDesc(namespace: string, tableId: number, description: string): Promise<void> {
  await apiFetch(`${ns(namespace)}/schema/tables/${tableId}`, { method: 'PUT', body: JSON.stringify({ description }) });
}

export async function toggleSchemaTable(namespace: string, tableId: number): Promise<void> {
  await apiFetch(`${ns(namespace)}/schema/tables/${tableId}/toggle`, { method: 'PUT' });
}

export async function updateSchemaColumnDesc(namespace: string, colId: number, description: string): Promise<void> {
  await apiFetch(`${ns(namespace)}/schema/columns/${colId}`, { method: 'PUT', body: JSON.stringify({ description }) });
}

export async function reindexSchema(namespace: string): Promise<{ indexed: number }> {
  return apiFetch(`${ns(namespace)}/schema/reindex`, { method: 'POST' });
}

export async function saveSchemaPositions(namespace: string, positions: Record<string, { x: number; y: number }>): Promise<void> {
  await apiFetch(`${ns(namespace)}/schema/positions`, { method: 'PUT', body: JSON.stringify({ positions }) });
}

export interface TableSummary {
  table: string;
  column_count: number;
}

export async function getAvailableTables(namespace: string): Promise<TableSummary[]> {
  return apiFetch<TableSummary[]>(`${ns(namespace)}/schema/tables-available`);
}

export async function addTables(namespace: string, tables: string[]): Promise<{ ok: boolean; added: number; skipped: number }> {
  return apiFetch(`${ns(namespace)}/schema/tables/add`, { method: 'POST', body: JSON.stringify({ tables }) });
}

export async function deleteTable(namespace: string, tableName: string): Promise<void> {
  await apiFetch(`${ns(namespace)}/schema/tables/${encodeURIComponent(tableName)}`, { method: 'DELETE' });
}

// ── Relations ─────────────────────────────────────────────────────────────────

export async function listRelations(namespace: string): Promise<SqlRelation[]> {
  return apiFetch<SqlRelation[]>(`${ns(namespace)}/relations`);
}

export async function createRelation(namespace: string, data: Omit<SqlRelation, 'id'>): Promise<{ id: number }> {
  return apiFetch(`${ns(namespace)}/relations`, { method: 'POST', body: JSON.stringify(data) });
}

export async function deleteRelation(namespace: string, id: number): Promise<void> {
  await apiFetch(`${ns(namespace)}/relations/${id}`, { method: 'DELETE' });
}

export async function suggestRelationsAI(namespace: string, targetTables?: string[]): Promise<{ suggestions: Array<{ from_table: string; from_col: string; to_table: string; to_col: string; relation_type: string; reason: string }> }> {
  return apiFetch(`${ns(namespace)}/relations/suggest-ai`, {
    method: 'POST',
    body: JSON.stringify({ target_tables: targetTables ?? [] }),
  });
}

// ── Synonyms ──────────────────────────────────────────────────────────────────

export async function listSynonyms(namespace: string): Promise<SqlSynonym[]> {
  return apiFetch<SqlSynonym[]>(`${ns(namespace)}/synonyms`);
}

export async function createSynonym(namespace: string, data: Omit<SqlSynonym, 'id'>): Promise<{ id: number }> {
  return apiFetch(`${ns(namespace)}/synonyms`, { method: 'POST', body: JSON.stringify(data) });
}

export async function deleteSynonym(namespace: string, id: number): Promise<void> {
  await apiFetch(`${ns(namespace)}/synonyms/${id}`, { method: 'DELETE' });
}

export async function bulkDeleteSynonyms(namespace: string, ids: number[]): Promise<{ deleted: number }> {
  return apiFetch(`${ns(namespace)}/synonyms/bulk-delete`, { method: 'POST', body: JSON.stringify({ ids }) });
}

export async function reindexSynonyms(namespace: string): Promise<{ count: number }> {
  return apiFetch(`${ns(namespace)}/synonyms/reindex`, { method: 'POST' });
}

export async function generateSynonymsAI(namespace: string, targetTables?: string[]): Promise<{ generated: number; created: number; skipped_invalid: number }> {
  return apiFetch(`${ns(namespace)}/synonyms/generate-ai`, {
    method: 'POST',
    body: JSON.stringify({ target_tables: targetTables ?? [] }),
  });
}

// ── Fewshots ──────────────────────────────────────────────────────────────────

export async function listSqlFewshots(namespace: string, status = 'all'): Promise<SqlFewshot[]> {
  return apiFetch<SqlFewshot[]>(`${ns(namespace)}/fewshots?status=${status}`);
}

export async function createSqlFewshot(namespace: string, data: Omit<SqlFewshot, 'id' | 'hits'>): Promise<{ id: number }> {
  return apiFetch(`${ns(namespace)}/fewshots`, { method: 'POST', body: JSON.stringify(data) });
}

export async function createSqlFewshotFromFeedback(namespace: string, question: string, sql: string): Promise<{ id: number; skipped: boolean }> {
  return apiFetch(`${ns(namespace)}/fewshots/from-feedback`, { method: 'POST', body: JSON.stringify({ question, sql }) });
}

export async function updateSqlFewshotStatus(namespace: string, id: number, status: 'approved' | 'pending' | 'rejected'): Promise<void> {
  await apiFetch(`${ns(namespace)}/fewshots/${id}/status?status=${status}`, { method: 'PATCH' });
}

export async function deleteSqlFewshot(namespace: string, id: number): Promise<void> {
  await apiFetch(`${ns(namespace)}/fewshots/${id}`, { method: 'DELETE' });
}

export async function bulkDeleteFewshots(namespace: string, ids: number[]): Promise<{ deleted: number }> {
  return apiFetch(`${ns(namespace)}/fewshots/bulk-delete`, { method: 'POST', body: JSON.stringify({ ids }) });
}

export async function reindexFewshots(namespace: string): Promise<{ count: number }> {
  return apiFetch(`${ns(namespace)}/fewshots/reindex`, { method: 'POST' });
}

export async function generateFewshotsAI(namespace: string): Promise<{ generated: number; created: number; skipped_duplicates: number }> {
  return apiFetch(`${ns(namespace)}/fewshots/generate-ai`, { method: 'POST' });
}

// ── Pipeline ──────────────────────────────────────────────────────────────────

export async function listPipelineStages(): Promise<SqlPipelineStage[]> {
  return apiFetch<SqlPipelineStage[]>('/text2sql/pipeline');
}

export async function togglePipelineStage(id: string, isEnabled: boolean): Promise<void> {
  await apiFetch(`/text2sql/pipeline/${id}/toggle`, { method: 'PUT', body: JSON.stringify({ is_enabled: isEnabled }) });
}

// ── Audit Log ─────────────────────────────────────────────────────────────────

export async function listAuditLogs(
  namespace: string, page = 1, limit = 50,
  opts?: { status?: string; dateFrom?: string; dateTo?: string },
): Promise<{ items: SqlAuditLog[]; total: number }> {
  const params = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (opts?.status && opts.status !== 'all') params.set('status', opts.status);
  if (opts?.dateFrom) params.set('date_from', opts.dateFrom);
  if (opts?.dateTo) params.set('date_to', opts.dateTo);
  return apiFetch(`${ns(namespace)}/audit-logs?${params}`);
}

// ── Cache ─────────────────────────────────────────────────────────────────────

export async function listSqlCache(namespace: string): Promise<SqlCacheEntry[]> {
  return apiFetch<SqlCacheEntry[]>(`${ns(namespace)}/cache`);
}

export async function deleteSqlCacheEntry(namespace: string, id: number): Promise<void> {
  await apiFetch(`${ns(namespace)}/cache/${id}`, { method: 'DELETE' });
}

export async function clearSqlCache(namespace: string): Promise<{ deleted: number }> {
  return apiFetch(`${ns(namespace)}/cache`, { method: 'DELETE' });
}
