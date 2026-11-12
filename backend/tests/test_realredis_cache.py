"""시맨틱 캐시 무효화 — 실제 Redis로 키 개수 전/후(backlog 0-0h C③, 11/11).

왜: 시연 녹화 중 `DELETE /api/admin/cache` 가 200인데 같은 질문 답이 글자까지 같아 "API가 Redis를 실제로 비우나"가 의심됐다.
기존 테스트는 conftest가 shared.cache를 가짜로 바꿔 두어 실제로 지워지는지 본 적이 없다 — 응답 코드가 아니라 남은 키 개수로 본다.

시험 전용 칸(db 7)만 쓴다 — 서비스는 0번, 시연 사본 백엔드는 5번. 시작·끝에 7번만 비운다. Redis에 못 붙으면 건너뛴다.
"""
import asyncio
import os
import types

import numpy as np
import pytest

from tests.realdb import load

TEST_DB_URL = os.environ.get("TEST_REDIS_URL", "redis://ops-redis:6379/7")


def _vec(seed: int) -> list[float]:
    v = np.random.default_rng(seed).standard_normal(1024).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


@pytest.fixture()
def cache():
    redis = pytest.importorskip("redis.asyncio")
    mod = load("cache_real", "shared/cache.py")
    mod.settings = types.SimpleNamespace(redis_url=TEST_DB_URL)
    mod._redis_client = None

    async def ping():
        r = redis.from_url(TEST_DB_URL)
        try:
            await r.ping()
            await r.flushdb()
        finally:
            await r.aclose()
    try:
        asyncio.run(ping())
    except Exception as e:  # noqa: BLE001 — Redis 없는 환경은 건너뜀
        pytest.skip(f"Redis 연결 안 됨: {e}")
    yield mod

    async def cleanup():
        r = redis.from_url(TEST_DB_URL)
        try:
            await r.flushdb()
        finally:
            await r.aclose()
    asyncio.run(cleanup())


def _run(mod, coro_fn):
    async def go():
        mod._redis_client = None   # 이벤트 루프마다 새 연결(asyncio.run이 루프를 새로 만든다)
        try:
            return await coro_fn()
        finally:
            if mod._redis_client is not None:
                await mod._redis_client.aclose()
                mod._redis_client = None
    return asyncio.run(go())


def test_invalidate_namespace_deletes_only_that_namespace_keys(cache):
    async def scenario():
        await cache.set_cached("시험_A", "rag", _vec(1), {"query": "q1"})
        await cache.set_cached("시험_A", "rag", _vec(2), {"query": "q2"})
        await cache.set_cached("시험_B", "rag", _vec(3), {"query": "q3"})
        await cache.set_cached("시험_A*", "rag", _vec(4), {"query": "q4"})   # glob 특수 문자 이름 — 시험_A 비우기에 안 걸려야
        r = await cache._get_redis()
        before = await r.dbsize()
        deleted = await cache.invalidate_namespace("시험_A")
        keys = sorted(k.decode() for k in [k async for k in r.scan_iter("semcache:*")])
        hit_after = await cache.get_cached("시험_A", "rag", _vec(1))
        hit_other = await cache.get_cached("시험_B", "rag", _vec(3))
        return before, deleted, keys, hit_after, hit_other

    before, deleted, keys, hit_after, hit_other = _run(cache, scenario)
    assert before == 4
    assert deleted == 2                              # 반환값과
    assert len(keys) == 2                            # 실제로 남은 키 개수가 같다
    assert all(not k.startswith("semcache:시험_A:") for k in keys)
    assert hit_after is None                         # 비운 뒤 같은 질문은 캐시에 안 걸린다
    assert hit_other["query"] == "q3" and hit_other["_cache_key"].startswith("semcache:시험_B:")   # 다른 컬렉션은 그대로 + 어느 키인지


def test_hit_key_can_be_deleted_exactly(cache):
    """근거가 죽은 캐시를 버릴 때 그 키만 지운다(리뷰 L5) — 적중이 돌려준 키로 delete_entry → 같은 질문 다시 물으면 미스, 다른 키는 그대로."""
    async def scenario():
        await cache.set_cached("시험_앱 시스템", "rag", _vec(1), {"query": "q1"})   # 띄어쓰기 있는 이름
        await cache.set_cached("시험_앱 시스템", "rag", _vec(2), {"query": "q2"})
        hit = await cache.get_cached("시험_앱 시스템", "rag", _vec(1))
        ok = await cache.delete_entry(hit["_cache_key"])
        again = await cache.get_cached("시험_앱 시스템", "rag", _vec(1))
        r = await cache._get_redis()
        return ok, again, await r.dbsize()

    ok, again, left = _run(cache, scenario)
    assert ok is True and again is None and left == 1


def test_invalidate_glob_named_namespace_is_exact(cache):
    async def scenario():
        await cache.set_cached("시험_A", "rag", _vec(1), {"query": "q1"})
        await cache.set_cached("시험_A*", "rag", _vec(4), {"query": "q4"})
        r = await cache._get_redis()
        deleted = await cache.invalidate_namespace("시험_A*")
        left = [k.decode() async for k in r.scan_iter("semcache:*")]
        return deleted, left

    deleted, left = _run(cache, scenario)
    assert deleted == 1
    assert len(left) == 1 and left[0].startswith("semcache:시험_A:")


def test_invalidate_all_counts_match_remaining_zero(cache):
    async def scenario():
        for i in range(7):
            await cache.set_cached(f"시험_{i % 3}", "rag", _vec(10 + i), {"query": f"q{i}"})
        r = await cache._get_redis()
        deleted = await cache.invalidate_all()
        left = [k async for k in r.scan_iter("semcache:*")]
        return deleted, len(left)

    deleted, left = _run(cache, scenario)
    assert (deleted, left) == (7, 0)
