"""DB 스키마 사전 / 공통코드 적재 — parser.py의 결정론 파싱 결과를 RDB에 넣는다.

LLM 호출이 전혀 없다(순수 구조화 표라 의미 판단 불필요, §3). 벡터 임베딩도 안 한다
(정확 조회 전용 데이터라 `ref_db_column`/`ref_common_code`에 embedding 컬럼 자체가 없음).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from core.database import get_conn, resolve_namespace_id
from service.refdata.parser import ParsedCommonCode, ParsedDbColumn


@dataclass
class RefDataIngestSummary:
    inserted: int = 0
    skipped_empty: int = 0


async def ingest_db_columns(namespace: str, rows: list[ParsedDbColumn], source_file: str) -> RefDataIngestSummary:
    summary = RefDataIngestSummary()
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")
        for r in rows:
            if not r.table_name or not r.column_name:
                summary.skipped_empty += 1
                continue
            await conn.execute(
                """
                INSERT INTO ref_db_column
                    (namespace_id, table_name, table_comment, column_name, column_comment,
                     data_type, nullable, source_file)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                ns_id, r.table_name, r.table_comment or None, r.column_name, r.column_comment or None,
                r.data_type or None, r.nullable or None, source_file,
            )
            summary.inserted += 1
    return summary


async def ingest_common_codes(namespace: str, rows: list[ParsedCommonCode], source_file: str) -> RefDataIngestSummary:
    summary = RefDataIngestSummary()
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")
        for r in rows:
            if not r.group_code or not r.code_id:
                summary.skipped_empty += 1
                continue
            await conn.execute(
                """
                INSERT INTO ref_common_code
                    (namespace_id, work_code, group_code, group_code_name, code_id, code_name,
                     mgmt_values, source_file)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                ns_id, r.work_code or None, r.group_code, r.group_code_name or None,
                r.code_id, r.code_name or None,
                json.dumps(r.mgmt_values, ensure_ascii=False) if r.mgmt_values else None,
                source_file,
            )
            summary.inserted += 1
    return summary


async def search_db_columns(namespace: str, query: str, top_k: int = 10) -> list[dict]:
    """테이블/컬럼명·설명에 대한 키워드 검색 — `search.py`의 policy_param 패턴과 동일하게
    `to_tsvector`/`to_tsquery`(lexeme 단위 매칭)를 쓴다. 정확 조회 데이터라 벡터 채널이
    아예 없음(embedding 컬럼 없음 — §2 결정 규칙)."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT table_name, table_comment, column_name, column_comment, data_type, nullable,
                   ts_rank(to_tsvector('simple', table_name || ' ' || COALESCE(table_comment,'') || ' ' ||
                           column_name || ' ' || COALESCE(column_comment,'')), q.tsq) AS rank
            FROM ref_db_column
            CROSS JOIN LATERAL (
                SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS tsq
                FROM (SELECT DISTINCT lexeme FROM unnest(to_tsvector('simple', $2))) t
                WHERE lexeme IS NOT NULL
            ) q
            WHERE namespace_id = $1
              AND to_tsvector('simple', table_name || ' ' || COALESCE(table_comment,'') || ' ' ||
                  column_name || ' ' || COALESCE(column_comment,'')) @@ q.tsq
            ORDER BY rank DESC LIMIT $3
            """,
            ns_id, query, top_k,
        )
    return [dict(r) for r in rows]


async def search_common_codes(namespace: str, query: str, top_k: int = 10) -> list[dict]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT work_code, group_code, group_code_name, code_id, code_name, mgmt_values,
                   ts_rank(to_tsvector('simple', COALESCE(group_code_name,'') || ' ' || COALESCE(code_name,'')), q.tsq) AS rank
            FROM ref_common_code
            CROSS JOIN LATERAL (
                SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS tsq
                FROM (SELECT DISTINCT lexeme FROM unnest(to_tsvector('simple', $2))) t
                WHERE lexeme IS NOT NULL
            ) q
            WHERE namespace_id = $1
              AND to_tsvector('simple', COALESCE(group_code_name,'') || ' ' || COALESCE(code_name,'')) @@ q.tsq
            ORDER BY rank DESC LIMIT $3
            """,
            ns_id, query, top_k,
        )
    return [dict(r) for r in rows]
