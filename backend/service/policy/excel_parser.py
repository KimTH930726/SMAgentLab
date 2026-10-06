"""정책서 엑셀 파서 — 시트 판별 + 헤더 퍼지매핑 + 동적 깊이 감지.

docs/policy-doc-pipeline-plan.md §1 실측 근거: 팀마다 분류 컬럼 깊이가 다르다(대분류/중분류/
소분류 3단 vs 정책항목/세부항목 2단, 심지어 같은 팀 안에서도 시트마다 다름 — 딜리버스 파일의
"장바구니" 시트와 "배민정산" 시트가 서로 다른 헤더 구조였음). 그래서 카테고리 컬럼을 고정된
필드명으로 매핑하지 않고, "정책명 컬럼 앞에 있는 컬럼 전부"를 순서대로 category_path 배열에
담는 방식으로 처리한다 — text2sql 엑셀 임포터(archive/with-text2sql 브랜치)의 헤더 퍼지매핑
패턴(_HEADER_CANDIDATES + _normalize)을 그대로 재사용.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Optional

import openpyxl

# ── 헤더 퍼지매핑 — 실측 2개 팀 샘플 기준(§1), 새 팀에서 다른 표현이 나오면 후보 추가 ──

_POLICY_NAME_CANDIDATES = ["정책명", "기능명", "policy_name", "policy name"]
_BODY_CANDIDATES = ["조건/상세", "조건상세", "조건 및 상세", "조건·상세", "상세", "조건", "정책 상세", "내용", "정책 내용"]
_REMARK_CANDIDATES = ["비고", "비고 (예시)", "비고(예시)", "참고", "note", "remark", "remarks"]
_NO_CANDIDATES = ["no", "no.", "순번", "번호"]

_GLOSSARY_TERM_CANDIDATES = ["용어명", "용어", "term"]
_GLOSSARY_DEF_CANDIDATES = ["용어 정의", "용어정의", "정의", "definition"]


def _normalize(s) -> str:
    return str(s or "").strip().lower().replace(" ", "").replace("·", "").replace("/", "")


def _find_col(headers: list[str], candidates: list[str]) -> Optional[int]:
    """헤더 목록에서 후보 중 하나와 일치하는 첫 컬럼 인덱스. 못 찾으면 None."""
    normalized_candidates = {_normalize(c) for c in candidates}
    for i, h in enumerate(headers):
        if _normalize(h) in normalized_candidates:
            return i
    return None


@dataclass
class ParsedPolicyRow:
    category_path: list[str]
    policy_name: str
    raw_body: str
    remark: Optional[str]
    source_row: int  # 1-based, 헤더 포함 실제 엑셀 행 번호


@dataclass
class ParsedGlossaryRow:
    term: str
    description: str
    remark: Optional[str]
    source_row: int


@dataclass
class ParsedSheet:
    sheet_name: str
    kind: str  # "glossary" | "policy" | "unknown"
    policy_rows: list[ParsedPolicyRow] = field(default_factory=list)
    glossary_rows: list[ParsedGlossaryRow] = field(default_factory=list)
    skip_reason: Optional[str] = None  # kind="unknown"일 때 왜 못 읽었는지
    # 어느 엑셀 칸을 어디로 읽었는지(사용자에게 그대로 보여 줌) — 팀마다 헤더 이름이 달라 조용히 빠지는 칸을 드러내려고
    columns: Optional[dict] = None
    warnings: list[str] = field(default_factory=list)


def add_header_alias(field_name: str, header: str) -> None:
    """새 팀 템플릿의 헤더 이름을 그 실행에서만 후보에 추가(변환기 --map) — 코드 수정 없이 변환.
    field_name: 정책명 | 본문 | 비고 | 용어 | 정의"""
    target = {"정책명": _POLICY_NAME_CANDIDATES, "본문": _BODY_CANDIDATES, "비고": _REMARK_CANDIDATES,
              "용어": _GLOSSARY_TERM_CANDIDATES, "정의": _GLOSSARY_DEF_CANDIDATES}.get(field_name)
    if target is None:
        raise ValueError(f"--map은 정책명·본문·비고·용어·정의 중 하나만 지정할 수 있습니다(받은 값: {field_name})")
    target.insert(0, header)


def _cell_to_text(v) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _detect_header_row(rows: list[tuple]) -> Optional[int]:
    """정책/용어집 헤더가 있는 행의 인덱스(0-based)를 찾는다.

    실측 샘플이 파일 맨 위 몇 행이 비어있거나 제목행인 경우가 있어(§1), "정책명" 또는
    "용어명" 계열 헤더가 나오는 첫 행을 찾는다. 못 찾으면 첫 non-empty 행을 헤더로 가정.
    """
    all_candidates = (
        _POLICY_NAME_CANDIDATES + _GLOSSARY_TERM_CANDIDATES
        + ["대분류", "정책", "정책항목", "정책 항목"]
    )
    normalized_candidates = {_normalize(c) for c in all_candidates}
    for i, row in enumerate(rows):
        texts = [_normalize(c) for c in row]
        if any(t in normalized_candidates for t in texts):
            return i
    for i, row in enumerate(rows):
        if any(_cell_to_text(c) for c in row):
            return i
    return None


def _classify_sheet(headers: list[str]) -> str:
    if _find_col(headers, _GLOSSARY_TERM_CANDIDATES) is not None and _find_col(headers, _GLOSSARY_DEF_CANDIDATES) is not None:
        return "glossary"
    if _find_col(headers, _POLICY_NAME_CANDIDATES) is not None:
        return "policy"
    return "unknown"


def _policy_columns(headers: list[str]) -> dict:
    """정책 시트 칸 위치 — 파싱과 \"읽은 칸\" 표시가 같은 규칙을 쓰도록 한 곳에서(/code-review: 따로 계산하면 어긋남)."""
    name_col = _find_col(headers, _POLICY_NAME_CANDIDATES)
    body_col = _find_col(headers, _BODY_CANDIDATES)
    remark_col = _find_col(headers, _REMARK_CANDIDATES)
    no_col = _find_col(headers, _NO_CANDIDATES)
    exclude = {c for c in (no_col, name_col, body_col, remark_col) if c is not None}
    category_cols = [i for i in range(name_col) if i not in exclude] if name_col is not None else []
    return {"name": name_col, "body": body_col, "remark": remark_col, "category": category_cols}


