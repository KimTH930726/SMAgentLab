"""파일 포맷별 파싱 어댑터 — 다양한 입력을 통일 포맷으로 변환."""
import csv
import io
import itertools
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ParsedDocument:
    """파싱된 문서 통일 포맷."""
    source_type: str  # txt, md, pdf, xlsx, csv, confluence, web
    source_name: str
    raw_text: str
    sections: list[dict] = field(default_factory=list)  # [{title, content, level}]
    tables: list[dict] = field(default_factory=list)     # [{headers, rows}]
    metadata: dict = field(default_factory=dict)


# 줄 단위 제목 인식(2026-10-07) — txt·md·PDF·붙여넣기 공통. 예전엔 경로마다 따로였다: 붙여넣기는 "## "만, md는 코드 블록 안
# "# 주석"도 제목으로, PDF는 `^(?:\d+[\.\-])+\s*`라 "3.5kg"·"2026-10-07"을 제목으로 잘라 본문을 바꾸고 제목엔 번호만 남겼다.
# 규칙(데이터 무관 일반 규칙):
#   - "#"~"######" + 공백 → 레벨 = # 개수
#   - 두 단계 이상 번호("1.2", "1.2.1.", "1-1") + 공백 + 글자, 100자 이하 → 레벨 = 번호 단계 수
#     ("1." 한 단계는 목록 항목으로 본다 — 본문 "1. 대분류 적용 가능"을 제목으로 쪼개지 않게)
#   - 숫자 바로 뒤에 공백이 아닌 글자가 오면 제목 아님("3.5kg"), 번호 한 단계가 4자리 이상이면 날짜·코드("2026-10-07 배포")
#   - 문장으로 끝나면(마침표, "~다") 번호로 시작해도 본문("1.5 이상이면 할인한다")
#   - ``` / ~~~ 코드 블록 안은 제목으로 보지 않음, PDF 쪽 표시("--- Page N ---")는 본문에서 뺌
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")
_OUTLINE_HEADING_RE = re.compile(r"^\s{0,3}(\d{1,2}(?:[.\-]\d{1,2})+)[.)]?\s+(\S.*)$")   # 단계당 2자리 — IP("192.168…")·날짜 제외
_SENTENCE_END_RE = re.compile(r"(\.|。|다|:|：)\s*$")   # "요"는 명사(개요·필요)와 겹쳐 제외 — "~요."는 마침표로 걸림
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_PDF_PAGE_RE = re.compile(r"^--- Page \d+ ---$")


def heading_of(line: str):
    """제목 줄이면 (레벨, 제목), 아니면 None."""
    m = _MD_HEADING_RE.match(line)
    if m:
        return len(m.group(1)), m.group(2).strip()
    m = _OUTLINE_HEADING_RE.match(line)
    if m and len(line.strip()) <= 100 and not _SENTENCE_END_RE.search(m.group(2)):
        depth = len(re.split(r"[.\-]", m.group(1)))
        return depth, line.strip()
    return None


def extract_heading_sections(text: str) -> list[dict]:
    """텍스트 → [{title, content, level}]. 첫 제목 앞 서두는 level 0 섹션."""
    sections: list[dict] = []
    title, level, lines = "", 0, []
    in_fence = False
    for line in (text or "").split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            lines.append(line)
            continue
        if not in_fence and _PDF_PAGE_RE.match(line.strip()):
            continue
        h = None if in_fence else heading_of(line)
        if h:
            if title or "\n".join(lines).strip():
                sections.append({"title": title, "content": "\n".join(lines).strip(), "level": level})
            level, title = h
            lines = []
        else:
            lines.append(line)
    if title or "\n".join(lines).strip():
        sections.append({"title": title, "content": "\n".join(lines).strip(), "level": level})
    return sections


def parse_text(content: str, filename: str) -> ParsedDocument:
    """일반 텍스트 파싱 — 제목이 2개 이상이면 섹션으로(없으면 단락 분할)."""
    sections = extract_heading_sections(content)
    return ParsedDocument(
        source_type="txt",
        source_name=filename,
        raw_text=content,
        sections=sections if sum(1 for x in sections if x["title"]) >= 2 else [],
    )


