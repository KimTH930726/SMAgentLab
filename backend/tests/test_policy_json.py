"""정책서 표준 JSON 입력 (2026-10-06, v2.124) — 사내 문서 보안 암호화로 서버가 엑셀을 못 여는 경우의 표준 경로.

엑셀 파서 출력과 1:1이라 같은 파이프라인을 타야 하고(왕복 동일), 오타·누락은 아무것도 적재하기 전에 위치와 함께 거부돼야
한다(식별키가 조용히 틀어지지 않게). source_name이 있으면 그걸 source_file로 써서 엑셀→JSON 전환이 "파일명 변경"으로
보이지 않아야 한다.
"""
import importlib.util as _ilu
import io
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import openpyxl
import pytest

_backend_dir = Path(__file__).resolve().parent.parent


def _real(name: str, rel_path: str):
    mod = sys.modules.get(name)
    if isinstance(mod, types.ModuleType) and getattr(mod, "__file__", None):
        return mod
    spec = _ilu.spec_from_file_location(name, str(_backend_dir / rel_path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


if not isinstance(sys.modules.get("service.policy"), MagicMock):
    sys.modules["service.policy"] = MagicMock()
_pkg = sys.modules["service.policy"]
excel_parser = _real("service.policy.excel_parser", "service/policy/excel_parser.py")
decompose = _real("service.policy.decompose", "service/policy/decompose.py")
json_format = _real("service.policy.json_format", "service/policy/json_format.py")
_pkg.excel_parser, _pkg.decompose, _pkg.json_format = excel_parser, decompose, json_format
service = _real("service.policy.service", "service/policy/service.py")
service.json_format = json_format   # 다른 테스트가 먼저 service를 로드했으면 패키지 MagicMock 속성을 잡고 있을 수 있음


def _doc(**over):
    d = {"schema": json_format.SCHEMA_V1, "source_name": "정책서.xlsx", "sheets": [
        {"name": "온라인", "kind": "policy", "rows": [
            {"row": 3, "category_path": ["주문", "취소"], "policy_name": "카드 환불", "body": "당일 승인취소", "remark": None}]},
        {"name": "용어집", "kind": "glossary", "rows": [{"row": 2, "term": "SR", "definition": "주문 시스템", "remark": None}]},
    ]}
    d.update(over)
    return json.dumps(d, ensure_ascii=False).encode()


def test_valid_json_parses_to_same_structure_as_excel():
    sheets, source, warnings = json_format.parse_policy_json(_doc())
    assert source == "정책서.xlsx" and warnings == []
    p = sheets[0].policy_rows[0]
    assert (p.category_path, p.policy_name, p.raw_body, p.remark, p.source_row) == (["주문", "취소"], "카드 환불", "당일 승인취소", None, 3)
    assert sheets[1].glossary_rows[0].description == "주문 시스템"


def test_round_trip_excel_to_json_equals_direct_excel_parse():
    """변환기(엑셀 → JSON) 결과를 다시 읽으면 엑셀을 직접 파싱한 것과 같아야 한다 — 병합셀 채우기·제목 행·줄바꿈 본문 포함."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "온라인"
    ws.append(["비즈니스 정책서"])
    ws.append(["No", "대분류", "중분류", "정책명", "조건/상세", "비고"])
    ws.append([1, "주문", "취소", "카드 환불", "당일\n익일 3~7일", None])
    ws.append([2, None, None, "부분 취소", "1회만", "예외"])
    buf = io.BytesIO()
    wb.save(buf)
    direct = excel_parser.parse_workbook(buf.getvalue())
    doc = json_format.to_policy_json(direct, source_name="x.xlsx")
    back, _, _ = json_format.parse_policy_json(json.dumps(doc, ensure_ascii=False).encode())
    assert [(r.category_path, r.policy_name, r.raw_body, r.remark, r.source_row) for r in back[0].policy_rows] == \
           [(r.category_path, r.policy_name, r.raw_body, r.remark, r.source_row) for r in direct[0].policy_rows]
    assert back[0].policy_rows[1].category_path == ["주문", "취소"]


@pytest.mark.parametrize("mutate, where, problem", [
    (lambda d: d.update(schema="v0"), "파일 전체", "변환기로 만든 파일이 아닙니다"),
    (lambda d: d["sheets"][0]["rows"][0].update(policy_name="  "), "'온라인' 시트 3행", "정책명이 비어 있습니다"),
    (lambda d: d["sheets"][0]["rows"].append(dict(d["sheets"][0]["rows"][0])), "'온라인' 시트 3행", "같은 행이 두 번"),
    (lambda d: d["sheets"][0].update(kind="table"), "'온라인' 시트", "시트 종류"),
    (lambda d: d["sheets"][0]["rows"][0].update(category_path="주문"), "'온라인' 시트 3행", "분류 경로"),
    (lambda d: d["sheets"].append(dict(d["sheets"][0])), "'온라인' 시트", "같은 이름의 시트"),
])
def test_invalid_json_rejected_with_excel_location_and_fix(mutate, where, problem):
    """사용자가 엑셀에서 바로 찾을 수 있는 위치(시트 이름 + 엑셀 행 번호)와 고치는 방법이 함께 나와야 한다."""
    d = json.loads(_doc())
    mutate(d)
    with pytest.raises(json_format.PolicyJsonError) as e:
        json_format.parse_policy_json(json.dumps(d, ensure_ascii=False).encode())
    p = e.value.problems[0]
    assert p["where"] == where and problem in p["problem"] and p["fix"]
    assert "반영되지 않았습니다" in e.value.detail()["message"]


def test_missing_key_suggests_likely_alias():
    """필수 키가 없고 비슷한 이름이 있으면 "혹시 이것?" 안내 — 다른 도구로 만든 JSON."""
    d = json.loads(_doc())
    row = d["sheets"][0]["rows"][0]
    row["name"] = row.pop("policy_name")
    with pytest.raises(json_format.PolicyJsonError) as e:
        json_format.parse_policy_json(json.dumps(d, ensure_ascii=False).encode())
    assert "'name' 항목이 있는데" in e.value.problems[0]["problem"]


def test_unknown_keys_are_warnings_not_errors():
    """모르는 키는 반영을 막지 않고 경고로만(필드가 늘어나거나 다른 도구로 만든 JSON에서 덜 깨지게)."""
    d = json.loads(_doc())
    d["exported_by"] = "x"
    d["sheets"][0]["rows"][0]["owner"] = "홍길동"
    sheets, _, warnings = json_format.parse_policy_json(json.dumps(d, ensure_ascii=False).encode())
    assert len(sheets[0].policy_rows) == 1
    assert any("exported_by" in w for w in warnings) and any("owner" in w for w in warnings)


def test_not_json_rejected():
    with pytest.raises(json_format.PolicyJsonError) as e:
        json_format.parse_policy_json(b"\x00\x01 not json")
    assert "암호화" in e.value.problems[0]["problem"]


def test_excel_reports_columns_and_warns_when_body_column_missing():
    """팀마다 헤더 이름이 다르다 — 본문 칸을 못 찾으면 조용히 빈 본문이 아니라 경고 + 읽은 칸 표시."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "딜리버스"
    ws.append(["No", "정책항목", "기능명", "설명 글"])
    ws.append([1, "배달", "배차 실패", "3회 실패 시 수동 배차"])
    buf = io.BytesIO()
    wb.save(buf)
    sh = excel_parser.parse_workbook(buf.getvalue())[0]
    assert sh.columns["정책명"] == "기능명" and sh.columns["본문"] is None and sh.columns["분류"] == ["정책항목"]
    assert any("본문 칸" in w and "--map" in w for w in sh.warnings)


def test_encrypted_excel_gives_json_guidance():
    """사내 문서 보안 암호화 파일(엑셀 형식 아님) — 서버 오류 대신 JSON 변환 안내."""
    with pytest.raises(ValueError) as e:
        excel_parser.parse_workbook(b"DRM-encrypted-bytes")
    assert "JSON" in str(e.value)


@pytest.mark.asyncio
async def test_import_json_uses_source_name_as_source_file(monkeypatch):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=False)
    conn.execute = AsyncMock(return_value="OK")

    class _Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False
    conn.transaction = MagicMock(side_effect=lambda: _Tx())
    inserted = []

    async def fetchrow(query, *args):
        if "INSERT INTO policy_item" in query:
            inserted.append(args)
            return {"id": 1, "logical_id": 1}
        return None
    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    monkeypatch.setattr(service, "get_conn", MagicMock(return_value=conn))
    monkeypatch.setattr(service, "resolve_namespace_id", AsyncMock(return_value=1))
    monkeypatch.setattr(service, "create_glossary", AsyncMock())
    monkeypatch.setattr(decompose, "decompose_policy_body",
                        AsyncMock(return_value=[decompose.Segment(type="narrative", text="당일 승인취소")]))
    monkeypatch.setattr(service, "embedding_service", MagicMock(embed_batch=AsyncMock(return_value=[[0.1, 0.2]])))
    monkeypatch.setitem(sys.modules, "shared.cache", MagicMock(invalidate_namespace=AsyncMock(return_value=0)))

    res = await service.import_excel("ns", "sys", "upload_20261006.json", _doc())
    # 올린 파일 이름이 원본 이름과 다르면 "사라진 정책" 판단은 건너뛰고 알린다(다른 엑셀 정책 오표시 방지)
    assert len(res.warnings) == 1 and "사라진 정책 확인은 건너뛰었습니다" in res.warnings[0]
    assert res.source_file == "정책서.xlsx"
    assert inserted[0][6] == "정책서.xlsx" and inserted[0][7] == "온라인" and inserted[0][8] == 3   # source_file·시트·행
    assert [s.kind for s in res.sheets] == ["policy", "glossary"] and res.sheets[1].glossary_added == 1


def test_all_empty_bodies_rejected_as_unread_body_column():
    """본문이 전부 비어 있으면 변환 때 본문 칸을 못 읽은 것 — 그대로 반영하면 모든 정책이 빈 본문 새 버전이 되므로 막는다."""
    d = json.loads(_doc())
    for r in d["sheets"][0]["rows"]:
        r["body"] = ""
    with pytest.raises(json_format.PolicyJsonError) as e:
        json_format.parse_policy_json(json.dumps(d, ensure_ascii=False).encode())
    assert "본문이 모두 비어 있습니다" in e.value.problems[0]["problem"] and "--map" in e.value.problems[0]["fix"]


def test_problem_count_says_more_only_when_truncated():
    d = json.loads(_doc())
    d["sheets"][0]["rows"] = [{"row": i, "category_path": ["a"], "policy_name": " ", "body": "b", "remark": None} for i in range(2, 22)]
    with pytest.raises(json_format.PolicyJsonError) as e:          # 정확히 20건 — "이상" 아님
        json_format.parse_policy_json(json.dumps(d, ensure_ascii=False).encode())
    assert "이상" not in str(e.value)
    d["sheets"][0]["rows"].append({"row": 99, "category_path": ["a"], "policy_name": " ", "body": "b", "remark": None})
    with pytest.raises(json_format.PolicyJsonError) as e:
        json_format.parse_policy_json(json.dumps(d, ensure_ascii=False).encode())
    assert "이상(처음 20건만 표시)" in str(e.value)


@pytest.mark.parametrize("head, expect", [
    (b"PK\x03\x04broken", "손상"),
    (bytes.fromhex("D0CF11E0A1B11AE1") + b"x", ".xls"),
    (b"plain text", "엑셀 파일이 아닙니다"),
])
def test_excel_open_failure_says_what_to_do(head, expect):
    """엑셀을 못 열 때 원인별로 할 일이 다르다 — 손상 / 옛 .xls·보안 암호화 / 엑셀 아님."""
    with pytest.raises(ValueError) as e:
        excel_parser.parse_workbook(head)
    assert expect in str(e.value)
