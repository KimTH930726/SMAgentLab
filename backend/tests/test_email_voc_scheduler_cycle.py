"""VOC 스케줄러 사이클 집계 — 메일함 결과가 사이클 표(ops_email_poll_cycle) 줄로 빠짐없이 합쳐지는지(11/04 확인 실패 칸).

conftest.py가 sys.modules["service"]를 MagicMock으로 바꿔 두므로(다른 email_voc 테스트와 같음) 파일 경로로 직접 로드한다.
"""
import asyncio
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# scheduler가 `from service.email_voc import ...`를 하므로 그 패키지 자리에 가짜를 둔다(진짜 pipeline 등은 아래에서 함수 단위로 바꿈)
sys.modules.setdefault("service.email_voc", MagicMock())
_spec = _ilu.spec_from_file_location(
    "email_voc_scheduler_under_test", str(Path(__file__).resolve().parent.parent / "service" / "email_voc" / "scheduler.py"))
scheduler = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = scheduler
_spec.loader.exec_module(scheduler)


def _mailbox(**kw):
    base = {"mailbox_upn": "m@x", "ok": True, "fetched": 0, "analyzed": 0, "notified": 0, "notify_failed": 0,
            "skipped_duplicate": 0, "skipped_low_relevance": 0, "skipped_not_it": 0, "skipped_confirm_failed": 0}
    return {**base, **kw}


def test_cycle_sums_confirm_failed_across_namespaces_and_mailboxes(monkeypatch):
    results = {
        "A": {"mailboxes": [_mailbox(analyzed=3, skipped_confirm_failed=2), _mailbox(analyzed=1, skipped_confirm_failed=1)]},
        "B": {"mailboxes": [_mailbox(analyzed=5, notified=1), _mailbox(mailbox_upn="bad@x", ok=False, error="401", skipped_confirm_failed=4)]},
    }
    monkeypatch.setattr(scheduler, "_list_namespaces_with_active_routing", AsyncMock(return_value=["A", "B"]))
    monkeypatch.setattr(scheduler.routing_service, "get_graph_credentials_decrypted", AsyncMock(return_value={"x": 1}))
    monkeypatch.setattr(scheduler.pipeline, "run_manual_collection", AsyncMock(side_effect=lambda ns, *a, **k: results[ns]))
    recorded = {}

    async def _record(started, finished, agg, errors):
        recorded.update(agg=dict(agg), errors=list(errors))
    monkeypatch.setattr(scheduler, "_record_cycle", _record)
    scheduler._running_cycle = False

    asyncio.run(scheduler.run_cycle_now({"email_lookback_days": 1}))

    agg = recorded["agg"]
    assert agg["total_skipped_confirm_failed"] == 7     # 2 + 1 + 4 — 다른 합계처럼 중간에 실패한 메일함까지 센 만큼 더함
    assert agg["total_analyzed"] == 9 and agg["total_notified"] == 1
    assert (agg["mailboxes_ok"], agg["mailboxes_failed"], agg["namespaces_processed"]) == (3, 1, 2)
    assert recorded["errors"] == ["B/bad@x: 401"]


def test_cycle_tolerates_mailbox_result_without_confirm_failed(monkeypatch):
    """옛 결과(키 없음)도 0으로 — 사이클 기록이 KeyError로 통째 안 남는 일이 없게."""
    old = _mailbox(analyzed=2)
    del old["skipped_confirm_failed"]
    monkeypatch.setattr(scheduler, "_list_namespaces_with_active_routing", AsyncMock(return_value=["A"]))
    monkeypatch.setattr(scheduler.routing_service, "get_graph_credentials_decrypted", AsyncMock(return_value={"x": 1}))
    monkeypatch.setattr(scheduler.pipeline, "run_manual_collection", AsyncMock(return_value={"mailboxes": [old]}))
    recorded = {}

    async def _record(started, finished, agg, errors):
        recorded.update(agg=dict(agg))
    monkeypatch.setattr(scheduler, "_record_cycle", _record)
    scheduler._running_cycle = False

    asyncio.run(scheduler.run_cycle_now({"email_lookback_days": 1}))
    assert recorded["agg"]["total_skipped_confirm_failed"] == 0
