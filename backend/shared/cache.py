"""Semantic Cache — Redis 기반 유사 질문 캐싱.

normalize_embeddings=True로 생성된 벡터는 dot product = cosine similarity.
TTL 내 코사인 유사도 > SIMILARITY_THRESHOLD 이면 캐시 히트.
Redis 연결 실패 시 모든 함수가 graceful degradation (캐시 없이 동작).
"""
from __future__ import annotations

import json
import hashlib
import logging
import re

import numpy as np

from core.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 1800            # 30분
_DEFAULT_THRESHOLD = 0.88    # cosine similarity (한국어 단문 최적값)
MAX_CANDIDATES = 200          # 비교할 최대 캐시 엔트리 수

_redis_client = None
_cache_enabled = True
_cache_ttl: int = _DEFAULT_TTL
_similarity_threshold: float = _DEFAULT_THRESHOLD


def normalize_query(text: str) -> str:
    """캐시 embed용 쿼리 정규화.

    - 앞뒤 공백 제거 + 연속 공백 → 단일 공백
    - 한글 자모 사이 공백 제거: "섹션 도구" == "섹션도구"
    - 소문자 변환 (영문 포함 쿼리 대비)
    """
    text = " ".join(text.strip().split())
    text = re.sub(r"(?<=[\uAC00-\uD7A3])\s+(?=[\uAC00-\uD7A3])", "", text)
    return text.lower()


def _to_int(raw) -> int:
    """Redis hget 결과(bytes or None) → int 안전 변환."""
    if raw is None:
        return 0
    if isinstance(raw, bytes):
        return int(raw.decode())
    return int(raw)


def is_cache_enabled() -> bool:
    return _cache_enabled


def set_cache_enabled(enabled: bool) -> None:
    global _cache_enabled
    _cache_enabled = enabled
    logger.info("[Cache] 캐시 %s", "활성화" if enabled else "비활성화")


def get_cache_ttl() -> int:
    return _cache_ttl


def set_cache_ttl(ttl: int) -> None:
    global _cache_ttl
    _cache_ttl = max(60, min(ttl, 86400))
    logger.info("[Cache] TTL → %ds", _cache_ttl)


def get_similarity_threshold() -> float:
    return _similarity_threshold


def set_similarity_threshold(threshold: float) -> None:
    global _similarity_threshold
    _similarity_threshold = max(0.5, min(threshold, 1.0))
    logger.info("[Cache] 유사도 임계값 → %.2f", _similarity_threshold)


async def _get_redis():
    global _redis_client
    if _redis_client is None and settings.redis_url:
        try:
            import redis.asyncio as redis
            _redis_client = redis.from_url(settings.redis_url, decode_responses=False)
            await _redis_client.ping()
            logger.info("[Cache] Redis 연결 성공: %s", settings.redis_url)
        except Exception as e:
            logger.warning("[Cache] Redis 연결 실패 (캐시 비활성화): %s", e)
            _redis_client = None
    return _redis_client


def _make_key(namespace: str, vec: list[float]) -> str:
    h = hashlib.md5(str(vec[:8]).encode()).hexdigest()[:12]
    return f"semcache:{namespace}:{h}"


async def get_cached(namespace: str, query_vec: list[float]) -> dict | None:
    """유사 질문 캐시 조회. 없거나 Redis 미연결이면 None 반환."""
    if not _cache_enabled:
        return None
    r = await _get_redis()
    if r is None:
        return None
    try:
        pattern = f"semcache:{namespace}:*"
        keys: list[bytes] = []
        async for key in r.scan_iter(pattern, count=100):
            keys.append(key)
            if len(keys) >= MAX_CANDIDATES:
                break
        if not keys:
            return None

        q = np.array(query_vec, dtype=np.float32)
        best_score, best_key = 0.0, None

        # 파이프라인으로 모든 키의 임베딩을 한번에 배치 조회
        async with r.pipeline() as pipe:
            for key in keys:
                pipe.hget(key, "emb")
            emb_raws = await pipe.execute()

        for key, raw in zip(keys, emb_raws):
            if not raw:
                continue
            cached_vec = np.frombuffer(raw, dtype=np.float32)
            if len(cached_vec) != len(q):
                continue
            score = float(np.dot(q, cached_vec))  # normalize=True → dot = cosine
            if score > best_score:
                best_score, best_key = score, key

        if best_score >= _similarity_threshold and best_key:
            payload_raw = await r.hget(best_key, "payload")
            if payload_raw:
                await r.hincrby(best_key, "hits", 1)
                logger.info("[Cache HIT] namespace=%s cosine=%.4f", namespace, best_score)
                return json.loads(payload_raw)
    except Exception as e:
        logger.warning("[Cache] 조회 실패 (무시): %s", e)
    return None


