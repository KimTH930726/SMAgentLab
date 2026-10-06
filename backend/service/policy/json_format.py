"""정책서 JSON 형식 — 엑셀 대신 올리는 표준 입력 (2026-10-06, v2.124).

왜: 사내 문서 보안(DRM)으로 암호화된 엑셀은 서버가 열 수 없다. 사람이 쓰는 원본은 엑셀 그대로 두고, 시스템에 넣는 건
이 JSON으로 통일한다 — 엑셀 파서의 출력(`excel_parser.ParsedSheet`)과 1:1이라 분해·버전 관리(식별키 매칭) 파이프라인을
그대로 탄다. MD는 표 병합·줄바꿈 본문이 깨지고 결국 지식 문서 경로로 가 정책 구조(파라미터·식별키)를 잃어서 택하지 않았다.

형식(`docs/tech/policy-json-format.md`가 원본):
    {"schema": "opslens/policy/v1",
     "sheets": [{"name": "온라인스토어", "kind": "policy",
                 "rows": [{"row": 2, "category_path": ["주문", "취소"], "policy_name": "...", "body": "...", "remark": null}]},
                {"name": "용어집", "kind": "glossary",
                 "rows": [{"row": 2, "term": "...", "definition": "...", "remark": null}]}]}

검증은 엄격하게(모르는 필드도 오류) — 오타("policy_nmae")가 조용히 빈 값으로 들어가 식별키가 틀어지는 걸 막는다.
나중에 필드(정책 ID·적용 조건 등)가 생기면 schema 버전을 올려 늘린다. 쓰지 않는 필드를 미리 만들지 않는다.
"""
from __future__ import annotations

import json

from service.policy.excel_parser import ParsedGlossaryRow, ParsedPolicyRow, ParsedSheet

SCHEMA_V1 = "opslens/policy/v1"
_MAX_ERRORS = 20
_POLICY_FIELDS = {"row", "category_path", "policy_name", "body", "remark"}
_GLOSSARY_FIELDS = {"row", "term", "definition", "remark"}
# 필수 키가 없을 때 "혹시 이것?" 힌트 — 다른 도구로 만든 JSON에서 흔한 이름(별칭으로 받아 주지는 않음: 실사례 생길 때)
_KEY_HINTS = {
    "policy_name": ("정책명", "name", "policy", "title", "기능명"),
    "category_path": ("분류", "category", "categories", "path"),
    "body": ("본문", "content", "detail", "details", "조건/상세", "내용"),
    "term": ("용어", "용어명", "name"),
    "definition": ("정의", "용어 정의", "description", "desc"),
}
_LABEL = {"policy_name": "정책명", "category_path": "분류 경로", "body": "본문", "term": "용어", "definition": "용어 정의"}
_REMAKE = "변환기(excel_to_policy_json.py)로 다시 만들어 올려 주세요"


def _problem(where: str, problem: str, fix: str) -> dict:
    return {"where": where, "problem": problem, "fix": fix}


class PolicyJsonError(ValueError):
    """검증 실패 — 사용자가 바로 고칠 수 있게 "어디(시트·엑셀 행) / 무엇이 문제 / 어떻게 고치나" 목록(최대 20개)."""

    def __init__(self, problems: list[dict], more: bool = False):
        self.problems = problems
        head = f"올린 파일을 반영하지 않았습니다 — 문제 {len(problems)}건" + (" 이상(처음 20건만 표시)" if more else "")
        super().__init__(head + ": " + " / ".join(f"{p['where']}: {p['problem']}" for p in problems[:5]))

    def detail(self) -> dict:
        return {"message": str(self).split(":")[0] + ". 아래를 고친 뒤 다시 올려 주세요(아무것도 반영되지 않았습니다).",
                "problems": self.problems}


def _is_text(v) -> bool:
    return isinstance(v, str)


def _missing_key_hint(r: dict, key: str) -> str:
    near = [k for k in r if k in _KEY_HINTS.get(key, ())]
    return f" 대신 '{near[0]}' 항목이 있는데, 이것이라면 키 이름을 {key}로 바꿔 주세요." if near else ""


