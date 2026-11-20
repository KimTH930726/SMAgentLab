"""컨플 첨부 글(11/19 ⑦) — 본문이 가리키는 문서 첨부만, 워드·PPT 글 추출, 상한, 페이지 끝 섹션, 원장 그대로 다시 분할, 실패 격리."""
import asyncio
import io
import zipfile

from bs4 import BeautifulSoup

from agents.knowledge_rag.ingestion import confluence_attach as ca
from agents.knowledge_rag.ingestion import web_crawler as w
from agents.knowledge_rag.ingestion.source_ledger import rebuild_parsed

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'


def _zip(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, c in files.items():
            z.writestr(n, c)
    return b.getvalue()


def docx(*paras) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p[:3]}</w:t></w:r><w:r><w:t>{p[3:]}</w:t></w:r></w:p>" for p in paras)
    return _zip({"word/document.xml": f"<w:document {W}><w:body>{body}</w:body></w:document>"})


def pptx(*slides) -> bytes:
    files = {}
    for i, s in enumerate(slides, 1):
        files[f"ppt/slides/slide{i}.xml"] = f"<p:sld {A}><a:p><a:r><a:t>{s}</a:t></a:r></a:p></p:sld>"
    files["ppt/slides/slide10.xml"] = f"<p:sld {A}><a:p><a:r><a:t>열번째</a:t></a:r></a:p></p:sld>"   # 숫자 순서(문자 순서면 10이 2 앞)
    return _zip(files)


def test_find_only_referenced_documents_not_images():
    s = ('<ac:image><ri:attachment ri:filename="화면.png" /></ac:image>'
         '<ac:image><ri:attachment ri:filename="표지.pdf" /></ac:image>'   # 그림으로 넣은 pdf는 그림
         '<ac:structured-macro ac:name="view-file"><ac:parameter ac:name="name"><ri:attachment ri:filename="매뉴얼.xlsx" /></ac:parameter>'
         '</ac:structured-macro><p><ac:link><ri:attachment ri:filename="가이드.docx" /></ac:link></p>'
         '<p><ac:link><ri:attachment ri:filename="가이드.docx" /></ac:link></p><p><ac:link><ri:attachment ri:filename="a.zip" /></ac:link></p>')
    assert ca.find_doc_attachments(s) == ["가이드.docx"]   # 엑셀·CSV·zip은 글을 읽지 않는다(11/19 문서형만 — 자리 표시만 남음)


def test_find_caps_files():
    s = "".join(f'<p><ac:link><ri:attachment ri:filename="f{i}.pdf" /></ac:link></p>' for i in range(30))
    assert len(ca.find_doc_attachments(s)) == ca.ATTACH_MAX_FILES


def test_docx_and_pptx_text():
    assert ca.attachment_text(docx("정산 절차 안내", "마감 후 재처리"), "a.docx") == "정산 절차 안내\n마감 후 재처리"
    t = ca.attachment_text(pptx("첫장", "둘째장"), "b.pptx")
    assert t.index("[슬라이드 1]") < t.index("[슬라이드 2]") < t.index("열번째")


def test_text_cap():
    t = ca.attachment_text(("가" * (ca.ATTACH_TEXT_MAX + 50)).encode(), "c.txt")
    assert len(t) <= ca.ATTACH_TEXT_MAX + 30 and t.endswith("원문 첨부에서 확인)")


def test_appended_as_heading_section():
    html = ca.append_attachments("<p>본문</p>", {"매뉴얼.xlsx": "시트1\n값A"})
    soup = BeautifulSoup(w.prepare_storage_html(html), "lxml")
    secs = w._extract_heading_sections(soup)
    assert any(s.get("title") == "첨부: 매뉴얼.xlsx" and "값A" in s.get("content", "") for s in secs), secs
    assert "본문" in w._extract_text(soup)


