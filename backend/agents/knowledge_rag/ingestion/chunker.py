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
        # paste(붙여넣기)는 대부분 컨플루언스·문서 일부를 복사해 오는 경로라 같은 규칙(2026-10-07)
        always_split = doc.source_type in ("confluence", "web", "paste")
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
            if _table_already_in_text(tbl, doc.raw_text):
                continue   # md처럼 표가 본문(섹션)에 그대로 있으면 별도 표 청크는 이중 저장(2026-10-07 감사)
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
    # 짧아도 본문이 있으면 남긴다 — 예전엔 50자 미만이면 버려 짧은 섹션("환불 불가 상품")이 통째로 사라졌다(2026-10-07)
    chunks = [c for c in chunks if c.text.strip() and (len(c.text.strip()) >= min_chars or _has_body(c.text))]

    logger.info("청킹 완료: %s → %d개 청크 (strategy=%s)", doc.source_name, len(chunks), strategy)
    return chunks


def _is_heading_line(line: str) -> bool:
    return line.lstrip().startswith("#")


def _has_body(text: str) -> bool:
    """제목 줄 말고 본문 줄이 하나라도 있나 — 짧아도 본문이 있으면 버리지 않는다(유실 방지)."""
    return any(ln.strip() and not _is_heading_line(ln) for ln in text.split("\n"))


def _emit_carry(chunks: list, text: str, ancestors: Optional[list[str]]) -> None:
    """하위 본문 없이 끝난 대기 도입부 — 끝의 제목뿐인 줄은 떼고(매달린 제목 방지), 본문이 있으면 내보낸다."""
    lines = text.split("\n")
    while lines and (not lines[-1].strip() or _is_heading_line(lines[-1])):
        lines.pop()
    out = "\n".join(lines).strip()
    if out and _has_body(out):
        chunks.append(Chunk(text=out, idx=len(chunks), heading_path=list(ancestors or [])))


