"""DB 스키마 사전 / 공통코드 마크다운 표 파서 — 결정론적, LLM 사용 안 함.

`docs/tech/data-storage-philosophy.md` §3 "포맷 결정 → 결정론 파서 → LLM은 의미만" 원칙 —
이 데이터는 애초에 의미 판단이 필요 없는 순수 구조화 표(코드값 조회용)라 LLM 자체가
불필요하다.

원본 파일("SR 테이블, 컬럼 - 비번제거.md", "SR 공통코드.md")은 2026-09-10 데이터 정리
때 rag_knowledge에서 마크다운 표가 일반 문서 청커로 잘려 헤더 없이 저장된 오염 데이터로
확인돼 soft-delete 후 영구 삭제됐다 — 업로드 시 원본 파일이 디스크에 저장되지 않고
메모리에서 청킹만 되는 구조라 복구 불가. **이 파서는 그 내용을 재현하는 게 아니라,
같은 형식의 데이터가 앞으로 다시 들어올 때 쓸 구조**다. 컬럼 순서는 삭제 전 관찰한
헤더 순서를 기준으로 한다 — 실제 재업로드 시 `parse_db_column_table()`/
`parse_common_code_table()` 결과를 반드시 사람이 몇 줄 훑어보고 컬럼이 제대로
갈라졌는지 확인할 것(실물 파일로 재검증된 적 없음).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_SEP_RE = re.compile(r"^-{2,}$")


def _strip_outer_pipe(line: str) -> str:
    """줄 맨 앞/뒤의 `|` 딱 하나씩만 제거한다. `str.strip('|')`을 쓰면 앞쪽이 빈 셀이 연달아
    있는 행(예: 병합셀 스타일로 `||||05|이름|`)에서 앞쪽 빈 셀들까지 통째로 사라져 컬럼
    위치가 밀리는 버그가 생긴다(실측으로 확인, 2026-09-11) — 딱 한 글자씩만 벗겨낸다."""
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return line


def _parse_markdown_table_rows(text: str) -> list[list[str]]:
    """마크다운 파이프 테이블에서 헤더+구분선(`|---|---|` 행)을 찾아 건너뛰고 데이터 행만
    위치 기준 리스트로 반환한다. 헤더 이름이 표 안에서 중복되는 경우(예: "COMMENTS"가
    두 번 — 테이블 설명/컬럼 설명 각각)가 있어 이름이 아니라 컬럼 위치로 다룬다."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    sep_idx = None
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in _strip_outer_pipe(line).split("|")]
        if cells and all(_SEP_RE.fullmatch(c) for c in cells):
            sep_idx = i
            break
    if sep_idx is None:
        return []
    rows = []
    for line in lines[sep_idx + 1:]:
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in _strip_outer_pipe(line).split("|")]
        rows.append(cells)
    return rows


def _cell(cells: list[str], idx: int) -> str:
    return cells[idx].strip() if idx < len(cells) else ""


@dataclass
class ParsedDbColumn:
    table_name: str
    table_comment: str
    column_name: str
    column_comment: str
    data_type: str
    nullable: str


def parse_db_column_table(text: str) -> list[ParsedDbColumn]:
    """컬럼 순서 가정: TABLE_NAME | COMMENTS(테이블 설명) | COLUMN_NAME |
    COMMENTS(컬럼 설명) | DATA_TYPE_LENGTH | NULLABLE.

    forward-fill 없음 — 삭제 전 관찰한 원본은 매 행마다 TABLE_NAME이 반복돼 있었음
    (병합셀이 아니라 매 행이 완결된 레코드)."""
    results = []
    for cells in _parse_markdown_table_rows(text):
        table_name = _cell(cells, 0)
        if not table_name:
            continue
        results.append(ParsedDbColumn(
            table_name=table_name,
            table_comment=_cell(cells, 1),
            column_name=_cell(cells, 2),
            column_comment=_cell(cells, 3),
            data_type=_cell(cells, 4),
            nullable=_cell(cells, 5),
        ))
    return results


@dataclass
class ParsedCommonCode:
    work_code: str
    group_code: str
    group_code_name: str
    code_id: str
    code_name: str
    mgmt_values: dict[str, str]


def parse_common_code_table(text: str, mgmt_col_names: Optional[list[str]] = None) -> list[ParsedCommonCode]:
    """컬럼 순서 가정: WORK_CODE | GROUP_CODE | GROUP_CODE_NAME | CODE_ID | CODE_NAME |
    MNGMN_ITM01_VALUE | MNGMN_ITM02_VALUE | ... (관리항목 컬럼 개수는 가변 — §2 결정
    규칙대로 JSONB에 담음).

    forward-fill 적용: WORK_CODE/GROUP_CODE/GROUP_CODE_NAME은 같은 코드그룹의 여러
    코드값이 이어질 때 첫 행에만 값이 있고 나머지는 빈 칸인 패턴(엑셀 병합셀을 마크다운
    으로 내보낼 때 흔함, `excel_parser.py`의 category_path forward-fill과 동일 이유)이
    관찰돼 마지막 non-empty 값을 이어받는다."""
    mgmt_col_names = mgmt_col_names or []
    results = []
    last_work = last_group = last_group_name = ""
    for cells in _parse_markdown_table_rows(text):
        code_id = _cell(cells, 3)
        if not code_id:
            continue
        work = _cell(cells, 0) or last_work
        group = _cell(cells, 1) or last_group
        group_name = _cell(cells, 2) or last_group_name
        last_work, last_group, last_group_name = work, group, group_name

        mgmt_values: dict[str, str] = {}
        for i, name in enumerate(mgmt_col_names):
            val = _cell(cells, 5 + i)
            if val:
                mgmt_values[name] = val

        results.append(ParsedCommonCode(
            work_code=work, group_code=group, group_code_name=group_name,
            code_id=code_id, code_name=_cell(cells, 4), mgmt_values=mgmt_values,
        ))
    return results