def parse_policy_json(data: bytes) -> tuple[list[ParsedSheet], str | None, list[str]]:
    """JSON 바이트 → (시트 목록, 원본 파일명 source_name, 경고 목록).

    필수값이 빠지거나 형식이 깨지면 아무것도 적재하지 않도록 전부 모아 PolicyJsonError(일부만 들어가면 빠진 정책이 "원본에서
    사라짐"으로 잘못 표시된다). 모르는 키는 오류가 아니라 경고 — 무시하고 진행하되 결과 화면에 알린다. 위치는 배열 순번이
    아니라 사용자가 엑셀에서 찾을 수 있는 "시트 이름 + 엑셀 행 번호"로.
    source_name(변환기가 원본 엑셀 파일명을 넣음)이 있으면 임포트가 그걸 source_file로 쓴다 — 엑셀로 올리던 정책을 JSON으로
    바꿔 올려도 "파일명 변경"으로 보이지 않고, 골든셋의 파일명 기반 파트 매핑도 그대로 맞는다."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PolicyJsonError([_problem(
            "파일 전체", "JSON 파일로 읽을 수 없습니다(손상됐거나 사내 문서 보안으로 다시 암호화됐을 수 있음).",
            _REMAKE + ". 변환한 파일도 열리지 않으면 보안 담당자에게 JSON 파일 암호화 예외를 요청해야 합니다.")]) from e

    problems: list[dict] = []
    warnings: list[str] = []
    overflow = [False]

    def bad(where: str, problem: str, fix: str = _REMAKE) -> None:
        if len(problems) < _MAX_ERRORS:
            problems.append(_problem(where, problem, fix))
        else:
            overflow[0] = True

    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_V1:
        raise PolicyJsonError([_problem(
            "파일 전체", "정책서 변환기로 만든 파일이 아닙니다(형식 표시 schema가 없거나 다름).", _REMAKE + ".")])
    extra = set(doc) - {"schema", "sheets", "source_name"}
    if extra:
        warnings.append(f"알 수 없는 항목 {sorted(extra)}은(는) 무시했습니다.")
    source_name = doc.get("source_name")
    if source_name is not None and (not _is_text(source_name) or not source_name.strip()):
        warnings.append("원본 파일명(source_name)이 비어 있어 올린 파일 이름을 출처로 썼습니다.")
        source_name = None
    sheets_in = doc.get("sheets")
    if not isinstance(sheets_in, list) or not sheets_in:
        raise PolicyJsonError([_problem("파일 전체", "시트가 하나도 없습니다.", "정책 시트가 있는 엑셀로 " + _REMAKE + ".")])

    out: list[ParsedSheet] = []
    names: set[str] = set()
    for si, sh in enumerate(sheets_in):
        if not isinstance(sh, dict):
            bad(f"{si + 1}번째 시트", "형식이 깨졌습니다.")
            continue
        name, kind, rows = sh.get("name"), sh.get("kind"), sh.get("rows")
        if not _is_text(name) or not name.strip():
            bad(f"{si + 1}번째 시트", "시트 이름이 비어 있습니다.")
            continue
        if name in names:
            bad(f"'{name}' 시트", "같은 이름의 시트가 두 번 있습니다.", "엑셀에서 시트 이름을 서로 다르게 바꾼 뒤 " + _REMAKE + ".")
        names.add(name)
        where = f"'{name}' 시트"
        if set(sh) - {"name", "kind", "rows"}:
            warnings.append(f"{where}: 알 수 없는 항목 {sorted(set(sh) - {'name', 'kind', 'rows'})}은(는) 무시했습니다.")
        if kind not in ("policy", "glossary"):
            bad(where, f"시트 종류가 정책(policy)·용어집(glossary)이 아닙니다(현재: {kind!r}).")
            continue
        if not isinstance(rows, list):
            bad(where, "행 목록이 깨졌습니다.")
            continue
        sheet = ParsedSheet(sheet_name=name, kind=kind)
        seen_rows: set[int] = set()
        allowed = _POLICY_FIELDS if kind == "policy" else _GLOSSARY_FIELDS
        unknown_keys: set[str] = set()
        for ri, r in enumerate(rows):
            if not isinstance(r, dict):
                bad(f"{where} {ri + 1}번째 항목", "형식이 깨졌습니다.")
                continue
            unknown_keys |= set(r) - allowed
            row_no = r.get("row")
            if not isinstance(row_no, int) or isinstance(row_no, bool) or row_no < 1:
                bad(f"{where} {ri + 1}번째 항목", "엑셀 행 번호(row)가 없습니다.")
                continue
            loc = f"{where} {row_no}행"
            if row_no in seen_rows:
                bad(loc, "같은 행이 두 번 들어 있습니다.")
            seen_rows.add(row_no)
            remark = r.get("remark")
            if remark is not None and not _is_text(remark):
                warnings.append(f"{loc}: 비고가 글자가 아니라 비웠습니다.")
                remark = None
            if kind == "policy":
                path, pname, body = r.get("category_path"), r.get("policy_name"), r.get("body")
                if not _is_text(pname) or not pname.strip():
                    if "policy_name" not in r:
                        bad(loc, "정책명(policy_name) 항목이 없습니다." + _missing_key_hint(r, "policy_name"))
                    else:
                        bad(loc, "정책명이 비어 있습니다.", f"엑셀 {row_no}행에 정책명을 채우거나 그 행을 지운 뒤 " + _REMAKE + ".")
                    continue
                if not isinstance(path, list) or not all(_is_text(p) for p in path):
                    bad(loc, f"'{pname}'의 분류 경로(category_path)가 없거나 형식이 깨졌습니다."
                        + (_missing_key_hint(r, "category_path") if "category_path" not in r else ""))
                    continue
                if body is None:
                    body = ""
                    warnings.append(f"{loc} '{pname}': 본문이 없어 빈 본문으로 넣었습니다.")
                elif not _is_text(body):
                    bad(loc, f"'{pname}'의 본문(body) 형식이 깨졌습니다.")
                    continue
                sheet.policy_rows.append(ParsedPolicyRow(
                    category_path=[p.strip() for p in path], policy_name=pname.strip(), raw_body=body.strip(),
                    remark=(remark.strip() or None) if _is_text(remark) else None, source_row=row_no))
            else:
                term, definition = r.get("term"), r.get("definition")
                for key, v in (("term", term), ("definition", definition)):
                    if not _is_text(v) or not v.strip():
                        bad(loc, f"{_LABEL[key]}({key})이(가) " + ("없습니다." + _missing_key_hint(r, key) if key not in r
                                                                  else "비어 있습니다."),
                            f"엑셀 {row_no}행의 {_LABEL[key]}을(를) 채운 뒤 " + _REMAKE + ".")
                        break
                else:
                    sheet.glossary_rows.append(ParsedGlossaryRow(
                        term=term.strip(), description=definition.strip(),
                        remark=(remark.strip() or None) if _is_text(remark) else None, source_row=row_no))
        if unknown_keys:
            warnings.append(f"{where}: 알 수 없는 항목 {sorted(unknown_keys)}은(는) 무시했습니다.")
        # 본문이 전부 비어 있으면 변환 때 본문 칸을 못 읽었을 가능성이 크다 — 그대로 반영하면 모든 정책이 빈 본문의 새 버전으로
        # 바뀌므로 막는다(/code-review: 변환기 경고는 JSON에 안 남아 업로드한 사람은 모를 수 있음)
        if kind == "policy" and sheet.policy_rows and not any(r.raw_body for r in sheet.policy_rows):
            bad(where, f"정책 {len(sheet.policy_rows)}건의 본문이 모두 비어 있습니다(변환 때 본문 칸을 못 읽었을 수 있음).",
                "변환기 출력의 '읽은 칸'에서 본문을 확인하고, 헤더 이름이 다르면 --map 본문=\"헤더 이름\"으로 다시 변환해 올려 주세요.")
        out.append(sheet)
    if problems:
        raise PolicyJsonError(problems, more=overflow[0])
    return out, (source_name.strip() if source_name else None), warnings


def to_policy_json(sheets: list[ParsedSheet], source_name: str | None = None) -> dict:
    """파싱 결과 → 표준 JSON(변환기용). 읽지 못한 시트(unknown)는 넣지 않는다 — 변환기가 따로 경고한다."""
    doc: dict = {"schema": SCHEMA_V1}
    if source_name:
        doc["source_name"] = source_name
    doc["sheets"] = []
    for sh in sheets:
        if sh.kind == "policy":
            rows = [{"row": r.source_row, "category_path": r.category_path, "policy_name": r.policy_name,
                     "body": r.raw_body, "remark": r.remark} for r in sh.policy_rows]
        elif sh.kind == "glossary":
            rows = [{"row": r.source_row, "term": r.term, "definition": r.description, "remark": r.remark}
                    for r in sh.glossary_rows]
        else:
            continue
        doc["sheets"].append({"name": sh.sheet_name, "kind": sh.kind, "rows": rows})
    return doc
