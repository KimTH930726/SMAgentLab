"""로그인 실패 제한 — Redis가 없어도 잠금이 동작해야 한다(예전엔 Redis 장애 동안 무차별 대입 방어가 꺼졌다, 2026-10-20)."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_spec = importlib.util.spec_from_file_location("rate_limit_under_test", str(Path(__file__).resolve().parents[1] / "shared/rate_limit.py"))
with patch.dict(sys.modules, {"core.config": SimpleNamespace(settings=SimpleNamespace(redis_url=""))}):
    rl = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(rl)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    rl._mem_count.clear()
    rl._mem_locked.clear()
    monkeypatch.setattr(rl, "settings", SimpleNamespace(redis_url=""))   # Redis 없음
    monkeypatch.setattr(rl, "_redis_client", None)


@pytest.mark.asyncio
async def test_locks_after_max_attempts_without_redis():
    for _ in range(rl.MAX_ATTEMPTS - 1):
        await rl.record_failure("kim", "1.2.3.4")
    assert not await rl.is_locked("kim", "1.2.3.4")
    await rl.record_failure("kim", "1.2.3.4")
    assert await rl.is_locked("kim", None)            # 사용자 기준
    assert await rl.is_locked("lee", "1.2.3.4")       # 같은 IP의 다른 사용자도


@pytest.mark.asyncio
async def test_window_and_lockout_expire(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: t[0])
    for _ in range(rl.MAX_ATTEMPTS - 1):
        await rl.record_failure("kim", None)
    t[0] += rl.WINDOW_SECONDS + 1                      # 창이 지나면 다시 센다
    await rl.record_failure("kim", None)
    assert not await rl.is_locked("kim", None)
    for _ in range(rl.MAX_ATTEMPTS):
        await rl.record_failure("kim", None)
    assert await rl.is_locked("kim", None)
    t[0] += rl.LOCKOUT_SECONDS + 1                     # 잠금은 풀린다
    assert not await rl.is_locked("kim", None)


@pytest.mark.asyncio
async def test_success_resets():
    for _ in range(rl.MAX_ATTEMPTS):
        await rl.record_failure("kim", None)
    await rl.reset("kim", None)
    assert not await rl.is_locked("kim", None)


class _FailingRedis:
    async def get(self, *a):
        raise ConnectionError("down")

    async def incr(self, *a):
        raise ConnectionError("down")


@pytest.mark.asyncio
async def test_redis_failing_midway_falls_back_to_memory(monkeypatch):
    monkeypatch.setattr(rl, "settings", SimpleNamespace(redis_url="redis://x"))
    monkeypatch.setattr(rl, "_redis_client", _FailingRedis())
    for _ in range(rl.MAX_ATTEMPTS):
        await rl.record_failure("kim", None)          # Redis 기록 실패 → 메모리로
    assert await rl.is_locked("kim", None)             # Redis 조회가 실패해도 메모리 잠금은 유효


@pytest.mark.asyncio
async def test_memory_records_pruned_when_large(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: t[0])
    monkeypatch.setattr(rl, "_MEM_PRUNE_AT", 5)
    for i in range(6):
        await rl.record_failure(f"u{i}", None)
    t[0] += rl.WINDOW_SECONDS + 1
    await rl.record_failure("new", None)
    assert set(rl._mem_count) == {rl._key("user", "new")}   # 지난 기록 정리


@pytest.mark.asyncio
async def test_redis_reconnect_not_retried_every_call(monkeypatch):
    calls = []

    class _R:
        @staticmethod
        def from_url(*a, **k):
            calls.append(1)
            raise ConnectionError("down")
    monkeypatch.setattr(rl, "settings", SimpleNamespace(redis_url="redis://x"))
    monkeypatch.setitem(sys.modules, "redis.asyncio", _R)
    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(asyncio=_R))
    monkeypatch.setattr(rl, "_redis_failed_at", 0.0)
    await rl.is_locked("kim", None)
    await rl.is_locked("kim", None)
    assert len(calls) == 1                              # 30초 안엔 다시 안 붙어 본다
