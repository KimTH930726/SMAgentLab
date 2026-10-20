"""업무 영역 수정 권한 (2026-10-20, 조직·업무 영역 분리) — 프론트 utils/domainAccess.ts canEditDomain과 같은 규칙.

관리자 → 가능 / 조회 전용 → 불가 / 담당 파트가 정해진 영역 → 그 파트 사람만 / 담당이 없는 영역 → 관리자만.
예전엔 담당이 없으면 "공통"이라 누구나 고칠 수 있었다 — 정책서 영역 대부분이 담당 미지정이라 그대로 두면 아무나 고친다.
"""
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

# conftest가 core.dependencies를 통째로 가짜로 바꿔 둔다 — 실제 파일을 경로로 읽어 규칙 자체를 시험한다
_spec = importlib.util.spec_from_file_location("_real_dependencies", Path(__file__).resolve().parents[1] / "core" / "dependencies.py")
dep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dep)
check_namespace_ownership = dep.check_namespace_ownership


def _db(owner_part_id, kind="domain"):
    conn = AsyncMock()
    conn.fetchval.return_value = owner_part_id                                    # 조회 검사
    conn.fetchrow.return_value = {"owner_part_id": owner_part_id, "kind": kind}   # 수정 검사

    @asynccontextmanager
    async def get_conn():
        yield conn
    return patch.object(dep, "get_conn", get_conn)


def _user(role="user", part_id=7):
    return {"id": 1, "username": "u", "role": role, "part_id": part_id, "part": "p"}


@pytest.mark.asyncio
@pytest.mark.parametrize("owner, user, allowed", [
    (7, _user(), True),                              # 담당 파트 사람
    (8, _user(), False),                             # 다른 파트
    (None, _user(), False),                          # 담당 없음 → 관리자만
    (None, _user(part_id=None), False),              # 소속 없는 일반 사용자도 담당 없는 영역 못 고침(None == None 아님)
    (7, _user(role="viewer"), False),                # 조회 전용
    (None, _user(role="admin", part_id=None), True),  # 관리자(소속 없음)
    (8, _user(role="admin", part_id=None), True),
])
async def test_domain_edit_rule(owner, user, allowed):
    with _db(owner):
        if allowed:
            await check_namespace_ownership("영역", user)
        else:
            with pytest.raises(HTTPException) as e:
                await check_namespace_ownership("영역", user)
            assert e.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "user"])
async def test_group_domain_holds_no_data(role):
    """상위 묶음(kind=group)은 데이터를 갖지 않는다 — 관리자도 막는다(목록에서 빠져 넣으면 안 보이는 데이터가 됨, 리뷰 L2)."""
    with _db(None, kind="group"):
        with pytest.raises(HTTPException) as e:
            await check_namespace_ownership("고객", _user(role=role, part_id=None))
    assert e.value.status_code == 400


@pytest.mark.parametrize("owner, user, allowed", [
    (None, _user(), False), (None, _user(role="viewer"), False), (7, _user(role="viewer"), False),
    (7, _user(), True), (8, _user(), False), (None, _user(role="admin", part_id=None), True),
])
def test_bulk_paths_share_the_same_rule(owner, user, allowed):
    """여러 영역을 한 번에 검사하는 경로(질의 기록 일괄 삭제)도 같은 함수 — 예전 인라인 사본은 담당 없는 영역·조회 전용을 통과시켰다(리뷰 M1)."""
    assert (dep.edit_denied_reason(owner, user) is None) == allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("owner, user, allowed", [
    (None, _user(), True),               # 조회는 담당 없는 영역도 누구나(수정만 관리자) — 리뷰 M5
    (7, _user(), True), (8, _user(), False),
    (None, _user(role="viewer"), False),
    (8, _user(role="admin", part_id=None), True),
])
async def test_read_rule_is_separate_from_edit(owner, user, allowed):
    with _db(owner):
        if allowed:
            await dep.check_namespace_read("영역", user)
        else:
            with pytest.raises(HTTPException):
                await dep.check_namespace_read("영역", user)
