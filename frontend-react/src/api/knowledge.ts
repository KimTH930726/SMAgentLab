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
  LocateResult,
  LocationChoice,
  OverrideReason,
} from '../types';

// Knowledge CRUD

// 업무구분 키워드 전용 목록 API는 10/22 업무구분 폐지로 삭제

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
  content?: string,
): Promise<{ id: number; status: string; merged_into?: number }> {
  try {
    return await apiFetch(`/knowledge/${id}/resolve`, {
      method: 'POST',
      body: JSON.stringify({ action, target_id: targetId ?? null, content: content ?? null }),
    });
  } catch (err) {
    console.error('resolveDuplicate error:', err);
    throw err;
  }
}

/** 평가 게이트 "이상해요" — 정정 검토(개선 원장)에 검색 노이즈로 기록. query는 어떤 질의 결과였는지(담당자 맥락용) */
export async function flagKnowledgeForReview(
  knowledgeId: number, namespace: string, query?: string,
): Promise<{ status: string }> {
  try {
    return await apiFetch(`/knowledge/${knowledgeId}/flag-for-review`, {
      method: 'POST',
      body: JSON.stringify({ namespace, reason: 'search_noise', query: query ?? null }),
    });
  } catch (err) {
    console.error('flagKnowledgeForReview error:', err);
    throw err;
  }
}

/** 넣을 곳·종류 판정(E2) — AI 판단 원장에 한 줄 남기고 decision_id를 준다. 저장은 하지 않는다. */
// 이 화면에서 받은 위치 판단 id — 등록 화면이 판단을 미리 시작하면(10/28) 내용을 고칠 때마다 하나씩 생긴다.
// 등록할 때 이번에 안 쓴 것을 보내 원장에서 지운다(최종 판단만 남김, 서버는 확정 전·내 것만 지움). 못 보낸 것은 서버가 하루 뒤 정리.
const openDecisions = new Set<number>();
/** 보낼 목록(지우지 않음) — 판단 없이 등록하면(통계·VOC 화면의 등록) 서버가 지우지 않으니 보내지도 않는다 */
function discardsFor(keep: number | null | undefined): number[] {
  return keep == null ? [] : [...openDecisions].filter((id) => id !== keep).slice(-50);
}
/** 등록이 성공한 뒤에만 잊는다 — 실패하면 다시 시도할 때 또 보낸다(10/28 리뷰 L1) */
function forget(ids: number[], keep: number | null | undefined) {
  for (const id of ids) openDecisions.delete(id);
  if (keep != null) openDecisions.delete(keep);
}

export async function locateKnowledge(text: string, presetNamespace?: string | null): Promise<LocateResult> {
  const r = await apiFetch<LocateResult>('/knowledge/locate', {
    method: 'POST',
    body: JSON.stringify({ text, preset_namespace: presetNamespace ?? null }),
  });
  if (typeof r.decision_id === 'number') openDecisions.add(r.decision_id);
  return r;
}

