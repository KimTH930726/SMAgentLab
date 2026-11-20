"""컨플 첨부 문서 글 읽기(11/19 최종 적재 진단 ⑦).

본문이 가리키는 첨부(첨부 보기 매크로·첨부 링크)만 — 페이지에 붙어만 있는 옛 첨부는 안 본다. ERP 공간은 지식이 첨부에 몰려 있었다
(엑셀 163·PPT 163·PDF 145·워드 28, 본문엔 "매뉴얼 참고"만). 그림·draw.io처럼 뽑은 글을 원장(media.attachments)에 남겨 다시 분할이
네트워크 없이 같은 결과를 낸다. 페이지 끝에 "첨부: 파일명" 제목 섹션으로 붙인다(조각 제목 경로에 첨부 이름이 남는다).
PDF·엑셀·CSV·글은 파일 등록과 같은 파서(adapters), 워드·PPT는 zip 안 XML에서 글만(새 패키지 없이 — 폐쇄망 이미지 그대로).
안 하는 것: 옛 형식(.xls·.ppt·.doc), 그림 안 글(OCR), 압축 파일 풀기, 다른 페이지의 첨부.
"""
from __future__ import annotations

import asyncio
import html as _html
import io
import logging
import re
import zipfile
from typing import Optional

logger = logging.getLogger(__name__)

# 문서형만(11/19 태훈 승인 — 최적화): 엑셀·CSV는 정기 보고·데이터 표 덩어리가 대부분(홈페이지 첨부 76 중 엑셀 61, 평균 6.7천 자)이라
# 조각·임베딩 비용만 크고 검색 소음 — 형식 기준으로 뺀다(이름 패턴 규칙 아님). 엑셀 자리엔 참조 자리 표시 "[첨부: 이름 — 원문 페이지에서 확인]"만.
DOC_EXTS = ("pdf", "docx", "pptx", "txt", "md")
ATTACH_MAX_FILES = 10          # 페이지당
ATTACH_MAX_BYTES = 15 * 1024 * 1024
ATTACH_MAX_UNZIPPED = 60 * 1024 * 1024   # 워드·PPT 안 XML을 풀었을 때 합계 상한(압축 폭탄 — 리뷰 L3)
ATTACH_TEXT_MAX = 8000         # 첨부 하나에서 넣는 글자 — 넘으면 "…(이하 원문 첨부에서 확인)"

_IMAGE_RE = re.compile(r"<ac:image\b.*?</ac:image>|<ac:image\b[^>]*/>", re.S)
# 스스로 닫힌 참조 = 이 페이지 첨부. 안에 <ri:page>·<ri:blog-post>가 있으면 다른 페이지 첨부라 건너뛴다(같은 이름의 엉뚱한 파일 — 리뷰 L5)
_ATTACH_REF_RE = re.compile(r'<ri:attachment\b[^>]*?ri:filename="([^"]+)"[^>]*?(?:/>|>(.*?)</ri:attachment>)', re.S)
_OTHER_PAGE_RE = re.compile(r"<ri:(?:page|blog-post|space)\b")


def find_doc_attachments(storage: str) -> list[str]:
    """본문이 가리키는 이 페이지 문서 첨부 이름(문서 순서, 중복 없이, 최대 ATTACH_MAX_FILES) — 그림 태그 안 첨부는 그림이라 뺀다."""
    out: list[str] = []
    for m in _ATTACH_REF_RE.finditer(_IMAGE_RE.sub("", storage or "")):
        if m.group(2) and _OTHER_PAGE_RE.search(m.group(2)):
            continue
        name = _html.unescape(m.group(1))
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in DOC_EXTS and name not in out:
            out.append(name)
    return out[:ATTACH_MAX_FILES]


def _xml_text(z: zipfile.ZipFile, names: list[str], para_tag: str, text_tag: str) -> str:
    """zip 안 XML의 문단 글. 대체 내용(mc:Fallback)은 빼고, 글자는 가장 가까운 문단에만 — 글상자 안 문단이 바깥 문단에 또 들어가던 것(리뷰 L6)."""
    from lxml import etree
    lines: list[str] = []
    for n in names:
        root = etree.fromstring(z.read(n))
        for fb in list(root.iter("{*}Fallback")):
            fb.getparent().remove(fb)
        for p in root.iter("{*}" + para_tag):
            own = [x for x in p.iter("{*}" + text_tag)
                   if next((a for a in x.iterancestors() if etree.QName(a).localname == para_tag), None) is p]
            t = "".join(x.text or "" for x in own).strip()
            if t:
                lines.append(t)
    return "\n".join(lines)


