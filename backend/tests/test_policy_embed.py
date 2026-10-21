"""정책 서술 조각 임베딩(service/policy/service.py) — 기동 때 한 번 재임베딩.

옛 엑셀 가져오기 파이프라인 테스트(test_policy_pipeline.py)는 그 파이프라인을 뺄 때(v0 재구축 2단계, 2026-10-21) 함께 지웠고,
남은 임베딩 규칙 테스트만 여기로 옮겼다. conftest가 service 패키지를 MagicMock으로 바꿔 두므로 파일 경로로 로드한다.
"""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent
_spec = _ilu.spec_from_file_location("service.policy.service", str(_backend_dir / "service/policy/service.py"))
service = _ilu.module_from_spec(_spec)
sys.modules["service.policy.service"] = service
_spec.loader.exec_module(service)


@pytest.fixture
def patch_db(monkeypatch):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=1)
    conn.execute = AsyncMock()
    monkeypatch.setattr(service, "get_conn", MagicMock(return_value=conn))
    return conn


class TestReembedChunks:
    """기동 시 재임베딩 — 임베딩하는 사이 편집된 조각은 덮지 않는다(비교 후 갱신, 리뷰 10/13)."""

    @pytest.mark.asyncio
    async def test_compare_and_set_and_marker(self, patch_db, monkeypatch):
        conn = patch_db
        conn.fetchval = AsyncMock(return_value=None)          # 아직 안 함
        conn.fetch = AsyncMock(return_value=[
            {"id": 1, "chunk_text": "조각1", "policy_name": "정책", "category_path": ["a"]},
            {"id": 2, "chunk_text": "정책 (a): 대체", "policy_name": "정책", "category_path": ["a"]},
        ])
        tx = MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
        conn.transaction = MagicMock(return_value=tx)
        conn.executemany = AsyncMock()
        emb = MagicMock(embed_batch=AsyncMock(side_effect=lambda texts: [[0.1] for _ in texts]))
        monkeypatch.setattr(service, "embedding_service", emb)
        await service.reembed_chunks_if_needed()
        assert emb.embed_batch.call_args.args[0] == ["정책 (a): 조각1", "정책 (a): 대체"]   # 머리 한 번만
        sql, args = conn.executemany.call_args.args
        assert "AND chunk_text = $3" in sql
        assert [a[2] for a in args] == ["조각1", "정책 (a): 대체"]                       # 읽은 그 글일 때만 갱신
        assert service.CHUNK_EMBED_VERSION in conn.execute.call_args.args

    @pytest.mark.asyncio
    async def test_skips_when_marker_current(self, patch_db, monkeypatch):
        patch_db.fetchval = AsyncMock(return_value=service.CHUNK_EMBED_VERSION)
        emb = MagicMock(embed_batch=AsyncMock())
        monkeypatch.setattr(service, "embedding_service", emb)
        await service.reembed_chunks_if_needed()
        emb.embed_batch.assert_not_called()
