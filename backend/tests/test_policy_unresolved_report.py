"""Tests for service/policy/unresolved_report.py — unresolved 팀별 집계."""
import importlib.util as _ilu
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent

sys.modules["service.policy"] = MagicMock()
_spec = _ilu.spec_from_file_location(
    "service.policy.unresolved_report", str(_backend_dir / "service" / "policy" / "unresolved_report.py")
)
unresolved_report = _ilu.module_from_spec(_spec)
sys.modules["service.policy.unresolved_report"] = unresolved_report
_spec.loader.exec_module(unresolved_report)


def _make_fake_conn(rows=None):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=rows or [])
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    def _apply(rows=None):
        conn = _make_fake_conn(rows)
        monkeypatch.setattr(unresolved_report, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(unresolved_report, "resolve_namespace_id", AsyncMock(return_value=1))
        return conn
    return _apply


class TestGetUnresolvedSummary:
    @pytest.mark.asyncio
    async def test_namespace_not_found_raises(self, patch_db, monkeypatch):
        patch_db()
        monkeypatch.setattr(unresolved_report, "resolve_namespace_id", AsyncMock(return_value=None))
        with pytest.raises(ValueError, match="네임스페이스"):
            await unresolved_report.get_unresolved_summary("없는곳")

    @pytest.mark.asyncio
    async def test_no_rows_returns_empty_summary(self, patch_db):
        patch_db(rows=[])
        result = await unresolved_report.get_unresolved_summary("ns")
        assert result.total_items == 0
        assert result.total_segments == 0
        assert result.by_system == []

    @pytest.mark.asyncio
    async def test_groups_by_system_key_and_counts(self, patch_db):
        rows = [
            {
                "id": 1, "logical_id": 1, "system_key": "카드", "policy_name": "상태 정책",
                "category_path": ["2.상태"],
                "unresolved_segments": json.dumps([
                    {"text": "등록 : 미등록 → 등록", "reason": "상태 전이 규칙(A→B), 구조화 방법 미정"},
                ]),
            },
            {
                "id": 2, "logical_id": 2, "system_key": "카드", "policy_name": "해지 정책",
                "category_path": ["3.해지"],
                "unresolved_segments": json.dumps([
                    {"text": "해지 : 정상 → 해지", "reason": "상태 전이 규칙(A→B)"},
                    {"text": "부분 애매한 조항", "reason": "분류 불가"},
                ]),
            },
            {
                "id": 3, "logical_id": 3, "system_key": "딜리버스", "policy_name": "배달 정책",
                "category_path": ["1.배달"],
                "unresolved_segments": json.dumps([{"text": "미배정 상태", "reason": "상태 전이"}]),
            },
        ]
        patch_db(rows=rows)

        result = await unresolved_report.get_unresolved_summary("ns")

        assert result.total_items == 3
        assert result.total_segments == 4
        assert len(result.by_system) == 2
        # segment_count 내림차순 정렬 — 카드(3세그먼트)가 딜리버스(1세그먼트)보다 먼저
        assert result.by_system[0].system_key == "카드"
        assert result.by_system[0].item_count == 2
        assert result.by_system[0].segment_count == 3
        assert result.by_system[1].system_key == "딜리버스"
        assert result.by_system[1].segment_count == 1

    @pytest.mark.asyncio
    async def test_missing_system_key_grouped_as_unspecified(self, patch_db):
        rows = [{
            "id": 1, "logical_id": 1, "system_key": None, "policy_name": "정책",
            "category_path": [],
            "unresolved_segments": json.dumps([{"text": "내용", "reason": "사유"}]),
        }]
        patch_db(rows=rows)

        result = await unresolved_report.get_unresolved_summary("ns")

        assert result.by_system[0].system_key == "(미지정)"

    @pytest.mark.asyncio
    async def test_null_unresolved_segments_skipped(self, patch_db):
        rows = [{
            "id": 1, "logical_id": 1, "system_key": "카드", "policy_name": "정책",
            "category_path": [], "unresolved_segments": None,
        }]
        patch_db(rows=rows)

        result = await unresolved_report.get_unresolved_summary("ns")

        assert result.total_items == 0
        assert result.by_system == []

    @pytest.mark.asyncio
    async def test_dict_segments_not_json_string_handled(self, patch_db):
        # asyncpg는 JSONB 컬럼을 드라이버 설정에 따라 str 또는 dict로 반환할 수 있음 — 둘 다 처리
        rows = [{
            "id": 1, "logical_id": 1, "system_key": "카드", "policy_name": "정책",
            "category_path": [],
            "unresolved_segments": [{"text": "내용", "reason": "사유"}],
        }]
        patch_db(rows=rows)

        result = await unresolved_report.get_unresolved_summary("ns")

        assert result.total_items == 1
        assert result.by_system[0].items[0].segments[0].text == "내용"

    @pytest.mark.asyncio
    async def test_system_key_filter_passed_to_query(self, patch_db):
        conn = patch_db(rows=[])
        await unresolved_report.get_unresolved_summary("ns", system_key="카드")

        call = conn.fetch.call_args
        assert "system_key = $2" in call.args[0]
        assert call.args[-1] == "카드"

    @pytest.mark.asyncio
    async def test_no_system_key_filter_omits_clause(self, patch_db):
        conn = patch_db(rows=[])
        await unresolved_report.get_unresolved_summary("ns")

        call = conn.fetch.call_args
        assert "system_key = $2" not in call.args[0]
        assert len(call.args) == 2  # sql + ns_id만


class TestPromoteSegmentToNarrative:
    """2026-09-16 신규 — "조회만 있고 액션이 없다"는 지적으로 추가된 유일한 쓰기 액션."""

    def _make_conn(self, unresolved_segments, next_chunk_idx=0):
        # fetchval은 실제로 "SELECT COALESCE(MAX(chunk_idx)+1, 0)"을 모킹하는 자리라
        # +1까지 이미 계산된 "다음 인덱스" 값을 바로 준다(실제 쿼리가 그렇게 짜여있음).
        conn = MagicMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=False)
        conn.fetchrow = AsyncMock(return_value={"unresolved_segments": json.dumps(unresolved_segments)})
        conn.fetchval = AsyncMock(return_value=next_chunk_idx)
        conn.execute = AsyncMock()
        return conn

    @pytest.mark.asyncio
    async def test_namespace_not_found_raises(self, monkeypatch):
        monkeypatch.setattr(unresolved_report, "get_conn", MagicMock(return_value=self._make_conn([])))
        monkeypatch.setattr(unresolved_report, "resolve_namespace_id", AsyncMock(return_value=None))
        with pytest.raises(ValueError, match="네임스페이스"):
            await unresolved_report.promote_segment_to_narrative("없는곳", 1, 0)

    @pytest.mark.asyncio
    async def test_item_not_found_raises(self, monkeypatch):
        conn = self._make_conn([])
        conn.fetchrow = AsyncMock(return_value=None)
        monkeypatch.setattr(unresolved_report, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(unresolved_report, "resolve_namespace_id", AsyncMock(return_value=1))
        with pytest.raises(ValueError, match="정책 항목"):
            await unresolved_report.promote_segment_to_narrative("ns", 999, 0)

    @pytest.mark.asyncio
    async def test_out_of_range_index_raises(self, monkeypatch):
        conn = self._make_conn([{"text": "내용", "reason": "사유"}])
        monkeypatch.setattr(unresolved_report, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(unresolved_report, "resolve_namespace_id", AsyncMock(return_value=1))
        with pytest.raises(ValueError, match="segment_index"):
            await unresolved_report.promote_segment_to_narrative("ns", 1, 5)

    @pytest.mark.asyncio
    async def test_inserts_chunk_and_removes_segment(self, monkeypatch):
        conn = self._make_conn(
            [{"text": "첫 세그먼트", "reason": "사유1"}, {"text": "둘째 세그먼트", "reason": "사유2"}],
            next_chunk_idx=3,
        )
        monkeypatch.setattr(unresolved_report, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(unresolved_report, "resolve_namespace_id", AsyncMock(return_value=1))
        monkeypatch.setattr(unresolved_report.embedding_service, "embed", AsyncMock(return_value=[0.1] * 4))

        remaining = await unresolved_report.promote_segment_to_narrative("ns", 1, 0)

        assert remaining == 1  # 2개 중 0번 인덱스를 편입했으니 1개 남음

        insert_call = conn.execute.call_args_list[0]
        assert "INSERT INTO policy_chunk" in insert_call.args[0]
        assert insert_call.args[2] == "첫 세그먼트"  # chunk_text
        assert insert_call.args[4] == 3  # chunk_idx = MAX(2)+1

        update_call = conn.execute.call_args_list[1]
        assert "UPDATE policy_item" in update_call.args[0]
        updated_segments = json.loads(update_call.args[1])
        assert updated_segments == [{"text": "둘째 세그먼트", "reason": "사유2"}]  # 0번만 제거됨
        assert update_call.args[2] == "partial"  # 아직 1개 남아있으니 partial

    @pytest.mark.asyncio
    async def test_last_segment_promoted_sets_parse_status_parsed(self, monkeypatch):
        conn = self._make_conn([{"text": "유일한 세그먼트", "reason": "사유"}])
        monkeypatch.setattr(unresolved_report, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(unresolved_report, "resolve_namespace_id", AsyncMock(return_value=1))
        monkeypatch.setattr(unresolved_report.embedding_service, "embed", AsyncMock(return_value=[0.1] * 4))

        remaining = await unresolved_report.promote_segment_to_narrative("ns", 1, 0)

        assert remaining == 0
        update_call = conn.execute.call_args_list[1]
        assert json.loads(update_call.args[1]) == []
        assert update_call.args[2] == "parsed"  # 마지막 unresolved까지 없어졌으니 완전히 parsed
