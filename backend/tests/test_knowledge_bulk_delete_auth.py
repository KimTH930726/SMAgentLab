"""POST /api/knowledge/bulk-delete 권한 검사 (2026-10-02).

권한 검사가 아예 없어 조회 전용·다른 파트 사용자도 아무 지식이나 지울 수 있었다. 단건 삭제·bulk-update와 같은 기준으로
대상 지식이 속한 파트마다 소유권을 확인하고, 하나라도 막히면 아무것도 지우지 않아야 한다.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agents.knowledge_rag.knowledge.router import BulkDeleteRequest, bulk_remove_knowledge

R = "agents.knowledge_rag.knowledge.router"
USER = {"id": 1, "username": "u", "part": "p", "part_id": 1, "role": "user"}


@pytest.mark.asyncio
async def test_forbidden_namespace_blocks_whole_delete():
    async def ownership(ns, user):
        if ns == "남의 파트":
            raise HTTPException(status_code=403, detail="no")

    delete = AsyncMock(return_value=2)
    with patch(f"{R}.service.get_knowledge_namespaces", AsyncMock(return_value=["내 파트", "남의 파트"])), \
         patch(f"{R}.check_namespace_ownership", AsyncMock(side_effect=ownership)), \
         patch(f"{R}.service.bulk_delete_knowledge", delete):
        with pytest.raises(HTTPException) as e:
            await bulk_remove_knowledge(BulkDeleteRequest(ids=[1, 2]), user=USER)
    assert e.value.status_code == 403
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_namespaces_delete():
    delete = AsyncMock(return_value=2)
    owner = AsyncMock()
    with patch(f"{R}.service.get_knowledge_namespaces", AsyncMock(return_value=["내 파트"])), \
         patch(f"{R}.check_namespace_ownership", owner), \
         patch(f"{R}.service.bulk_delete_knowledge", delete):
        assert await bulk_remove_knowledge(BulkDeleteRequest(ids=[1, 2]), user=USER) == {"deleted": 2}
    owner.assert_awaited_once_with("내 파트", USER)
    delete.assert_awaited_once_with([1, 2])