def _open_zip(data: bytes, names: list[str]) -> zipfile.ZipFile:
    z = zipfile.ZipFile(io.BytesIO(data))
    if sum(z.getinfo(n).file_size for n in names) > ATTACH_MAX_UNZIPPED:
        z.close()
        raise ValueError("압축을 풀면 너무 큼")
    return z


def _docx_text(data: bytes) -> str:
    names = ["word/document.xml"]
    with _open_zip(data, names) as z:
        return _xml_text(z, names, "p", "t")


def _pptx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z0:
        slides = sorted((n for n in z0.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
    parts = []
    with _open_zip(data, slides) as z:
        for i, n in enumerate(slides, 1):
            t = _xml_text(z, [n], "p", "t")
            if t:
                parts.append(f"[슬라이드 {i}]\n{t}")
    return "\n".join(parts)


def attachment_text(data: bytes, name: str) -> str:
    """첨부 바이트 → 글(상한 적용). 못 읽으면 예외."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext == "docx":
        text = _docx_text(data)
    elif ext == "pptx":
        text = _pptx_text(data)
    else:
        from agents.knowledge_rag.ingestion.adapters import parse_file
        text = parse_file(data, name).raw_text
    text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    return text if len(text) <= ATTACH_TEXT_MAX else text[:ATTACH_TEXT_MAX] + "…(이하 원문 첨부에서 확인)"


async def _download(client, url: str, headers: dict) -> Optional[bytes]:
    """상한까지만 받는다 — 크기 정보가 없는 첨부도 통째로 받기 전에 끊는다(리뷰 L4). 넘으면 None."""
    buf = bytearray()
    async with client.stream("GET", url, headers=headers) as r:
        r.raise_for_status()
        async for chunk in r.aiter_bytes():
            buf += chunk
            if len(buf) > ATTACH_MAX_BYTES:
                return None
    return bytes(buf)


async def fetch_attachments(client, base: str, page_id: str, names: list[str], headers: dict) -> list[list[str]]:
    """같은 페이지 첨부에서 문서 글 [[이름, 글], …] — 문서 순서(jsonb 객체는 키를 다시 정렬해 미리보기와 적재 순서가 달라졌다, 리뷰 M5).
    못 받거나 못 읽은 것은 빠진다(자리엔 "[첨부: 이름 — 원문 페이지에서 확인]"이 남음 — 로그 남김)."""
    if not names:
        return []
    try:
        r = await client.get(f"{base}/rest/api/content/{page_id}/child/attachment", headers=headers, params={"limit": 500})
        r.raise_for_status()
        atts = {a.get("title"): a for a in r.json().get("results", [])}
    except Exception:   # noqa: BLE001
        logger.warning("첨부 목록 실패(page %s) — 첨부 글 없이 이름만", page_id, exc_info=True)
        return []
    out: list[list[str]] = []
    for name in names:
        a = atts.get(name)
        dl = (a or {}).get("_links", {}).get("download")
        size = ((a or {}).get("extensions") or {}).get("fileSize") or 0
        if not dl:
            logger.info("첨부 없음(page %s, %s)", page_id, name)
            continue
        if size > ATTACH_MAX_BYTES:
            logger.info("첨부가 커서 건너뜀(page %s, %s, %s bytes)", page_id, name, size)
            continue
        try:
            data = await _download(client, base + dl, headers)
            if data is None:
                logger.info("첨부가 커서 건너뜀(page %s, %s — 받는 중 상한)", page_id, name)
                continue
            text = await asyncio.to_thread(attachment_text, data, name)   # 큰 PDF·엑셀 파싱이 채팅 응답을 멈추지 않게(리뷰 M6)
            if text:
                out.append([name, text])
        except Exception:   # noqa: BLE001 — 한 첨부 실패는 그 첨부만
            logger.warning("첨부 읽기 실패(page %s, %s)", page_id, name, exc_info=True)
    return out


def append_attachments(storage: str, attachments) -> str:
    """파싱용 storage 사본 끝에 첨부 섹션("첨부: 이름" 제목 + 글). [[이름, 글], …] 순서 그대로(옛 dict도 읽는다)."""
    if not attachments:
        return storage
    parts = [storage]
    for name, text in (attachments.items() if isinstance(attachments, dict) else attachments):
        parts.append(f"<h2>{_html.escape('첨부: ' + name)}</h2>")
        parts.append("".join(f"<p>{_html.escape(line)}</p>" for line in text.split("\n") if line.strip()))
    return "".join(parts)
