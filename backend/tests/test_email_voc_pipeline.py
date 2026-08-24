"""Tests for service/email_voc/pipeline.py — _existing_message_ids/list_history/get_knowledge_refs.

이 모듈은 오늘까지 유닛테스트가 하나도 없이 실 collect/run 파이프라인 실행으로만
검증돼왔다 — 자가검증 점검 중 발견한 구멍을 메운다.

conftest.py가 격리 목적으로 sys.modules["service"] 전체를 MagicMock으로 치환해두므로
(teams_notify/graph_client 테스트와 동일한 문제), 파일 경로 기반으로 직접 로드한다.
core.database는 conftest.py가 이미 모듈 단위로 mock해뒀지만, 그 mock은 전체 테스트
스위트가 공유하는 싱글턴이라 여기서 그대로 건드리면 다른 테스트에 값이 새어나갈 위험이
있다 — 대신 각 테스트마다 이 모듈에 로컬 fake connection을 patch해서 격리한다.
"""
import importlib.util as _ilu
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent

# pipeline.py는 모듈 최상단에서 `from service.email_voc import delegated_auth,
# graph_client, routing_service, teams_notify`를 한다 — conftest.py가 sys.modules
# ["service"]를 MagicMock으로 통째로 치환해둬서(__path__가 없어) 그냥은 이 서브패키지
# import 자체가 "'service' is not a package"로 실패한다. teams_notify/service.py
# 단독 로드 테스트에는 없던 문제 — 그 파일들은 service.email_voc 서브모듈을 직접
# import하지 않기 때문. 여기서만 필요한 스텁을 미리 등록해준다.
_email_voc_pkg = MagicMock()
sys.modules["service.email_voc"] = _email_voc_pkg
for _submod in ("delegated_auth", "graph_client", "routing_service", "teams_notify", "service"):
    sys.modules[f"service.email_voc.{_submod}"] = MagicMock()

_spec = _ilu.spec_from_file_location(
    "email_voc_pipeline_under_test", str(_backend_dir / "service" / "email_voc" / "pipeline.py"),
)
pipeline = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = pipeline
_spec.loader.exec_module(pipeline)


def _make_fake_conn(fetch_return=None):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=fetch_return or [])
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    """이 모듈(pipeline)이 바인딩해둔 get_conn/resolve_namespace_id를 로컬로 교체.

    반환값: (fake_conn 설정 함수, resolve_namespace_id 반환값 설정 함수)
    """
    conn = _make_fake_conn()
    monkeypatch.setattr(pipeline, "get_conn", MagicMock(return_value=conn))
    resolve_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(pipeline, "resolve_namespace_id", resolve_mock)
    return conn, resolve_mock