def _chunk_by_sections(doc: ParsedDocument, max_chars: int, min_chars: int, always_split: bool = False) -> list[Chunk]:
    """섹션 기반 분할 — 섹션이 너무 크면 재분할, (always_split이 아니면) 작으면 병합.

    2026-10-07 재작성 — 실데이터 결함(컨플루언스 일괄 등록 51청크 중 20쌍이 인접 청크와 같은 줄 공유):
    예전엔 "짧은 섹션을 다음 청크 앞에 다시 붙이기"가 다음 섹션이 하위인지 보지 않아 형제·상위에도
    붙었고, always_split에선 그 짧은 섹션이 자기 청크로도 한 번 더 나가 같은 내용이 두 청크에 들어가고
    다음 상위 제목이 앞 청크 끝에 매달렸다. 이제 규칙(불변식 — tests/test_chunker.py TestChunkInvariants):
      1) 짧은 상위 도입부(다음 섹션이 그 하위)는 따로 내보내지 않고 하위 섹션 청크 앞에 **한 번만** 붙인다
         — 앞 청크에 섞지 않는다(앞 청크 끝에 다음 제목이 매달리지 않게).
      2) 본문 없는 제목뿐인 섹션은 하위가 없으면 버린다(내용 없음), 하위가 있으면 1)로 하위 앞에.
      3) 본문이 있는 섹션은 짧아도 버리지 않는다(chunk_document 최소 길이 필터가 본문 있는 청크는 남김).
      4) 조건/예외 연결 표현("단, …")으로 시작하면 앞 청크에 붙인다 — 단 max_chars 안에서만, 넘으면 앞
         섹션 제목을 달고 새 청크로. 앞 섹션이 커서 재분할됐으면(버퍼 없음) 자기 제목만 단 새 청크(기존 동작과 같음).
      5) max_chars를 넘는 섹션은 단락으로 다시 나누되, 나뉜 조각마다 자기 섹션 제목을 붙인다.
    heading_path = 청크를 시작한 섹션의 조상 제목들(자기 제외).
    """
    sections = doc.sections
    chunks: list[Chunk] = []
    buffer_text = ""
    buffer_title = ""
    buffer_heading_path: list[str] = []
    # 1)의 대기 중인 도입부 — (텍스트, 그 도입부를 시작한 섹션의 조상)
    carry_text = ""
    carry_ancestors: Optional[list[str]] = None
    heading_stack: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal buffer_text, buffer_title, buffer_heading_path
        if buffer_text.strip():
            chunks.append(Chunk(text=buffer_text.strip(), idx=len(chunks), section_title=buffer_title,
                                heading_path=list(buffer_heading_path)))
        buffer_text, buffer_title, buffer_heading_path = "", "", []

    for i, sec in enumerate(sections):
        level = sec.get("level") or 0
        title = sec.get("title", "") or ""
        if level > 0:
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
        ancestors = [t for _, t in heading_stack]
        next_level = (sections[i + 1].get("level") or 0) if i + 1 < len(sections) else None

        raw_content = _strip_macro_artifacts(sec.get("content") or "").strip()
        section_text = f"## {title}\n{raw_content}".strip() if title else raw_content
        has_child = level > 0 and next_level is not None and next_level > level

        def push_heading() -> None:
            if level > 0 and title:
                heading_stack.append((level, title))

        # 1)·2) 짧은 상위 도입부 / 제목뿐인 상위 → 하위 앞에 붙이려고 대기
        if has_child and len(raw_content) < SHORT_INTRO_CHARS:
            flush()
            carry_text = f"{carry_text}\n{section_text}" if carry_text else section_text
            if carry_ancestors is None:
                carry_ancestors = ancestors
            push_heading()
            continue
        # 2) 하위도 본문도 없는 제목 — 버린다(내용 없음)
        if not raw_content:
            if carry_text:   # 대기 중인 도입부는 버리지 않는다(이 제목은 그 하위가 아님 — 따로 내보냄)
                _emit_carry(chunks, carry_text, carry_ancestors)
                carry_text, carry_ancestors = "", None
            push_heading()
            continue

        if carry_text:
            # 대기 중인 도입부는 이 섹션이 하위일 때만 앞에 붙는다(has_child로 대기시켰으니 바로 다음은 하위)
            # 한 단락으로 이어 붙인다 — 큰 섹션이 단락으로 나뉠 때 도입부만 따로 떨어져 앞 조각 끝에 제목이 매달리지 않게
            section_text = f"{carry_text}\n{section_text}"
            # 상위 맥락은 이 하위 섹션 기준(붙인 도입부 제목 포함) — 큰 섹션이 나뉘어 도입부가 첫 조각에만 있어도 다음 조각이 맥락을 잃지 않게
            start_path = ancestors
            carry_text, carry_ancestors = "", None
            flush()
            starts_new = True
        else:
            start_path = ancestors
            starts_new = False

        if not starts_new and buffer_text:
            fits = len(buffer_text) + len(section_text) + 2 <= max_chars
            is_continuation = bool(_CONTINUATION_RE.match(raw_content))
            # 4) 연결 표현 / 병합 모드(always_split 아님). 짧은 본문은 따로 둬도 최소 길이 필터가 지킨다(본문 있으면 유지)
            if fits and (is_continuation or not always_split):
                buffer_text += "\n\n" + section_text
                push_heading()
                continue
            if is_continuation and buffer_title:
                section_text = f"## {buffer_title}\n{section_text}"

        flush()
        if len(section_text) > max_chars:
            # 5) 큰 섹션 재분할 — 조각마다 자기 제목
            # 조각마다 붙일 제목 머리말만큼 덜어서 나눈다 — 붙인 뒤에도 max_chars 이내(리뷰: 2005자·긴 제목이면 5004자)
            head = len(f"## {title}\n") if title else 0
            subs = _chunk_by_paragraphs(section_text, max(max_chars - head, max_chars // 2), min_chars)
            for k, sc in enumerate(subs):
                if k > 0 and title and not sc.text.startswith(f"## {title}"):
                    sc.text = f"## {title}\n{sc.text}"
                sc.section_title = title
                sc.heading_path = list(start_path)
                sc.idx = len(chunks)
                chunks.append(sc)
        else:
            buffer_text = section_text
            buffer_title = title
            buffer_heading_path = start_path
        push_heading()

    flush()
    if carry_text:
        _emit_carry(chunks, carry_text, carry_ancestors)
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

        if len(para) > max_chars:
            # 큰 단락은 버퍼 유무와 상관없이 고정 크기로 재분할(예전엔 버퍼가 있으면 큰 단락이 그대로 다음 버퍼가 돼
            # max를 넘는 청크가 나왔다 — 2026-10-07 감사)
            if buffer.strip():
                chunks.append(Chunk(text=buffer.strip(), idx=len(chunks)))
                buffer = ""
            chunks.extend(_chunk_by_lines(para, max_chars))
        elif buffer and len(buffer) + len(para) + 2 > max_chars:
            if buffer.strip():
                chunks.append(Chunk(text=buffer.strip(), idx=len(chunks)))
            buffer = para
        else:
            buffer = (buffer + "\n\n" + para) if buffer else para

    if buffer.strip():
        chunks.append(Chunk(text=buffer.strip(), idx=len(chunks)))

    return chunks


def _chunk_by_lines(text: str, max_chars: int) -> list[Chunk]:
    """빈 줄 없는 큰 단락 — 줄 경계에서 나눈다. 한 줄이 max보다 길 때만 그 줄을 고정 길이로(2026-10-07 리뷰: HTML 섹션 본문은
    빈 줄이 없어 통째로 고정 길이 분할로 가서 문장 중간에서 잘리고 다음 청크가 앞 줄 일부를 다시 담았다)."""
    chunks: list[Chunk] = []
    buf: list[str] = []
    size = 0
    for line in text.split("\n"):
        if len(line) > max_chars:
            if buf:
                chunks.append(Chunk(text="\n".join(buf).strip(), idx=len(chunks)))
                buf, size = [], 0
            chunks.extend(_chunk_fixed_size(line, max_chars, overlap_chars=50))
            continue
        if buf and size + len(line) + 1 > max_chars:
            chunks.append(Chunk(text="\n".join(buf).strip(), idx=len(chunks)))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf and "\n".join(buf).strip():
        chunks.append(Chunk(text="\n".join(buf).strip(), idx=len(chunks)))
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

        if end < len(text):
            # overlap 시작을 단어 경계로 — 단어 중간("0 word0211")에서 시작하지 않게(2026-10-07)
            nxt = end - overlap_chars
            ws = max(text.rfind(" ", start + 1, nxt + 1), text.rfind("\n", start + 1, nxt + 1))
            start = ws + 1 if ws > start else nxt
        else:
            start = end

    return chunks


def _table_already_in_text(table: dict, text: str) -> bool:
    headers = [h for h in table.get("headers", []) if h]
    if not headers or not text:
        return False
    return any("|" in ln and all(h in ln for h in headers) for ln in text.split("\n"))


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
