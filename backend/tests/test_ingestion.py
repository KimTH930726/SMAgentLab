"""Tests for Tier 1 지식 인제스천 — split_text_to_chunks + bulk_create_knowledge + CSV 파싱."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── split_text_to_chunks 테스트 (순수 함수, mock 불필요) ─────────────────────

from agents.knowledge_rag.knowledge.service import split_text_to_chunks


class TestSplitTextToChunks:
    """텍스트 분할 유틸 함수 검증."""

    # ── strategy="heading" ──

    def test_heading_split_basic(self):
        text = "## 1. 개요\n내용1\n\n## 2. 설치\n내용2\n\n## 3. 설정\n내용3"
        chunks = split_text_to_chunks(text, "heading")
        assert len(chunks) == 3
        assert "개요" in chunks[0]
        assert "설치" in chunks[1]
        assert "설정" in chunks[2]

    def test_heading_split_h3(self):
        text = "### A\nfoo\n### B\nbar"
        chunks = split_text_to_chunks(text, "heading")
        assert len(chunks) == 2

    def test_heading_single_section_returns_one(self):
        text = "## 유일한 섹션\n내용만 있음"
        chunks = split_text_to_chunks(text, "heading")
        assert len(chunks) == 1

    def test_heading_no_headers_returns_all(self):
        """헤더 없으면 전체를 하나의 청크로."""
        text = "헤더 없는 일반 텍스트입니다."
        chunks = split_text_to_chunks(text, "heading")
        assert len(chunks) == 1
        assert chunks[0] == text

    # ── strategy="blank_line" ──

    def test_blank_line_split(self):
        text = "첫 단락\n\n두번째 단락\n\n세번째 단락"
        chunks = split_text_to_chunks(text, "blank_line")
        assert len(chunks) == 3

    def test_blank_line_multiple_blanks(self):
        text = "A\n\n\n\nB"
        chunks = split_text_to_chunks(text, "blank_line")
        assert len(chunks) == 2

    def test_blank_line_no_blanks_returns_all(self):
        text = "연속된 텍스트\n줄바꿈만 있음\n빈 줄 없음"
        chunks = split_text_to_chunks(text, "blank_line")
        assert len(chunks) == 1

    # ── strategy="separator" ──

    def test_separator_split(self):
        text = "섹션1\n---\n섹션2\n---\n섹션3"
        chunks = split_text_to_chunks(text, "separator")
        assert len(chunks) == 3

    def test_separator_long_dashes(self):
        text = "A\n------\nB"
        chunks = split_text_to_chunks(text, "separator")
        assert len(chunks) == 2

    def test_separator_no_separator_returns_all(self):
        text = "구분선 없는 텍스트"
        chunks = split_text_to_chunks(text, "separator")
        assert len(chunks) == 1

    # ── strategy="auto" ──

    def test_auto_prefers_heading(self):
        """auto: 헤더가 있으면 heading 전략 사용."""
        text = "## A\ncontent A\n\n## B\ncontent B"
        chunks = split_text_to_chunks(text, "auto")
        assert len(chunks) == 2
        assert "## A" in chunks[0]

    def test_auto_falls_back_to_separator(self):
        """auto: 헤더 없고 --- 있으면 separator 전략."""
        text = "파트1\n---\n파트2\n---\n파트3"
        chunks = split_text_to_chunks(text, "auto")
        assert len(chunks) == 3

    def test_auto_falls_back_to_blank_line(self):
        """auto: 헤더도 ---도 없으면 빈 줄 전략."""
        text = "단락1\n\n단락2\n\n단락3"
        chunks = split_text_to_chunks(text, "auto")
        assert len(chunks) == 3

    def test_auto_single_block_returns_one(self):
        """auto: 분할 기준이 전혀 없으면 전체를 하나로."""
        text = "분할할 수 없는 연속 텍스트입니다."
        chunks = split_text_to_chunks(text, "auto")
        assert len(chunks) == 1

    # ── strategy="none" ──

    def test_none_returns_single(self):
        text = "## A\nfoo\n\n## B\nbar"
        chunks = split_text_to_chunks(text, "none")
        assert len(chunks) == 1

    # ── 에지 케이스 ──

    def test_empty_string(self):
        assert split_text_to_chunks("", "auto") == []

    def test_whitespace_only(self):
        assert split_text_to_chunks("   \n\n  ", "auto") == []

    def test_strips_whitespace(self):
        text = "  \n## A\n  내용  \n\n## B\n  내용2  \n  "
        chunks = split_text_to_chunks(text, "heading")
        for c in chunks:
            assert c == c.strip()

    def test_mixed_content_heading_priority(self):
        """헤더 + 빈 줄 + --- 혼합 → auto에서 heading 우선."""
        text = "## 서론\n내용\n\n---\n\n## 본론\n내용2"
        chunks = split_text_to_chunks(text, "auto")
        assert len(chunks) == 2  # heading 기준


# ─── bulk_create_knowledge 테스트 ────────────────────────────────────────────

class TestBulkCreateKnowledge:
    """벌크 등록 서비스 함수 검증."""

    @pytest.mark.asyncio
    async def test_namespace_not_found_raises(self):
        """존재하지 않는 namespace → ValueError."""
        fake_conn = MagicMock()
        fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
        fake_conn.__aexit__ = AsyncMock(return_value=False)
        fake_conn.fetchval = AsyncMock(return_value=None)  # resolve_namespace_id → None

        with patch("agents.knowledge_rag.knowledge.service.get_conn", return_value=fake_conn), \
             patch("agents.knowledge_rag.knowledge.service.resolve_namespace_id", AsyncMock(return_value=None)):
            from agents.knowledge_rag.knowledge.service import bulk_create_knowledge
            with pytest.raises(ValueError, match="not found"):
                await bulk_create_knowledge("nonexistent", [{"content": "test", "category": "공통지식"}])

    @pytest.mark.asyncio
    async def test_empty_items_returns_zero(self):
        """빈 items → created=0."""
        fake_conn = MagicMock()
        fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
        fake_conn.__aexit__ = AsyncMock(return_value=False)
        fake_conn.fetchval = AsyncMock(return_value=1)
        fake_conn.execute = AsyncMock()

        fake_emb = MagicMock()
        fake_emb.embed_batch = AsyncMock(return_value=[])

        with patch("agents.knowledge_rag.knowledge.service.get_conn", return_value=fake_conn), \
             patch("agents.knowledge_rag.knowledge.service.resolve_namespace_id", AsyncMock(return_value=1)), \
             patch("agents.knowledge_rag.knowledge.service.embedding_service", fake_emb):
            from agents.knowledge_rag.knowledge.service import bulk_create_knowledge
            result = await bulk_create_knowledge("test-ns", [])
            assert result["created"] == 0
            # 항목이 0건이어도 추적용 job 행은 항상 생성됨 (job_id는 None이 아님)
            assert result["job_id"] == 1

    @pytest.mark.asyncio
    async def test_with_source_file_creates_job(self):
        """source_file 지정 → ingestion job 생성."""
        call_count = {"n": 0}

        fake_conn = MagicMock()
        fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
        fake_conn.__aexit__ = AsyncMock(return_value=False)

        async def mock_fetchval(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return 1  # job_id (INSERT INTO rag_ingestion_job RETURNING id)
            return False  # cancel_requested (UPDATE ... RETURNING cancel_requested)
        fake_conn.fetchval = AsyncMock(side_effect=mock_fetchval)
        fake_conn.execute = AsyncMock()
        fake_conn.executemany = AsyncMock()
        fake_conn.fetch = AsyncMock(return_value=[])

        fake_emb = MagicMock()
        fake_emb.embed_batch = AsyncMock(return_value=[[0.1] * 768, [0.2] * 768])

        with patch("agents.knowledge_rag.knowledge.service.get_conn", return_value=fake_conn), \
             patch("agents.knowledge_rag.knowledge.service.resolve_namespace_id", AsyncMock(return_value=1)), \
             patch("agents.knowledge_rag.knowledge.service.embedding_service", fake_emb):
            from agents.knowledge_rag.knowledge.service import bulk_create_knowledge
            result = await bulk_create_knowledge(
                "test-ns",
                [{"content": "지식1", "category": "공통지식"}, {"content": "지식2", "category": "공통지식"}],
                source_file="test.csv",
                source_type="csv_import",
                background=False,
            )
            assert result["created"] == 2
            # embed_batch가 호출됨
            fake_emb.embed_batch.assert_called_once_with(["지식1", "지식2"])

    @pytest.mark.asyncio
    async def test_missing_category_auto_resolved_not_rejected(self):
        """카테고리 자동 관리(2026-09-24) — 예전엔 category가 비어있으면 _require_category가
        ValueError로 등록 자체를 거부했다. 지금은 resolve_or_create_category()가 대신 값을
        채워 넣어 등록이 그대로 성공해야 한다(수동/파일/텍스트/Teams 등록 전부 해당)."""
        fake_conn = MagicMock()
        fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
        fake_conn.__aexit__ = AsyncMock(return_value=False)
        fake_conn.fetchval = AsyncMock(side_effect=[1, False])
        fake_conn.execute = AsyncMock()
        fake_conn.executemany = AsyncMock()
        fake_conn.fetch = AsyncMock(return_value=[])

        fake_emb = MagicMock()
        fake_emb.embed_batch = AsyncMock(return_value=[[0.1] * 768])

        with patch("agents.knowledge_rag.knowledge.service.get_conn", return_value=fake_conn), \
             patch("agents.knowledge_rag.knowledge.service.resolve_namespace_id", AsyncMock(return_value=1)), \
             patch("agents.knowledge_rag.knowledge.service.embedding_service", fake_emb), \
             patch("service.admin.service.resolve_or_create_category", AsyncMock(return_value="자동배정됨")):
            from agents.knowledge_rag.knowledge.service import bulk_create_knowledge
            result = await bulk_create_knowledge(
                "test-ns", [{"content": "카테고리 없는 지식"}], background=False,
            )
            assert result["created"] == 1
            # 실제 INSERT는 executemany(rows)로 나가고, category는 각 row 튜플의
            # 5번째 값(namespace_id, content, embedding, base_weight, category, ...)
            insert_call = next(
                c for c in fake_conn.executemany.call_args_list if "INSERT INTO rag_knowledge" in c.args[0]
            )
            rows = insert_call.args[1]
            assert rows[0][4] == "자동배정됨"

    @pytest.mark.asyncio
    async def test_batches_commit_independently_before_job_completes(self):
        """현재 동작 문서화(2026-09-22, RAG 거버넌스 감사 v2) — 배치별로 독립 커밋되고,
        각 행은 삽입 즉시 status='active'가 되어 검색에 노출된다. job 전체가
        'completed'로 바뀌는 건 모든 배치가 끝난 뒤 딱 한 번뿐이라, 배치가 여러 개면
        "일부만 처리된 job"의 앞쪽 배치 내용이 뒤쪽 배치가 아직 진행 중인 동안에도
        이미 검색 가능한 상태가 된다(원자적 활성화 아님 — 알려진 개선 대상, 지금은
        고치지 않고 이 테스트로 현재 동작만 캡처해둔다. 고칠 때 이 테스트가 기준선).
        """
        executemany_calls = []

        fake_conn = MagicMock()
        fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
        fake_conn.__aexit__ = AsyncMock(return_value=False)
        fake_conn.fetchval = AsyncMock(side_effect=[1, False, False])  # job_id, cancel_requested x2
        fake_conn.fetch = AsyncMock(return_value=[])

        async def record_executemany(query, rows):
            executemany_calls.append((query, rows))
        fake_conn.executemany = AsyncMock(side_effect=record_executemany)
        fake_conn.execute = AsyncMock()

        fake_emb = MagicMock()
        fake_emb.embed_batch = AsyncMock(side_effect=lambda texts: [[0.1] * 768 for _ in texts])

        with patch("agents.knowledge_rag.knowledge.service.get_conn", return_value=fake_conn), \
             patch("agents.knowledge_rag.knowledge.service.resolve_namespace_id", AsyncMock(return_value=1)), \
             patch("agents.knowledge_rag.knowledge.service.embedding_service", fake_emb), \
             patch("agents.knowledge_rag.knowledge.service._INGEST_BATCH_SIZE", 1):
            from agents.knowledge_rag.knowledge.service import bulk_create_knowledge
            await bulk_create_knowledge(
                "test-ns",
                [{"content": "지식1", "category": "공통지식"}, {"content": "지식2", "category": "공통지식"}],
                background=False,
            )

        # 배치 크기 1로 강제했으니 rag_knowledge INSERT executemany가 배치마다(=2번)
        # 독립적으로 호출됨 — 한 번의 트랜잭션으로 묶이지 않는다는 뜻
        knowledge_inserts = [c for c in executemany_calls if "INSERT INTO rag_knowledge" in c[0]]
        assert len(knowledge_inserts) == 2
        # 각 배치의 행이 곧바로 status='active'로 삽입됨(뒤 배치의 완료를 기다리지 않음)
        for _, rows in knowledge_inserts:
            assert rows[0][11] == "active"  # rows 튜플의 12번째 값이 status 컬럼


# ─── CSV 파싱 로직 테스트 (router 레벨) ──────────────────────────────────────

class TestCreateKnowledgeCategoryAutoResolve:
    """create_knowledge()(단건 등록) 카테고리 자동 관리(2026-09-24) — bulk 경로와 동일한
    resolve_or_create_category() 안전망이 단건 등록(ManualForm)에도 걸려있는지 확인."""

    @pytest.mark.asyncio
    async def test_missing_category_auto_resolved_not_rejected(self):
        fake_conn = MagicMock()
        fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
        fake_conn.__aexit__ = AsyncMock(return_value=False)
        fake_conn.execute = AsyncMock()
        fake_conn.transaction = MagicMock()
        fake_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
        fake_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        fake_conn.fetchrow = AsyncMock(return_value={
            "id": 1, "namespace_id": 1, "content": "카테고리 없는 지식", "base_weight": 1.0,
            "category": "자동배정됨", "status": "active",
            "created_by_part": None, "created_by_user_id": None,
            "created_at": "2026-09-24", "updated_at": "2026-09-24",
        })

        fake_emb = MagicMock()
        fake_emb.embed = AsyncMock(return_value=[0.1] * 1024)

        with patch("agents.knowledge_rag.knowledge.service.get_conn", return_value=fake_conn), \
             patch("agents.knowledge_rag.knowledge.service.resolve_namespace_id", AsyncMock(return_value=1)), \
             patch("agents.knowledge_rag.knowledge.service.embedding_service", fake_emb), \
             patch("agents.knowledge_rag.knowledge.service.find_similar_active_knowledge", AsyncMock(return_value=[])), \
             patch("agents.knowledge_rag.knowledge.service.get_thresholds", return_value={"duplicate_min_similarity": 0.95}), \
             patch("service.admin.service.resolve_or_create_category", AsyncMock(return_value="자동배정됨")):
            from agents.knowledge_rag.knowledge.service import create_knowledge
            result = await create_knowledge("test-ns", "카테고리 없는 지식")
            assert result["category"] == "자동배정됨"
            insert_call = next(
                c for c in fake_conn.fetchrow.call_args_list if "INSERT INTO rag_knowledge" in c.args[0]
            )
            assert "자동배정됨" in insert_call.args


class TestCsvParsing:
    """CSV 파싱 + 컬럼 매핑 로직 검증."""

    def test_basic_csv_parsing(self):
        """기본 CSV 파싱."""
        import csv
        import io

        text = "내용,카테고리,시스템\n쿠폰 발급 절차,쿠폰,ops-coupon\n배치 실행 방법,배치,ops-batch\n"
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["내용"] == "쿠폰 발급 절차"
        assert rows[1]["카테고리"] == "배치"

    def test_csv_column_mapping(self):
        """컬럼 매핑 적용."""
        import csv
        import io
        import json

        mapping = {"content": "설명", "category": "분류"}
        text = "설명,분류\n테스트 내용,장애\n"
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)

        items = []
        for row in rows:
            content = row.get(mapping["content"], "").strip()
            if not content:
                continue
            item = {"content": content}
            if mapping.get("category") and row.get(mapping["category"]):
                item["category"] = row[mapping["category"]].strip()
            items.append(item)

        assert len(items) == 1
        assert items[0]["content"] == "테스트 내용"
        assert items[0]["category"] == "장애"

    def test_csv_empty_content_skipped(self):
        """content가 비어있는 행은 skip."""
        import csv
        import io

        text = "내용,카테고리\n,쿠폰\n유효한 내용,배치\n  ,장애\n"
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)

        items = []
        for row in rows:
            content = row.get("내용", "").strip()
            if not content:
                continue
            items.append({"content": content})

        assert len(items) == 1
        assert items[0]["content"] == "유효한 내용"

    def test_csv_bom_handling(self):
        """UTF-8 BOM 처리 — router에서 utf-8-sig로 디코딩하는 것과 동일."""
        import csv
        import io

        # BOM이 포함된 바이트 (실제 파일 업로드 시나리오)
        raw_bytes = "\ufeff내용,카테고리\n테스트,쿠폰\n".encode("utf-8")
        # router.py 로직: raw.decode("utf-8-sig")
        decoded = raw_bytes.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(decoded))
        rows = list(reader)
        assert len(rows) == 1
        assert "내용" in rows[0]
        assert rows[0]["내용"] == "테스트"


# ─── Pydantic 스키마 검증 ────────────────────────────────────────────────────

class TestSchemas:
    """신규 Pydantic 스키마 검증."""

    def test_bulk_create_request(self):
        from agents.knowledge_rag.knowledge.schemas import BulkCreateRequest
        req = BulkCreateRequest(
            namespace="test",
            items=[{"content": "hello"}],
            source_file="test.csv",
            source_type="csv_import",
        )
        assert req.namespace == "test"
        assert len(req.items) == 1
        assert req.source_type == "csv_import"

    def test_bulk_create_request_defaults(self):
        from agents.knowledge_rag.knowledge.schemas import BulkCreateRequest, BulkKnowledgeItem
        req = BulkCreateRequest(
            namespace="ns",
            items=[BulkKnowledgeItem(content="test")],
        )
        assert req.source_file is None
        assert req.source_type == "manual"
        assert req.items[0].base_weight == 1.0

    def test_knowledge_out_has_source_fields(self):
        from agents.knowledge_rag.knowledge.schemas import KnowledgeOut
        out = KnowledgeOut(
            id=1, namespace="ns",
            content="test", base_weight=1.0,
            source_file="data.csv", source_chunk_idx=3, source_type="csv_import",
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        assert out.source_file == "data.csv"
        assert out.source_chunk_idx == 3
        assert out.source_type == "csv_import"

    def test_knowledge_out_source_fields_optional(self):
        from agents.knowledge_rag.knowledge.schemas import KnowledgeOut
        out = KnowledgeOut(
            id=1, namespace="ns",
            content="test", base_weight=1.0,
            created_at="2026-01-01", updated_at="2026-01-01",
        )
        assert out.source_file is None
        assert out.source_chunk_idx is None
        assert out.source_type is None

    def test_ingestion_job_out(self):
        from agents.knowledge_rag.knowledge.schemas import IngestionJobOut
        job = IngestionJobOut(
            id=1, namespace_id=1, source_file="test.csv", source_type="csv_import",
            status="completed", total_chunks=10, created_chunks=10,
            auto_glossary=0, chunk_strategy=None,
            error_message=None, created_at="2026-01-01", completed_at="2026-01-01",
        )
        assert job.status == "completed"
        assert job.total_chunks == 10
