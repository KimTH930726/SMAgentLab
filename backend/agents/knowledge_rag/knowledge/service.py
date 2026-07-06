"""지식 베이스, 용어집 CRUD 서비스."""
from __future__ import annotations

from typing import Optional

from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service

_KNOWLEDGE_COLS = """k.id, n.name AS namespace, k.container_name, k.target_tables,
    k.content, k.query_template, k.base_weight, k.category,
    k.source_file, k.source_chunk_idx, k.source_type,
    k.created_by_part, k.created_by_user_id,
    k.created_at::text, k.updated_at::text"""


# ─── rag_knowledge ────────────────────────────────────────────────────────────

async def create_knowledge(
    namespace: str,
    content: str,
    container_name: Optional[str] = None,
    target_tables: Optional[list[str]] = None,
    query_template: Optional[str] = None,
    base_weight: float = 1.0,
    category: Optional[str] = None,
    *,
    created_by_part: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> dict:
    embedding = await embedding_service.embed(content)
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"Namespace '{namespace}' not found")
        row = await conn.fetchrow(
            f"""
            INSERT INTO rag_knowledge
                (namespace_id, container_name, target_tables, content,
                 query_template, embedding, base_weight, category,
                 created_by_part, created_by_user_id)
            VALUES ($1, $2, $3, $4, $5, $6::vector, $7, $8, $9, $10)
            RETURNING id, namespace_id, container_name, target_tables,
                      content, query_template, base_weight, category,
                      created_by_part, created_by_user_id,
                      created_at::text, updated_at::text
            """,
            ns_id, container_name, target_tables, content,
            query_template, str(embedding), base_weight, category,
            created_by_part, created_by_user_id,
        )
        result = dict(row)
        result["namespace"] = namespace
    return result


async def update_knowledge(
    knowledge_id: int,
    content: Optional[str] = None,
    container_name: Optional[str] = None,
    target_tables: Optional[list[str]] = None,
    query_template: Optional[str] = None,
    base_weight: Optional[float] = None,
    category: Optional[str] = None,
    *,
    updated_by_part: Optional[str] = None,
    updated_by_user_id: Optional[int] = None,
) -> Optional[dict]:
    async with get_conn() as conn:
        current = await conn.fetchrow(
            "SELECT k.*, n.name AS ns_name FROM rag_knowledge k JOIN ops_namespace n ON k.namespace_id = n.id WHERE k.id = $1",
            knowledge_id,
        )
        if not current:
            return None

        new_content = content if content is not None else current["content"]
        new_container = container_name if container_name is not None else current["container_name"]
        new_tables = target_tables if target_tables is not None else current["target_tables"]
        new_template = query_template if query_template is not None else current["query_template"]
        new_weight = base_weight if base_weight is not None else current["base_weight"]
        # category=None은 "변경 없음", category=""는 "NULL로 초기화"로 처리
        new_category = category if category is not None else current.get("category")
        if new_category == "":
            new_category = None

        new_embedding = str(await embedding_service.embed(new_content)) if content else str(current["embedding"])

        row = await conn.fetchrow(
            """
            UPDATE rag_knowledge
            SET container_name=$1, target_tables=$2, content=$3,
                query_template=$4, embedding=$5::vector, base_weight=$6,
                category=$8,
                updated_at=NOW()
            WHERE id = $7
            RETURNING id, namespace_id, container_name, target_tables,
                      content, query_template, base_weight, category,
                      created_by_part, created_by_user_id,
                      created_at::text, updated_at::text
            """,
            new_container, new_tables, new_content,
            new_template, new_embedding, new_weight, knowledge_id,
            new_category,
        )
        if not row:
            return None
        result = dict(row)
        result["namespace"] = current["ns_name"]
    return result


async def delete_knowledge(knowledge_id: int) -> bool:
    async with get_conn() as conn:
        result = await conn.execute(
            "DELETE FROM rag_knowledge WHERE id = $1", knowledge_id
        )
    return result == "DELETE 1"


