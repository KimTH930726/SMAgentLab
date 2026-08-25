"""Tests for service/email_voc/pattern_detection.py — 반복 VOC 클러스터링 로직.

conftest.py가 sys.modules["service"]를 MagicMock으로 치환해두므로(다른 email_voc
테스트와 동일한 문제), 파일 경로 기반으로 직접 로드한다. 이 모듈은 service.email_voc
서브모듈을 import하지 않아 pipeline.py 테스트처럼 별도 스텁 등록이 필요 없다.
"""
import importlib.util as _ilu
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent
_spec = _ilu.spec_from_file_location(
    "email_voc_pattern_detection_under_test", str(_backend_dir / "service" / "email_voc" / "pattern_detection.py"),
)
pattern_detection = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = pattern_detection
_spec.loader.exec_module(pattern_detection)


def _make_fake_conn():
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    conn = _make_fake_conn()
    monkeypatch.setattr(pattern_detection, "get_conn", MagicMock(return_value=conn))
    resolve_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(pattern_detection, "resolve_namespace_id", resolve_mock)
    return conn, resolve_mock


_SETTINGS = {"email_pattern_similarity_threshold": 0.85, "email_pattern_window_days": 7, "email_pattern_min_count": 3}
_NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)


class TestNormalizeSubject:
    def test_strips_korean_reply_prefix(self):
        assert pattern_detection._normalize_subject("회신: 배달 문의") == "배달 문의"

    def test_strips_re_prefix_case_insensitive(self):
        assert pattern_detection._normalize_subject("RE: 배달 문의") == "배달 문의"
        assert pattern_detection._normalize_subject("Re: 배달 문의") == "배달 문의"

    def test_strips_nested_reply_prefixes(self):
        assert pattern_detection._normalize_subject("Re: RE: Fwd: 배달 문의") == "배달 문의"

    def test_no_prefix_passthrough(self):
        assert pattern_detection._normalize_subject("배달 문의") == "배달 문의"

    def test_none_subject_returns_empty(self):
        assert pattern_detection._normalize_subject(None) == ""


