"""로그인 브루트포스 방어 — Redis 기반 실패 횟수 제한.

Redis 연결 실패 시 제한 없이 통과시킨다(graceful degradation) — shared/cache.py와
동일한 방침. 이 프로젝트가 폐쇄망에 배포될 수도 있어(docs/deployment-closed-network.md),
Redis가 없다고 로그인 자체가 막히면 안 된다.
"""
from __future__ import annotations

import logging

from core.config import settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300     # 5분 내 실패 횟수를 센다
LOCKOUT_SECONDS = 300    # 임계값 초과 시 5분간 잠금

_redis_client = None


async def _get_redis():
    global _redis_client
    if _redis_client is None and settings.redis_url:
        try:
            import redis.asyncio as redis
            _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            await _redis_client.ping()
        except Exception as e:
            logger.warning("[RateLimit] Redis 연결 실패 (제한 없이 동작): %s", e)
            _redis_client = None
    return _redis_client


def _key(kind: str, identifier: str) -> str:
    return f"loginlimit:{kind}:{identifier.strip().lower()}"


async def is_locked(username: str, client_ip: str | None) -> bool:
    """이 사용자명 또는 이 IP가 현재 잠금 상태인지 확인."""
    r = await _get_redis()
    if r is None:
        return False
    try:
        user_locked = await r.get(f"{_key('user', username)}:locked")
        if user_locked:
            return True
        if client_ip:
            ip_locked = await r.get(f"{_key('ip', client_ip)}:locked")
            if ip_locked:
                return True
    except Exception as e:
        logger.warning("[RateLimit] 잠금 상태 조회 실패 (무시): %s", e)
    return False


async def record_failure(username: str, client_ip: str | None) -> None:
    """로그인 실패 기록. WINDOW_SECONDS 내 MAX_ATTEMPTS회 초과 시 LOCKOUT_SECONDS간 잠금."""
    r = await _get_redis()
    if r is None:
        return
    try:
        for kind, ident in (("user", username), ("ip", client_ip)):
            if not ident:
                continue
            count_key = _key(kind, ident)
            count = await r.incr(count_key)
            if count == 1:
                await r.expire(count_key, WINDOW_SECONDS)
            if count >= MAX_ATTEMPTS:
                await r.set(f"{count_key}:locked", "1", ex=LOCKOUT_SECONDS)
                logger.warning("[RateLimit] %s=%s 5분간 로그인 잠금 (실패 %d회)", kind, ident, count)
    except Exception as e:
        logger.warning("[RateLimit] 실패 기록 실패 (무시): %s", e)


async def reset(username: str, client_ip: str | None) -> None:
    """로그인 성공 시 카운터 초기화."""
    r = await _get_redis()
    if r is None:
        return
    try:
        for kind, ident in (("user", username), ("ip", client_ip)):
            if not ident:
                continue
            key = _key(kind, ident)
            await r.delete(key, f"{key}:locked")
    except Exception as e:
        logger.warning("[RateLimit] 카운터 초기화 실패 (무시): %s", e)