def _parse_policy_sheet(headers: list[str], data_rows: list[tuple], header_row_idx: int) -> list[ParsedPolicyRow]:
    cols = _policy_columns(headers)
    name_col, body_col, remark_col = cols["name"], cols["body"], cols["remark"]
    if name_col is None:
        return []
    # 카테고리 컬럼 = No.와 정책명 사이의 나머지 컬럼 전부(§1: 팀마다 깊이가 달라 동적으로 결정)
    category_cols = cols["category"]

    # 병합셀 대응(forward-fill): openpyxl은 세로 병합 영역의 첫 셀에만 값을 주고 나머지는
    # None을 반환한다(read_only 모드 실측 확인, 2026-09-03). 대분류/중분류처럼 같은 값이
    # 여러 row에 걸쳐 병합되는 게 실제 정책서에서 흔한 패턴(실측 샘플의 "딜리버스_배민정산"
    # 시트 등)이라, 컬럼별로 마지막 non-empty 값을 기억해뒀다가 빈 칸을 채운다.
    last_seen: dict[int, str] = {}

    results = []
    for offset, row in enumerate(data_rows):
        policy_name = _cell_to_text(row[name_col]) if name_col < len(row) else ""
        if not policy_name:
            continue  # 빈 행 스킵(병합셀로 인한 공백 행 포함)
        category_path = []
        for i in category_cols:
            val = _cell_to_text(row[i]) if i < len(row) else ""
            if val:
                last_seen[i] = val
            else:
                val = last_seen.get(i, "")
            category_path.append(val)
        raw_body = _cell_to_text(row[body_col]) if body_col is not None and body_col < len(row) else ""
        remark = _cell_to_text(row[remark_col]) if remark_col is not None and remark_col < len(row) else None
        results.append(ParsedPolicyRow(
            category_path=category_path,
            policy_name=policy_name,
            raw_body=raw_body,
            remark=remark or None,
            source_row=header_row_idx + 2 + offset,  # 1-based 엑셀 행 번호(헤더 다음 행부터)
        ))
    return results


def _parse_glossary_sheet(headers: list[str], data_rows: list[tuple], header_row_idx: int) -> list[ParsedGlossaryRow]:
    term_col = _find_col(headers, _GLOSSARY_TERM_CANDIDATES)
    def_col = _find_col(headers, _GLOSSARY_DEF_CANDIDATES)
    remark_col = _find_col(headers, _REMARK_CANDIDATES)
    if term_col is None or def_col is None:
        return []

    results = []
    for offset, row in enumerate(data_rows):
        term = _cell_to_text(row[term_col]) if term_col < len(row) else ""
        description = _cell_to_text(row[def_col]) if def_col < len(row) else ""
        if not term or not description:
            continue
        remark = _cell_to_text(row[remark_col]) if remark_col is not None and remark_col < len(row) else None
        results.append(ParsedGlossaryRow(
            term=term, description=description, remark=remark or None,
            source_row=header_row_idx + 2 + offset,
        ))
    return results


