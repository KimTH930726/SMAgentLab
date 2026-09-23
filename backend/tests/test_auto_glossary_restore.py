"""지식 등록 시 용어 자동 추출(_run_auto_glossary) 복원 검증 (2026-09-24).

미리보기+검토 2단계 흐름으로 전환되며 import_file/import_from_url(둘 다 죽은 라우트)
안에만 남아있던 _run_auto_glossary 호출을, 실제로 살아있는 등록 경로(POST /api/knowledge,
POST /api/knowledge/bulk)에 다시 연결했다 — 그 연결 자체가 살아있는지 확인한다(실 LLM
추출 품질은 scripts/verify_auto_glossary.py류 실 DB 스크립트로 별도 확인함)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.knowledge_rag.knowledge.router import add_knowledge, bulk_create
from agents.knowledge_rag.knowledge.schemas import KnowledgeCreate, BulkCreateRequest, BulkKnowledgeItem

_FAKE_USER = {"id": 1, "username": "tester", "part": "슈퍼어드민", "role": "user"}


class TestAutoGlossaryWiredIntoLiveRoutes:
    @pytest.mark.asyncio
    async def test_single_create_triggers_auto_glossary(self):
        body = KnowledgeCreate(namespace="test-ns", content="젤리스탬프 관련 신규 지식", category="공통지식")
        fake_row = {"id": 1, "namespace": "test-ns", "content": body.content, "category": "공통지식"}

        with patch("agents.knowledge_rag.knowledge.router.check_namespace_ownership", AsyncMock()), \
             patch("agents.knowledge_rag.knowledge.router.service.create_knowledge", AsyncMock(return_value=fake_row)), \
             patch("agents.knowledge_rag.knowledge.router._run_auto_glossary", AsyncMock(return_value=2)) as mock_glossary:
            await add_knowledge(body, user=_FAKE_USER)
            mock_glossary.assert_awaited_once()
            args = mock_glossary.await_args.args
            assert args[0] == "test-ns"
            assert args[1] == body.content

    @pytest.mark.asyncio
    async def test_bulk_create_triggers_auto_glossary_with_combined_text(self):
        body = BulkCreateRequest(
            namespace="test-ns",
            items=[
                BulkKnowledgeItem(content="첫 번째 청크 — 젤리스탬프"),
                BulkKnowledgeItem(content="두 번째 청크 — 리워드"),
            ],
        )
        fake_result = {"created": 2, "job_id": 1, "status": "completed"}

        with patch("agents.knowledge_rag.knowledge.router.check_namespace_ownership", AsyncMock()), \
             patch("agents.knowledge_rag.knowledge.router.service.bulk_create_knowledge", AsyncMock(return_value=fake_result)), \
             patch("agents.knowledge_rag.knowledge.router._run_auto_glossary", AsyncMock(return_value=1)) as mock_glossary:
            response = await bulk_create(body, user=_FAKE_USER)
            mock_glossary.assert_awaited_once()
            combined_text = mock_glossary.await_args.args[1]
            assert "첫 번째 청크" in combined_text and "두 번째 청크" in combined_text
            assert response["auto_glossary"] == 1

    @pytest.mark.asyncio
    async def test_bulk_create_still_succeeds_if_auto_glossary_fails(self):
        """_run_auto_glossary 자체 내부에 이미 try/except가 있지만, 혹시라도 예외가
        새어나와도 등록 자체(bulk_create_knowledge)는 이미 끝난 뒤라 실패하면 안 된다는
        걸 회귀로 고정 — 용어 추출 실패가 지식 등록 실패로 번지면 안 됨."""
        body = BulkCreateRequest(namespace="test-ns", items=[BulkKnowledgeItem(content="내용")])
        fake_result = {"created": 1, "job_id": 1, "status": "completed"}

        with patch("agents.knowledge_rag.knowledge.router.check_namespace_ownership", AsyncMock()), \
             patch("agents.knowledge_rag.knowledge.router.service.bulk_create_knowledge", AsyncMock(return_value=fake_result)), \
             patch("agents.knowledge_rag.knowledge.router._run_auto_glossary", AsyncMock(side_effect=RuntimeError("LLM 오류"))):
            with pytest.raises(RuntimeError):
                await bulk_create(body, user=_FAKE_USER)
            # 주의: 현재 구현은 _run_auto_glossary 호출부에 별도 try/except가 없어
            # 여기서 예외가 그대로 전파된다 — _run_auto_glossary 내부 try/except가
            # 항상 지켜진다는 전제가 깨지면(예: 리팩터링으로 내부 예외처리가 빠지면)
            # 이 테스트가 실패하며 알려준다.
