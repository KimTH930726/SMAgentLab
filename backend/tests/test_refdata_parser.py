"""Tests for service/refdata/parser.py — DB 스키마 사전/공통코드 마크다운 표 결정론 파서.

여기 쓰인 예시 데이터는 전부 합성(synthetic) 데이터다 — 실제 삭제된 원본 파일을 재현한
게 아니라, 관찰했던 컬럼 구조(TABLE_NAME/COMMENTS/COLUMN_NAME/COMMENTS/DATA_TYPE_LENGTH/
NULLABLE, WORK_CODE/GROUP_CODE/GROUP_CODE_NAME/CODE_ID/CODE_NAME/MNGMN_ITM*_VALUE)만
반영한 것. 실물 파일로 재검증 전까지는 이 구조 가정이 맞는지 확정 불가.

파일 경로 기반 spec 로딩을 쓰는 이유: 다른 테스트 모듈이 `sys.modules["service"]`를
MagicMock으로 치환해둬서(conftest.py) 같은 pytest 세션에서 일반 `from service...import`가
깨진다 — parser.py 자체는 외부 의존성 없는 순수 함수라 이렇게 격리해서 로드해도 무방
(test_policy_korean_text.py와 동일 패턴)."""
import importlib.util as _ilu
import sys
from pathlib import Path

_spec = _ilu.spec_from_file_location(
    "service.refdata.parser", str(Path(__file__).resolve().parent.parent / "service" / "refdata" / "parser.py")
)
_parser_mod = _ilu.module_from_spec(_spec)
sys.modules["service.refdata.parser"] = _parser_mod
_spec.loader.exec_module(_parser_mod)
parse_common_code_table = _parser_mod.parse_common_code_table
parse_db_column_table = _parser_mod.parse_db_column_table


class TestParseDbColumnTable:
    def test_parses_basic_rows(self):
        text = """
|TABLE_NAME|COMMENTS|COLUMN_NAME|COMMENTS|DATA_TYPE_LENGTH|NULLABLE|
|---|---|---|---|---|---|
|MSR_ACCOUNT_AUTH_HISTORY|계좌인증이력|USER_NUMBER|사용자번호|VARCHAR2(10)|Y|
|MSR_ACCOUNT_AUTH_HISTORY|계좌인증이력|FORMULA_CODE|요청채널|VARCHAR2(1)|Y|
"""
        rows = parse_db_column_table(text)
        assert len(rows) == 2
        assert rows[0].table_name == "MSR_ACCOUNT_AUTH_HISTORY"
        assert rows[0].table_comment == "계좌인증이력"
        assert rows[0].column_name == "USER_NUMBER"
        assert rows[0].column_comment == "사용자번호"
        assert rows[0].data_type == "VARCHAR2(10)"
        assert rows[0].nullable == "Y"
        assert rows[1].column_name == "FORMULA_CODE"

    def test_skips_rows_without_table_name(self):
        text = """
|TABLE_NAME|COMMENTS|COLUMN_NAME|COMMENTS|DATA_TYPE_LENGTH|NULLABLE|
|---|---|---|---|---|---|
||||||
|T1|설명|COL1|컬럼설명|VARCHAR2(5)|N|
"""
        rows = parse_db_column_table(text)
        assert len(rows) == 1
        assert rows[0].table_name == "T1"

    def test_no_separator_line_returns_empty(self):
        assert parse_db_column_table("그냥 산문 텍스트\n표가 아님") == []

    def test_missing_trailing_cells_treated_as_empty(self):
        text = """
|TABLE_NAME|COMMENTS|COLUMN_NAME|COMMENTS|DATA_TYPE_LENGTH|NULLABLE|
|---|---|---|---|---|---|
|T1|설명|COL1|
"""
        rows = parse_db_column_table(text)
        assert len(rows) == 1
        assert rows[0].column_name == "COL1"
        assert rows[0].data_type == ""
        assert rows[0].nullable == ""


class TestParseCommonCodeTable:
    def test_parses_basic_rows(self):
        text = """
|WORK_CODE|GROUP_CODE|GROUP_CODE_NAME|CODE_ID|CODE_NAME|
|---|---|---|---|---|
|COM|C0002|서비스코드|02|모바일|
|COM|C0002|서비스코드|05|홀케이크|
"""
        rows = parse_common_code_table(text)
        assert len(rows) == 2
        assert rows[0].work_code == "COM"
        assert rows[0].group_code == "C0002"
        assert rows[0].group_code_name == "서비스코드"
        assert rows[0].code_id == "02"
        assert rows[0].code_name == "모바일"

    def test_forward_fills_merged_cell_style_blanks(self):
        """엑셀 병합셀을 마크다운으로 내보내면 같은 그룹의 후속 행이 WORK_CODE/GROUP_CODE/
        GROUP_CODE_NAME 칸이 비어있는 경우가 있다 — 직전 값을 이어받아야 함."""
        text = """
|WORK_CODE|GROUP_CODE|GROUP_CODE_NAME|CODE_ID|CODE_NAME|
|---|---|---|---|---|
|COM|C0002|서비스코드|02|모바일|
||||05|홀케이크|
"""
        rows = parse_common_code_table(text)
        assert len(rows) == 2
        assert rows[1].work_code == "COM"
        assert rows[1].group_code == "C0002"
        assert rows[1].group_code_name == "서비스코드"
        assert rows[1].code_id == "05"

    def test_mgmt_values_collected_into_dict(self):
        text = """
|WORK_CODE|GROUP_CODE|GROUP_CODE_NAME|CODE_ID|CODE_NAME|MNGMN_ITM01_VALUE|MNGMN_ITM02_VALUE|
|---|---|---|---|---|---|---|
|COM|C0003|결제수단|01|카드|Y|국내전용|
"""
        rows = parse_common_code_table(text, mgmt_col_names=["MNGMN_ITM01_VALUE", "MNGMN_ITM02_VALUE"])
        assert len(rows) == 1
        assert rows[0].mgmt_values == {"MNGMN_ITM01_VALUE": "Y", "MNGMN_ITM02_VALUE": "국내전용"}

    def test_empty_mgmt_values_excluded_from_dict(self):
        text = """
|WORK_CODE|GROUP_CODE|GROUP_CODE_NAME|CODE_ID|CODE_NAME|MNGMN_ITM01_VALUE|MNGMN_ITM02_VALUE|
|---|---|---|---|---|---|---|
|COM|C0003|결제수단|01|카드|Y||
"""
        rows = parse_common_code_table(text, mgmt_col_names=["MNGMN_ITM01_VALUE", "MNGMN_ITM02_VALUE"])
        assert rows[0].mgmt_values == {"MNGMN_ITM01_VALUE": "Y"}

    def test_skips_rows_without_code_id(self):
        text = """
|WORK_CODE|GROUP_CODE|GROUP_CODE_NAME|CODE_ID|CODE_NAME|
|---|---|---|---|---|
|COM|C0002|서비스코드||이름만있음|
|COM|C0002|서비스코드|02|모바일|
"""
        rows = parse_common_code_table(text)
        assert len(rows) == 1
        assert rows[0].code_id == "02"
