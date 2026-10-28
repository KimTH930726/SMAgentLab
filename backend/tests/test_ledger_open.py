"""AI 판단 원장 중간 이력(10/28) — 등록 화면이 미리 시작한 위치 판단 중 버린 것만 지운다: 내 것 · 확정 전 · 그 종류 · 쓴 판단(keep) 제외.
확정 안 된 채 오래된 줄은 판단을 새로 낼 때 같이 정리(OPEN_TTL_HOURS)."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_spec = _ilu.spec_from_file_location("ledger_open_under_test", str(Path(__file__).resolve().parent.parent / "service" / "decision" / "ledger.py"))
led = _ilu.module_from_spec(_spec)
sys.modules["ledger_open_under_test"] = led
_spec.loader.exec_module(led)


@pytest.mark.asyncio
async def test_discard_only_open_own_kind_and_never_keep():
    conn = MagicMock(execute=AsyncMock(return_value="DELETE 1"))
    n = await led.discard_open(conn, "knowledge_location", [5, 6, 7], user_id=3, keep=6)
    sql, ids, kind, uid = conn.execute.call_args.args
    assert (ids, kind, uid, n) == ([5, 7], "knowledge_location", 3, 1)
    assert "final_value IS NULL" in sql and "created_by_user_id IS NOT DISTINCT FROM $3" in sql and "kind = $2" in sql


@pytest.mark.asyncio
async def test_discard_nothing_left_skips_db():
    conn = MagicMock(execute=AsyncMock())
    assert await led.discard_open(conn, "knowledge_location", [6], user_id=3, keep=6) == 0
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_purge_stale_only_open_and_old():
    conn = MagicMock(execute=AsyncMock(return_value="DELETE 2"))
    assert await led.purge_stale_open(conn, "knowledge_location") == 2
    sql, kind, hours = conn.execute.call_args.args
    assert "final_value IS NULL" in sql and "created_at < NOW()" in sql and (kind, hours) == ("knowledge_location", led.OPEN_TTL_HOURS)
