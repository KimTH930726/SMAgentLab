"""관리 도메인 — 네임스페이스 CRUD 서비스."""
from __future__ import annotations

from typing import Optional

from core.database import get_conn


async def list_namespaces() -> list[str]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT name FROM ops_namespace ORDER BY name"
        )
    return [r["name"] for r in rows]


async def list_namespaces_detail() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.name, n.description,
                   p.name AS owner_part,
                   n.created_at::text,
                   n.created_by_user_id,
                   u.username AS created_by_username,
                   COALESCE(k.cnt, 0) AS knowledge_count,
                   COALESCE(g.cnt, 0) AS glossary_count
            FROM ops_namespace n
            LEFT JOIN ops_part p ON n.owner_part_id = p.id
            LEFT JOIN ops_user u ON n.created_by_user_id = u.id
            LEFT JOIN (
                SELECT namespace_id, COUNT(*) AS cnt FROM rag_knowledge GROUP BY namespace_id
            ) k ON n.id = k.namespace_id
            LEFT JOIN (
                SELECT namespace_id, COUNT(*) AS cnt FROM rag_glossary GROUP BY namespace_id
            ) g ON n.id = g.namespace_id
            ORDER BY n.name
            """
        )
    return [dict(r) for r in rows]


async def create_namespace(
    name: str, description: str = "",
    owner_part: str | None = None, created_by_user_id: int | None = None,
) -> dict:
    async with get_conn() as conn:
        # owner_part name → id 변환
        owner_part_id = None
        if owner_part:
            owner_part_id = await conn.fetchval(
                "SELECT id FROM ops_part WHERE name = $1", owner_part
            )
        row = await conn.fetchrow(
            """
            INSERT INTO ops_namespace (name, description, owner_part_id, created_by_user_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description
            RETURNING id, name, description, created_at::text
            """,
            name, description, owner_part_id, created_by_user_id,
        )
        # owner_part name 보충
        result = dict(row)
        result["owner_part"] = owner_part
    return result


async def rename_namespace(old_name: str, new_name: str) -> bool:
    async with get_conn() as conn:
        existing = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops_namespace WHERE name = $1)", new_name)
        if existing:
            return False
        result = await conn.execute(
            "UPDATE ops_namespace SET name = $2 WHERE name = $1", old_name, new_name,
        )
        return "UPDATE 1" in result


async def delete_namespace(name: str) -> bool:
    async with get_conn() as conn:
        # ON DELETE CASCADE로 자식 테이블 자동 삭제됨
        result = await conn.execute("DELETE FROM ops_namespace WHERE name = $1", name)
    return "DELETE 1" in result
