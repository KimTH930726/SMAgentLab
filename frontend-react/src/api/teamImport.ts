import { apiFetch } from './client';

/** 팀 적재(컨플 수집기, #98) — 수집 대상 → 분류 미리보기 → 분류 확정·적재 → 롤백. 설계 confluence-curation-layer-design §12. */

export type BatchStatus = 'previewing' | 'preview' | 'failed' | 'loading' | 'loaded' | 'rolled_back';
export type PageStatus = 'queued' | 'auto' | 'review' | 'existing' | 'excluded' | 'held' | 'restricted' | 'empty' | 'failed' | 'loaded';
export type ReviewReason = 'low_confidence' | 'unowned' | 'not_writable' | 'ai_failed' | 'default_conflict';

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
  todo: number;           // 사람이 할 일(미리보기 중: 분류 안 한 검토 필요 + 안 본 표본)
}

export interface ImportBatch extends ImportBatchRow {
  sampling_required: boolean;
  truncated: boolean;
  error: string | null;
  job_ids: number[];
  created_by_user_id: number | null;
  load_total: number;
  load_done: number;
  default_namespace_id: number | null;   // 공간 기본 위치(사람이 한 번) — 없으면 AI가 페이지마다
}

export interface ImportPage {
  id: number;
  page_id: string;
  title: string | null;
  bundle: string | null;
  path: string | null;    // 수집 대상 아래 상위 페이지 제목들(" › ") — 화면 판단용, AI 입력엔 안 씀
  depth: number;
  status: PageStatus;
  prev_status: PageStatus | null;
  reason: ReviewReason | null;
  chunk_count: number;
  ai_namespace_id: number | null;
  proposed_namespace_id: number | null;
  confident: boolean;
  final_namespace_id: number | null;
  assigned_by: 'ai' | 'space_default' | null;   // 자동으로 정해진 곳을 누가 정했나 — 공간 기본값은 사람 결정
  sample_ns: number | null;
  sample_state: 'pending' | 'ok' | 'wrong' | null;
  job_id: number | null;
  signals: { neighbors: Record<string, number>; reason?: string; fallback?: string; error?: string; restricted?: string; doc_type?: string };
}

export interface ImportCollection {
  id: number;
  label: string;
  kind: string;                    // domain(정책·시스템)
  area?: 'policy' | 'system';    // 없으면 policy(옛 서버)
  unowned: boolean;
  writable: boolean;     // 이 사람이 넣을 수 있나(담당 규칙) — 확정 때 서버가 다시 본다
  parts: (string | null)[];
}

export interface ImportBatchDetail {
  batch: ImportBatch;
  pages: ImportPage[];
  collections: ImportCollection[];
  blockers: string[];
  sample_targets: Record<string, number>;   // 표본 묶음("ai:<id>" · "space_default:<id>")별 목표 — 정정이 나오면 2배
  sample_size: number;
  reasons: Record<ReviewReason, string>;
}

const BASE = '/knowledge/team-import';

export const listImportBatches = () => apiFetch<ImportBatchRow[]>(BASE);
export const getImportBatch = (id: number) => apiFetch<ImportBatchDetail>(`${BASE}/${id}`);
export const startImport = (url: string, teamId?: number | null, defaultNs?: number | null) =>
  apiFetch<{ id: number }>(BASE, { method: 'POST', body: JSON.stringify({ url, team_id: teamId ?? null, default_namespace_id: defaultNs ?? null }) });
export const getImportCollections = (teamId?: number | null) =>
  apiFetch<ImportCollection[]>(`${BASE}/collections${teamId ? `?team_id=${teamId}` : ''}`);
export const setImportDefault = (id: number, namespaceId: number | null) =>
  apiFetch<Record<string, number>>(`${BASE}/${id}/default`, { method: 'POST', body: JSON.stringify({ namespace_id: namespaceId }) });
/** 표본 묶음 키 — 서버 team_import.sample_key와 같은 규칙 */
export const sampleKey = (p: ImportPage) => {
  const ns = p.sample_ns ?? p.ai_namespace_id;
  return ns == null ? null : `${p.assigned_by ?? 'ai'}:${ns}`;
};
export const updateImportPages = (id: number, ids: number[], action: 'classify' | 'hold' | 'exclude' | 'include', namespaceId?: number) =>
  apiFetch<{ updated: number }>(`${BASE}/${id}/pages`, { method: 'POST', body: JSON.stringify({ ids, action, namespace_id: namespaceId ?? null }) });
export const reviewImportSample = (id: number, pageRowId: number, ok: boolean, namespaceId?: number | null) =>
  apiFetch<{ added: number }>(`${BASE}/${id}/samples`, { method: 'POST', body: JSON.stringify({ id: pageRowId, ok, namespace_id: namespaceId ?? null }) });
export const confirmImport = (id: number) => apiFetch<{ status: string }>(`${BASE}/${id}/confirm`, { method: 'POST' });
/** 묶음 단위 연결(10/30) — 묶음마다 컬렉션(null = 제외) + 하위 갈래 예외. 규칙 저장 + 전 페이지 정함(적재는 이어서 confirmImport) */
export const connectImport = (id: number, bundles: { bundle: string; namespace_id: number | null; groups: { name: string; namespace_id: number | null }[] }[]) =>
  apiFetch<{ status: string; pages: number; excluded: number }>(`${BASE}/${id}/connect`, { method: 'POST', body: JSON.stringify({ bundles }) });
export const rollbackImport = (id: number) => apiFetch<{ chunks: number; documents: number; terms: number }>(`${BASE}/${id}/rollback`, { method: 'POST' });

/** 원본 페이지 주소 — 수집 대상 주소의 호스트 + pageId(검토할 때 원문을 바로 연다). */
export const confluencePageUrl = (rootUrl: string, pageId: string) => {
  try {
    return `${new URL(rootUrl).origin}/pages/viewpage.action?pageId=${encodeURIComponent(pageId)}`;
  } catch {
    return null;
  }
};

/** 연결된 공간(10/30 컨플 동기화) — 하루 1회 증분 · 주 1회 전체 대조. 담당 = 토큰 주인 */
export interface SourceConnection {
  id: number; team_id: number; team_name: string | null; root_url: string; title: string | null;
  status: 'ok' | 'running' | 'error' | 'stopped'; error: string | null; owner: string | null;
  last_incremental_at: string | null; last_full_at: string | null; last_run_at: string | null;
  last_result: {
    mode: 'incremental' | 'full'; changed: number; updated: number; added: number; unchanged: number; restricted: number; held: number;
    conflict: number; no_rule: number; excluded: number; missing: number; failed: number; incomplete: boolean;
    elsewhere: number; denied: number; too_many_missing: boolean;
  } | null;
  /** 다음 동기화에 다시 시도할 페이지 — 충돌(사람이 고친 조각)·실패 */
  retry_pages: { page_id: string; title: string | null; why: 'conflict' | 'failed' }[];
}
export const listConnections = () => apiFetch<SourceConnection[]>(`${BASE}/connections`);
export const syncConnection = (id: number, mode: 'incremental' | 'full') =>
  apiFetch<{ status: string }>(`${BASE}/connections/${id}/sync`, { method: 'POST', body: JSON.stringify({ mode }) });
