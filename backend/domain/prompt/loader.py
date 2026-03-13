"""프롬프트 로더 — DB에서 func_key로 프롬프트를 조회하여 반환."""
import logging
from typing import Optional

from core.database import get_conn

logger = logging.getLogger(__name__)

# 인메모리 캐시 (서버 재시작 시 초기화)
_cache: dict[str, str] = {}


async def get_prompt(func_key: str, fallback: str = "") -> str:
    """func_key에 해당하는 프롬프트 content를 반환.

    캐시에 있으면 캐시에서 반환, 없으면 DB 조회 후 캐시.
    DB에도 없으면 fallback을 반환.
    """
    if func_key in _cache:
        return _cache[func_key]

    try:
        async with get_conn() as conn:
            row = await conn.fetchrow(
                "SELECT content FROM ops_prompt WHERE func_key = $1", func_key
            )
        if row:
            _cache[func_key] = row["content"]
            return row["content"]
    except Exception:
        logger.warning("프롬프트 로드 실패: %s", func_key, exc_info=True)

    return fallback


def invalidate_cache(func_key: Optional[str] = None) -> None:
    """캐시 무효화. func_key가 None이면 전체 캐시 초기화."""
    if func_key is None:
        _cache.clear()
    else:
        _cache.pop(func_key, None)
