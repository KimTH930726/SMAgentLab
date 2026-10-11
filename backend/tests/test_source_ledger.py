"""원문 원장 (2026-10-08) — 원문 = SoT, 청크 = 다시 만들 수 있는 파생물.

핵심 약속: 원장의 원문을 지금 코드로 다시 분할한 결과 = 처음 등록(미리보기) 때 결과(같은 코드면 청크 동일 → 다시 분할해도 변화 없음).
그리고 사람이 고친 청크가 있으면 다시 분할이 조용히 덮지 않는다.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from bs4 import BeautifulSoup

from agents.knowledge_rag.ingestion import source_ledger as L
from agents.knowledge_rag.ingestion import web_crawler as w
from agents.knowledge_rag.ingestion.adapters import ParsedDocument
from agents.knowledge_rag.ingestion.chunker import chunk_document
from agents.knowledge_rag.knowledge.service import split_text_to_items

PASTE = ("1.1. 개요\n원장 시나리오 문장 A 입니다 충분히 길게.\n"
         "1.2. 처리\n원장 시나리오 문장 B 입니다 충분히 길게.\n"
         "1.3. 예외\n원장 시나리오 문장 C 입니다 충분히 길게.")
HTML = ('<h1>매장</h1><p>도입</p><h2>1.1 관리</h2><p>' + '관리 본문 ' * 30 + '</p>'
        '<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[SELECT 1]]></ac:plain-text-body></ac:structured-macro>'
        '<h2>1.2 개점</h2><table><tr><th>코드</th><th>뜻</th></tr><tr><td>A</td><td>개점</td></tr></table>')


def _row(**kw):
    base = {"source_type": "paste", "raw_format": "text", "raw_content": PASTE, "title": "t", "parent_title": None,
            "source_ref": None, "source_version": None, "chunk_strategy": "auto"}
    return {**base, **kw}


def test_paste_rechunk_equals_preview():
    preview = split_text_to_items(PASTE, "auto")
    again = L.rechunk_items(_row())
    assert [x["content"] for x in again] == [x["content"] for x in preview]
    assert [x["heading_path"] for x in again] == [x["heading_path"] or None for x in preview]


def test_confluence_rechunk_equals_preview_path():
    """미리보기(fetch → 파싱 → 분할 → 페이지 경로)와 원장 다시 분할이 같은 결과."""
    soup = BeautifulSoup(w.prepare_storage_html(HTML), "lxml")
    doc = ParsedDocument(source_type="confluence", source_name="매장 페이지", raw_text=w._extract_text(soup),
                         sections=w._extract_heading_sections(soup),
                         metadata={"title": "매장 페이지", "parent_title": "쿠팡이츠", "page_id": "123", "raw_html": HTML})
    from agents.knowledge_rag.knowledge.router import _enrich_heading_path
    preview = [(c.text, _enrich_heading_path(doc, c.heading_path, "매장 페이지") or None)
               for c in chunk_document(doc, strategy="section")]
    again = L.rechunk_items(_row(source_type="confluence", raw_format="html", raw_content=HTML, title="매장 페이지",
                                 parent_title="쿠팡이츠", source_ref="123", chunk_strategy="section"))
    assert [(x["content"], x["heading_path"]) for x in again] == preview
    assert any("SELECT 1" in x["content"] for x in again)          # 코드 매크로도 원문에서 다시 살아남
    assert all(x["heading_path"][0] == "매장 페이지" for x in again)


def test_draft_for_xlsx_is_skipped_and_html_kept_raw():
    assert L.content_hash("a") != L.content_hash("b")

    class C:
        calls = []

        async def fetchrow(self, *a):
            return None

        async def fetchval(self, sql, *a):
            C.calls.append(a)
            return 7
    import asyncio
    x = ParsedDocument(source_type="xlsx", source_name="f.xlsx", raw_text="r")
    assert asyncio.run(L.save_draft_for_parsed(C(), 1, x, chunk_strategy="auto", user_id=1)) is None
    h = ParsedDocument(source_type="confluence", source_name="p", raw_text="파싱된 글", metadata={"raw_html": "<p>원문</p>", "page_id": "9"})
    assert asyncio.run(L.save_draft_for_parsed(C(), 1, h, chunk_strategy="section", user_id=1)) == 7
    args = C.calls[-1]
    assert args[1] == "confluence" and args[2] == "9" and args[5] == "html" and args[6] == "<p>원문</p>"   # 파싱 결과가 아니라 원문 HTML


class _Conn:
    def __init__(self, doc, current):
        self.doc, self.current = doc, current

    async def fetchrow(self, sql, *a):
        return self.doc

    async def fetch(self, sql, *a):
        return self.current

    async def fetchval(self, sql, *a):
        return None

    async def execute(self, *a):
        return None


def _patch_conn(doc, current):
    @asynccontextmanager
    async def cm():
        yield _Conn(doc, current)
    return patch("agents.knowledge_rag.knowledge.service.get_conn", cm)


DOC = {**_row(), "id": 5, "status": "active", "namespace": "ns", "registered_partial": False}


@pytest.mark.asyncio
async def test_rechunk_no_change_when_same():
    from agents.knowledge_rag.knowledge import service
    current = [{"id": i, "content": x["content"], "heading_path": x["heading_path"], "category": "c", "source_type": "paste_split",
                "source_file": "f", "edited": False} for i, x in enumerate(split_text_to_items(PASTE, "auto"))]
    bulk = AsyncMock()
    with _patch_conn(DOC, current), patch.object(service, "bulk_create_knowledge", bulk):
        r = await service.rechunk_source_document(5)
    assert r["changed"] is False and not bulk.called


@pytest.mark.asyncio
async def test_rechunk_blocked_by_human_edit_then_force():
    from agents.knowledge_rag.knowledge import service
    current = [{"id": 1, "content": "옛 조각(사람이 고침)", "heading_path": None, "category": "c", "source_type": "paste_split",
                "source_file": "f", "edited": True}]
    bulk = AsyncMock(return_value={"job_id": 9, "status": "completed"})
    with _patch_conn(DOC, current), patch.object(service, "bulk_create_knowledge", bulk):
        r = await service.rechunk_source_document(5)
        assert r.get("blocked") is True and r["edited"] == 1 and not bulk.called
        r = await service.rechunk_source_document(5, force=True)
    assert r["changed"] is True
    items = bulk.call_args.args[1]
    assert all(it["source_document_id"] == 5 and it["category"] == "c" for it in items)   # 원문 연결·기존 업무구분 유지
    assert bulk.call_args.kwargs["background"] is False


@pytest.mark.asyncio
async def test_rechunk_only_active_document():
    from agents.knowledge_rag.knowledge import service
    with _patch_conn({**DOC, "status": "superseded"}, []):
        with pytest.raises(ValueError):
            await service.rechunk_source_document(5)



# ── 코드 리뷰(2026-10-08) 반영 ──────────────────────────────────────────────

@pytest.mark.parametrize("st,raw,strategy", [
    ("md", "# 제목\n본문 하나입니다 길게 길게\n```\n# 주석\n```\n## 둘째\n" + "가" * 300, "section"),
    ("txt", "1.1 가\n본문 가입니다 충분히 길게 씁니다\n1.2 나\n본문 나입니다 충분히 길게 씁니다", "section"),
    ("txt", "제목 없는 문단 하나\n\n또 문단 둘", "paragraph"),
    ("pdf", "--- Page 1 ---\n1.1 개요\n" + "개요 본문 " * 40 + "\n--- Page 2 ---\n더 본문", "section"),
])
def test_file_types_rechunk_equals_preview(st, raw, strategy):
    """리뷰: 파일 종류마다 미리보기 파서와 다시 분할이 달라질 수 있었다(PDF는 제목 개수 거름 차이) — 같은 파서 함수로."""
    from agents.knowledge_rag.ingestion import adapters as a
    if st == "md":
        doc = a.parse_markdown(raw, "t.md")
    elif st == "pdf":
        doc = ParsedDocument(source_type="pdf", source_name="t", raw_text=raw, sections=a.extract_heading_sections(raw))
    else:
        doc = a.parse_text(raw, "t.txt")
    preview = [(c.text, list(c.heading_path or []) or None) for c in chunk_document(doc, strategy=strategy)]
    again = L.rechunk_items(_row(source_type=st, raw_content=raw, title="t", chunk_strategy=strategy))
    assert [(x["content"], x["heading_path"]) for x in again] == preview


def test_web_rechunk_uses_main_element_like_preview():
    """리뷰: 다시 분할이 페이지 전체(광고·저작권 등 본문 밖)를 읽어 미리보기와 달랐다."""
    html = ('<html><head><title>T</title></head><body><nav>메뉴</nav><main><h2>A</h2><p>' + "본문 " * 40 + '</p>'
            '<h2>B</h2><p>' + "둘째 " * 40 + '</p></main><div>본문 밖 광고 문구입니다 길게 길게</div></body></html>')
    doc = w.parse_web_html(html, "http://x")
    preview = [c.text for c in chunk_document(doc, strategy="section")]
    again = [x["content"] for x in L.rechunk_items(_row(source_type="web", raw_format="html", raw_content=html,
                                                          source_ref="http://x", chunk_strategy="section"))]
    assert again == preview and not any("광고" in t for t in again)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,reason", [
    ({"removed": 2}, "removed"),
    ({"partial": True}, "partial"),
    ({"mixed": True}, "mixed_category"),
])
async def test_rechunk_blocked_when_people_touched_it(extra, reason):
    """리뷰: 사람이 뺀 조각(삭제·반려·등록 때 체크 해제)이나 조각별 업무구분을 다시 분할이 조용히 되살리거나 통일했다."""
    from agents.knowledge_rag.knowledge import service
    current = [{"id": 1, "content": "옛 조각", "heading_path": None, "category": "c", "source_type": "paste_split",
                "source_file": "f", "edited": False}]
    if extra.get("mixed"):
        current.append({**current[0], "id": 2, "content": "옛 조각2", "category": "d"})
    doc = {**DOC, "registered_partial": bool(extra.get("partial"))}

    class C(_Conn):
        async def fetchval(self, sql, *a):
            return extra.get("removed", 0)
    @asynccontextmanager
    async def cm():
        yield C(doc, current)
    bulk = AsyncMock()
    with patch("agents.knowledge_rag.knowledge.service.get_conn", cm), patch.object(service, "bulk_create_knowledge", bulk):
        r = await service.rechunk_source_document(5)
    assert r["blocked"] is True and r[reason] and not bulk.called


@pytest.mark.asyncio
async def test_rechunk_failed_registration_is_an_error_not_success():
    from agents.knowledge_rag.knowledge import service
    current = [{"id": 1, "content": "옛 조각", "heading_path": None, "category": "c", "source_type": "paste_split",
                "source_file": "f", "edited": False}]
    with _patch_conn(DOC, current), patch.object(service, "bulk_create_knowledge",
                                                  AsyncMock(return_value={"job_id": 1, "status": "failed"})):
        with pytest.raises(ValueError):
            await service.rechunk_source_document(5)


@pytest.mark.asyncio
async def test_rechunk_empty_result_is_an_error():
    from agents.knowledge_rag.knowledge import service
    with _patch_conn({**DOC, "raw_content": "   "}, []), patch.object(service, "bulk_create_knowledge", AsyncMock()):
        with pytest.raises(ValueError):
            await service.rechunk_source_document(5)


def test_draft_reuse_only_same_type_ref_hash_drafts_and_nul_stripped():
    """리뷰: 해시만 같으면 다른 파일·다른 종류의 원문 행(심지어 사용 중)을 재사용해 엉뚱한 원문의 청크를 폐기할 수 있었다."""
    import asyncio
    seen = {}

    class C:
        async def fetchrow(self, sql, *a):
            seen["reuse_sql"], seen["reuse_args"] = sql, a
            return None

        async def fetchval(self, sql, *a):
            seen["insert_args"] = a
            return 3
    asyncio.run(L.save_draft(C(), ns_id=1, source_type="file", source_ref="b.md", title="b", parent_title=None,
                             raw_format="text", raw_content="같은\x00내용", chunk_strategy="auto"))
    assert "status = 'draft'" in seen["reuse_sql"] and "source_type = $2" in seen["reuse_sql"]
    assert "source_ref IS NOT DISTINCT FROM $3" in seen["reuse_sql"]
    assert seen["insert_args"][6] == "같은내용"                       # NUL 제거(청크 저장과 같게)
