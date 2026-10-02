"""지식 가중치 입력 제거 (2026-10-02, v2.121).

가중치(검색 점수 × (1 + base_weight))는 쓰인 적이 없고(활성 지식 전부 1.0), 파트 사용자 누구나 0~3을 고를 수 있던 데다
API 상한도 없어 한 사람이 자기 문서를 늘 맨 위로 올릴 수 있었다. 틀린 검색은 배수로 덮지 않고 내용·업무구분·용어집을
고친다 → 화면·API·문서 분석 자동 가중치 입력을 모두 뺐다. 옛 화면이 base_weight를 보내도 서비스까지 안 내려가는지 고정한다.
"""
from unittest.mock import AsyncMock, patch

import pytest

from agents.knowledge_rag.knowledge.router import add_knowledge, bulk_create, modify_knowledge
from agents.knowledge_rag.knowledge.schemas import BulkCreateRequest, KnowledgeCreate, KnowledgeUpdate

R = "agents.knowledge_rag.knowledge.router"
ADMIN = {"id": 2, "username": "a", "part": "p", "role": "admin"}


@pytest.mark.asyncio
async def test_old_client_weight_is_dropped_everywhere():
    create = AsyncMock(return_value={})
    update = AsyncMock(return_value={"id": 1})
    bulk = AsyncMock(return_value={})
    with patch(f"{R}.check_namespace_ownership", AsyncMock()), \
         patch(f"{R}._require_resource_namespace", AsyncMock(return_value="ns")), \
         patch(f"{R}.service.get_knowledge_namespace", AsyncMock(return_value="ns")), \
         patch(f"{R}.service.create_knowledge", create), \
         patch(f"{R}.service.update_knowledge", update), \
         patch(f"{R}.service.bulk_create_knowledge", bulk), \
         patch(f"{R}._run_auto_glossary", AsyncMock(return_value=0)):
        # 관리자라도, 옛 화면처럼 base_weight를 실어 보내도
        await add_knowledge(KnowledgeCreate.model_validate(
            {"namespace": "ns", "content": "c", "category": "공통지식", "base_weight": 3.0}), user=ADMIN)
        await modify_knowledge(1, KnowledgeUpdate.model_validate({"content": "c", "base_weight": 0.1}), user=ADMIN)
        await bulk_create(BulkCreateRequest.model_validate(
            {"namespace": "ns", "items": [{"content": "a", "base_weight": 2.0}]}), user=ADMIN)

    assert "base_weight" not in create.await_args.kwargs
    assert "base_weight" not in update.await_args.kwargs
    assert all("base_weight" not in it for it in bulk.await_args.kwargs["items"])