class TestExistingMessageIds:
    @pytest.mark.asyncio
    async def test_empty_input_skips_db_call(self, patch_db):
        conn, _ = patch_db
        result = await pipeline._existing_message_ids(1, [])
        assert result == set()
        conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_set_of_existing_ids(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = [{"source_message_id": "a"}, {"source_message_id": "b"}]
        result = await pipeline._existing_message_ids(1, ["a", "b", "c"])
        assert result == {"a", "b"}
        conn.fetch.assert_awaited_once()
        args = conn.fetch.call_args.args
        assert args[1] == 1
        assert args[2] == ["a", "b", "c"]


class TestGetKnowledgeRefs:
    @pytest.mark.asyncio
    async def test_empty_ids_skips_db_call(self, patch_db):
        conn, resolve_mock = patch_db
        result = await pipeline.get_knowledge_refs("ns", [])
        assert result == []
        conn.fetch.assert_not_called()
        resolve_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_namespace_returns_empty(self, patch_db):
        conn, resolve_mock = patch_db
        resolve_mock.return_value = None
        result = await pipeline.get_knowledge_refs("no-such-ns", [1, 2])
        assert result == []
        conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_matched_rows_scoped_to_namespace(self, patch_db):
        conn, resolve_mock = patch_db
        resolve_mock.return_value = 42
        conn.fetch.return_value = [
            {"id": 1, "content": "내용1", "category": "DB", "container_name": None},
            {"id": 2, "content": "내용2", "category": None, "container_name": "container"},
        ]
        result = await pipeline.get_knowledge_refs("딜리버스 DB", [1, 2, 999])
        assert result == [
            {"id": 1, "content": "내용1", "category": "DB", "container_name": None},
            {"id": 2, "content": "내용2", "category": None, "container_name": "container"},
        ]
        args = conn.fetch.call_args.args
        assert args[1] == 42  # namespace_id로 스코핑됐는지
        assert args[2] == [1, 2, 999]


def _stub_routing_row(**overrides) -> dict:
    row = {
        "id": 10, "mailbox_upn": "voc@example.com", "part": "테스트", "is_active": True,
        "teams_webhook_url": "https://webhook.example.com", "mail_folder_id": None,
        "oncall_contact_name": None,
    }
    row.update(overrides)
    return row


def _stub_message(**overrides) -> dict:
    msg = {
        "id": "msg1", "subject": "배달이 너무 늦어요", "sender": "a@example.com",
        "received_at": None, "body": "배달이 너무 늦게 와서 불만입니다",
    }
    msg.update(overrides)
    return msg


class TestRunManualCollectionCategoryGate:
    """IT와 무관한 단순 CS 불만까지 너무 많이 Teams로 온다는 실사용 피드백 —
    category=not_it_related이면 이력에는 남기되 Teams 발송은 생략하는 게이트 검증.
    """

    def _patch_common(self, monkeypatch, *, category: str, relevance_score: float = 0.9):
        monkeypatch.setattr(pipeline.routing_service, "list_routing", AsyncMock(return_value=[_stub_routing_row()]))
        monkeypatch.setattr(
            pipeline.routing_service, "get_settings",
            AsyncMock(return_value={"email_relevance_min_score": 0.3}),
        )
        monkeypatch.setattr(pipeline.graph_client, "fetch_messages", AsyncMock(return_value=[_stub_message()]))
        monkeypatch.setattr(pipeline, "_existing_message_ids", AsyncMock(return_value=set()))
        monkeypatch.setattr(pipeline, "_strip_forwarded_chain", lambda b: b)
        monkeypatch.setattr(
            pipeline, "check_relevance",
            AsyncMock(return_value=SimpleNamespace(top_score=relevance_score, mapped_term=None, results=[], context="")),
        )
        monkeypatch.setattr(
            pipeline, "analyze_email",
            AsyncMock(return_value={
                "category": category, "severity": "high", "mismatch_flagged": False,
                "knowledge_ref_ids": [], "knowledge_refs": [], "resolution_draft": None,
                "reasoning": "배송 자체에 대한 불만으로 IT 시스템 문제 아님", "mapped_term": None,
            }),
        )
        # teams_notify는 파일 상단에서 MagicMock으로 스텁된 서브모듈 — 발송 함수를
        # AsyncMock으로 교체해 실제로 호출됐는지/안 됐는지 검증 가능하게 한다.
        monkeypatch.setattr(pipeline.teams_notify, "build_teams_message", MagicMock(return_value={"text": "x"}))
        monkeypatch.setattr(
            pipeline.teams_notify, "send_teams_notification", AsyncMock(return_value=(True, None)),
        )

    @pytest.mark.asyncio
    async def test_not_it_related_skips_teams_but_records_history(self, patch_db, monkeypatch):
        conn, _ = patch_db
        conn.fetchrow.return_value = {"id": 1}
        self._patch_common(monkeypatch, category="not_it_related")

        result = await pipeline.run_manual_collection(
            "ns", date(2026, 8, 1), date(2026, 8, 21), access_token="tok", skip_credential_resolution=True,
        )

        mailbox_result = result["mailboxes"][0]
        assert mailbox_result["skipped_not_it"] == 1
        assert mailbox_result["analyzed"] == 1
        assert mailbox_result["notified"] == 0
        pipeline.teams_notify.send_teams_notification.assert_not_called()
        # record_analysis()가 실제로 INSERT를 실행했는지 — 이력에는 남아야 함
        conn.fetchrow.assert_awaited()

    @pytest.mark.asyncio
    async def test_system_error_still_notifies(self, patch_db, monkeypatch):
        """게이트가 not_it_related만 걸러내고 다른 카테고리는 그대로 통과하는지 회귀 확인."""
        conn, _ = patch_db
        conn.fetchrow.return_value = {"id": 1}
        self._patch_common(monkeypatch, category="system_error")

        result = await pipeline.run_manual_collection(
            "ns", date(2026, 8, 1), date(2026, 8, 21), access_token="tok", skip_credential_resolution=True,
        )

        mailbox_result = result["mailboxes"][0]
        assert mailbox_result["skipped_not_it"] == 0
        assert mailbox_result["notified"] == 1
        pipeline.teams_notify.send_teams_notification.assert_awaited_once()


class TestListHistory:
    @pytest.mark.asyncio
    async def test_unknown_namespace_returns_empty(self, patch_db):
        conn, resolve_mock = patch_db
        resolve_mock.return_value = None
        result = await pipeline.list_history("no-such-ns")
        assert result == []
        conn.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_filters_returns_rows_as_dicts(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = [{"id": 1, "subject": "s"}]
        result = await pipeline.list_history("ns", limit=50, offset=0)
        assert result == [{"id": 1, "subject": "s"}]

    @pytest.mark.asyncio
    async def test_filters_are_combined_with_and(self, patch_db):
        conn, _ = patch_db
        await pipeline.list_history(
            "ns", severity="high", status="notified", mismatch_only=True, keyword="결제",
        )
        sql = conn.fetch.call_args.args[0]
        assert "a.severity = $" in sql
        assert "a.status = $" in sql
        assert "a.mismatch_flagged = true" in sql
        assert "ILIKE" in sql
        params = conn.fetch.call_args.args[1:]
        assert "high" in params
        assert "notified" in params
        assert "%결제%" in params

    @pytest.mark.asyncio
    async def test_no_filters_omits_optional_conditions(self, patch_db):
        conn, _ = patch_db
        await pipeline.list_history("ns")
        sql = conn.fetch.call_args.args[0]
        assert "a.severity = $" not in sql
        assert "a.status = $" not in sql
        assert "a.mismatch_flagged = true" not in sql
        assert "ILIKE" not in sql
