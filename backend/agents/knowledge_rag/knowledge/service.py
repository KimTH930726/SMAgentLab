"""지식 베이스, 용어집 CRUD 서비스."""
from __future__ import annotations

from typing import Optional

from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service

_KNOWLEDGE_COLS = """k.id, n.name AS namespace, k.container_name, k.target_tables,
    k.content, k.query_template, k.base_weight, k.category,
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
                category=$10,
                created_by_part=$8, created_by_user_id=$9,
                updated_at=NOW()
            WHERE id = $7
            RETURNING id, namespace_id, container_name, target_tables,
                      content, query_template, base_weight, category,
                      created_by_part, created_by_user_id,
                      created_at::text, updated_at::text
            """,
            new_container, new_tables, new_content,
            new_template, new_embedding, new_weight, knowledge_id,
            updated_by_part or current["created_by_part"],
            updated_by_user_id or current["created_by_user_id"],
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


async def list_knowledge(namespace: Optional[str] = None) -> list[dict]:
    _cols_with_user = """k.id, n.name AS namespace, k.container_name, k.target_tables,
        k.content, k.query_template, k.base_weight, k.category,
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
        if updated_by_part is not None and updated_by_user_id is not None:
            row = await conn.fetchrow(
                """
                UPDATE rag_glossary
                SET term = $1, description = $2, embedding = $3::vector,
                    created_by_part = $5, created_by_user_id = $6
                WHERE id = $4
                RETURNING id, namespace_id, term, description, created_by_part, created_by_user_id
                """,
                term, description, str(embedding), glossary_id,
                updated_by_part, updated_by_user_id,
            )
        else:
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