def test_rebuild_uses_ledger_attachments_only():
    row = {"source_type": "confluence", "raw_content": "<p>본문</p>", "title": "t", "parent_title": None, "source_ref": "1",
           "source_version": 1}
    assert "값A" not in rebuild_parsed({**row, "media": {"diagram_text": True}}).raw_text       # 옛 원장 — 예전 그대로
    assert "값A" in rebuild_parsed({**row, "media": {"attachments": {"m.xlsx": "값A"}}}).raw_text   # 옛 dict 형식도 읽음
    assert "값A" in rebuild_parsed({**row, "media": {"attachments": [["m.xlsx", "값A"]]}}).raw_text


def test_order_survives_json_roundtrip():
    # 리뷰 M5 — jsonb 객체는 키를 길이순으로 다시 정렬한다. 목록이면 문서 순서 그대로(미리보기 = 적재 = 다시 분할)
    import json
    att = [["매뉴얼_긴이름.pdf", "첫째"], ["b.pdf", "둘째"]]
    html = ca.append_attachments("<p>x</p>", json.loads(json.dumps(att)))
    assert html.index("첫째") < html.index("둘째")


def test_skips_other_page_attachment_ref():
    s = ('<p><ac:link><ri:attachment ri:filename="남의.pdf"><ri:page ri:content-title="다른 페이지" /></ri:attachment></ac:link></p>'
         '<p><ac:link><ri:attachment ri:filename="내것.pdf" /></ac:link></p>')
    assert ca.find_doc_attachments(s) == ["내것.pdf"]


def test_docx_textbox_and_fallback_not_duplicated():
    xml = (f'<w:document {W} xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>'
           '<w:p><w:r><w:t>바깥</w:t></w:r><w:r><mc:AlternateContent><mc:Choice><w:txbxContent><w:p><w:r><w:t>상자글</w:t></w:r></w:p>'
           '</w:txbxContent></mc:Choice><mc:Fallback><w:txbxContent><w:p><w:r><w:t>상자글</w:t></w:r></w:p></w:txbxContent></mc:Fallback>'
           '</mc:AlternateContent></w:r></w:p></w:body></w:document>')
    t = ca.attachment_text(_zip({"word/document.xml": xml}), "x.docx")
    assert t.count("상자글") == 1 and t.count("바깥") == 1


def test_zip_bomb_refused(monkeypatch):
    monkeypatch.setattr(ca, "ATTACH_MAX_UNZIPPED", 10)
    import pytest
    with pytest.raises(ValueError):
        ca.attachment_text(docx("열 글자 넘는 문단입니다"), "a.docx")


class _Resp:
    def __init__(self, json=None, content=b"", status=200):
        self._json, self.content, self.status_code = json, content, status

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        for i in range(0, len(self.content), 4):
            yield self.content[i:i + 4]


class FakeClient:
    def __init__(self, files):
        self.files = files   # name -> (bytes or Exception, size)

    async def get(self, url, headers=None, params=None):
        return _Resp({"results": [{"title": n, "_links": {"download": f"/dl/{n}"}, "extensions": ({"fileSize": sz} if sz else {})}
                                  for n, (_, sz) in self.files.items()]})

    def stream(self, method, url, headers=None):
        data, _ = self.files[url.rsplit("/", 1)[-1]]
        return _Resp(status=500) if isinstance(data, Exception) else _Resp(content=data)


def test_fetch_isolates_failures_and_skips_big():
    cl = FakeClient({"ok.docx": (docx("정상 문서"), 100), "big.pdf": (b"x", ca.ATTACH_MAX_BYTES + 1),
                     "bad.pptx": (RuntimeError(), 10), "broken.docx": (b"not a zip", 10), "z.txt": ("두번째".encode(), 0)})
    out = asyncio.run(ca.fetch_attachments(cl, "http://c", "1", ["ok.docx", "big.pdf", "bad.pptx", "broken.docx", "missing.pdf", "z.txt"], {}))
    assert out == [["ok.docx", "정상 문서"], ["z.txt", "두번째"]]


def test_download_cut_at_cap_without_size_info(monkeypatch):
    monkeypatch.setattr(ca, "ATTACH_MAX_BYTES", 10)
    cl = FakeClient({"big.txt": (b"x" * 50, 0)})   # 크기 정보 없음 — 받는 중에 끊는다
    assert asyncio.run(ca.fetch_attachments(cl, "http://c", "1", ["big.txt"], {})) == []
