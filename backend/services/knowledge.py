"""지식 베이스, 용어집, 네임스페이스 CRUD 서비스."""
from __future__ import annotations

from typing import Optional

from database import get_conn
from services.embedding import embedding_service

_KNOWLEDGE_COLS = """id, namespace, container_name, target_tables,
    content, query_template, base_weight, created_at::text, updated_at::text"""


# ─── ops_knowledge ────────────────────────────────────────────────────────────

async def create_knowledge(
    namespace: str,
    content: str,
    container_name: Optional[str] = None,
    target_tables: Optional[list[str]] = None,
    query_template: Optional[str] = None,
    base_weight: float = 1.0,
) -> dict:
    embedding = await embedding_service.embed(content)
    async with get_conn() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO ops_knowledge
                (namespace, container_name, target_tables, content,
                 query_template, embedding, base_weight)
            VALUES ($1, $2, $3, $4, $5, $6::vector, $7)
            RETURNING {_KNOWLEDGE_COLS}
            """,
            namespace, container_name, target_tables, content,
            query_template, str(embedding), base_weight,
        )
    return dict(row)


async def update_knowledge(
    knowledge_id: int,
    content: Optional[str] = None,
    container_name: Optional[str] = None,
    target_tables: Optional[list[str]] = None,
    query_template: Optional[str] = None,
    base_weight: Optional[float] = None,
) -> Optional[dict]:
    async with get_conn() as conn:
        current = await conn.fetchrow(
            "SELECT * FROM ops_knowledge WHERE id = $1", knowledge_id
        )
        if not current:
            return None

        new_content = content if content is not None else current["content"]
        new_container = container_name if container_name is not None else current["container_name"]
        new_tables = target_tables if target_tables is not None else current["target_tables"]
        new_template = query_template if query_template is not None else current["query_template"]
        new_weight = base_weight if base_weight is not None else current["base_weight"]

        # content 변경 시 임베딩 재생성, 아니면 기존 유지
        new_embedding = str(await embedding_service.embed(new_content)) if content else str(current["embedding"])

        row = await conn.fetchrow(
            f"""
            UPDATE ops_knowledge
            SET container_name=$1, target_tables=$2, content=$3,
                query_template=$4, embedding=$5::vector, base_weight=$6,
                updated_at=NOW()
            WHERE id = $7
            RETURNING {_KNOWLEDGE_COLS}
            """,
            new_container, new_tables, new_content,
            new_template, new_embedding, new_weight, knowledge_id,
        )
    return dict(row) if row else None


async def delete_knowledge(knowledge_id: int) -> bool:
    async with get_conn() as conn:
        result = await conn.execute(
            "DELETE FROM ops_knowledge WHERE id = $1", knowledge_id
        )
    return result == "DELETE 1"


async def list_knowledge(namespace: Optional[str] = None) -> list[dict]:
    async with get_conn() as conn:
        if namespace:
            rows = await conn.fetch(
                f"SELECT {_KNOWLEDGE_COLS} FROM ops_knowledge WHERE namespace = $1 ORDER BY created_at DESC",
                namespace,
            )
        else:
            rows = await conn.fetch(
                f"SELECT {_KNOWLEDGE_COLS} FROM ops_knowledge ORDER BY namespace, created_at DESC"
            )
    return [dict(r) for r in rows]


# ─── ops_glossary ─────────────────────────────────────────────────────────────

async def create_glossary(namespace: str, term: str, description: str) -> dict:
    embedding = await embedding_service.embed(description)
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ops_glossary (namespace, term, description, embedding)
            VALUES ($1, $2, $3, $4::vector)
            RETURNING id, namespace, term, description
            """,
            namespace, term, description, str(embedding),
        )
    return dict(row)


async def list_glossary(namespace: Optional[str] = None) -> list[dict]:
    async with get_conn() as conn:
        if namespace:
            rows = await conn.fetch(
                "SELECT id, namespace, term, description FROM ops_glossary WHERE namespace = $1 ORDER BY id DESC",
                namespace,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, namespace, term, description FROM ops_glossary ORDER BY id DESC"
            )
    return [dict(r) for r in rows]


async def update_glossary(glossary_id: int, term: str, description: str) -> Optional[dict]:
    embedding = await embedding_service.embed(description)
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            UPDATE ops_glossary
            SET term = $1, description = $2, embedding = $3::vector
            WHERE id = $4
            RETURNING id, namespace, term, description
            """,
            term, description, str(embedding), glossary_id,
        )
    return dict(row) if row else None


async def delete_glossary(glossary_id: int) -> bool:
    async with get_conn() as conn:
        result = await conn.execute(
            "DELETE FROM ops_glossary WHERE id = $1", glossary_id
        )
    return result == "DELETE 1"


# ─── Namespaces ───────────────────────────────────────────────────────────────

async def list_namespaces() -> list[str]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT name AS namespace FROM ops_namespace
            UNION SELECT DISTINCT namespace FROM ops_knowledge
            UNION SELECT DISTINCT namespace FROM ops_glossary
            ORDER BY namespace
            """
        )
    return [r["namespace"] for r in rows]


async def list_namespaces_detail() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            WITH all_ns AS (
                SELECT name, COALESCE(description, '') AS description, created_at
                FROM ops_namespace
                UNION
                SELECT DISTINCT namespace, '', NOW()
                FROM ops_knowledge WHERE namespace NOT IN (SELECT name FROM ops_namespace)
                UNION
                SELECT DISTINCT namespace, '', NOW()
                FROM ops_glossary WHERE namespace NOT IN (SELECT name FROM ops_namespace)
            ),
            k_cnt AS (SELECT namespace, COUNT(*) AS cnt FROM ops_knowledge GROUP BY namespace),
            g_cnt AS (SELECT namespace, COUNT(*) AS cnt FROM ops_glossary GROUP BY namespace)
            SELECT n.name, n.description, n.created_at::text,
                   COALESCE(k.cnt, 0) AS knowledge_count,
                   COALESCE(g.cnt, 0) AS glossary_count
            FROM all_ns n
            LEFT JOIN k_cnt k ON n.name = k.namespace
            LEFT JOIN g_cnt g ON n.name = g.namespace
            ORDER BY n.name
            """
        )
    return [dict(r) for r in rows]


async def create_namespace(name: str, description: str = "") -> dict:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ops_namespace (name, description)
            VALUES ($1, $2)
            ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description
            RETURNING id, name, description, created_at::text
            """,
            name, description,
        )
    return dict(row)


async def delete_namespace(name: str) -> bool:
    async with get_conn() as conn:
        result = await conn.execute("DELETE FROM ops_namespace WHERE name = $1", name)
    return "DELETE 1" in result
