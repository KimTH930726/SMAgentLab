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

# 조건/예외 연결 표현 — 이 표현으로 시작하는 문단·섹션은 직전 내용의 예외/단서라 따로
# 떨어지면 의미가 깨진다(2026-09-22, 사용자 제안 반영 — B2C 정책 "1. 자사몰만 적용"이
# 뒤 항목들과 분리되던 실사고, SHORT_INTRO_CHARS 메커니즘의 일반화). 문장 맨 앞에서만
# 매치 — 본문 중간에 이 단어가 나오는 정상 문장까지 오탐하지 않도록.
_CONTINUATION_RE = re.compile(r"^(단[,\s]|다만[,\s]|예외\s*[:：]|주의\s*[:：]|주의사항)")


def _strip_macro_artifacts(text: str) -> str:
    lines = [ln for ln in text.split("\n") if _MACRO_ARTIFACT_SIGNATURE not in ln]
    return "\n".join(lines)


@dataclass
class Chunk:
    """분할된 지식 청크."""
    text: str
    idx: int
    section_title: Optional[str] = None
    # 이 청크(정확히는 이 청크를 시작한 섹션)의 조상 헤딩 제목들, 레벨이 얕은 것부터
    # 순서대로(자기 자신은 제외 — section_title에 이미 있음). h1~h4 레벨은
    # web_crawler.py의 _extract_heading_sections()가 이미 추출해두고 있었지만
    # 지금까지 청킹 단계에서 버려지고 있었다(2026-09-22 — Parent-Child 문맥 유실
    # 실사고 이후 추가, docs/tech/rag-improvement-wbs.md 2-7).
    heading_path: list[str] = None
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.heading_path is None:
            self.heading_path = []


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
        # confluence-chunking-spec.md §3 실측 결론(2026-09-16): 리포트형/체크리스트형
        # 둘 다 "합쳐도 max_chars 이하면 병합"이 손해였음 — 섹션이 2개 이상이면 무조건
        # 섹션당 1청크로 분리하는 게 실측으로 더 나음(89문항 유사도 비교, hit@10 개선).
        # 이 함수는 confluence 전용이 아니라 txt/md/pdf 등도 공유해서(스펙 문서의 경고
        # 그대로) 다른 소스 타입까지 한꺼번에 바꾸지 않고 confluence/web처럼 실측된
        # 소스 타입에만 한정 적용한다.
        always_split = doc.source_type in ("confluence", "web")
        chunks = _chunk_by_sections(doc, max_chars, min_chars, always_split=always_split)
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


def _chunk_by_sections(doc: ParsedDocument, max_chars: int, min_chars: int, always_split: bool = False) -> list[Chunk]:
    """섹션 기반 분할 — 섹션이 너무 크면 재분할, 너무 작으면 병합.

    always_split=True면 "합쳐도 max_chars 이하니 병합"은 안 하고 섹션마다 원칙적으로
    별도 청크로 flush한다(confluence-chunking-spec.md §3 실측 결론) — 단, 조건/예외
    연결 표현(_CONTINUATION_RE)으로 시작하는 섹션만은 always_split 여부와 무관하게
    항상 직전 버퍼에 강제 병합한다("단, 서비스를 재시작하면 안 된다"류 문장이 앞
    문단과 분리되면 의미가 깨지므로 — SHORT_INTRO_CHARS 메커니즘의 일반화).
    """
    chunks: list[Chunk] = []
    buffer_title = ""
    buffer_heading_path: list[str] = []
    buffer_text = ""
    # 직전 섹션이 짧은 도입부였다면 그 "## 제목\n내용" 문자열을 들고 있다가, 그게 방금
    # flush된 버퍼에 딸려가고 그 다음 섹션(원래 이 도입부가 설명하려던 하위 섹션)이 새
    # 버퍼로 고립될 때 다시 앞에 붙여준다.
    prev_short_intro: Optional[str] = None
    # 조상 헤딩 스택 — (level, title). 같은 레벨 이하가 나오면 그 아래 조상들은 더 이상
    # 유효하지 않으므로 pop한다(예: h2 다음에 h2가 또 나오면 이전 h2는 형제, 조상 아님).
    heading_stack: list[tuple[int, str]] = []

    for sec in doc.sections:
        level = sec.get("level") or 0
        title = sec.get("title", "")
        if level > 0:
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
        current_ancestors = [t for _, t in heading_stack]

        raw_content = _strip_macro_artifacts(sec["content"])
        section_text = raw_content
        if sec["title"]:
            section_text = f"## {sec['title']}\n{raw_content}"
        is_short = len(raw_content.strip()) < SHORT_INTRO_CHARS
        is_continuation = bool(buffer_text) and bool(_CONTINUATION_RE.match(raw_content.strip()))

        fits_in_buffer = bool(buffer_text) and len(buffer_text) + len(section_text) <= max_chars
        # 병합 조건: 조건/예외 연결 표현이면 무조건(always_split이어도) 병합.
        # 그 외엔 always_split이 아니고 크기가 맞을 때만 병합(기존 동작).
        if is_continuation or (not always_split and fits_in_buffer):
            buffer_text += "\n\n" + section_text
            prev_short_intro = section_text if is_short else None
            if level > 0 and title:
                heading_stack.append((level, title))
            continue

        # 버퍼가 차있으면 flush
        if buffer_text.strip():
            chunks.append(Chunk(text=buffer_text.strip(), idx=len(chunks), section_title=buffer_title, heading_path=buffer_heading_path))

        prefix = (prev_short_intro + "\n\n") if prev_short_intro else ""
        prev_short_intro = None

        # 현재 섹션이 max 초과면 paragraph로 재분할
        if len(section_text) > max_chars:
            sub_chunks = _chunk_by_paragraphs(prefix + section_text, max_chars, min_chars)
            for sc in sub_chunks:
                sc.section_title = sec.get("title", "")
                sc.heading_path = current_ancestors
            chunks.extend(sub_chunks)
            buffer_text = ""
            buffer_title = ""
            buffer_heading_path = []
        else:
            buffer_text = prefix + section_text
            buffer_title = sec.get("title", "")
            buffer_heading_path = current_ancestors
            if is_short:
                prev_short_intro = section_text

        if level > 0 and title:
            heading_stack.append((level, title))

    # 마지막 버퍼 flush
    if buffer_text.strip():
        chunks.append(Chunk(text=buffer_text.strip(), idx=len(chunks), section_title=buffer_title, heading_path=buffer_heading_path))

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
