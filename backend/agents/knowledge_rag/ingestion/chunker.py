"""청킹 엔진 — 문서를 최적 크기의 지식 단위로 분할."""
import logging
import re
from dataclasses import dataclass
from typing import Optional

from agents.knowledge_rag.ingestion.adapters import ParsedDocument

logger = logging.getLogger(__name__)

# 토큰 근사: 한국어 1글자 ≈ 1.5토큰, 영어 1단어 ≈ 1.3토큰
MIN_CHUNK_CHARS = 50
MAX_CHUNK_CHARS = 2000
OVERLAP_CHARS = 100

# 짧은 상위 섹션 도입부(예: "## 1.3.2 신규 주문\n...성공 이후에 신규 주문을 전송한다.")가
# 그 직전 섹션(1.3.1, 이미 max_chars에 가까움)에 붙어버리면, 정작 그 도입부가 설명하려던
# 하위 섹션(1.3.2.1)은 부모 제목 없이 통째로 새 청크로 분리돼 맥락을 잃는다(2026-09-17,
# 신규 Confluence 일괄 임포트 파일럿에서 실측 — "## 1.3.2.1. 시점"만 있고 이게 "신규 주문"
# 얘기라는 걸 알 길이 없는 청크가 2건 나옴). 짧은 섹션은 뒤따르는 하위 섹션 쪽으로 넘겨준다.
SHORT_INTRO_CHARS = 150

# Confluence 다이어그램/흐름도 macro가 텍스트 추출 과정에서 그대로 깨져 들어오는 잔재 —
# 같은 파일럿에서 4건 실측, 전부 "흐름도" 절 아래 이 정확한 서명("false auto top")을
# 포함한 한 줄짜리 파라미터 덤프였다(예: "true 3.11. 장바구니 선계산 false auto top
# DlvsDeletePrivacy true 771 7", "true DlvsAllocationStatus false auto top true 1533 2").
# true/false를 세는 일반 휴리스틱은 정상 문장(예: "true/false 값을 가진다")을 오탐할 수
# 있어, 실측된 정확한 서명 문자열만 좁게 매치한다 — 다른 macro 잔재 패턴이 새로 발견되면
# 그때 추가.
_MACRO_ARTIFACT_SIGNATURE = "false auto top"


def _strip_macro_artifacts(text: str) -> str:
    lines = [ln for ln in text.split("\n") if _MACRO_ARTIFACT_SIGNATURE not in ln]
    return "\n".join(lines)


@dataclass
class Chunk:
    """분할된 지식 청크."""
    text: str
    idx: int
    section_title: Optional[str] = None
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


def chunk_document(
    doc: ParsedDocument,
    strategy: str = "auto",
    max_chars: int = MAX_CHUNK_CHARS,
    min_chars: int = MIN_CHUNK_CHARS,
    overlap_chars: int = OVERLAP_CHARS,
) -> list[Chunk]:
    """ParsedDocument → Chunk 리스트.

    strategy:
      - auto: 섹션이 있으면 section, 없으면 paragraph
      - section: doc.sections 기반 분할
      - paragraph: 빈 줄 기반 분할
      - fixed: 고정 크기 분할
    """
    if strategy == "auto":
        strategy = "section" if doc.sections and len(doc.sections) > 1 else "paragraph"

    # "section"은 doc.sections가 있어야 의미가 있음 — LLM Analyzer가 파일 확장자만
    # 보고(txt 등) 내용에 헤더가 있다고 판단해 "section"을 명시적으로 반환하면
    # doc.sections가 비어 있어도 그대로 _chunk_by_sections가 호출돼 청크 0건이
    # 등록되는 무음 실패가 있었음 — auto 분기와 동일하게 안전망을 건다.
    if strategy == "section" and not doc.sections:
        strategy = "paragraph"

    if strategy == "section":
        chunks = _chunk_by_sections(doc, max_chars, min_chars)
    elif strategy == "paragraph":
        chunks = _chunk_by_paragraphs(doc.raw_text, max_chars, min_chars)
    elif strategy == "fixed":
        chunks = _chunk_fixed_size(doc.raw_text, max_chars, overlap_chars)
    else:
        chunks = _chunk_by_paragraphs(doc.raw_text, max_chars, min_chars)

    # 테이블 청크 추가 — xlsx/csv는 섹션에 이미 행 데이터가 포함되므로 제외
    # Confluence/웹/PDF 등 본문에 삽입된 표만 별도 청크로 추가
    if doc.source_type not in ("xlsx", "csv"):
        for tbl in doc.tables:
            table_text = _table_to_markdown(tbl)
            if table_text.strip():
                chunks.append(Chunk(
                    text=table_text,
                    idx=len(chunks),
                    section_title="[표 데이터]",
                    metadata={"is_table": True},
                ))

    # 인덱스 재부여
    for i, c in enumerate(chunks):
        c.idx = i

    # 빈 청크 제거
    chunks = [c for c in chunks if c.text.strip() and len(c.text.strip()) >= min_chars]

    logger.info("청킹 완료: %s → %d개 청크 (strategy=%s)", doc.source_name, len(chunks), strategy)
    return chunks


