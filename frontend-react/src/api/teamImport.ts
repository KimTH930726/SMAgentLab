import { apiFetch } from './client';

/** 팀 적재(컨플 수집기, #98) — 수집 대상 → 분류 미리보기 → 분류 확정·적재 → 롤백. 설계 confluence-curation-layer-design §12. */

export type BatchStatus = 'previewing' | 'preview' | 'failed' | 'loading' | 'loaded' | 'rolled_back' | 'cancelled';
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
  /** 게시 시각 — 비어 있고 적재됨이면 "검수 대기"(채팅에 아직 안 나옴, 11/03) */
  published_at?: string | null;
  /** 게시 중(11/05) — 뒤에서 도는 동안 세워져 있다 */
  publishing_at?: string | null;
  publish_check?: PublishCheck | null;
  /** 읽기 제한 페이지도 넣기(관리자, 11/05) — 켠 시각·사람 */
  include_restricted_at?: string | null;
  include_restricted_name?: string | null;
}

/** 게시 전 검색 확인 결과(로컬 LLM 질문 → 벡터 상위 3에 그 페이지가 드나) */
export interface PublishCheck {
  checked_at: string;
  /** 도는 중(11/05) — total = 만들 질문 수, asked = 지금까지. interrupted = 재시작으로 끊김 */
  running?: boolean;
  total?: number;
  interrupted?: boolean;
  /** 중간에 멈춤(임베딩·DB 오류 등) — 센 데까지만 저장됨 */
  error?: string;
  skipped?: string;
  asked?: number;
  found?: number;
  failed?: number;
  misses?: { question: string; title: string | null }[];
  /** 질문마다(리포트 창, 11/05) — 옛 결과엔 없음 */
  items?: { question: string; title: string | null; page_id: string | null; found: boolean; rank: number | null;
    /** 벡터 상위 3에 실제로 나온 것(못 찾았을 때 "대신 나온 것") — 11/05 뒤 결과만 */
    got?: { title: string | null; page_id: string | null; sim: number; same: boolean }[] }[];
}

/** 검수 대기 카드 — 보류 작업 · 조각 · 중복 의심 · 품질 경고(종류별 작업 수) */
/** warnings = 종류 → 페이지 수 · warning_places = 종류 → 모두 몇 곳 · warning_items = 페이지별(리포트 창, 11/05) */
export interface HeldSummary { jobs: number; chunks: number; dup: number; warnings: Record<string, number>;
  warning_places?: Record<string, number>; warning_items?: { kind: string; n: number; title: string | null; page_id: string | null; job_id: number }[];
  /** 이웃 겹침(11/05) — defect = 원문보다 많이 나온 줄이 있는 쌍 · source_repeat = 원문이 원래 같은 줄을 반복 · unchecked_jobs = 원문 대조 못 한 작업 */
  overlap?: { defect: number; source_repeat: number; unchecked_jobs: number; types: Record<string, number> };
  overlap_items?: { job_id: number; title: string | null; page_id: string | null; defect: number; source_repeat: number; checked: boolean }[] }

/** 한 페이지 이웃 겹침 상세 — a·b = 조각 번호(1부터), lines.source = 원문 등장 수(null = 대조 못 함) */
export interface OverlapDetail { checked: boolean; chunks: number;
  pairs: { a: number; b: number; a_id: number; b_id: number; defect: boolean;
    lines: { h: string; t: 'table' | 'text'; extra: boolean; chunks: number; source: number | null; text: string | null }[] }[] }

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
  signals: { neighbors: Record<string, number>; reason?: string; fallback?: string; error?: string; restricted?: string; doc_type?: string;
    /** 원래 읽기 제한(page/ancestor) — 관리자가 포함해 넣은 페이지 */
    restricted_included?: string };
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

/** 게시 중 진행(11/05) — done/total = 작업(페이지), phase glossary = 작업은 끝나고 자동 용어 추출 중 */
export interface PublishProgress { done: number; total: number; phase: 'jobs' | 'glossary'; started_at: string }

export interface ImportBatchDetail {
  batch: ImportBatch;
  held: HeldSummary | null;
  publish_progress?: PublishProgress | null;
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
/** includeRestricted — 읽기 제한 페이지도 적재(관리자만, 11/05). 누가·언제 켰는지 묶음에 남는다 */
export const startImport = (url: string, teamId?: number | null, defaultNs?: number | null, includeRestricted = false) =>
  apiFetch<{ id: number }>(BASE, { method: 'POST', body: JSON.stringify({ url, team_id: teamId ?? null, default_namespace_id: defaultNs ?? null,
    include_restricted: includeRestricted }) });
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
export const getOverlapDetail = (id: number, jobId: number) => apiFetch<OverlapDetail>(`${BASE}/${id}/overlap/${jobId}`);
/** 둘 다 뒤에서 돈다(11/05) — 바로 돌아오고, 진행은 배치 상세(publish_check.running · publish_progress)로 */
export const publishCheckImport = (id: number) => apiFetch<{ status: 'running' }>(`${BASE}/${id}/publish-check`, { method: 'POST' });
export const publishImport = (id: number) => apiFetch<{ status: 'publishing' }>(`${BASE}/${id}/publish`, { method: 'POST' });
export const rollbackImport = (id: number) => apiFetch<{ chunks: number; documents: number; terms: number; released: number }>(`${BASE}/${id}/rollback`, { method: 'POST' });

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
    elsewhere: number; denied: number; too_many_missing: boolean; archived?: number;
  } | null;
  /** 다음 동기화에 다시 시도할 페이지 — 충돌(사람이 고친 조각)·실패 */
  retry_pages: { page_id: string; title: string | null; why: 'conflict' | 'failed' }[];
}
export const listConnections = () => apiFetch<SourceConnection[]>(`${BASE}/connections`);
/** 동기화로 바뀐 페이지(11/05) — 바뀐 줄 수 큰 순, used = 최근 usage_days일 채팅 근거 횟수, conflicts = 사람이 고친 조각 때문에 반영 안 함 */
export interface SyncChange { new_id: number; old_id: number; page_id: string; title: string | null; namespace: string; activated_at: string | null;
  added: number; removed: number; used: number }
export const listSyncChanges = (connId: number, days = 30) =>
  apiFetch<{ days: number; usage_days: number; items: SyncChange[]; conflicts: { page_id: string; title: string | null }[]; capped: boolean }>(`${BASE}/connections/${connId}/changes?days=${days}`);
export const getSyncChange = (connId: number, newId: number) =>
  apiFetch<{ title: string | null; page_id: string; before: string; after: string }>(`${BASE}/connections/${connId}/changes/${newId}`);
export const syncConnection = (id: number, mode: 'incremental' | 'full') =>
  apiFetch<{ status: string }>(`${BASE}/connections/${id}/sync`, { method: 'POST', body: JSON.stringify({ mode }) });