export async function createKnowledge(payload: KnowledgeCreatePayload): Promise<KnowledgeItem> {
  try {
    const discards = discardsFor(payload.decision_id);
    const created = await apiFetch<KnowledgeItem>('/knowledge', {
      method: 'POST',
      body: JSON.stringify({ ...payload, discard_decision_ids: discards }),
    });
    forget(discards, payload.decision_id);
    return created;
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

/** 다른 표현 지우기 — 지운 표현은 AI가 다시 붙이지 않는다(v2.128) */
export async function deleteGlossarySynonym(id: number): Promise<void> {
  await apiFetch<void>(`/knowledge/glossary/synonyms/${id}`, { method: 'DELETE' });
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
  ids: number[], fields: { source_type?: string },
): Promise<{ updated: number }> {
  return apiFetch('/knowledge/bulk-update', { method: 'POST', body: JSON.stringify({ ids, ...fields }) });
}

export async function bulkDeleteGlossary(ids: number[]): Promise<{ deleted: number }> {
  return apiFetch('/knowledge/glossary/bulk-delete', { method: 'POST', body: JSON.stringify({ ids }) });
}

export async function vectorSearchKnowledge(namespace: string, query: string, topK = 30): Promise<(KnowledgeItem & { similarity: number })[]> {
  return apiFetch('/knowledge/search', { method: 'POST', body: JSON.stringify({ namespace, query, top_k: topK }) });
}

/** 지식 검색 화면(11/04) — 채팅과 같은 검색(용어집 + 벡터·키워드) 결과 카드 */
export interface ExploreItem {
  id: number;
  namespace: string;
  content: string;
  heading_path: string[];
  source_type: string | null;
  source_file: string | null;
  /** 관련도(가중치 뺀 벡터·키워드 결합 점수, 채팅의 근거 판정과 같은 값) */
  score: number;
  v_score: number;
  k_score: number;
  /** 채팅 답의 근거로 쓰인 횟수 */
  used_count: number;
  updated_at: string | null;
  created_at: string | null;
  created_by_username: string | null;
}

export async function exploreKnowledge(namespace: string, query: string, topK = 30): Promise<{ query: string; mapped_term: string | null; items: ExploreItem[] }> {
  return apiFetch('/knowledge/explore', { method: 'POST', body: JSON.stringify({ namespace, query, top_k: topK }) });
}

export async function vectorSearchGlossary(namespace: string, query: string, topK = 30): Promise<(GlossaryItem & { similarity: number })[]> {
  return apiFetch('/knowledge/glossary/search', { method: 'POST', body: JSON.stringify({ namespace, query, top_k: topK }) });
}

// ─── Bulk / Ingestion ───────────────────────────────────────────────────────

export async function bulkCreateKnowledge(
  namespace: string,
  items: Array<{
    content: string; heading_path?: string[] | null; source_document_id?: number | null; source_document_partial?: boolean;
    confluence_page_id?: string | null; confluence_version?: number | null;
  }>,
  sourceFile?: string,
  sourceType = 'manual',
  confirmReplace = false,
  location?: { decisionId: number | null; choice: LocationChoice; overrideReason?: OverrideReason | null } | null,
): Promise<{ created: number; job_id: number; status: string; unlinked_sources?: number[] }> {
  const discards = discardsFor(location?.decisionId);
  const res = await apiFetch<{ created: number; job_id: number; status: string; unlinked_sources?: number[] }>('/knowledge/bulk', {
    method: 'POST',
    body: JSON.stringify({ namespace, items, source_file: sourceFile, source_type: sourceType, confirm_replace: confirmReplace,
      decision_id: location?.decisionId ?? null, location_choice: location?.choice ?? null,
      override_reason: location?.overrideReason ?? null, discard_decision_ids: discards }),
  });
  forget(discards, location?.decisionId);
  return res;
}

/** 같은 원본 재등록 확인(A안 2026-10-12) — 교체될 기존 조각에 사람이 손댄 흔적. */
export interface ReplaceCheck {
  needs_confirm: boolean;
  edited: number;          // 내용을 고친 조각
  removed: number;         // 삭제·반려한 조각
  partial: boolean;        // 일부 조각만 골라 등록했음
  unlinked_sources: number[];
}

export async function checkBulkReplace(namespace: string, sourceDocumentIds: number[], confluencePageIds: string[]): Promise<ReplaceCheck> {
  return apiFetch('/knowledge/bulk/replace-check', {
    method: 'POST',
    body: JSON.stringify({ namespace, source_document_ids: sourceDocumentIds, confluence_page_ids: confluencePageIds }),
  });
}

// 지식의 문서 안 위치(2026-10-07) — 분할 방식별(섹션 구조/문서 순서/단건)
export interface KnowledgeBrief { id: number; title: string; preview: string; truncated?: boolean; heading_path: string[] }
export interface KnowledgeStructure {
  id: number;
  namespace: string;
  mode: 'section' | 'sequence' | 'single';
  heading_path: string[];
  source_file: string | null;
  source_type: string | null;
  position: number | null;
  total: number | null;
  prev: KnowledgeBrief | null;
  next: KnowledgeBrief | null;
  expansion: KnowledgeBrief[];
  outline?: KnowledgeOutlineItem[];
  source_document?: SourceDocumentInfo | null;
}

export interface SourceDocumentInfo {
  id: number; title: string | null; status: string; raw_format: string; parser_version: string | null; chars: number; created_at: string;
}

export interface RechunkResult {
  changed: boolean; blocked?: boolean; edited?: number; removed?: number; partial?: boolean;
  old: number; new: number; job_id?: number; status?: string;
  quality?: { warnings: string[] } | null;
}

export async function rechunkSourceDocument(docId: number, force = false): Promise<RechunkResult> {
  return apiFetch(`/knowledge/source-documents/${docId}/rechunk${force ? '?force=true' : ''}`, { method: 'POST' });
}

export interface KnowledgeOutlineItem { id: number; title: string; depth: number; is_self: boolean; attached: boolean; preview: string; truncated: boolean }

export async function getKnowledgeStructure(id: number): Promise<KnowledgeStructure> {
  return apiFetch(`/knowledge/${id}/structure`);
}

export async function previewTextSplit(
  rawText: string,
  strategy = 'auto',
  namespace?: string,
): Promise<{ chunks: string[]; heading_paths?: string[][]; count: number; detected_strategy: string; source_document_id?: number | null }> {
  // namespace: 원문 원장 초안 저장(2026-10-08) — 확정 때 source_document_id를 청크에 실어 보낸다
  return apiFetch('/knowledge/import/text-split/preview', {
    method: 'POST',
    body: JSON.stringify({ raw_text: rawText, strategy, namespace }),
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
  /** 같은 글이 이미 있어 저장 안 한 조각 수 · 그 기존 지식 id(11/04) */
  skipped_exact_chunks?: number;
  skipped_exact_ids?: number[];
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
  /** 저장된 청크 품질 검사(2026-10-07) — 완료 후 채워짐 */
  quality?: { chunks: number; adjacent_overlap: number; trailing_heading: number; heading_only: number;
              with_heading_path: number; warnings: string[] } | null;
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
  analyzer: Record<string, unknown> | null;
  source_name: string;
  page_count: number | null;
}

export interface FilePreviewResult {
  source_name: string;
  source_type: string;
  page_count: number | null;
  total_chars: number;
  sections: number;
  tables: number;
  chunks: Array<{ idx: number; text: string; title: string | null; heading_path?: string[] }>;
  source_document_id?: number | null;
  chunk_count: number;
  detected_strategy: string;
}

export async function previewFileUpload(
  file: File,
  strategy?: string,
  namespace?: string,
): Promise<FilePreviewResult> {
  const form = new FormData();
  form.append('file', file);
  if (strategy) form.append('strategy', strategy);
  if (namespace) form.append('namespace', namespace);   // 원문 원장(2026-10-08)
  return apiFetch('/knowledge/import/file/preview', { method: 'POST', body: form });
}

// ─── URL / Confluence 인제스천 ───────────────────────────────────────────────

export interface UrlImportResult {
  created: number;
  job_id: number | null;
  chunks: number;
  source_name: string;
  source_type: string;
  url: string;
}

export interface UrlPreviewResult {
  source_name: string;
  source_type: string;
  total_chars: number;
  sections: number;
  chunks: Array<{ idx: number; text: string; title: string | null; heading_path?: string[] }>;
  source_document_id?: number | null;
  chunk_count: number;
  url: string;
  confluence_page_id: string | null;
  confluence_version: number | null;
  structure: { heading_sections: number; structured: boolean };
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

export interface ConfluenceBulkChunk {
  idx: number;
  page_id: string;
  page_title: string;
  text: string;
  title: string | null;
  heading_path: string[] | null;
  confluence_page_id: string | null;
  confluence_version: number | null;
  /** 원문 원장 id(2026-10-08) — 페이지별 원문 */
  source_document_id?: number | null;
}

export interface ConfluenceBulkPreviewResult {
  chunks: ConfluenceBulkChunk[];
  chunk_count: number;
  pages: Array<{
    page_id: string; title: string; chunk_start: number; chunk_count: number;
    /** 헤딩(h1~h4)으로 나뉜 섹션 수 — 2개 미만이면 구조 없음(섹션 경계·부모 확장이 약해짐) */
    heading_sections: number; structured: boolean;
  }>;
  failed_pages: Array<{ page_id: string; title: string; error: string; restricted?: 'page' | 'ancestor' | 'unknown'; held_chunks?: number }>;   // restricted = 읽기 제한이라 건너뜀(기록만)
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
