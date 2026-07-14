import { apiFetch } from './client';
import type {
  KnowledgeItem,
  KnowledgeCreatePayload,
  KnowledgeUpdatePayload,
  KnowledgeStatus,
  DuplicateMatch,
  GlossaryItem,
  GlossaryCreatePayload,
  GlossaryUpdatePayload,
} from '../types';

// Knowledge CRUD

export async function getKnowledge(namespace: string, status?: KnowledgeStatus): Promise<KnowledgeItem[]> {
  try {
    const params = new URLSearchParams({ namespace });
    if (status) params.set('status', status);
    return await apiFetch<KnowledgeItem[]>(`/knowledge?${params.toString()}`);
  } catch (err) {
    console.error('getKnowledge error:', err);
    throw err;
  }
}

export async function getDuplicateMatches(id: number): Promise<DuplicateMatch[]> {
  try {
    return await apiFetch<DuplicateMatch[]>(`/knowledge/${id}/duplicate-matches`);
  } catch (err) {
    console.error('getDuplicateMatches error:', err);
    throw err;
  }
}

export async function resolveDuplicate(
  id: number,
  action: 'approve' | 'reject' | 'merge',
  targetId?: number,
): Promise<{ id: number; status: string; merged_into?: number }> {
  try {
    return await apiFetch(`/knowledge/${id}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ action, target_id: targetId ?? null }),
    });
  } catch (err) {
    console.error('resolveDuplicate error:', err);
    throw err;
  }
}

export async function createKnowledge(payload: KnowledgeCreatePayload): Promise<KnowledgeItem> {
  try {
    return await apiFetch<KnowledgeItem>('/knowledge', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error('createKnowledge error:', err);
    throw err;
  }
}

export async function updateKnowledge(id: number, payload: KnowledgeUpdatePayload): Promise<KnowledgeItem> {
  try {
    return await apiFetch<KnowledgeItem>(`/knowledge/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error('updateKnowledge error:', err);
    throw err;
  }
}

export async function deleteKnowledge(id: number): Promise<void> {
  try {
    await apiFetch<void>(`/knowledge/${id}`, { method: 'DELETE' });
  } catch (err) {
    console.error('deleteKnowledge error:', err);
    throw err;
  }
}

// Glossary CRUD

export async function getGlossary(namespace: string): Promise<GlossaryItem[]> {
  try {
    return await apiFetch<GlossaryItem[]>(`/knowledge/glossary?namespace=${encodeURIComponent(namespace)}`);
  } catch (err) {
    console.error('getGlossary error:', err);
    throw err;
  }
}

export async function createGlossaryItem(payload: GlossaryCreatePayload): Promise<GlossaryItem> {
  try {
    return await apiFetch<GlossaryItem>('/knowledge/glossary', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error('createGlossaryItem error:', err);
    throw err;
  }
}

export async function updateGlossaryItem(id: number, payload: GlossaryUpdatePayload): Promise<GlossaryItem> {
  try {
    return await apiFetch<GlossaryItem>(`/knowledge/glossary/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error('updateGlossaryItem error:', err);
    throw err;
  }
}

export async function deleteGlossaryItem(id: number): Promise<void> {
  try {
    await apiFetch<void>(`/knowledge/glossary/${id}`, { method: 'DELETE' });
  } catch (err) {
    console.error('deleteGlossaryItem error:', err);
    throw err;
  }
}

export async function bulkDeleteKnowledge(ids: number[]): Promise<{ deleted: number }> {
  return apiFetch('/knowledge/bulk-delete', { method: 'POST', body: JSON.stringify({ ids }) });
}

export async function bulkUpdateKnowledge(
  ids: number[], fields: { category?: string; source_type?: string },
): Promise<{ updated: number }> {
  return apiFetch('/knowledge/bulk-update', { method: 'POST', body: JSON.stringify({ ids, ...fields }) });
}

export async function bulkDeleteGlossary(ids: number[]): Promise<{ deleted: number }> {
  return apiFetch('/knowledge/glossary/bulk-delete', { method: 'POST', body: JSON.stringify({ ids }) });
}

export async function vectorSearchKnowledge(namespace: string, query: string, topK = 30): Promise<(KnowledgeItem & { similarity: number })[]> {
  return apiFetch('/knowledge/search', { method: 'POST', body: JSON.stringify({ namespace, query, top_k: topK }) });
}

export async function vectorSearchGlossary(namespace: string, query: string, topK = 30): Promise<(GlossaryItem & { similarity: number })[]> {
  return apiFetch('/knowledge/glossary/search', { method: 'POST', body: JSON.stringify({ namespace, query, top_k: topK }) });
}

// ─── Bulk / Ingestion ───────────────────────────────────────────────────────

export async function bulkCreateKnowledge(
  namespace: string,
  items: Array<{ content: string; category?: string; container_name?: string; target_tables?: string[]; query_template?: string }>,
  sourceFile?: string,
  sourceType = 'manual',
): Promise<{ created: number; job_id: number; status: string }> {
  return apiFetch('/knowledge/bulk', {
    method: 'POST',
    body: JSON.stringify({ namespace, items, source_file: sourceFile, source_type: sourceType }),
  });
}

export async function importCsv(
  file: File,
  namespace: string,
  columnMapping: Record<string, string>,
  category?: string,
): Promise<{ created: number; job_id: number | null }> {
  const form = new FormData();
  form.append('file', file);
  form.append('namespace', namespace);
  form.append('column_mapping', JSON.stringify(columnMapping));
  if (category) form.append('category', category);
  return apiFetch('/knowledge/import/csv', { method: 'POST', body: form });
}

export async function importTextSplit(
  namespace: string,
  rawText: string,
  strategy = 'auto',
  category?: string,
): Promise<{ created: number; job_id: number | null; chunks: number }> {
  return apiFetch('/knowledge/import/text-split', {
    method: 'POST',
    body: JSON.stringify({ namespace, raw_text: rawText, strategy, category }),
  });
}

export async function previewTextSplit(
  rawText: string,
  strategy = 'auto',
): Promise<{ chunks: string[]; count: number }> {
  return apiFetch('/knowledge/import/text-split/preview', {
    method: 'POST',
    body: JSON.stringify({ raw_text: rawText, strategy }),
  });
}

export interface IngestionJob {
  id: number;
  namespace_id: number;
  source_file: string | null;
  source_type: string | null;
  status: string;
  total_chunks: number;
  created_chunks: number;
  pending_chunks: number;
  auto_glossary: number;
  auto_fewshot: number;
  chunk_strategy: string | null;
  error_message: string | null;
  created_by_user_id: number | null;
  created_by_username: string | null;
  created_at: string;
  completed_at: string | null;
}

export async function getIngestionJobs(namespace: string): Promise<IngestionJob[]> {
  return apiFetch<IngestionJob[]>(`/knowledge/ingestion-jobs?namespace=${encodeURIComponent(namespace)}`);
}

export interface IngestionJobStatus {
  id: number;
  namespace_id: number;
  source_file: string | null;
  source_type: string | null;
  status: string;
  total_chunks: number;
  created_chunks: number;
  pending_chunks: number;
  cancel_requested: boolean;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export async function getIngestionJobStatus(jobId: number): Promise<IngestionJobStatus> {
  return apiFetch<IngestionJobStatus>(`/knowledge/ingestion-jobs/${jobId}`);
}

export async function cancelIngestionJob(jobId: number): Promise<{ id: number; status: string }> {
  return apiFetch(`/knowledge/ingestion-jobs/${jobId}/cancel`, { method: 'POST' });
}

// ─── File Upload (Tier 2) ───────────────────────────────────────────────────

export interface FileUploadResult {
  created: number;
  job_id: number | null;
  chunks: number;
  auto_glossary: number;
  auto_fewshot: number;
  analyzer: Record<string, unknown> | null;
  source_name: string;
  page_count: number | null;
}

export async function importFile(
  file: File,
  namespace: string,
  opts?: { chunkStrategy?: string; category?: string; autoAnalyze?: boolean; autoTag?: boolean; autoGlossary?: boolean; autoFewshot?: boolean },
): Promise<FileUploadResult> {
  const form = new FormData();
  form.append('file', file);
  form.append('namespace', namespace);
  form.append('chunk_strategy', opts?.chunkStrategy ?? 'auto');
  if (opts?.category) form.append('category', opts.category);
  if (opts?.autoAnalyze) form.append('auto_analyze', 'true');
  if (opts?.autoTag) form.append('auto_tag', 'true');
  if (opts?.autoGlossary) form.append('auto_glossary', 'true');
  if (opts?.autoFewshot) form.append('auto_fewshot', 'true');
  return apiFetch('/knowledge/import/file', { method: 'POST', body: form });
}

export interface FilePreviewResult {
  source_name: string;
  source_type: string;
  page_count: number | null;
  total_chars: number;
  sections: number;
  tables: number;
  chunks: Array<{ idx: number; text: string; title: string | null }>;
  chunk_count: number;
}

export async function previewFileUpload(
  file: File,
): Promise<FilePreviewResult> {
  const form = new FormData();
  form.append('file', file);
  return apiFetch('/knowledge/import/file/preview', { method: 'POST', body: form });
}

// ─── URL / Confluence 인제스천 ───────────────────────────────────────────────

export interface UrlImportResult {
  created: number;
  job_id: number | null;
  chunks: number;
  auto_glossary: number;
  source_name: string;
  source_type: string;
  url: string;
}

export interface UrlPreviewResult {
  source_name: string;
  source_type: string;
  total_chars: number;
  sections: number;
  chunks: Array<{ idx: number; text: string; title: string | null }>;
  chunk_count: number;
  url: string;
}

export async function importFromUrl(
  namespace: string,
  url: string,
  opts?: { confluenceToken?: string; chunkStrategy?: string; category?: string; autoTag?: boolean; autoGlossary?: boolean },
): Promise<UrlImportResult> {
  return apiFetch('/knowledge/import/url', {
    method: 'POST',
    body: JSON.stringify({
      namespace,
      url,
      confluence_token: opts?.confluenceToken || null,
      chunk_strategy: opts?.chunkStrategy ?? 'auto',
      category: opts?.category || null,
      auto_tag: opts?.autoTag ?? false,
      auto_glossary: opts?.autoGlossary ?? false,
    }),
  });
}

export async function previewUrl(
  namespace: string,
  url: string,
): Promise<UrlPreviewResult> {
  return apiFetch('/knowledge/import/url/preview', {
    method: 'POST',
    body: JSON.stringify({ namespace, url }),
  });
}

// ─── Confluence 트리 + 일괄 인제스천 ───────────────────────────────────────

export interface ConfluenceTreeNode {
  page_id: string;
  title: string;
  url: string;
  depth: number;
  parent_id: string | null;
}

export interface ConfluenceTreeResponse {
  root: ConfluenceTreeNode;
  tree: ConfluenceTreeNode[];
  truncated: boolean;
  max_depth_reached: boolean;
  max_depth: number;
  max_pages: number;
}

export async function previewConfluenceTree(
  url: string,
  opts?: { confluenceToken?: string; maxDepth?: number; maxPages?: number },
): Promise<ConfluenceTreeResponse> {
  return apiFetch('/knowledge/import/url/tree', {
    method: 'POST',
    body: JSON.stringify({
      url,
      confluence_token: opts?.confluenceToken || null,
      max_depth: opts?.maxDepth ?? 3,
      max_pages: opts?.maxPages ?? 100,
    }),
  });
}

export interface ConfluenceBulkResult {
  created: number;
  job_id: number | null;
  chunks: number;
  pages_succeeded: number;
  pages_failed: number;
  failed_pages: Array<{ page_id: string; error: string }>;
  page_summaries: Array<{ page_id: string; title: string; chunks: number; chars: number }>;
  auto_glossary: number;
  source_name: string;
  source_type: string;
}

export async function importConfluenceBulk(
  namespace: string,
  baseUrl: string,
  pages: Array<{ page_id: string; title?: string; url?: string }>,
  opts?: { confluenceToken?: string; chunkStrategy?: string; category?: string; autoTag?: boolean; autoGlossary?: boolean },
): Promise<ConfluenceBulkResult> {
  return apiFetch('/knowledge/import/url/bulk-pages', {
    method: 'POST',
    body: JSON.stringify({
      namespace,
      base_url: baseUrl,
      pages,
      confluence_token: opts?.confluenceToken || null,
      chunk_strategy: opts?.chunkStrategy ?? 'auto',
      category: opts?.category || null,
      auto_tag: opts?.autoTag ?? false,
      auto_glossary: opts?.autoGlossary ?? false,
    }),
  });
}

export interface ConfluenceBulkChunk {
  idx: number;
  page_id: string;
  page_title: string;
  text: string;
  title: string | null;
}

export interface ConfluenceBulkPreviewResult {
  chunks: ConfluenceBulkChunk[];
  chunk_count: number;
  pages: Array<{ page_id: string; title: string; chunk_start: number; chunk_count: number }>;
  failed_pages: Array<{ page_id: string; title: string; error: string }>;
}

export async function previewConfluenceBulk(
  namespace: string,
  baseUrl: string,
  pages: Array<{ page_id: string; title?: string; url?: string }>,
  opts?: { confluenceToken?: string; chunkStrategy?: string },
): Promise<ConfluenceBulkPreviewResult> {
  return apiFetch('/knowledge/import/url/bulk-pages/preview', {
    method: 'POST',
    body: JSON.stringify({
      namespace,
      base_url: baseUrl,
      pages,
      confluence_token: opts?.confluenceToken || null,
      chunk_strategy: opts?.chunkStrategy ?? 'auto',
    }),
  });
}

// Glossary AI Suggestions

export type GlossarySuggestSource = 'questions' | 'knowledge';

export async function suggestGlossaryTerms(
  namespace: string,
  limit: number = 50,
  source: GlossarySuggestSource = 'questions',
): Promise<{ suggestions: Array<{ term: string; description: string }>; message: string }> {
  return apiFetch(`/admin/glossary/suggest?namespace=${encodeURIComponent(namespace)}&limit=${limit}&source=${source}`, { method: 'POST' });
}

export async function applyGlossarySuggestion(namespace: string, term: string, description: string): Promise<void> {
  return apiFetch('/admin/glossary/suggest/apply', {
    method: 'POST',
    body: JSON.stringify({ namespace, term, description }),
  });
}