def _chunk_by_sections(doc: ParsedDocument, max_chars: int, min_chars: int) -> list[Chunk]:
    """섹션 기반 분할 — 섹션이 너무 크면 재분할, 너무 작으면 병합."""
    chunks: list[Chunk] = []
    buffer_title = ""
    buffer_text = ""
    # 직전 섹션이 짧은 도입부였다면 그 "## 제목\n내용" 문자열을 들고 있다가, 그게 방금
    # flush된 버퍼에 딸려가고 그 다음 섹션(원래 이 도입부가 설명하려던 하위 섹션)이 새
    # 버퍼로 고립될 때 다시 앞에 붙여준다.
    prev_short_intro: Optional[str] = None

    for sec in doc.sections:
        raw_content = _strip_macro_artifacts(sec["content"])
        section_text = raw_content
        if sec["title"]:
            section_text = f"## {sec['title']}\n{raw_content}"
        is_short = len(raw_content.strip()) < SHORT_INTRO_CHARS

        # 버퍼와 합쳤을 때 max 이하면 병합
        if buffer_text and len(buffer_text) + len(section_text) <= max_chars:
            buffer_text += "\n\n" + section_text
            prev_short_intro = section_text if is_short else None
            continue

        # 버퍼가 차있으면 flush
        if buffer_text.strip():
            chunks.append(Chunk(text=buffer_text.strip(), idx=len(chunks), section_title=buffer_title))

        prefix = (prev_short_intro + "\n\n") if prev_short_intro else ""
        prev_short_intro = None

        # 현재 섹션이 max 초과면 paragraph로 재분할
        if len(section_text) > max_chars:
            sub_chunks = _chunk_by_paragraphs(prefix + section_text, max_chars, min_chars)
            for sc in sub_chunks:
                sc.section_title = sec.get("title", "")
            chunks.extend(sub_chunks)
            buffer_text = ""
            buffer_title = ""
        else:
            buffer_text = prefix + section_text
            buffer_title = sec.get("title", "")
            if is_short:
                prev_short_intro = section_text

    # 마지막 버퍼 flush
    if buffer_text.strip():
        chunks.append(Chunk(text=buffer_text.strip(), idx=len(chunks), section_title=buffer_title))

    return chunks


def _chunk_by_paragraphs(text: str, max_chars: int, min_chars: int) -> list[Chunk]:
    """단락(빈 줄) 기반 분할 — 너무 작은 단락은 병합."""
    paragraphs = re.split(r'\n\s*\n', text)
    chunks: list[Chunk] = []
    buffer = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if buffer and len(buffer) + len(para) + 2 > max_chars:
            # 버퍼 flush
            if buffer.strip():
                chunks.append(Chunk(text=buffer.strip(), idx=len(chunks)))
            buffer = para
        elif len(para) > max_chars:
            # 버퍼 먼저 flush
            if buffer.strip():
                chunks.append(Chunk(text=buffer.strip(), idx=len(chunks)))
                buffer = ""
            # 큰 단락은 고정 크기로 재분할
            sub = _chunk_fixed_size(para, max_chars, overlap_chars=50)
            chunks.extend(sub)
        else:
            buffer = (buffer + "\n\n" + para) if buffer else para

    if buffer.strip():
        chunks.append(Chunk(text=buffer.strip(), idx=len(chunks)))

    return chunks


def _chunk_fixed_size(text: str, max_chars: int, overlap_chars: int) -> list[Chunk]:
    """고정 크기 분할 + overlap."""
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk_text = text[start:end]

        # 단어 경계에서 자르기 (마지막이 아닌 경우)
        if end < len(text):
            last_space = chunk_text.rfind(" ")
            last_newline = chunk_text.rfind("\n")
            cut = max(last_space, last_newline)
            if cut > max_chars // 2:
                chunk_text = chunk_text[:cut]
                end = start + cut

        if chunk_text.strip():
            chunks.append(Chunk(text=chunk_text.strip(), idx=len(chunks)))

        start = end - overlap_chars if end < len(text) else end

    return chunks


def _table_to_markdown(table: dict) -> str:
    """표 데이터를 마크다운 테이블 포맷으로 변환."""
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    if not headers:
        return ""

    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        # 셀 수 맞추기
        cells = row + [""] * (len(headers) - len(row))
        lines.append("| " + " | ".join(cells[:len(headers)]) + " |")

    return "\n".join(lines)
