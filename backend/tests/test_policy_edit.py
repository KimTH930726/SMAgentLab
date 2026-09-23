"""Tests for service/policy/edit.py — 반려 항목의 파라미터/서술 수정 + 재검토 전환."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent

sys.modules["service.policy"] = MagicMock()
_spec = _ilu.spec_from_file_location(
    "service.policy.edit", str(_backend_dir / "service" / "policy" / "edit.py")
)
edit = _ilu.module_from_spec(_spec)
sys.modules["service.policy.edit"] = edit
_spec.loader.exec_module(edit)


def _make_fake_conn(row):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetchrow = AsyncMock(return_value=row)
    conn.execute = AsyncMock()
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    def _apply(row):
        conn = _make_fake_conn(row)
        monkeypatch.setattr(edit, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(edit, "resolve_namespace_id", AsyncMock(return_value=1))
        return conn
    return _apply


class TestUpdateParam:
    @pytest.mark.asyncio
    async def test_namespace_not_found_raises(self, patch_db, monkeypatch):
        patch_db({"policy_item_id": 1, "status": "rejected"})
        monkeypatch.setattr(edit, "resolve_namespace_id", AsyncMock(return_value=None))
        with pytest.raises(ValueError, match="네임스페이스"):
            await edit.update_param("없는곳", 1, "이름", None, None, None)

    @pytest.mark.asyncio
    async def test_param_not_found_raises(self, patch_db):
        patch_db(None)
        with pytest.raises(ValueError, match="파라미터를 찾을 수 없습니다"):
            await edit.update_param("ns", 999, "이름", None, None, None)

    @pytest.mark.asyncio
    async def test_non_rejected_status_raises(self, patch_db):
        patch_db({"policy_item_id": 5, "status": "pending_review"})
        with pytest.raises(ValueError, match="반려\\(rejected\\)"):
            await edit.update_param("ns", 1, "이름", None, None, None)

    @pytest.mark.asyncio
    async def test_blank_name_raises(self, patch_db):
        patch_db({"policy_item_id": 5, "status": "rejected"})
        with pytest.raises(ValueError, match="항목명"):
            await edit.update_param("ns", 1, "   ", None, None, None)

    @pytest.mark.asyncio
    async def test_updates_param_and_resubmits(self, patch_db):
        conn = patch_db({"policy_item_id": 5, "status": "rejected"})

        result = await edit.update_param("ns", 1, "최대개수", "일반 배달", "20", "개")

        assert result == "pending_review"
        update_call = conn.execute.call_args_list[0]
        assert "UPDATE policy_param" in update_call.args[0]
        assert update_call.args[1] == "최대개수"
        assert update_call.args[2] == "일반 배달"
        assert update_call.args[3] == "20"
        assert update_call.args[4] == "개"
        assert update_call.args[5] == 1  # param_id

        resubmit_call = conn.execute.call_args_list[1]
        assert "UPDATE policy_item" in resubmit_call.args[0]
        assert "pending_review" in resubmit_call.args[0]
        assert resubmit_call.args[1] == 5  # policy_item_id

    @pytest.mark.asyncio
    async def test_unit_truncated_to_50_chars(self, patch_db):
        conn = patch_db({"policy_item_id": 5, "status": "rejected"})
        long_unit = "x" * 60

        await edit.update_param("ns", 1, "이름", None, None, long_unit)

        update_call = conn.execute.call_args_list[0]
        assert len(update_call.args[4]) == 50


class TestUpdateNarrative:
    @pytest.mark.asyncio
    async def test_blank_text_raises(self, patch_db):
        with pytest.raises(ValueError, match="비워둘 수 없습니다"):
            await edit.update_narrative("ns", 1, "   ")

    @pytest.mark.asyncio
    async def test_chunk_not_found_raises(self, patch_db):
        patch_db(None)
        with pytest.raises(ValueError, match="서술을 찾을 수 없습니다"):
            await edit.update_narrative("ns", 999, "새 내용")

    @pytest.mark.asyncio
    async def test_non_rejected_status_raises(self, patch_db):
        patch_db({"policy_item_id": 5, "status": "active"})
        with pytest.raises(ValueError, match="반려\\(rejected\\)"):
            await edit.update_narrative("ns", 1, "새 내용")

    @pytest.mark.asyncio
    async def test_updates_narrative_reembeds_and_resubmits(self, patch_db, monkeypatch):
        conn = patch_db({"policy_item_id": 7, "status": "rejected"})
        monkeypatch.setattr(edit.embedding_service, "embed", AsyncMock(return_value=[0.2] * 4))

        result = await edit.update_narrative("ns", 3, "고친 서술 내용")

        assert result == "pending_review"
        update_call = conn.execute.call_args_list[0]
        assert "UPDATE policy_chunk" in update_call.args[0]
        assert update_call.args[1] == "고친 서술 내용"
        assert update_call.args[3] == 3  # chunk_id

        resubmit_call = conn.execute.call_args_list[1]
        assert "pending_review" in resubmit_call.args[0]
        assert resubmit_call.args[1] == 7  # policy_item_id
