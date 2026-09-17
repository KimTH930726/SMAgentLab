"""Tests for Tier 2 — 파일 어댑터 + 청킹 엔진 + LLM 태거."""
import pytest
from unittest.mock import AsyncMock, MagicMock


# ─── 파일 어댑터 테스트 ──────────────────────────────────────────────────────

from agents.knowledge_rag.ingestion.adapters import (
    parse_text, parse_markdown, parse_file, ParsedDocument,
)


class TestParseText:
    def test_basic(self):
        doc = parse_text("hello world", "test.txt")
        assert doc.source_type == "txt"
        assert doc.raw_text == "hello world"
        assert doc.sections == []

    def test_filename_preserved(self):
        doc = parse_text("content", "my_doc.txt")
        assert doc.source_name == "my_doc.txt"


class TestParseMarkdown:
    def test_sections_extracted(self):
        md = "## 개요\n내용1\n## 설치\n내용2"
        doc = parse_markdown(md, "guide.md")
        assert doc.source_type == "md"
        assert len(doc.sections) == 2
        assert doc.sections[0]["title"] == "개요"
        assert doc.sections[1]["title"] == "설치"

    def test_nested_headings(self):
        md = "# 큰제목\n\n## 중제목\n내용\n\n### 소제목\n세부내용"
        doc = parse_markdown(md, "doc.md")
        assert len(doc.sections) == 3

    def test_no_headings(self):
        md = "그냥 텍스트\n두번째 줄"
        doc = parse_markdown(md, "plain.md")
        assert len(doc.sections) == 1
        assert doc.sections[0]["title"] == ""

    def test_table_extraction(self):
        md = "텍스트\n\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"
        doc = parse_markdown(md, "table.md")
        assert len(doc.tables) == 1
        assert doc.tables[0]["headers"] == ["A", "B"]
        assert len(doc.tables[0]["rows"]) == 2

    def test_table_no_separator_ignored(self):
        """구분선 없는 파이프는 테이블로 인식 안 됨."""
        md = "| not | a | table |\nplain text"
        doc = parse_markdown(md, "test.md")
        assert len(doc.tables) == 0


class TestParseFile:
    def test_txt_extension(self):
        doc = parse_file("hello".encode("utf-8"), "test.txt")
        assert doc.source_type == "txt"

    def test_md_extension(self):
        doc = parse_file("## Title\ncontent".encode("utf-8"), "test.md")
        assert doc.source_type == "md"

    def test_unknown_extension_fallback(self):
        doc = parse_file("some text".encode("utf-8"), "data.log")
        assert doc.source_type == "txt"

    def test_binary_raises(self):
        with pytest.raises(ValueError):
            parse_file(b"\x00\x01\x02\xff", "binary.xyz")

    def test_bom_handled(self):
        bom_text = "\ufeffBOM content".encode("utf-8")
        doc = parse_file(bom_text, "bom.txt")
        assert "BOM content" in doc.raw_text


# ─── 청킹 엔진 테스트 ───────────────────────────────────────────────────────

from agents.knowledge_rag.ingestion.chunker import (
    chunk_document, Chunk, _table_to_markdown,
)


class TestChunkDocument:
    # min_chars=50이므로 테스트 텍스트는 충분히 길어야 하고,
    # 병합 방지를 위해 각 청크가 단독으로도 의미 있는 크기여야 함
    _LONG = "이것은 충분히 긴 텍스트입니다. 각 청크가 분리되려면 max_chars보다 합이 커야 합니다. " * 10  # ~500자

    def _make_doc(self, text="", sections=None, tables=None, name="test.md"):
        return ParsedDocument(
            source_type="md", source_name=name, raw_text=text,
            sections=sections or [], tables=tables or [],
        )

    def test_auto_uses_sections_when_available(self):
        doc = self._make_doc(
            text=f"## A\n{self._LONG}\n## B\n{self._LONG}",
            sections=[
                {"title": "A", "content": self._LONG, "level": 2},
                {"title": "B", "content": self._LONG, "level": 2},
            ],
        )
        # max_chars를 작게 → 병합 방지
        chunks = chunk_document(doc, strategy="auto", max_chars=200)
        assert len(chunks) >= 2

    def test_auto_falls_back_to_paragraph(self):
        doc = self._make_doc(text=f"{self._LONG}\n\n{self._LONG}\n\n{self._LONG}")
        chunks = chunk_document(doc, strategy="auto", max_chars=200)
        # 각 단락이 200자 초과이면 재분할되므로 >= 3
        assert len(chunks) >= 3

    def test_section_strategy(self):
        doc = self._make_doc(
            sections=[
                {"title": "A", "content": self._LONG, "level": 2},
                {"title": "B", "content": self._LONG, "level": 2},
            ],
        )
        chunks = chunk_document(doc, strategy="section", max_chars=200)
        assert len(chunks) >= 2

    def test_paragraph_strategy(self):
        doc = self._make_doc(text=f"{self._LONG}\n\n{self._LONG}\n\n{self._LONG}")
        chunks = chunk_document(doc, strategy="paragraph", max_chars=200)
        # 각 단락이 200자 초과이면 재분할되므로 >= 3
        assert len(chunks) >= 3

    def test_fixed_strategy(self):
        doc = self._make_doc(text="A" * 5000)
        chunks = chunk_document(doc, strategy="fixed", max_chars=1000)
        assert len(chunks) >= 5

    def test_tables_added_as_chunks(self):
        doc = self._make_doc(
            text=self._LONG,
            tables=[{"headers": ["Col1", "Col2"], "rows": [["data1", "data2"]] * 3}],
        )
        chunks = chunk_document(doc, strategy="paragraph")
        table_chunks = [c for c in chunks if c.metadata.get("is_table")]
        assert len(table_chunks) == 1

    def test_empty_doc(self):
        doc = self._make_doc(text="")
        chunks = chunk_document(doc)
        assert chunks == []

    def test_small_sections_merged(self):
        """작은 섹션들은 병합됨 → min_chars 이상이면 최소 1개."""
        doc = self._make_doc(
            sections=[
                {"title": "A", "content": self._LONG, "level": 2},
                {"title": "B", "content": self._LONG, "level": 2},
            ],
        )
        chunks = chunk_document(doc, strategy="section", max_chars=5000)
        assert len(chunks) >= 1

    def test_chunk_indices_sequential(self):
        doc = self._make_doc(text=f"{self._LONG}\n\n{self._LONG}\n\n{self._LONG}")
        chunks = chunk_document(doc, strategy="paragraph")
        for i, c in enumerate(chunks):
            assert c.idx == i


