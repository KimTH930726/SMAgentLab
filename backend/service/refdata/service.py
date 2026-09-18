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


async def has_refdata(namespace: str) -> bool:
    """policy_search.has_policy_data()와 동일한 이유의 게이트(2026-09-18) — 이 데이터가
    없는 네임스페이스(대부분)에서 매 채팅 턴마다 두 테이블을 괜히 조회하는 낭비를 막는다."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return False
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM ref_common_code WHERE namespace_id = $1) "
            "OR EXISTS(SELECT 1 FROM ref_db_column WHERE namespace_id = $1)",
            ns_id,
        )
        return bool(exists)


async def search_db_columns(namespace: str, query: str, top_k: int = 10) -> list[dict]:
    """테이블/컬럼명·설명에 대한 키워드 검색 — `search.py`의 policy_param 패턴과 동일하게
    `to_tsvector`/`to_tsquery`(lexeme 단위 매칭)를 쓴다. 정확 조회 데이터라 벡터 채널이
    아예 없음(embedding 컬럼 없음 — §2 결정 규칙).

    policy_strip_ko()로 한국어 조사/어미를 걷어내고 매칭(2026-09-18, retrieval.py의
    일반지식 키워드 검색과 동일한 이유·동일한 함수 재사용) — 안 그러면 "DS14가"처럼
    조사가 붙은 자연어 질의가 본문의 깔끔한 "DS14"와 매칭이 안 된다."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT table_name, table_comment, column_name, column_comment, data_type, nullable,
                   ts_rank(to_tsvector('simple', policy_strip_ko(table_name || ' ' || COALESCE(table_comment,'') || ' ' ||
                           column_name || ' ' || COALESCE(column_comment,''))), q.tsq) AS rank
            FROM ref_db_column
            CROSS JOIN LATERAL (
                SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS tsq
                FROM (SELECT DISTINCT lexeme FROM unnest(to_tsvector('simple', policy_strip_ko($2)))) t
                WHERE lexeme IS NOT NULL
            ) q
            WHERE namespace_id = $1
              AND to_tsvector('simple', policy_strip_ko(table_name || ' ' || COALESCE(table_comment,'') || ' ' ||
                  column_name || ' ' || COALESCE(column_comment,''))) @@ q.tsq
            ORDER BY rank DESC LIMIT $3
            """,
            ns_id, query, top_k,
        )
    return [dict(r) for r in rows]


async def search_common_codes(namespace: str, query: str, top_k: int = 10) -> list[dict]:
    """policy_strip_ko() 적용 이유는 search_db_columns() 문서화 참고 — 동일.

    code_id도 검색 대상 텍스트에 포함(2026-09-18) — "DS14가 뭐야?"처럼 사용자가
    코드값 자체를 묻는 질문이 가장 흔한 패턴인데, code_id를 안 넣으면 group_code_name/
    code_name(설명 텍스트)에만 매칭돼 정작 코드값으로는 못 찾는 문제가 있었다."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT work_code, group_code, group_code_name, code_id, code_name, mgmt_values,
                   ts_rank(to_tsvector('simple', policy_strip_ko(COALESCE(group_code_name,'') || ' ' || code_id || ' ' || COALESCE(code_name,''))), q.tsq) AS rank
            FROM ref_common_code
            CROSS JOIN LATERAL (
                SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS tsq
                FROM (SELECT DISTINCT lexeme FROM unnest(to_tsvector('simple', policy_strip_ko($2)))) t
                WHERE lexeme IS NOT NULL
            ) q
            WHERE namespace_id = $1
              AND to_tsvector('simple', policy_strip_ko(COALESCE(group_code_name,'') || ' ' || code_id || ' ' || COALESCE(code_name,''))) @@ q.tsq
            ORDER BY rank DESC LIMIT $3
            """,
            ns_id, query, top_k,
        )
    return [dict(r) for r in rows]
