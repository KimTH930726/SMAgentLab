"""관리 도메인 — 네임스페이스 CRUD 서비스."""
from __future__ import annotations

from typing import Optional

from core.database import get_conn


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
                SELECT name, COALESCE(description, '') AS description,
                       owner_part, created_at
                FROM ops_namespace
                UNION
                SELECT DISTINCT namespace, ''::text, NULL::varchar, NOW()
                FROM ops_knowledge WHERE namespace NOT IN (SELECT name FROM ops_namespace)
                UNION
                SELECT DISTINCT namespace, ''::text, NULL::varchar, NOW()
                FROM ops_glossary WHERE namespace NOT IN (SELECT name FROM ops_namespace)
            ),
            k_cnt AS (SELECT namespace, COUNT(*) AS cnt FROM ops_knowledge GROUP BY namespace),
            g_cnt AS (SELECT namespace, COUNT(*) AS cnt FROM ops_glossary GROUP BY namespace)
            SELECT n.name, n.description, n.owner_part, n.created_at::text,
                   COALESCE(k.cnt, 0) AS knowledge_count,
                   COALESCE(g.cnt, 0) AS glossary_count,
                   ns_real.created_by_user_id,
                   u.username AS created_by_username
            FROM all_ns n
            LEFT JOIN ops_namespace ns_real ON n.name = ns_real.name
            LEFT JOIN ops_user u ON ns_real.created_by_user_id = u.id
            LEFT JOIN k_cnt k ON n.name = k.namespace
            LEFT JOIN g_cnt g ON n.name = g.namespace
            ORDER BY n.name
            """
        )
    return [dict(r) for r in rows]


async def create_namespace(
    name: str, description: str = "",
    owner_part: str | None = None, created_by_user_id: int | None = None,
) -> dict:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ops_namespace (name, description, owner_part, created_by_user_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description
            RETURNING id, name, description, owner_part, created_at::text
            """,
            name, description, owner_part, created_by_user_id,
        )
    return dict(row)


async def delete_namespace(name: str) -> bool:
    async with get_conn() as conn:
        result = await conn.execute("DELETE FROM ops_namespace WHERE name = $1", name)
    return "DELETE 1" in result