class TestTableToMarkdown:
    def test_basic(self):
        result = _table_to_markdown({"headers": ["A", "B"], "rows": [["1", "2"]]})
        assert "| A | B |" in result
        assert "| 1 | 2 |" in result

    def test_empty_headers(self):
        assert _table_to_markdown({"headers": [], "rows": []}) == ""


# ─── LLM 태거 테스트 ────────────────────────────────────────────────────────

from agents.knowledge_rag.ingestion.tagger import (
    auto_tag_chunks, extract_glossary_terms, _parse_json_array,
)


class TestParseJsonArray:
    def test_plain_json(self):
        result = _parse_json_array('[{"a": 1}]')
        assert result == [{"a": 1}]

    def test_code_block(self):
        result = _parse_json_array('```json\n[{"b": 2}]\n```')
        assert result == [{"b": 2}]

    def test_invalid_raises(self):
        with pytest.raises(Exception):
            _parse_json_array("not json")

    def test_non_array_raises(self):
        with pytest.raises(ValueError):
            _parse_json_array('{"key": "value"}')


class TestAutoTagChunks:
    @pytest.mark.asyncio
    async def test_success(self):
        llm = MagicMock()
        llm.generate_once = AsyncMock(return_value='[{"idx": 0, "category": "쿠폰", "priority_score": 0.8}]')

        chunks = [{"idx": 0, "text": "쿠폰 발급 절차입니다."}]
        result = await auto_tag_chunks(chunks, ["쿠폰", "배치"], llm)
        assert len(result) == 1
        assert result[0]["category"] == "쿠폰"
        assert result[0]["priority_score"] == 0.8

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self):
        llm = MagicMock()
        llm.generate_once = AsyncMock(side_effect=Exception("LLM error"))

        chunks = [{"idx": 0, "text": "test"}, {"idx": 1, "text": "test2"}]
        result = await auto_tag_chunks(chunks, [], llm)
        assert len(result) == 2
        assert result[0]["priority_score"] == 0.5  # fallback

    @pytest.mark.asyncio
    async def test_empty_chunks(self):
        result = await auto_tag_chunks([], [], MagicMock())
        assert result == []


class TestExtractGlossaryTerms:
    @pytest.mark.asyncio
    async def test_success(self):
        llm = MagicMock()
        llm.generate_once = AsyncMock(return_value='[{"term": "쿠폰회수", "description": "만료 쿠폰 자동 회수"}]')

        result = await extract_glossary_terms("쿠폰회수 배치 설명...", [], llm)
        assert len(result) == 1
        assert result[0]["term"] == "쿠폰회수"

    @pytest.mark.asyncio
    async def test_duplicates_filtered(self):
        llm = MagicMock()
        llm.generate_once = AsyncMock(return_value='[{"term": "배치", "description": "정기 실행"}, {"term": "신규용어", "description": "새 용어"}]')

        result = await extract_glossary_terms("text", ["배치"], llm)
        assert len(result) == 1
        assert result[0]["term"] == "신규용어"

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self):
        llm = MagicMock()
        llm.generate_once = AsyncMock(side_effect=Exception("fail"))

        result = await extract_glossary_terms("text", [], llm)
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_text(self):
        result = await extract_glossary_terms("", [], MagicMock())
        assert result == []
