"""채팅 SSE 연결 유지 신호(11/06 반입 전 검사 B3) — service/chat/router.py `_with_keepalive`.

AWS ALB는 60초 동안 바이트가 없으면 끊는다. 검색·LLM 첫 글자까지 길어도 핑이 나가야 하고, 이벤트는 순서·내용 그대로,
오류는 원래대로 올라오고, 화면이 끊으면 안쪽 스트림이 바로 멈춰야 한다(LLM이 계속 돌면 비용).
router.py는 무거워 함수 소스만 떼어 실행한다(test_policy_visibility와 같은 방식)."""
import ast
import asyncio
import contextlib
from pathlib import Path

import pytest

_SRC = (Path(__file__).resolve().parent.parent / "service" / "chat" / "router.py").read_text(encoding="utf-8")


def _load():
    tree = ast.parse(_SRC)
    keep = [n for n in tree.body
            if (isinstance(n, ast.AsyncFunctionDef) and n.name == "_with_keepalive")
            or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("_KEEPALIVE_SEC", "_SSE_PING", "_STREAM_END") for t in n.targets))]
    ns = {"asyncio": asyncio, "contextlib": contextlib}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "router.py", "exec"), ns)
    return ns["_with_keepalive"]


_with_keepalive = _load()


async def _collect(gen):
    return [x async for x in gen]


def test_events_pass_through_in_order_without_pings_when_fast():
    async def events():
        for i in range(5):
            yield {"i": i}
    out = asyncio.run(_collect(_with_keepalive(events(), interval=1)))
    assert out == [{"i": i} for i in range(5)]


def test_pings_while_waiting_then_event():
    async def events():
        await asyncio.sleep(0.35)
        yield {"type": "token"}
    out = asyncio.run(_collect(_with_keepalive(events(), interval=0.1)))
    assert out[-1] == {"type": "token"}
    assert out[:-1] and all(x is None for x in out[:-1])   # 기다린 동안 핑(None)만
    assert len(out[:-1]) >= 2


def test_error_from_stream_is_raised_after_earlier_events():
    async def events():
        yield {"a": 1}
        raise RuntimeError("boom")
    got = []

    async def run():
        async for x in _with_keepalive(events(), interval=1):
            got.append(x)
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(run())
    assert got == [{"a": 1}]


def test_closing_consumer_stops_inner_stream():
    """화면이 끊음(aclose) → 안쪽 스트림이 취소돼 더 만들지 않는다."""
    state = {"made": 0, "cancelled": False}

    async def events():
        try:
            while True:
                state["made"] += 1
                yield state["made"]
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def run():
        gen = _with_keepalive(events(), interval=1)
        assert await gen.__anext__() == 1
        await gen.aclose()
        assert state["cancelled"] is True   # 기다리지 않고 바로 — aclose가 끝난 시점에 안쪽 정리도 끝(리뷰 M1, sleep이 가렸던 것)
        made = state["made"]
        await asyncio.sleep(0.05)
        return made

    made_at_close = asyncio.run(run())
    assert state["made"] == made_at_close   # 닫은 뒤 더 만들지 않음


def test_inner_cleanup_finishes_before_consumer_cleanup():
    """연결이 끊기면 에이전트 쪽 취소 정리(DB 저장 등 await 포함)가 바깥 안전장치보다 먼저 끝나야 한다 — 예전 같은 태스크 순서(리뷰 M1)."""
    order = []

    async def events():
        try:
            yield 1
            await asyncio.sleep(10)
        finally:
            await asyncio.sleep(0.02)   # 정리 중 DB 쓰기 흉내
            order.append("agent cleanup")

    async def consumer():
        try:
            async with contextlib.aclosing(_with_keepalive(events(), interval=5)) as it:
                async for _ in it:
                    pass
        finally:
            order.append("safety net")

    async def run():
        t = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(run())
    assert order == ["agent cleanup", "safety net"]


def test_cancel_while_waiting_stops_inner_stream():
    """응답 태스크가 취소됨(연결 끊김, 이벤트 기다리는 중) → 안쪽 스트림도 취소."""
    state = {"cancelled": False}

    async def events():
        try:
            await asyncio.sleep(10)
            yield 1
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def run():
        task = asyncio.create_task(_collect(_with_keepalive(events(), interval=5)))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.01)

    asyncio.run(run())
    assert state["cancelled"] is True