class TestDetectAndUpdateCluster:
    @pytest.mark.asyncio
    async def test_no_similar_candidates_returns_none(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = []
        result = await pattern_detection.detect_and_update_cluster(
            1, 100, "배달 지연 문의", [0.1, 0.2], _NOW, _SETTINGS,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_candidates_all_same_thread_returns_none(self, patch_db):
        """같은 스레드 답장뿐이면(정규화 제목 동일) 반복 발생으로 세지 않는다."""
        conn, _ = patch_db
        conn.fetch.return_value = [
            {"id": 1, "subject": "RE: 배달 지연 문의", "voc_cluster_id": None},
            {"id": 2, "subject": "Re: RE: 배달 지연 문의", "voc_cluster_id": None},
        ]
        result = await pattern_detection.detect_and_update_cluster(
            1, 100, "배달 지연 문의", [0.1, 0.2], _NOW, _SETTINGS,
        )
        assert result is None
        conn.fetchrow.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_new_cluster_when_no_existing_cluster_id(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = [
            {"id": 1, "subject": "배달 오배송 불만", "voc_cluster_id": None},
        ]
        conn.fetchrow.return_value = {
            "id": 55, "member_count": 2, "notified_at": None, "representative_subject": "배달 오배송 불만",
        }
        result = await pattern_detection.detect_and_update_cluster(
            1, 100, "배달이 잘못 왔어요", [0.1, 0.2], _NOW, _SETTINGS,
        )
        assert result["cluster_id"] == 55
        assert result["member_count"] == 2
        assert result["trigger"] is None  # min_count=3인데 아직 2건
        insert_sql = conn.fetchrow.call_args_list[0].args[0]
        assert "INSERT INTO ops_voc_cluster" in insert_sql
        # 새 클러스터의 대표 건(가장 유사한 이전 건)에도 voc_cluster_id가 채워져야 함
        update_calls = [c.args for c in conn.execute.call_args_list]
        assert any(args[1:] == (55, 1) for args in update_calls)
        assert any(args[1:] == (55, 100) for args in update_calls)

    @pytest.mark.asyncio
    async def test_joins_existing_cluster(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = [
            {"id": 1, "subject": "배달 오배송 불만", "voc_cluster_id": 55},
        ]
        conn.fetchrow.return_value = {
            "id": 55, "member_count": 3, "notified_at": None, "representative_subject": "배달 오배송 불만",
        }
        result = await pattern_detection.detect_and_update_cluster(
            1, 100, "배달이 또 잘못 왔어요", [0.1, 0.2], _NOW, _SETTINGS,
        )
        assert result["cluster_id"] == 55
        update_sql = conn.fetchrow.call_args_list[0].args[0]
        assert "UPDATE ops_voc_cluster SET member_count = member_count + 1" in update_sql

    @pytest.mark.asyncio
    async def test_trigger_fires_when_min_count_first_crossed(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = [
            {"id": 1, "subject": "배달 오배송 불만 A", "voc_cluster_id": 55},
            {"id": 2, "subject": "배달 오배송 불만 B", "voc_cluster_id": 55},
        ]
        conn.fetchrow.return_value = {
            "id": 55, "member_count": 3, "notified_at": None, "representative_subject": "배달 오배송 불만",
        }
        result = await pattern_detection.detect_and_update_cluster(
            1, 100, "배달이 또또 잘못 왔어요", [0.1, 0.2], _NOW, _SETTINGS,
        )
        assert result["trigger"] is not None
        assert result["trigger"]["member_count"] == 3
        assert result["trigger"]["sample_subjects"] == ["배달 오배송 불만 A", "배달 오배송 불만 B"]
        notify_calls = [c.args[0] for c in conn.execute.call_args_list]
        assert any("notified_at = NOW()" in sql for sql in notify_calls)

    @pytest.mark.asyncio
    async def test_no_retrigger_once_already_notified(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = [
            {"id": 1, "subject": "배달 오배송 불만 A", "voc_cluster_id": 55},
        ]
        conn.fetchrow.return_value = {
            "id": 55, "member_count": 5, "notified_at": _NOW, "representative_subject": "배달 오배송 불만",
        }
        result = await pattern_detection.detect_and_update_cluster(
            1, 100, "배달이 다섯번째로 잘못 왔어요", [0.1, 0.2], _NOW, _SETTINGS,
        )
        assert result["trigger"] is None

    @pytest.mark.asyncio
    async def test_sample_subjects_dedupes_and_excludes_representative(self, patch_db):
        """흔한 제목("[파손] 음료 쏟아짐 불만" 등)은 서로 다른 고객이 똑같이 쓰는 경우가
        많다 — 대표 제목과 겹치는 게 "다른 사례"로 다시 나열되면 헷갈린다는 실사용 피드백."""
        conn, _ = patch_db
        conn.fetch.return_value = [
            {"id": 1, "subject": "[파손] 음료 쏟아짐 불만", "voc_cluster_id": 55},  # 대표와 동일
            {"id": 2, "subject": "[파손] 음료 쏟아짐 불만", "voc_cluster_id": 55},  # 위와 중복
            {"id": 3, "subject": "뚜껑 열림", "voc_cluster_id": 55},
        ]
        conn.fetchrow.return_value = {
            "id": 55, "member_count": 4, "notified_at": None, "representative_subject": "[파손] 음료 쏟아짐 불만",
        }
        result = await pattern_detection.detect_and_update_cluster(
            1, 100, "음료가 또 쏟아졌어요", [0.1, 0.2], _NOW, _SETTINGS,
        )
        assert result["trigger"]["sample_subjects"] == ["뚜껑 열림"]


class TestGetClusterCoverage:
    @pytest.mark.asyncio
    async def test_covered_when_similarity_above_threshold(self, patch_db):
        conn, _ = patch_db
        conn.fetchrow.return_value = {"knowledge_id": 42, "similarity": 0.81, "snippet": "재배송 처리 절차..."}
        result = await pattern_detection.get_cluster_coverage(1, 55)
        assert result == {"covered": True, "snippet": "재배송 처리 절차..."}

    @pytest.mark.asyncio
    async def test_not_covered_when_similarity_below_threshold(self, patch_db):
        conn, _ = patch_db
        conn.fetchrow.return_value = {"knowledge_id": 9, "similarity": 0.4, "snippet": "무관한 문서"}
        result = await pattern_detection.get_cluster_coverage(1, 55)
        assert result == {"covered": False, "snippet": None}

    @pytest.mark.asyncio
    async def test_no_matching_knowledge_at_all(self, patch_db):
        conn, _ = patch_db
        conn.fetchrow.return_value = None
        result = await pattern_detection.get_cluster_coverage(1, 55)
        assert result == {"covered": False, "snippet": None}


class TestListClusters:
    @pytest.mark.asyncio
    async def test_unknown_namespace_returns_empty(self, patch_db):
        _, resolve_mock = patch_db
        resolve_mock.return_value = None
        result = await pattern_detection.list_clusters("no-such-ns")
        assert result == []

    @pytest.mark.asyncio
    async def test_covered_and_uncovered_clusters(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = [
            {
                "id": 1, "representative_subject": "배달 오배송 불만", "member_count": 5,
                "first_seen_at": _NOW, "last_seen_at": _NOW, "notified_at": _NOW,
                "knowledge_id": 42, "similarity": 0.81, "snippet": "오배송 처리 절차...",
                "primary_category": "system_error", "primary_severity": "high",
                "category_breakdown": '[{"category": "system_error", "count": 5}]',
            },
            {
                "id": 2, "representative_subject": "음료 쏟아짐", "member_count": 3,
                "first_seen_at": _NOW, "last_seen_at": _NOW, "notified_at": None,
                "knowledge_id": None, "similarity": None, "snippet": None,
                "primary_category": "not_it_related", "primary_severity": "medium",
                "category_breakdown": None,
            },
        ]
        result = await pattern_detection.list_clusters("ns")
        assert result[0]["has_knowledge_coverage"] is True
        assert result[0]["matched_knowledge_id"] == 42
        assert result[0]["primary_category"] == "system_error"
        assert result[0]["category_breakdown"] == [{"category": "system_error", "count": 5}]
        assert result[1]["has_knowledge_coverage"] is False
        assert result[1]["matched_knowledge_id"] is None
        assert result[1]["category_breakdown"] == []


class TestGetClusterMembers:
    @pytest.mark.asyncio
    async def test_unknown_namespace_returns_empty(self, patch_db):
        _, resolve_mock = patch_db
        resolve_mock.return_value = None
        result = await pattern_detection.get_cluster_members("no-such-ns", 1)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_members_as_dicts(self, patch_db):
        conn, _ = patch_db
        conn.fetch.return_value = [{"id": 1, "subject": "s", "sender": "a@b.com"}]
        result = await pattern_detection.get_cluster_members("ns", 55)
        assert result == [{"id": 1, "subject": "s", "sender": "a@b.com"}]
        args = conn.fetch.call_args.args
        assert args[1] == 1  # ns_id
        assert args[2] == 55  # cluster_id