def parse_markdown(content: str, filename: str) -> ParsedDocument:
    """마크다운 파싱 — 공통 제목 인식기로 섹션 추출(코드 블록 안 "#"은 제목 아님)."""
    sections = extract_heading_sections(content)

    # 테이블 추출 (마크다운 테이블)
    tables = _extract_md_tables(content)

    return ParsedDocument(
        source_type="md",
        source_name=filename,
        raw_text=content,
        sections=sections,
        tables=tables,
    )


def parse_pdf(content_bytes: bytes, filename: str) -> ParsedDocument:
    """PDF 파싱 — pymupdf 사용."""
    try:
        import pymupdf  # PyMuPDF
    except ImportError:
        try:
            import fitz as pymupdf  # 구 버전 호환
        except ImportError:
            logger.warning("pymupdf 미설치 — PDF를 텍스트로만 추출합니다.")
            # fallback: 바이너리를 디코딩 시도 (당연히 실패하지만 에러 메시지용)
            raise ImportError("PDF 파싱을 위해 pymupdf를 설치하세요: pip install pymupdf")

    doc = pymupdf.open(stream=content_bytes, filetype="pdf")
    try:
        # 암호화/비밀번호 보호 PDF 감지 — needs_pass 또는 is_encrypted 속성 확인
        if getattr(doc, "needs_pass", False) or getattr(doc, "is_encrypted", False):
            raise ValueError(
                "암호화(비밀번호 보호)된 PDF는 등록할 수 없습니다. "
                "원본 PDF의 비밀번호를 해제한 후 다시 업로드해주세요. "
                "(Acrobat → 도구 → 보호 → 암호화 → 보안 제거)"
            )

        all_text_lines: list[str] = []
        page_count = len(doc)

        for page_num in range(page_count):
            page = doc[page_num]
            text = page.get_text()
            all_text_lines.append(f"--- Page {page_num + 1} ---")
            all_text_lines.append(text)
    finally:
        doc.close()

    raw_text = "\n".join(all_text_lines)
    sections = extract_heading_sections(raw_text)

    return ParsedDocument(
        source_type="pdf",
        source_name=filename,
        raw_text=raw_text,
        sections=sections,
        metadata={"page_count": page_count},
    )


