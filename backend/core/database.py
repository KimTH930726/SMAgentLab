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
    """업무 영역 name → id. 없거나 상위 묶음(kind='group')이면 None — 묶음은 데이터를 갖지 않아 데이터 경로(채팅·대화방·신고·통계·
    등록)에선 없는 영역과 같다(2026-10-20 막 쓰기 테스트: 묶음에 대화방·신고가 생기고 통계는 0으로 답했다). 트리 편집은 직접 조회."""
    return await conn.fetchval("SELECT id FROM ops_namespace WHERE name = $1 AND kind <> 'group'", namespace)