async def bulk_delete_knowledge(ids: list[int]) -> int:
    if not ids:
        return 0
    async with get_conn() as conn:
        result = await conn.execute(
            "DELETE FROM rag_knowledge WHERE id = ANY($1::int[])", ids
        )
    return int(result.split()[-1])


async def vector_search_knowledge(namespace: str, query_vec: list[float], top_k: int = 30) -> list[dict]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT k.id, n.name AS namespace, k.container_name, k.target_tables,
                   k.content, k.query_template, k.base_weight, k.category,
                   k.source_file, k.source_chunk_idx, k.source_type,
                   k.created_by_part, k.created_by_user_id, u.username AS created_by_username,
                   k.created_at::text, k.updated_at::text,
                   1 - (k.embedding <=> $2::vector) AS similarity
            FROM rag_knowledge k
            JOIN ops_namespace n ON k.namespace_id = n.id
            LEFT JOIN ops_user u ON k.created_by_user_id = u.id
            WHERE k.namespace_id = $1 AND k.embedding IS NOT NULL
            ORDER BY k.embedding <=> $2::vector
            LIMIT $3
            """,
            ns_id, str(query_vec), top_k,
        )
    return [dict(r) for r in rows]


async def list_knowledge(namespace: Optional[str] = None) -> list[dict]:
    _cols_with_user = """k.id, n.name AS namespace, k.container_name, k.target_tables,
        k.content, k.query_template, k.base_weight, k.category,
        k.source_file, k.source_chunk_idx, k.source_type,
        k.created_by_part, k.created_by_user_id, u.username AS created_by_username,
        k.created_at::text, k.updated_at::text"""
    async with get_conn() as conn:
        if namespace:
            ns_id = await resolve_namespace_id(conn, namespace)
            if ns_id is None:
                return []
            rows = await conn.fetch(
                f"""
                SELECT {_cols_with_user}
                FROM rag_knowledge k
                JOIN ops_namespace n ON k.namespace_id = n.id
                LEFT JOIN ops_user u ON k.created_by_user_id = u.id
                WHERE k.namespace_id = $1
                ORDER BY k.created_at DESC
                """,
                ns_id,
            )
        else:
            rows = await conn.fetch(
                f"""
                SELECT {_cols_with_user}
                FROM rag_knowledge k
                JOIN ops_namespace n ON k.namespace_id = n.id
                LEFT JOIN ops_user u ON k.created_by_user_id = u.id
                ORDER BY n.name, k.created_at DESC
                """
            )
    return [dict(r) for r in rows]


async def get_knowledge_part(knowledge_id: int) -> Optional[str]:
    """리소스의 created_by_part 반환 (레거시 호환용)."""
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT created_by_part FROM rag_knowledge WHERE id = $1", knowledge_id
        )


async def get_knowledge_namespace(knowledge_id: int) -> Optional[str]:
    """리소스의 namespace name 반환 (네임스페이스 소유 파트 기반 권한 체크용)."""
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT n.name FROM rag_knowledge k JOIN ops_namespace n ON k.namespace_id = n.id WHERE k.id = $1",
            knowledge_id,
        )


# ─── rag_glossary ─────────────────────────────────────────────────────────────

async def create_glossary(
    namespace: str, term: str, description: str,
    *, created_by_part: Optional[str] = None, created_by_user_id: Optional[int] = None,
) -> dict:
    embedding = await embedding_service.embed(description)
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"Namespace '{namespace}' not found")
        row = await conn.fetchrow(
            """
            INSERT INTO rag_glossary (namespace_id, term, description, embedding, created_by_part, created_by_user_id)
            VALUES ($1, $2, $3, $4::vector, $5, $6)
            RETURNING id, namespace_id, term, description, created_by_part, created_by_user_id
            """,
            ns_id, term, description, str(embedding), created_by_part, created_by_user_id,
        )
        result = dict(row)
        result["namespace"] = namespace
    return result


async def list_glossary(namespace: Optional[str] = None) -> list[dict]:
    _cols = "g.id, n.name AS namespace, g.term, g.description, g.created_by_part, g.created_by_user_id, u.username AS created_by_username"
    async with get_conn() as conn:
        if namespace:
            ns_id = await resolve_namespace_id(conn, namespace)
            if ns_id is None:
                return []
            rows = await conn.fetch(
                f"""
                SELECT {_cols}
                FROM rag_glossary g
                JOIN ops_namespace n ON g.namespace_id = n.id
                LEFT JOIN ops_user u ON g.created_by_user_id = u.id
                WHERE g.namespace_id = $1
                ORDER BY g.id DESC
                """,
                ns_id,
            )
        else:
            rows = await conn.fetch(
                f"""
                SELECT {_cols}
                FROM rag_glossary g
                JOIN ops_namespace n ON g.namespace_id = n.id
                LEFT JOIN ops_user u ON g.created_by_user_id = u.id
                ORDER BY g.id DESC
                """
            )
    return [dict(r) for r in rows]


async def update_glossary(
    glossary_id: int, term: str, description: str,
    *, updated_by_part: Optional[str] = None, updated_by_user_id: Optional[int] = None,
) -> Optional[dict]:
    embedding = await embedding_service.embed(description)
    async with get_conn() as conn:
        # namespace name 조회 (응답용)
        ns_name = await conn.fetchval(
            "SELECT n.name FROM rag_glossary g JOIN ops_namespace n ON g.namespace_id = n.id WHERE g.id = $1",
            glossary_id,
        )
        row = await conn.fetchrow(
            """
            UPDATE rag_glossary
            SET term = $1, description = $2, embedding = $3::vector
            WHERE id = $4
            RETURNING id, namespace_id, term, description, created_by_part, created_by_user_id
            """,
            term, description, str(embedding), glossary_id,
        )
        if not row:
            return None
        result = dict(row)
        result["namespace"] = ns_name
    return result


async def delete_glossary(glossary_id: int) -> bool:
    async with get_conn() as conn:
        result = await conn.execute(
            "DELETE FROM rag_glossary WHERE id = $1", glossary_id
        )
    return result == "DELETE 1"


async def bulk_delete_glossary(ids: list[int]) -> int:
    if not ids:
        return 0
    async with get_conn() as conn:
        result = await conn.execute(
            "DELETE FROM rag_glossary WHERE id = ANY($1::int[])", ids
        )
    return int(result.split()[-1])


async def vector_search_glossary(namespace: str, query_vec: list[float], top_k: int = 30) -> list[dict]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT g.id, n.name AS namespace, g.term, g.description,
                   g.created_by_part, g.created_by_user_id, u.username AS created_by_username,
                   g.created_at::text,
                   1 - (g.embedding <=> $2::vector) AS similarity
            FROM rag_glossary g
            JOIN ops_namespace n ON g.namespace_id = n.id
            LEFT JOIN ops_user u ON g.created_by_user_id = u.id
            WHERE g.namespace_id = $1 AND g.embedding IS NOT NULL
            ORDER BY g.embedding <=> $2::vector
            LIMIT $3
            """,
            ns_id, str(query_vec), top_k,
        )
    return [dict(r) for r in rows]


