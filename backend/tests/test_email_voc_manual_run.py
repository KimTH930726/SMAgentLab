"""[지금 실행] 뒤에서 돌리기(11/06, service/email_voc/manual_run.py) — 잡기는 한 번만 · 끝나면 결과 · 실패하면 오류가 상태에 남는다.

conftest가 service 패키지를 가짜로 바꿔 두므로 파일 경로로 직접 로드하고, pipeline·core.database는 이 모듈에만 가짜로 끼운다.
"""
import asyncio
import importlib.util as _ilu
import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent
sys.modules.setdefault("service.email_voc", MagicMock())
sys.modules.setdefault("service.email_voc.pipeline", MagicMock())
_spec = _ilu.spec_from_file_location("email_voc_manual_run_under_test", str(_backend_dir / "service" / "email_voc" / "manual_run.py"))
mr = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = mr
_spec.loader.exec_module(mr)

D1, D2 = date(2026, 11, 1), date(2026, 11, 6)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    conn = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(mr, "get_conn", MagicMock(return_value=ctx))
    monkeypatch.setattr(mr, "resolve_namespace_id", AsyncMock(return_value=1))
    monkeypatch.setattr(mr, "_runs", {})
    pl = MagicMock()
    monkeypatch.setattr(mr, "pipeline", pl)
    return pl


async def _finish():
    for t in list(mr._tasks):
        await t


@pytest.mark.asyncio
async def test_claim_once_then_result(_isolate):
    gate = asyncio.Event()
    result = {"date_from": D1, "date_to": D2, "mailboxes": []}

    async def fake_run(ns, df, dt, *, progress):
        progress.update(phase="analyze", mailboxes=1, mailbox=1, total=2)
        await gate.wait()
        progress["done"] = 2
        return result
    _isolate.run_manual_collection = fake_run

    first = await mr.start("ns", D1, D2)
    assert first["running"] is True and first["result"] is None
    with pytest.raises(mr.AlreadyRunning):
        await mr.start("ns", D1, D2)
    await asyncio.sleep(0)
    mid = mr.status("ns")
    assert mid["running"] and (mid["done"], mid["total"]) == (0, 2)
    other = await mr.start("다른 ns", D1, D2)   # namespace가 다르면 따로 돈다
    assert other["running"]

    gate.set()
    await _finish()
    end = mr.status("ns")
    assert end["running"] is False and end["result"] is result and end["error"] is None and end["finished_at"]
    assert (end["done"], end["total"]) == (2, 2)
    again = await mr.start("ns", D1, D2)   # 끝난 뒤엔 다시 잡힌다
    assert again["running"]
    await _finish()


@pytest.mark.asyncio
async def test_failure_is_visible_in_status(_isolate):
    _isolate.run_manual_collection = AsyncMock(side_effect=RuntimeError("boom"))
    await mr.start("ns", D1, D2)
    await _finish()
    st = mr.status("ns")
    assert st["running"] is False and st["result"] is None and "RuntimeError" in st["error"]
    assert "boom" not in st["error"]   # 내부 메시지는 로그로만


@pytest.mark.asyncio
async def test_unknown_namespace_rejected_before_claim(_isolate, monkeypatch):
    monkeypatch.setattr(mr, "resolve_namespace_id", AsyncMock(return_value=None))
    with pytest.raises(ValueError):
        await mr.start("없음", D1, D2)
    assert mr.status("없음") is None and not mr._tasks


def test_no_status_before_any_run():
    assert mr.status("처음") is None
