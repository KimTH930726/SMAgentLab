from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from core.config import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        command_timeout=60,
    )


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_conn() -> AsyncIterator[asyncpg.Connection]:
    assert _pool is not None, "DB pool is not initialized"
    async with _pool.acquire(timeout=settings.db_pool_acquire_timeout) as conn:
        yield conn


async def resolve_namespace_id(conn: asyncpg.Connection, namespace: str) -> int | None:
    """namespace name → id 변환. 없으면 None."""
    return await conn.fetchval("SELECT id FROM ops_namespace WHERE name = $1", namespace)
