"""가입은 조회 전용(viewer)으로 시작 — 관리자 승인 후 일반 사용자(2026-10-20, 리뷰 M3).

가입은 인증 없이 열려 있고 파트를 고를 수 있다. 바로 user가 되면 그 파트 담당 업무 영역의 지식·정책을 곧장 고칠 수 있었다.
"""
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# conftest가 일부 패키지를 가짜로 바꿔 둔다 — 실제 파일을 경로로 읽는다
_spec = importlib.util.spec_from_file_location("_real_auth_service", Path(__file__).resolve().parents[1] / "service" / "auth" / "service.py")
service = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(service)


@pytest.mark.asyncio
async def test_register_starts_as_viewer():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"part_id": 3, "user_exists": False},
        {"id": 9, "username": "u", "role": "viewer", "part_id": 3, "is_active": True,
         "encrypted_llm_credentials": None, "created_at": "now"},
    ]

    @asynccontextmanager
    async def get_conn():
        yield conn

    with patch.object(service, "get_conn", get_conn), patch.object(service, "hash_password", lambda p: "h"):
        out = await service.register_user("u", "pw", "온라인서비스 파트")
    insert_sql = conn.fetchrow.await_args_list[1].args[0]
    assert "role" in insert_sql and "'viewer'" in insert_sql
    assert out["role"] == "viewer"
