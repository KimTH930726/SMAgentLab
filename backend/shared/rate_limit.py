"""로그인 브루트포스 방어 — 실패 횟수 제한(Redis, 없으면 프로세스 메모리).

Redis가 없어도 로그인 자체는 막히면 안 된다(폐쇄망 배포 — docs/deployment-closed-network.md). 예전엔 그 경우 제한 없이
통과시켰는데, 그러면 Redis 장애 동안 무차별 대입 방어가 통째로 꺼진다(인증 통제의 fail-open — 2026-10-20 대체 처리 전수 점검).
그래서 Redis를 못 쓰면 프로세스 메모리로 같은 규칙을 센다(백엔드는 uvicorn 프로세스 하나). 메모리 쪽은 재시작하면 초기화된다.
"""
from __future__ import annotations

import ipaddress
import logging
import time

from core.config import settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 300     # 5분 내 실패 횟수를 센다
LOCKOUT_SECONDS = 300    # 임계값 초과 시 5분간 잠금

_redis_client = None
# Redis 대신 쓰는 메모리 기록: 키 → (첫 실패 시각, 실패 수), 잠금 키 → 풀리는 시각
_mem_count: dict[str, tuple[float, int]] = {}
_mem_locked: dict[str, float] = {}
_MEM_PRUNE_AT = 10_000          # 기록이 이만큼 쌓이면 지난 것을 지운다(성공 로그인이 없는 이름은 계속 쌓였다, 리뷰 L3)
_REDIS_RETRY_SECONDS = 30       # Redis가 죽었을 때 매 로그인마다 연결을 다시 시도하지 않게(접속 대기·경고 로그가 매번 났다)
_redis_failed_at = 0.0


def client_ip(request) -> str | None:
    """로그인 실패 제한용 사용자 IP. 브라우저 요청은 nginx 컨테이너를 거쳐 와서 직접 연결 주소는 모두 nginx다 — 그걸로 세면 아무나
    5번 틀리면 전원이 5분간 못 들어왔다(2026-10-20 점검). nginx가 넘기는 X-Real-IP를 쓰되, 직접 연결이 사설·도커 망 주소일 때만
    믿는다(밖에서 백엔드로 바로 와서 머리글을 꾸미는 경우 대비 — 그래도 사용자 이름 기준 잠금은 그대로 걸린다)."""
    peer = request.client.host if request.client else None
    real = (request.headers.get("x-real-ip") or "").strip()
    if peer and real:
        try:
            p = ipaddress.ip_address(peer)
            if p.is_private or p.is_loopback:
                ipaddress.ip_address(real)   # 형식 검증
                return real
        except ValueError:
            pass
    return peer


async def _get_redis():
    global _redis_client, _redis_failed_at
    if _redis_client is None and settings.redis_url and time.time() - _redis_failed_at >= _REDIS_RETRY_SECONDS:
        try:
            import redis.asyncio as redis
            _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
            await _redis_client.ping()
        except Exception as e:
            logger.warning("[RateLimit] Redis 연결 실패 — 메모리로 제한(%d초 뒤 다시 시도): %s", _REDIS_RETRY_SECONDS, e)
            _redis_client = None
            _redis_failed_at = time.time()
    return _redis_client


def _key(kind: str, identifier: str) -> str:
    return f"loginlimit:{kind}:{identifier.strip().lower()}"


def _idents(username: str, client_ip: str | None):
    return [(k, i) for k, i in (("user", username), ("ip", client_ip)) if i]


def _mem_is_locked(keys: list[str]) -> bool:
    now = time.time()
    for k in keys:
        until = _mem_locked.get(k)
        if until and until > now:
            return True
        _mem_locked.pop(k, None)
    return False


def _mem_record(username: str, client_ip: str | None) -> None:
    now = time.time()
    if len(_mem_count) > _MEM_PRUNE_AT:
        for k in [k for k, (first, _) in _mem_count.items() if now - first > WINDOW_SECONDS]:
            del _mem_count[k]
        for k in [k for k, until in _mem_locked.items() if until <= now]:
            del _mem_locked[k]
    for kind, ident in _idents(username, client_ip):
        k = _key(kind, ident)
        first, n = _mem_count.get(k, (now, 0))
        if now - first > WINDOW_SECONDS:
            first, n = now, 0
        _mem_count[k] = (first, n + 1)
        if n + 1 >= MAX_ATTEMPTS:
            _mem_locked[k] = now + LOCKOUT_SECONDS
            logger.warning("[RateLimit] %s=%s 5분간 로그인 잠금 (실패 %d회, 메모리)", kind, ident, n + 1)


def _mem_reset(username: str, client_ip: str | None) -> None:
    for kind, ident in _idents(username, client_ip):
        k = _key(kind, ident)
        _mem_count.pop(k, None)
        _mem_locked.pop(k, None)


async def is_locked(username: str, client_ip: str | None) -> bool:
    """이 사용자명 또는 이 IP가 현재 잠금 상태인지 확인."""
    keys = [_key(k, i) for k, i in _idents(username, client_ip)]
    if _mem_is_locked(keys):          # Redis가 잠깐 죽었던 사이 메모리로 잠근 것도 유효
        return True
    r = await _get_redis()
    if r is None:
        return False
    try:
        for k in keys:
            if await r.get(f"{k}:locked"):
                return True
    except Exception as e:
        logger.warning("[RateLimit] 잠금 상태 조회 실패 — 메모리 기록만 사용: %s", e)
    return False


async def record_failure(username: str, client_ip: str | None) -> None:
    """로그인 실패 기록. WINDOW_SECONDS 내 MAX_ATTEMPTS회 초과 시 LOCKOUT_SECONDS간 잠금."""
    r = await _get_redis()
    if r is None:
        _mem_record(username, client_ip)
        return
    try:
        for kind, ident in _idents(username, client_ip):
            count_key = _key(kind, ident)
            count = await r.incr(count_key)
            if count == 1:
                await r.expire(count_key, WINDOW_SECONDS)
            if count >= MAX_ATTEMPTS:
                await r.set(f"{count_key}:locked", "1", ex=LOCKOUT_SECONDS)
                logger.warning("[RateLimit] %s=%s 5분간 로그인 잠금 (실패 %d회)", kind, ident, count)
    except Exception as e:
        logger.warning("[RateLimit] Redis 실패 기록 실패 — 메모리로 기록: %s", e)
        _mem_record(username, client_ip)


async def reset(username: str, client_ip: str | None) -> None:
    """로그인 성공 시 카운터 초기화."""
    _mem_reset(username, client_ip)
    r = await _get_redis()
    if r is None:
        return
    try:
        for kind, ident in _idents(username, client_ip):
            key = _key(kind, ident)
            await r.delete(key, f"{key}:locked")
    except Exception as e:
        logger.warning("[RateLimit] 카운터 초기화 실패 (무시): %s", e)