async def get_glossary_part(glossary_id: int) -> Optional[str]:
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT created_by_part FROM rag_glossary WHERE id = $1", glossary_id
        )


async def get_glossary_namespace(glossary_id: int) -> Optional[str]:
    """용어집의 namespace name 반환 (네임스페이스 소유 파트 기반 권한 체크용)."""
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT n.name FROM rag_glossary g JOIN ops_namespace n ON g.namespace_id = n.id WHERE g.id = $1",
            glossary_id,
        )


# ─── 벌크 등록 (Ingestion) ──────────────────────────────────────────────────

async def bulk_create_knowledge(
    namespace: str,
    items: list[dict],
    *,
    source_file: Optional[str] = None,
    source_type: str = "manual",
    created_by_part: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> dict:
    """여러 지식을 배치 임베딩으로 한번에 등록.

    Returns:
        {"created": int, "job_id": int | None}
    """
    import json as _json

    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"Namespace '{namespace}' not found")

    # 인제스천 작업 생성
    job_id = None
    if source_file:
        async with get_conn() as conn:
            job_id = await conn.fetchval("""
                INSERT INTO rag_ingestion_job
                    (namespace_id, source_file, source_type, status, total_chunks,
                     embedding_model, created_by_user_id)
                VALUES ($1, $2, $3, 'processing', $4, $5, $6) RETURNING id
            """, ns_id, source_file, source_type, len(items),
                "paraphrase-multilingual-mpnet-base-v2", created_by_user_id)

    # 배치 임베딩
    texts = [item["content"] for item in items]
    embeddings = await embedding_service.embed_batch(texts)

    # 벌크 INSERT
    created = 0
    try:
        async with get_conn() as conn:
            for i, (item, emb) in enumerate(zip(items, embeddings)):
                # PDF/XLSX 추출 텍스트에 null byte(\x00)가 포함되면 PostgreSQL이 거부함 → 제거
                content = item["content"].replace("\x00", "")
                await conn.execute("""
                    INSERT INTO rag_knowledge
                        (namespace_id, container_name, target_tables, content,
                         query_template, embedding, base_weight, category,
                         source_file, source_chunk_idx, source_type,
                         created_by_part, created_by_user_id)
                    VALUES ($1, $2, $3, $4, $5, $6::vector, $7, $8, $9, $10, $11, $12, $13)
                """,
                    ns_id,
                    item.get("container_name"),
                    item.get("target_tables"),
                    content,
                    item.get("query_template"),
                    str(emb),
                    item.get("base_weight", 1.0),
                    item.get("category"),
                    source_file,
                    i,
                    source_type,
                    created_by_part,
                    created_by_user_id,
                )
                created += 1
    except Exception:
        if job_id:
            async with get_conn() as conn:
                await conn.execute("""
                    UPDATE rag_ingestion_job
                    SET status = 'failed', created_chunks = $1, completed_at = NOW()
                    WHERE id = $2
                """, created, job_id)
        raise

    # 작업 완료 처리
    if job_id:
        async with get_conn() as conn:
            await conn.execute("""
                UPDATE rag_ingestion_job
                SET status = 'completed', created_chunks = $1, completed_at = NOW()
                WHERE id = $2
            """, created, job_id)

    return {"created": created, "job_id": job_id}


