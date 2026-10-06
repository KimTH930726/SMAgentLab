"""POST /api/knowledge/glossary/bulk-delete 권한 검사 (2026-10-06, v2.128) + 동의어 지우기 권한.

용어 일괄 삭제에 권한 검사가 없어 조회 전용·다른 파트 사용자도 아무 용어나 지울 수 있었다(지식 bulk-delete는 10/02에 고침).
대상 용어가 속한 파트마다 소유권을 확인하고, 하나라도 막히면 아무것도 지우지 않아야 한다. 동의어 지우기도 그 용어의 파트 기준.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agents.knowledge_rag.knowledge.router import BulkDeleteRequest, bulk_remove_glossary, remove_glossary_synonym

R = "agents.knowledge_rag.knowledge.router"
USER = {"id": 1, "username": "u", "part": "p", "part_id": 1, "role": "user"}


async def _ownership(ns, user):
    if ns == "남의 파트":
        raise HTTPException(status_code=403, detail="no")


@pytest.mark.asyncio
async def test_forbidden_namespace_blocks_whole_delete():
    delete = AsyncMock(return_value=2)
    with patch(f"{R}.service.get_glossary_namespaces", AsyncMock(return_value=["내 파트", "남의 파트"])), \
         patch(f"{R}.check_namespace_ownership", AsyncMock(side_effect=_ownership)), \
         patch(f"{R}.service.bulk_delete_glossary", delete):
        with pytest.raises(HTTPException) as e:
            await bulk_remove_glossary(BulkDeleteRequest(ids=[1, 2]), user=USER)
    assert e.value.status_code == 403
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_namespaces_delete():
    delete = AsyncMock(return_value=2)
    owner = AsyncMock()
    with patch(f"{R}.service.get_glossary_namespaces", AsyncMock(return_value=["내 파트"])), \
         patch(f"{R}.check_namespace_ownership", owner), \
         patch(f"{R}.service.bulk_delete_glossary", delete):
        assert await bulk_remove_glossary(BulkDeleteRequest(ids=[1, 2]), user=USER) == {"deleted": 2}
    owner.assert_awaited_once_with("내 파트", USER)
    delete.assert_awaited_once_with([1, 2])


@pytest.mark.asyncio
async def test_synonym_delete_checks_term_namespace():
    block = AsyncMock(return_value=True)
    with patch(f"{R}.service.get_synonym_namespace", AsyncMock(return_value="남의 파트")), \
         patch(f"{R}.check_namespace_ownership", AsyncMock(side_effect=_ownership)), \
         patch(f"{R}.service.block_glossary_synonym", block):
        with pytest.raises(HTTPException) as e:
            await remove_glossary_synonym(5, user=USER)
    assert e.value.status_code == 403
    block.assert_not_awaited()


@pytest.mark.asyncio
async def test_synonym_delete_missing_is_404():
    with patch(f"{R}.service.get_synonym_namespace", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as e:
            await remove_glossary_synonym(5, user=USER)
    assert e.value.status_code == 404
