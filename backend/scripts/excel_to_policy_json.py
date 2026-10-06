"""정책서 엑셀 → 표준 JSON 변환기 (2026-10-06, v2.124) — 정책 담당자 PC에서 실행.

사내 문서 보안(DRM)으로 암호화된 엑셀은 서버가 열 수 없어 이 JSON으로 올린다(관리자 › 정책 › 정책서 올리기). 파싱 규칙
(헤더 인식·병합 셀 채우기·행 번호)은 서버와 같은 `service/policy/excel_parser.parse_sheet_rows`를 그대로 쓰므로, 같은 엑셀을
서버에 직접 올렸을 때와 결과가 같다. 형식은 `docs/tech/policy-json-format.md`.

읽는 방법(자동 선택):
  1) openpyxl로 파일을 직접 읽는다.
  2) 실패하면(암호화 파일 등) Windows의 Excel 프로그램으로 열어 셀 값을 읽는다(pywin32 필요: pip install pywin32).
     Excel이 보안 문서를 열 수 있는 PC에서만 된다. --via-excel로 처음부터 이 방식을 쓸 수도 있다.

사용:
    python scripts/excel_to_policy_json.py 비즈니스정책서_온라인스토어.xlsx            # → 같은 이름 .json
    python scripts/excel_to_policy_json.py 정책서.xlsx -o out.json --via-excel
    python scripts/excel_to_policy_json.py 정책서.xlsx --map 본문="정책 내용"          # 헤더 이름이 다른 팀 템플릿

실행하면 시트마다 "어느 엑셀 칸을 어디로 읽었는지"를 보여 준다 — 본문 칸을 못 찾으면 경고가 나오니 --map으로 지정해 다시 돌린다.

만든 JSON이 다시 암호화되는지(회사 DRM 정책)는 처음 한 번 확인할 것 — 암호화되면 서버가 JSON도 못 읽는다.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    """서버 코드와 같은 파서를 쓰되, 이 스크립트는 서버 의존성(DB 등) 없이 돌아야 해서 파일 경로로 직접 로드한다."""
    spec = importlib.util.spec_from_file_location(name, _BACKEND / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


excel_parser = _load("service.policy.excel_parser", "service/policy/excel_parser.py")
json_format = _load("service.policy.json_format", "service/policy/json_format.py")


def read_with_openpyxl(path: Path):
    return excel_parser.parse_workbook(path.read_bytes())


def _norm_com(v):
    """Excel(COM) 값 → openpyxl과 같은 형태(/code-review). COM은 숫자를 전부 실수로(10 → 10.0), 날짜를 시간대 붙은
    pywintypes 날짜로 준다 — 그대로 글자로 바꾸면 "10.0"이 돼 직접 올린 엑셀과 식별키·내용이 달라지고, 같은 정책이 새 버전·
    "사라짐"으로 잘못 처리된다."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if hasattr(v, "tzinfo") and getattr(v, "tzinfo", None) is not None and hasattr(v, "replace"):
        import datetime as _dt
        return _dt.datetime(v.year, v.month, v.day, v.hour, v.minute, v.second)
    return v


def read_with_excel(path: Path):
    """Excel 프로그램(COM)으로 열어 시트별 셀 값을 읽는다 — 보안 문서는 Excel이 복호화해 연다."""
    try:
        import win32com.client  # type: ignore
    except ImportError as e:
        raise SystemExit("Excel로 읽으려면 pywin32가 필요합니다: pip install pywin32") from e
    app = win32com.client.DispatchEx("Excel.Application")
    app.Visible = False
    app.DisplayAlerts = False
    wb = None
    try:
        wb = app.Workbooks.Open(str(path.resolve()), ReadOnly=True)
        sheets = []
        for ws in wb.Worksheets:
            used = ws.UsedRange
            # UsedRange가 A1에서 시작하지 않으면 앞쪽 빈 행·열을 채워 원본 행 번호가 서버(openpyxl)와 같게 맞춘다
            top, left = used.Row, used.Column
            values = used.Value
            if values is None:
                rows = []
            elif not isinstance(values, tuple):
                rows = [(values,)]
            else:
                rows = [tuple(r) for r in values]
            rows = [tuple(_norm_com(v) for v in r) for r in rows]
            rows = [tuple([None] * (left - 1)) + r for r in rows]
            rows = [()] * (top - 1) + rows
            sheets.append(excel_parser.parse_sheet_rows(ws.Name, rows))
        return sheets
    finally:
        if wb is not None:
            wb.Close(SaveChanges=False)
        app.Quit()


def main() -> int:
    ap = argparse.ArgumentParser(description="정책서 엑셀 → 표준 JSON")
    ap.add_argument("xlsx")
    ap.add_argument("-o", "--output")
    ap.add_argument("--via-excel", action="store_true", help="처음부터 Excel 프로그램으로 읽기(보안 문서)")
    ap.add_argument("--map", action="append", default=[], metavar='항목="헤더 이름"',
                    help='헤더 이름이 다른 칸 지정(정책명·본문·비고·용어·정의). 예: --map 본문="정책 내용"')
    args = ap.parse_args()
    for m in args.map:
        field_name, _, header = m.partition("=")
        if not header:
            raise SystemExit(f'--map 형식: 항목="헤더 이름" (받은 값: {m})')
        excel_parser.add_header_alias(field_name.strip(), header.strip().strip('"'))
    src = Path(args.xlsx)
    out = Path(args.output) if args.output else src.with_suffix(".json")

    if args.via_excel:
        sheets = read_with_excel(src)
    else:
        try:
            sheets = read_with_openpyxl(src)
        except ValueError:
            if os.name != "nt":
                raise
            print("파일을 직접 열 수 없어(보안 문서일 수 있음) Excel 프로그램으로 읽습니다...", file=sys.stderr)
            sheets = read_with_excel(src)

    doc = json_format.to_policy_json(sheets, source_name=src.name)
    try:
        json_format.parse_policy_json(json.dumps(doc, ensure_ascii=False).encode("utf-8"))  # 서버와 같은 검증을 미리
    except json_format.PolicyJsonError as e:
        print("변환은 했지만 서버가 받지 않을 내용이 있어 저장하지 않았습니다:")
        for p in e.problems:
            print(f"  - {p['where']}: {p['problem']} → {p['fix']}")
        return 1

    label = {"policy": "정책", "glossary": "용어집", "unknown": "건너뜀"}
    for sh in sheets:
        n = len(sh.policy_rows) if sh.kind == "policy" else len(sh.glossary_rows)
        print(f"  [{label.get(sh.kind, sh.kind)}] {sh.sheet_name}: {n}행" + (f" — {sh.skip_reason}" if sh.kind == "unknown" else ""))
        if sh.columns:
            c = sh.columns
            print(f"      읽은 칸: 정책명←'{c['정책명']}', 분류←{c['분류'] or '(없음)'}, "
                  f"본문←{repr(c['본문']) if c['본문'] else '(못 찾음)'}, 비고←{repr(c['비고']) if c['비고'] else '(없음)'}")
        for w in sh.warnings:
            print(f"      ⚠ {w}")
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