async def list_ingestion_jobs(namespace: str) -> list[dict]:
    """인제스천 작업 이력 조회."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch("""
            SELECT id, namespace_id, source_file, source_type, status,
                   total_chunks, created_chunks, auto_glossary, auto_fewshot,
                   chunk_strategy, error_message,
                   created_at::text, completed_at::text
            FROM rag_ingestion_job
            WHERE namespace_id = $1
            ORDER BY created_at DESC
            LIMIT 50
        """, ns_id)
    return [dict(r) for r in rows]


def split_text_to_chunks(
    text: str,
    strategy: str = "auto",
) -> list[str]:
    """텍스트를 청크로 분할.

    strategy:
      - auto: ## 헤더 → 빈 줄 → --- 순서로 시도
      - heading: ## 헤더 기준
      - blank_line: 빈 줄 (\\n\\n) 기준
      - separator: --- 기준
      - none: 분할 안함
    """
    import re

    if strategy == "none" or not text.strip():
        return [text.strip()] if text.strip() else []

    if strategy == "heading" or strategy == "auto":
        # ## 헤더 기준 분할
        parts = re.split(r'\n(?=#{1,3}\s)', text)
        chunks = [p.strip() for p in parts if p.strip()]
        if len(chunks) > 1 or strategy == "heading":
            return chunks

    if strategy == "separator" or strategy == "auto":
        # --- 구분선 기준
        parts = re.split(r'\n---+\n', text)
        chunks = [p.strip() for p in parts if p.strip()]
        if len(chunks) > 1 or strategy == "separator":
            return chunks

    if strategy == "blank_line" or strategy == "auto":
        # 빈 줄 기준
        parts = re.split(r'\n\s*\n', text)
        chunks = [p.strip() for p in parts if p.strip()]
        if len(chunks) > 1:
            return chunks

    # fallback: 전체를 하나의 청크로
    return [text.strip()]
