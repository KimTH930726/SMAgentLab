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
    async def test_bulk_create_defers_auto_glossary_until_job_activation(self):
        """2026-09-28 변경 — 예전엔 bulk_create가 job 시작 직후 바로 _run_auto_glossary를 불러,
        job이 실패·취소돼도 용어만 남았다. 이제 라우트는 즉시 추출하지 않고 after_activation 훅으로
        넘기며(서비스가 전환 성공 뒤에만 실행 — test_ingestion.py TestBulkIngestionStagedActivation),
        그 훅을 실행하면 합친 텍스트로 추출이 일어나야 한다(복원된 연결 자체는 유지)."""
        body = BulkCreateRequest(
            namespace="test-ns",
            items=[
                BulkKnowledgeItem(content="첫 번째 청크 — 젤리스탬프"),
                BulkKnowledgeItem(content="두 번째 청크 — 리워드"),
            ],
        )
        fake_result = {"created": 0, "job_id": 1, "status": "processing"}
        bulk_mock = AsyncMock(return_value=fake_result)

        with patch("agents.knowledge_rag.knowledge.router.check_namespace_ownership", AsyncMock()),              patch("agents.knowledge_rag.knowledge.router.service.bulk_create_knowledge", bulk_mock),              patch("agents.knowledge_rag.knowledge.router._run_auto_glossary", AsyncMock(return_value=1)) as mock_glossary:
            response = await bulk_create(body, user=_FAKE_USER)
            mock_glossary.assert_not_awaited()  # 요청 시점엔 추출하지 않는다
            assert response == fake_result

            hook = bulk_mock.await_args.kwargs["after_activation"]
            assert await hook() == 1
            combined_text = mock_glossary.await_args.args[1]
            assert "첫 번째 청크" in combined_text and "두 번째 청크" in combined_text