def parse_xlsx(content_bytes: bytes, filename: str) -> ParsedDocument:
    """Excel(.xlsx) 파싱 — openpyxl 사용. 시트별로 텍스트 변환."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ImportError("XLSX 파싱을 위해 openpyxl을 설치하세요: pip install openpyxl")

    # xlsx는 내부적으로 zip 압축 — 구버전 .xls(binary)나 손상된 파일은 거부됨
    try:
        wb = load_workbook(io.BytesIO(content_bytes), data_only=True, read_only=True)
    except Exception as e:
        msg = str(e).lower()
        if "not a zip" in msg or "badzipfile" in msg or "file is not a zip" in msg:
            raise ValueError(
                "이 파일은 .xlsx 형식이 아닙니다. 다음 중 하나일 수 있습니다:\n"
                "  ① 구버전 .xls 파일 (Excel에서 '다른 이름으로 저장' → .xlsx로 변환 필요)\n"
                "  ② 파일이 손상됨 (다시 저장해보세요)\n"
                "  ③ 확장자만 .xlsx로 바뀐 다른 형식 (실제 내용은 CSV/HTML 등)"
            )
        raise ValueError(f"XLSX 파싱 실패: {e}")

    sections: list[dict] = []
    tables: list[dict] = []
    all_text_lines: list[str] = []
    total_rows = 0

    for sheet in wb.worksheets:
        sheet_name = sheet.title
        rows_data: list[list[str]] = []
        for row in sheet.iter_rows(values_only=True):
            # 빈 행은 스킵
            if not any(c is not None and str(c).strip() for c in row):
                continue
            cells = [("" if c is None else str(c).strip()) for c in row]
            rows_data.append(cells)

        if not rows_data:
            continue

        # 첫 행을 헤더로 가정
        headers = rows_data[0]
        body_rows = rows_data[1:] if len(rows_data) > 1 else []
        total_rows += len(body_rows)

        # 테이블 메타 보관
        tables.append({"sheet": sheet_name, "headers": headers, "rows": body_rows})

        # 텍스트 직렬화: "헤더: 값" per row (RAG 검색에 유리)
        # 행마다 빈 줄을 추가해 paragraph 청킹이 행 경계에서 분리되도록 함
        sheet_text_lines = [f"## 시트: {sheet_name}"]
        for row in body_rows:
            row_parts = []
            # zip_longest: 헤더보다 짧은 행의 끝 컬럼도 누락 없이 처리
            for h, v in itertools.zip_longest(headers, row, fillvalue=""):
                if v:
                    row_parts.append(f"{h}: {v}" if h else v)
            if row_parts:
                sheet_text_lines.append(" | ".join(row_parts))
                sheet_text_lines.append("")  # 행 구분 빈 줄 → paragraph 경계

        section_text = "\n".join(sheet_text_lines[1:]).strip()
        all_text_lines.extend(sheet_text_lines)
        all_text_lines.append("")

        sections.append({
            "title": f"시트: {sheet_name}",
            "content": section_text,
            "level": 1,
        })

    raw_text = "\n".join(all_text_lines).strip()

    return ParsedDocument(
        source_type="xlsx",
        source_name=filename,
        raw_text=raw_text,
        sections=sections,
        tables=tables,
        metadata={"sheet_count": len(wb.worksheets), "total_rows": total_rows},
    )


def _decode_text(content_bytes: bytes) -> str:
    """UTF-8-BOM → UTF-8 → EUC-KR(CP949) 순으로 디코딩 시도."""
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return content_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(
        "파일 인코딩을 인식할 수 없습니다. UTF-8 또는 EUC-KR(한글 Windows)로 저장된 파일만 지원합니다."
    )


def parse_csv(content_bytes: bytes, filename: str) -> ParsedDocument:
    """CSV 파싱 — 첫 행 헤더로 가정, 행별 직렬화."""
    text = _decode_text(content_bytes)
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return ParsedDocument(source_type="csv", source_name=filename, raw_text="")

    headers = [h.strip() for h in rows[0]]
    body_rows = rows[1:]

    lines = [f"## {filename}"]
    for row in body_rows:
        row_parts = []
        # zip_longest: 헤더보다 짧은 행의 끝 컬럼도 누락 없이 처리
        for h, v in itertools.zip_longest(headers, row, fillvalue=""):
            v = v.strip() if v else ""
            if v:
                row_parts.append(f"{h}: {v}" if h else v)
        if row_parts:
            lines.append(" | ".join(row_parts))
            lines.append("")  # 행 구분 빈 줄 → paragraph 경계

    return ParsedDocument(
        source_type="csv",
        source_name=filename,
        raw_text="\n".join(lines).strip(),
        tables=[{"headers": headers, "rows": body_rows}],
        metadata={"row_count": len(body_rows)},
    )


def parse_file(content_bytes: bytes, filename: str) -> ParsedDocument:
    """파일 확장자로 적절한 어댑터 선택."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "pdf":
        return parse_pdf(content_bytes, filename)
    elif ext in ("xlsx", "xlsm"):
        return parse_xlsx(content_bytes, filename)
    elif ext == "csv":
        return parse_csv(content_bytes, filename)
    elif ext in ("md", "markdown"):
        return parse_markdown(_decode_text(content_bytes), filename)
    elif ext in ("txt", "log", "text"):
        return parse_text(_decode_text(content_bytes), filename)
    else:
        # fallback: 텍스트로 시도
        try:
            text = content_bytes.decode("utf-8-sig")
            return parse_text(text, filename)
        except UnicodeDecodeError:
            raise ValueError(f"지원하지 않는 파일 형식: {ext}")


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _extract_md_tables(text: str) -> list[dict]:
    """마크다운 테이블 추출."""
    tables: list[dict] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # 테이블 헤더 감지: | col1 | col2 |
        if line.startswith("|") and line.endswith("|") and line.count("|") >= 3:
            headers = [h.strip() for h in line.split("|")[1:-1]]
            # 다음 줄이 구분선인지 확인: |---|---|
            if i + 1 < len(lines) and re.match(r'^\|[\s\-:]+\|', lines[i + 1].strip()):
                rows: list[list[str]] = []
                j = i + 2
                while j < len(lines):
                    row_line = lines[j].strip()
                    if not row_line.startswith("|"):
                        break
                    cells = [c.strip() for c in row_line.split("|")[1:-1]]
                    rows.append(cells)
                    j += 1
                if rows:
                    tables.append({"headers": headers, "rows": rows})
                i = j
                continue
        i += 1
    return tables
