"""업무 영역 관리 연산 (2026-10-20 막 쓰기 테스트 반영) — 실제 파일을 경로로 읽어 규칙을 시험한다(conftest가 service.*를 가짜로 바꿔 둠).

- 업무 영역을 지울 땐 글자 키 연결(ops_link)도 지운다
- 담당 지정: 없는 파트는 거부, 빈 문자열은 해제
- 사용자 수정은 보낸 칸만(COALESCE), 없는 파트는 거부(예전엔 소속을 조용히 비웠다)
"""
import importlib.util
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


admin_service = _load("_real_admin_service", "service/admin/service.py")
auth_service = _load("_real_auth_service2", "service/auth/service.py")


class _Conn:
    """fetchrow/fetchval/execute를 순서대로 돌려주는 가짜 연결 + 트랜잭션."""
    def __init__(self, fetchrow=(), fetchval=()):
        self.fetchrow = AsyncMock(side_effect=list(fetchrow))
        self.fetchval = AsyncMock(side_effect=list(fetchval))
        self.execute = AsyncMock(return_value="DELETE 1")

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield
        return tx()


def _patch(mod, conn):
    @asynccontextmanager
    async def get_conn():
        yield conn
    return patch.object(mod, "get_conn", get_conn)


@pytest.fixture(autouse=True)
def _no_cache():
    fake = types.SimpleNamespace(invalidate_namespace=AsyncMock())
    with patch.dict(sys.modules, {"shared": MagicMock(cache=fake), "shared.cache": fake}):
        yield


@pytest.mark.asyncio
async def test_delete_removes_text_key_links_too():
    conn = _Conn(fetchrow=[{"id": 7}])
    with _patch(admin_service, conn):
        status, _ = await admin_service.delete_namespace("x")
    sqls = [c.args[0] for c in conn.execute.await_args_list]
    assert status == 204
    assert any("DELETE FROM ops_link" in s for s in sqls)                     # 글자 키 연결 정리
    assert sqls.index(next(s for s in sqls if "ops_link" in s)) < sqls.index(next(s for s in sqls if "DELETE FROM ops_namespace" in s))


@pytest.mark.asyncio
async def test_missing_domain_delete_is_404():
    conn = _Conn(fetchrow=[None])
    with _patch(admin_service, conn):
        assert (await admin_service.delete_namespace("없음"))[0] == 404


def _settings(**kw):
    return types.SimpleNamespace(owner_part=kw.get("owner_part"), clear_owner=kw.get("clear_owner", False))


@pytest.mark.asyncio
async def test_settings_unknown_part_rejected():
    conn = _Conn(fetchrow=[{"id": 1}], fetchval=[None])
    with _patch(admin_service, conn), pytest.raises(ValueError, match="없는 파트"):
        await admin_service.update_namespace_settings("영역", _settings(owner_part="없는파트"))
    assert not any("UPDATE ops_namespace" in c.args[0] for c in conn.execute.await_args_list)


@pytest.mark.asyncio
async def test_settings_sets_owner():
    conn = _Conn(fetchrow=[{"id": 1}, {"name": "영역", "owner_part": "P"}], fetchval=[3])
    with _patch(admin_service, conn):
        out = await admin_service.update_namespace_settings("영역", _settings(owner_part="P"))
    assert ("UPDATE ops_namespace SET owner_part_id = $2 WHERE id = $1", 1, 3) in [c.args for c in conn.execute.await_args_list]
    assert out["owner_part"] == "P"


@pytest.mark.asyncio
async def test_user_update_only_sent_fields():
    """승인(역할만)은 소속을 None으로 넘기고 SQL이 COALESCE로 그대로 둔다 — 동시에 바뀐 소속을 덮지 않는다."""
    conn = _Conn(fetchrow=[{"id": 1, "username": "u", "role": "user", "part_id": 4, "is_active": True,
                            "has_llm_credentials": False, "created_at": "t", "part": "P"}])
    with _patch(auth_service, conn), patch.object(auth_service, "invalidate_user_cache", MagicMock()):
        out = await auth_service.update_user(1, role="user")
    sql, *params = conn.fetchrow.await_args.args
    assert "COALESCE($3, u.part_id)" in sql and params == [1, "user", None, None]
    assert out["part"] == "P"


@pytest.mark.asyncio
async def test_user_update_unknown_part_rejected():
    conn = _Conn(fetchval=[None])
    with _patch(auth_service, conn), pytest.raises(ValueError, match="없는 파트"):
        await auth_service.update_user(1, part="없는파트")
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_settings_empty_string_clears_owner():
    """빈 문자열 = 해제(스키마 설명과 같게 — 예전엔 아무 일도 안 하고 200, 재리뷰 L1)."""
    conn = _Conn(fetchrow=[{"id": 1}, {"name": "영역", "owner_part": None}])
    with _patch(admin_service, conn):
        await admin_service.update_namespace_settings("영역", _settings(owner_part=""))
    assert ("UPDATE ops_namespace SET owner_part_id = $2 WHERE id = $1", 1, None) in [c.args for c in conn.execute.await_args_list]
