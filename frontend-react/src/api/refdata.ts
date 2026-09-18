import { apiFetch } from './client';

// ─── 참조데이터(공통코드/DB스키마) 검색 — 평가 게이트 즉석 질의용(2026-09-18) ────────
// chat(agent.py)의 네 번째 RRF 축과 동일한 데이터를 조회한다.

export interface CommonCodeHit {
  work_code: string | null;
  group_code: string | null;
  group_code_name: string | null;
  code_id: string;
  code_name: string | null;
  mgmt_values: string | null;
  rank: number;
}

export interface DbColumnHit {
  table_name: string;
  table_comment: string | null;
  column_name: string;
  column_comment: string | null;
  data_type: string | null;
  nullable: string | null;
  rank: number;
}

export interface RefDataSearchResult {
  common_codes: CommonCodeHit[];
  db_columns: DbColumnHit[];
}

export async function searchRefdata(
  namespace: string, q: string, opts?: { topK?: number },
): Promise<RefDataSearchResult> {
  const params = new URLSearchParams({ namespace, q });
  if (opts?.topK) params.set('top_k', String(opts.topK));
  return apiFetch<RefDataSearchResult>(`/refdata/search?${params.toString()}`);
}