async def set_cached(namespace: str, query_vec: list[float], payload: dict) -> None:
    """캐시 저장. Redis 미연결이거나 캐시 비활성화 상태면 무시."""
    if not _cache_enabled:
        return
    r = await _get_redis()
    if r is None:
        return
    try:
        key = _make_key(namespace, query_vec)
        emb_bytes = np.array(query_vec, dtype=np.float32).tobytes()
        await r.hset(key, mapping={
            "emb": emb_bytes,
            "payload": json.dumps(payload, ensure_ascii=False),
            "hits": 0,
        })
        await r.expire(key, _cache_ttl)
        logger.info("[Cache SET] namespace=%s key=%s query=%s", namespace, key, payload.get("query", "")[:40])
    except Exception as e:
        logger.warning("[Cache] 저장 실패 (무시): %s", e)


async def invalidate_namespace(namespace: str) -> int:
    """namespace 캐시 전체 무효화. 지식베이스 업데이트 시 호출."""
    r = await _get_redis()
    if r is None:
        return 0
    try:
        count = 0
        async for key in r.scan_iter(f"semcache:{namespace}:*"):
            await r.delete(key)
            count += 1
        if count:
            logger.info("[Cache INVALIDATE] namespace=%s count=%d", namespace, count)
        return count
    except Exception as e:
        logger.warning("[Cache] 무효화 실패 (무시): %s", e)
        return 0


async def get_stats(namespace: str) -> dict:
    """namespace 캐시 통계 반환."""
    r = await _get_redis()
    if r is None:
        return {"connected": False, "total_entries": 0, "total_hits": 0, "enabled": _cache_enabled}
    try:
        total_entries = 0
        total_hits = 0
        async for key in r.scan_iter(f"semcache:{namespace}:*"):
            total_entries += 1
            total_hits += _to_int(await r.hget(key, "hits"))
        return {
            "connected": True,
            "total_entries": total_entries,
            "total_hits": total_hits,
            "enabled": _cache_enabled,
            "similarity_threshold": _similarity_threshold,
            "cache_ttl": _cache_ttl,
        }
    except Exception as e:
        logger.warning("[Cache] stats 조회 실패: %s", e)
        return {"connected": False, "total_entries": 0, "total_hits": 0, "enabled": _cache_enabled,
                "similarity_threshold": _similarity_threshold, "cache_ttl": _cache_ttl}


async def get_entries(namespace: str) -> list[dict]:
    """namespace 캐시 엔트리 목록 반환 (어드민 UI용)."""
    r = await _get_redis()
    if r is None:
        return []
    try:
        entries = []
        async for key in r.scan_iter(f"semcache:{namespace}:*"):
            payload_raw = await r.hget(key, "payload")
            hits_raw = await r.hget(key, "hits")
            ttl = await r.ttl(key)
            if not payload_raw:
                continue
            payload = json.loads(payload_raw)
            entries.append({
                "key": key.decode() if isinstance(key, bytes) else key,
                "query": payload.get("query", ""),
                "mapped_term": payload.get("mapped_term"),
                "ttl_seconds": max(ttl, 0),
                "hits": _to_int(hits_raw),
            })
        entries.sort(key=lambda x: x["hits"], reverse=True)
        return entries
    except Exception as e:
        logger.warning("[Cache] entries 조회 실패: %s", e)
        return []


async def load_config_from_db(conn) -> None:
    """앱 시작 시 DB에서 캐시 설정 로드."""
    try:
        rows = await conn.fetch(
            "SELECT key, value FROM ops_system_config WHERE key LIKE 'cache_%'"
        )
        for row in rows:
            k, v = row["key"], row["value"]
            if k == "cache_enabled":
                set_cache_enabled(v.lower() == "true")
            elif k == "cache_similarity_threshold":
                set_similarity_threshold(float(v))
            elif k == "cache_ttl":
                set_cache_ttl(int(v))
        if rows:
            logger.info("[Cache] DB 설정 로드: enabled=%s threshold=%.2f ttl=%d",
                        _cache_enabled, _similarity_threshold, _cache_ttl)
    except Exception as e:
        logger.warning("[Cache] DB 설정 로드 실패 (기본값 사용): %s", e)


async def save_config_to_db(conn, *, enabled: bool | None = None,
                            similarity_threshold: float | None = None,
                            cache_ttl: int | None = None) -> None:
    """캐시 설정 DB 저장 (upsert)."""
    updates: dict[str, str] = {}
    if enabled is not None:
        updates["cache_enabled"] = str(enabled).lower()
    if similarity_threshold is not None:
        updates["cache_similarity_threshold"] = str(similarity_threshold)
    if cache_ttl is not None:
        updates["cache_ttl"] = str(cache_ttl)
    for k, v in updates.items():
        await conn.execute(
            """INSERT INTO ops_system_config (key, value, updated_at)
               VALUES ($1, $2, NOW())
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
            k, v,
        )


async def delete_entry(key: str) -> bool:
    """단일 캐시 엔트리 삭제."""
    r = await _get_redis()
    if r is None:
        return False
    try:
        result = await r.delete(key)
        return result > 0
    except Exception as e:
        logger.warning("[Cache] 엔트리 삭제 실패: %s", e)
        return False
