import { apiFetch } from './client';

/** 팀 적재(컨플 수집기, #98) — 수집 대상 → 분류 미리보기 → 분류 확정·적재 → 롤백. 설계 confluence-curation-layer-design §12. */

export type BatchStatus = 'previewing' | 'preview' | 'failed' | 'loading' | 'loaded' | 'rolled_back';
export type PageStatus = 'queued' | 'auto' | 'review' | 'existing' | 'excluded' | 'held' | 'restricted' | 'empty' | 'failed' | 'loaded';
export type ReviewReason = 'low_confidence' | 'unowned' | 'not_writable' | 'ai_failed';

export interface ImportBatchRow {
  id: number;
  team_id: number;
  team_name: string | null;
  root_url: string;
  root_title: string | null;
  status: BatchStatus;
  total_pages: number;
  done_pages: number;
  created_at: string;
}

export interface ImportBatch extends ImportBatchRow {
  sampling_required: boolean;
  truncated: boolean;
  error: string | null;
  job_ids: number[];
  created_by_user_id: number | null;
  load_total: number;
  load_done: number;
}

export interface ImportPage {
  id: number;
  page_id: string;
  title: string | null;
  bundle: string | null;
  depth: number;
  status: PageStatus;
  prev_status: PageStatus | null;
  reason: ReviewReason | null;
  chunk_count: number;
  ai_namespace_id: number | null;
  proposed_namespace_id: number | null;
  confident: boolean;
  final_namespace_id: number | null;
  sample_state: 'pending' | 'ok' | 'wrong' | null;
  job_id: number | null;
  signals: { neighbors: Record<string, number>; reason?: string; fallback?: string; error?: string; restricted?: string; doc_type?: string };
}

export interface ImportCollection {
  id: number;
  label: string;
  kind: 'domain' | 'org_space';
  unowned: boolean;
  writable: boolean;     // 이 사람이 넣을 수 있나(담당 규칙) — 확정 때 서버가 다시 본다
  parts: (string | null)[];
}

export interface ImportBatchDetail {
  batch: ImportBatch;
  pages: ImportPage[];
  collections: ImportCollection[];
  blockers: string[];
  sample_targets: Record<string, number>;   // 컬렉션(AI가 고른 곳)별 표본 목표 — 정정이 나오면 2배
  sample_size: number;
  reasons: Record<ReviewReason, string>;
}

const BASE = '/knowledge/team-import';

export const listImportBatches = () => apiFetch<ImportBatchRow[]>(BASE);
export const getImportBatch = (id: number) => apiFetch<ImportBatchDetail>(`${BASE}/${id}`);
export const startImport = (url: string, teamId?: number | null) =>
  apiFetch<{ id: number }>(BASE, { method: 'POST', body: JSON.stringify({ url, team_id: teamId ?? null }) });
export const updateImportPages = (id: number, ids: number[], action: 'classify' | 'hold' | 'exclude' | 'include', namespaceId?: number) =>
  apiFetch<{ updated: number }>(`${BASE}/${id}/pages`, { method: 'POST', body: JSON.stringify({ ids, action, namespace_id: namespaceId ?? null }) });
export const reviewImportSample = (id: number, pageRowId: number, ok: boolean, namespaceId?: number | null) =>
  apiFetch<{ added: number }>(`${BASE}/${id}/samples`, { method: 'POST', body: JSON.stringify({ id: pageRowId, ok, namespace_id: namespaceId ?? null }) });
export const confirmImport = (id: number) => apiFetch<{ status: string }>(`${BASE}/${id}/confirm`, { method: 'POST' });
export const rollbackImport = (id: number) => apiFetch<{ chunks: number; documents: number; terms: number }>(`${BASE}/${id}/rollback`, { method: 'POST' });

/** 원본 페이지 주소 — 수집 대상 주소의 호스트 + pageId(검토할 때 원문을 바로 연다). */
export const confluencePageUrl = (rootUrl: string, pageId: string) => {
  try {
    return `${new URL(rootUrl).origin}/pages/viewpage.action?pageId=${encodeURIComponent(pageId)}`;
  } catch {
    return null;
  }
};