def parse_sheet_rows(sheet_name: str, rows: list[tuple]) -> ParsedSheet:
    """시트 하나(셀 값 2차원 목록) → 파싱 결과. 엑셀 파일(openpyxl)·Excel 프로그램(COM, 문서 보안 암호화로 파일을 직접
    못 여는 PC — scripts/excel_to_policy_json.py) 양쪽이 같은 규칙(헤더 인식·병합 셀 채우기·행 번호)을 쓰도록 분리."""
    header_idx = _detect_header_row(rows)
    if header_idx is None:
        return ParsedSheet(sheet_name=sheet_name, kind="unknown", skip_reason="빈 시트")
    headers = [_cell_to_text(c) for c in rows[header_idx]]
    data_rows = rows[header_idx + 1:]
    kind = _classify_sheet(headers)
    if kind == "glossary":
        return ParsedSheet(sheet_name=sheet_name, kind="glossary",
                           glossary_rows=_parse_glossary_sheet(headers, data_rows, header_idx))
    if kind == "policy":
        sheet = ParsedSheet(sheet_name=sheet_name, kind="policy",
                            policy_rows=_parse_policy_sheet(headers, data_rows, header_idx))
        cols = _policy_columns(headers)
        body_col, remark_col = cols["body"], cols["remark"]
        sheet.columns = {
            "정책명": headers[cols["name"]],
            "분류": [headers[i] or f"{i + 1}번째 칸" for i in cols["category"]],
            "본문": headers[body_col] if body_col is not None else None,
            "비고": headers[remark_col] if remark_col is not None else None,
        }
        if body_col is None:
            sheet.warnings.append(
                "본문 칸(조건/상세·내용 등)을 찾지 못해 본문이 빈 채로 들어갑니다 — 본문 칸의 헤더 이름을 확인하거나, "
                "변환기에 --map 본문=\"헤더 이름\"으로 지정해 다시 변환하세요.")
        if not sheet.policy_rows:
            sheet.warnings.append("정책명이 채워진 행이 하나도 없습니다.")
        return sheet
    return ParsedSheet(sheet_name=sheet_name, kind="unknown",
                       skip_reason=("정책 시트로 보이지 않아 건너뜀 — 정책 시트라면 헤더에 '정책명'(또는 기능명) 칸이 있어야 합니다. "
                                    f"(이 시트의 헤더: {', '.join(h for h in headers[:8] if h) or '없음'})"))


def parse_workbook(file_bytes: bytes) -> list[ParsedSheet]:
    """엑셀 파일 바이트 → 시트별 파싱 결과. 시트 종류(용어집/정책)는 위치가 아니라
    헤더 내용으로 판별한다(§1: "맨 앞 시트=용어집"이 관례일 뿐 강제가 아닐 수 있어 방어적으로).
    사내 문서 보안(DRM)으로 암호화된 파일은 엑셀 형식이 아니라 열리지 않는다 — 그 경우 JSON으로 변환해 올리라고 안내."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as e:  # zipfile.BadZipFile / InvalidFileException 등 — 원인별로 할 일이 달라 머리 바이트로 구분(/code-review)
        if file_bytes[:4] == b"PK\x03\x04":
            raise ValueError("엑셀 파일이 손상돼 열 수 없습니다 — 엑셀에서 열어 .xlsx로 다시 저장해 올려 주세요.") from e
        if file_bytes[:8] == bytes.fromhex("D0CF11E0A1B11AE1"):
            raise ValueError(
                "옛 엑셀 형식(.xls)이거나 사내 문서 보안으로 암호화된 파일이라 열 수 없습니다 — .xls면 엑셀에서 .xlsx로 다시 "
                "저장해 올리고, 보안 문서면 변환기(scripts/excel_to_policy_json.py)로 JSON을 만들어 올려 주세요.") from e
        raise ValueError(
            "엑셀 파일이 아닙니다(또는 사내 문서 보안으로 암호화된 파일) — 정책서 엑셀(.xlsx)이나 변환기로 만든 JSON을 올려 주세요.") from e
    try:
        return [parse_sheet_rows(ws.title, list(ws.iter_rows(values_only=True))) for ws in wb.worksheets]
    finally:
        wb.close()
