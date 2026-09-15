"""Tests for service/policy/pipeline_stats.py — 실험실 게이트 작업3 기준정보 축적 집계."""
import importlib.util as _ilu
import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent


def _load(name: str, rel_path: str):
    spec = _ilu.spec_from_file_location(name, str(_backend_dir / rel_path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sys.modules["service.policy"] = MagicMock()
pipeline_stats = _load("service.policy.pipeline_stats", "service/policy/pipeline_stats.py")


def _make_fake_conn(counts: dict, trend_rows: list):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)

    async def _fetchval(query, *args):
        # 순서 중요 — policy_param/policy_chunk 쿼리도 JOIN 때문에 "policy_item" 문자열을
        # 포함하므로, 더 구체적인 테이블명을 먼저 확인해야 한다.
        if "policy_param" in query:
            return counts["policy_param"]
        if "policy_chunk" in query:
            return counts["policy_chunk"]
        if "ref_db_column" in query:
            return counts["ref_db_column"]
        if "ref_common_code" in query:
            return counts["ref_common_code"]
        if "policy_item" in query and "count" in query:
            return counts["policy_item"]
        raise AssertionError(f"unexpected fetchval query: {query}")

    conn.fetchval = AsyncMock(side_effect=_fetchval)
    conn.fetch = AsyncMock(return_value=trend_rows)
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    def _apply(ns_id=1, counts=None, trend_rows=None):
        counts = counts or {
            "policy_item": 0, "policy_param": 0, "policy_chunk": 0,
            "ref_db_column": 0, "ref_common_code": 0,
        }
        conn = _make_fake_conn(counts, trend_rows or [])
        monkeypatch.setattr(pipeline_stats, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(pipeline_stats, "resolve_namespace_id", AsyncMock(return_value=ns_id))
        return conn
    return _apply


class TestGetPipelineStats:
    @pytest.mark.asyncio
    async def test_unknown_namespace_returns_all_zero(self, patch_db):
        patch_db(ns_id=None)
        result = await pipeline_stats.get_pipeline_stats("모르는 네임스페이스")
        assert result == {
            "policy_item": 0, "policy_param": 0, "policy_chunk": 0,
            "ref_db_column": 0, "ref_common_code": 0, "trend": [],
        }

    @pytest.mark.asyncio
    async def test_returns_counts_and_trend(self, patch_db):
        patch_db(
            counts={
                "policy_item": 378, "policy_param": 389, "policy_chunk": 597,
                "ref_db_column": 0, "ref_common_code": 0,
            },
            trend_rows=[{"day": date(2026, 9, 4), "n": 378}],
        )
        result = await pipeline_stats.get_pipeline_stats("온라인스토어 DB")
        assert result["policy_item"] == 378
        assert result["policy_param"] == 389
        assert result["policy_chunk"] == 597
        assert result["ref_db_column"] == 0
        assert result["ref_common_code"] == 0
        assert result["trend"] == [{"day": "2026-09-04", "count": 378}]
