"""정책 버전 관리 1단계 — 재임포트 때 "같은 정책"을 엑셀 위치가 아니라 식별키로 (2026-10-06, 10/1 회의 액션).

예전엔 (파일·시트·행번호)로만 찾아서 ① 행 하나만 끼워 넣어도 아래 행이 전부 다른 정책의 새 버전으로 잘못 이어지고
② 파일명이 바뀌면 전부 신규 + 옛 정책이 활성으로 남아 중복되고 ③ 엑셀에서 지운 정책이 영원히 활성이었다.
이 파일은 그 세 경우와 매칭 규칙(식별키 → 본문 1건 → 신규, 엑셀 안 중복 키, 사라짐은 시트 단위·검토 큐로)을 고정한다.
"""
import importlib.util as _ilu
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# conftest가 service 패키지를 MagicMock으로 막아 두므로 파일 경로로 실제 모듈을 로드한다(test_policy_pipeline.py와 같은 방식).
# 다른 테스트 파일이 이미 로드했으면 그 모듈을 그대로 쓴다(같은 이름을 다시 로드하면 서로 다른 객체가 섞임).
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
_pkg.excel_parser, _pkg.decompose = excel_parser, decompose
service = _real("service.policy.service", "service/policy/service.py")
risk = _real("service.policy.risk", "service/policy/risk.py")

R = excel_parser.ParsedPolicyRow


def _row(name, row, body=None, path=("a",), remark=None):
    return R(category_path=list(path), policy_name=name, raw_body=body or f"{name} 본문", remark=remark, source_row=row)


def _current(i, r: R, *, file="f.xlsx", sheet="시트1", status="active", missing=None):
    return {"id": i, "logical_id": i, "version": 1, "status": status,
            "content_hash": service._content_hash(r.category_path, r.policy_name, r.raw_body, r.remark),
            "pipeline_version": service.pipeline_version(), "category_path": r.category_path,
            "policy_name": r.policy_name, "raw_body": r.raw_body, "remark": r.remark,
            "source_file": file, "source_sheet": sheet, "source_row": r.source_row, "source_missing_at": missing}


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def env(monkeypatch):
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(side_effect=lambda: _Tx())
    conn.fetchval = AsyncMock(return_value=False)          # 승인된 정정 없음
    ids = iter(range(100, 200))
    inserted = []

    async def fetchrow(query, *args):
        if "INSERT INTO policy_item" in query:
            i = next(ids)
            inserted.append({"id": i, "logical_id": args[10] or i, "version": args[11], "supersedes": args[12],
                             "name": args[3], "row": args[8]})
            return {"id": i, "logical_id": args[10] or i}
        return None
    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    executed = []

    async def execute(query, *args):
        executed.append((" ".join(query.split()), args))
        return "UPDATE %d" % len(args[0]) if "source_missing_at = COALESCE" in query else "OK"
    conn.execute = AsyncMock(side_effect=execute)
    monkeypatch.setattr(service, "get_conn", MagicMock(return_value=conn))
    monkeypatch.setattr(service, "resolve_namespace_id", AsyncMock(return_value=1))
    monkeypatch.setattr(decompose, "decompose_policy_body",
                        AsyncMock(side_effect=lambda name, body: [decompose.Segment(type="narrative", text=body)]))
    emb = MagicMock(embed_batch=AsyncMock(side_effect=lambda texts: [[0.1] * 3 for _ in texts]))
    monkeypatch.setattr(service, "embedding_service", emb)
    monkeypatch.setitem(sys.modules, "shared.cache", MagicMock(invalidate_namespace=AsyncMock(return_value=0)))

    def run(current_rows, sheets, filename="f.xlsx"):
        conn.fetch = AsyncMock(return_value=current_rows)
        monkeypatch.setattr(excel_parser, "parse_workbook", lambda b: [
            excel_parser.ParsedSheet(sheet_name=name, kind="policy", policy_rows=rows) for name, rows in sheets])
        return service.import_excel("ns", "sys", filename, b"")
    def run_paste(current_rows, pasted, source_file="f.xlsx"):
        conn.fetch = AsyncMock(return_value=current_rows)
        return service.import_pasted("ns", "sys", source_file, pasted)
    env_obj = MagicMock(run=run, run_paste=run_paste, inserted=inserted, executed=executed)
    return env_obj


def _relocations(env):
    return [a for q, a in env.executed if q.startswith("UPDATE policy_item SET source_file")]


def _missing_update(env):
    return [a for q, a in env.executed if "source_missing_at = COALESCE" in q]


@pytest.mark.asyncio
async def test_row_inserted_above_keeps_identity(env):
    """행 삽입으로 아래가 한 칸씩 밀려도 같은 정책으로 이어짐 — 새 버전 0, 위치만 갱신, 신규는 끼워 넣은 1행뿐."""
    a, b = _row("A", 2), _row("B", 3)
    res = await env.run([_current(1, a), _current(2, b)], [("시트1", [_row("X", 2), _row("A", 3), _row("B", 4)])])
    s = res.sheets[0]
    assert (s.created_items, s.new_versions, s.unchanged_skipped, s.moved) == (1, 0, 2, 2)
    assert sorted((args[0], args[3]) for args in _relocations(env)) == [(1, 3), (2, 4)]
    assert [i["name"] for i in env.inserted] == ["X"] and res.missing_marked == 0


@pytest.mark.asyncio
async def test_renamed_file_no_duplicates(env):
    """파일명이 바뀌어도 같은 정책 — 신규 0, 위치(파일명)만 갱신, 옛 정책이 중복으로 남지 않음."""
    a, b = _row("A", 2), _row("B", 3)
    res = await env.run([_current(1, a), _current(2, b)], [("시트1", [a, b])], filename="f_v2.xlsx")
    s = res.sheets[0]
    assert (s.created_items, s.new_versions, s.moved) == (0, 0, 2)
    assert all(args[1] == "f_v2.xlsx" for args in _relocations(env)) and res.missing_marked == 0


@pytest.mark.asyncio
async def test_same_position_same_content_is_noop(env):
    a = _row("A", 2)
    res = await env.run([_current(1, a)], [("시트1", [a])])
    assert (res.sheets[0].unchanged_skipped, res.sheets[0].moved) == (1, 0) and not env.inserted


@pytest.mark.asyncio
async def test_moved_category_matched_by_body_becomes_new_version(env):
    """분류만 옮긴 경우 — 본문이 같은 1건과 이어 새 버전(logical_id 유지, 내용 해시에 분류가 들어가 버전은 오름)."""
    a = _row("A", 2, body="같은 본문")
    res = await env.run([_current(7, a)], [("시트1", [_row("A", 2, body="같은 본문", path=("b",))])])
    s = res.sheets[0]
    assert (s.matched_by_body, s.new_versions, s.created_items) == (1, 1, 0)
    assert env.inserted[0]["logical_id"] == 7 and env.inserted[0]["supersedes"] == 7


@pytest.mark.asyncio
async def test_ambiguous_body_match_is_new(env):
    """본문이 같은 현행 항목이 2건이면 어느 쪽인지 모르니 잇지 않는다 — 신규."""
    cur = [_current(1, _row("A", 2, body="공통")), _current(2, _row("B", 3, body="공통"))]
    res = await env.run(cur, [("시트1", [_row("A", 2, body="공통"), _row("B", 3, body="공통"), _row("C", 4, body="공통")])])
    s = res.sheets[0]
    assert (s.created_items, s.matched_by_body) == (1, 0) and env.inserted[0]["name"] == "C"


@pytest.mark.asyncio
async def test_duplicate_key_in_excel_second_is_new(env):
    a = _row("A", 2)
    res = await env.run([_current(1, a)], [("시트1", [a, _row("A", 3, body="다른 본문")])])
    s = res.sheets[0]
    assert (s.unchanged_skipped, s.duplicate_keys, s.created_items) == (1, 1, 1)


@pytest.mark.asyncio
async def test_removed_policy_goes_to_review_queue_not_deleted(env):
    """엑셀에서 지운 정책 — 자동 폐기 안 함, 사라짐 표시 + active면 pending_review로(검토 큐)."""
    a, b, c = _row("A", 2), _row("B", 3), _row("C", 4)
    res = await env.run([_current(1, a), _current(2, b), _current(3, c)], [("시트1", [a, b])])
    upd = _missing_update(env)
    assert len(upd) == 1 and upd[0][0] == [3] and res.missing_marked == 1
    q = [q for q, _ in env.executed if "source_missing_at = COALESCE" in q][0]
    assert "WHEN status = 'active' THEN 'pending_review'" in q and "deprecated" not in q.split("SET")[1].split("WHERE")[0]


@pytest.mark.asyncio
async def test_missing_only_for_sheets_in_this_file_and_not_rejected(env):
    """파일에 없는 시트의 항목(시트 일부만 올린 실수)과 이미 반려된 항목은 표시하지 않는다."""
    a = _row("A", 2)
    cur = [_current(1, a), _current(2, _row("Z", 2), sheet="시트2"), _current(3, _row("R", 3), status="rejected")]
    res = await env.run(cur, [("시트1", [a])])
    assert res.missing_marked == 0 and not _missing_update(env)


@pytest.mark.asyncio
async def test_reappearing_policy_clears_missing_flag(env):
    """한때 사라졌다가 다시 들어오면 표시 해제(위치 갱신과 같은 UPDATE)."""
    a = _row("A", 2)
    res = await env.run([_current(1, a, missing="2026-10-06")], [("시트1", [a])])
    assert res.sheets[0].moved == 1 and "source_missing_at = NULL" in [q for q, _ in env.executed
                                                                     if q.startswith("UPDATE policy_item SET source_file")][0]


def test_source_missing_is_high_risk_and_never_auto_passed():
    rk = risk.classify("parsed", 0, 1, False, False, source_missing=True)
    assert rk.level == "high" and rk.rule_key is None and rk.short == "원본에서 사라짐"
    assert risk.classify("parsed", 0, 1, False).level == "low"   # 다른 조건이 같으면 원래는 자동 통과 후보


# ── /code-review 지적 4건 회귀 방지 ──

@pytest.mark.asyncio
async def test_duplicate_key_rows_reuse_existing_items_on_reimport(env):
    """엑셀에 같은 키가 두 번 있고 DB에도 그 키 항목이 2건 — 같은 파일을 다시 올려도 신규·사라짐이 생기지 않는다."""
    a1, a2 = _row("A", 2, body="본문1"), _row("A", 3, body="본문2")
    res = await env.run([_current(1, a1), _current(2, a2)], [("시트1", [a1, a2])])
    s = res.sheets[0]
    assert (s.created_items, s.unchanged_skipped, s.duplicate_keys, res.missing_marked) == (0, 2, 1, 0)


@pytest.mark.asyncio
async def test_missing_scope_excludes_other_workbook_with_same_sheet_name(env):
    """같은 파트에 같은 시트 이름을 쓰는 다른 엑셀이 있어도 그 항목은 "사라짐"으로 표시하지 않는다."""
    a = _row("A", 2)
    other = _current(9, _row("Z", 2), file="다른파일.xlsx")
    res = await env.run([_current(1, a), other], [("시트1", [a])])
    assert res.missing_marked == 0 and not _missing_update(env)


@pytest.mark.asyncio
async def test_missing_scope_follows_renamed_file(env):
    """파일명이 바뀌면서 정책 하나가 빠진 경우 — 옛 파일명 항목도 범위에 들어가 사라짐으로 표시된다."""
    a, b = _row("A", 2), _row("B", 3)
    res = await env.run([_current(1, a), _current(2, b)], [("시트1", [a])], filename="f_v2.xlsx")
    assert _missing_update(env)[0][0] == [2] and res.missing_marked == 1


@pytest.mark.asyncio
async def test_exact_key_wins_over_earlier_body_match(env):
    """앞 행이 이름만 바뀌어 본문이 B와 같아도, 뒤에 B의 정확한 키 행이 있으면 B는 그 행이 가져간다(키 매칭 먼저)."""
    b = _row("B", 3, body="공유 본문")
    res = await env.run([_current(2, b)], [("시트1", [_row("B-새이름", 2, body="공유 본문"), b])])
    s = res.sheets[0]
    assert (s.unchanged_skipped, s.matched_by_body, s.created_items) == (1, 0, 1)
    assert env.inserted[0]["name"] == "B-새이름"


@pytest.mark.asyncio
async def test_reappearing_reviewed_item_returns_to_active(env):
    """사라짐으로 큐에 올라갔다가 그대로 돌아온 항목 — 전에 승인된 적 있으면(reviewed_at) active로 복귀하는 SQL."""
    a = _row("A", 2)
    await env.run([_current(1, a, missing="2026-10-06")], [("시트1", [a])])
    q = [q for q, _ in env.executed if q.startswith("UPDATE policy_item SET source_file")][0]
    assert "reviewed_at IS NOT NULL THEN 'active'" in q and "source_missing_at = NULL" in q


# ── 붙여넣기 임포트 (2026-10-06, v2.127) — 보안(DRM) 엑셀은 서버·브라우저가 못 열어 엑셀에서 복사해 붙여넣는다 ──

_HEADER = "No\t대분류\t정책명\t조건/상세\t비고"


def test_pasted_table_handles_excel_quoting_and_trailing_blank_rows():
    """엑셀은 칸 안 줄바꿈·따옴표가 있으면 그 칸을 큰따옴표로 감싸고("" = 따옴표), 끝에 빈 줄이 붙는다."""
    text = 'No\t정책명\t조건/상세\r\n1\t카드 환불\t"당일 취소\r\n익일 ""3~7일"""\r\n\t\t\r\n'
    rows = service.parse_pasted_table(text)
    assert rows == [("No", "정책명", "조건/상세"), ("1", "카드 환불", '당일 취소\n익일 "3~7일"')]


def test_pasted_sheet_parses_like_excel_including_merged_fill_and_row_numbers():
    """Ctrl+A로 복사하면 제목 행·빈 병합 칸까지 그대로 와서, 파일 업로드와 같은 엑셀 행 번호·분류 채우기가 나와야 한다."""
    text = "비즈니스 정책서\t\t\t\t\n" + _HEADER + "\n1\t주문\t카드 환불\t당일\t\n2\t\t부분 취소\t1회만\t예외\n"
    sheet = excel_parser.parse_sheet_rows("온라인", service.parse_pasted_table(text))
    assert sheet.kind == "policy"
    assert [(r.category_path, r.policy_name, r.source_row, r.remark) for r in sheet.policy_rows] == [
        (["주문"], "카드 환불", 3, None), (["주문"], "부분 취소", 4, "예외")]


@pytest.mark.asyncio
@pytest.mark.parametrize("source_file, pasted, msg", [
    ("", [("시트1", _HEADER)], "원본 정책서 파일 이름"),
    ("f.xlsx", [], "붙여넣은 시트가 없습니다"),
    ("f.xlsx", [(" ", _HEADER)], "시트 이름이 빈 칸"),
    ("f.xlsx", [("시트1", _HEADER), ("시트1 ", _HEADER)], "같은 시트 이름"),
    ("f.xlsx", [("시트1", "  \n\t\n")], "붙여넣은 내용이 없습니다"),
])
async def test_paste_rejects_before_writing_anything(env, source_file, pasted, msg):
    with pytest.raises(ValueError, match=msg):
        await env.run_paste([], pasted, source_file=source_file)
    assert not env.inserted and not env.executed


def _paste_text(rows):
    return _HEADER + "\n" + "\n".join(
        f"{i}\t{r.category_path[0]}\t{r.policy_name}\t{r.raw_body}\t" for i, r in enumerate(rows, 1))


@pytest.mark.asyncio
async def test_paste_same_content_changes_nothing(env):
    """붙여넣은 시트 내용이 그대로면 새 버전·신규 0 — 파일 업로드와 같은 매칭(식별키)을 탄다."""
    a, b = _row("A", 2), _row("B", 3)
    res = await env.run_paste([_current(1, a), _current(2, b)], [("시트1", _paste_text([a, b]))])
    s = res.sheets[0]
    assert (s.created_items, s.new_versions, s.unchanged_skipped, s.moved) == (0, 0, 2, 0)
    assert res.source_file == "f.xlsx" and res.missing_marked == 0 and not res.warnings


@pytest.mark.asyncio
async def test_paste_marks_genuinely_removed_policy(env):
    """기존 5개 중 1개가 빠진 정도면 실제로 지운 정책으로 보고 평소처럼 '사라짐' 표시."""
    rows = [_row(n, i) for i, n in enumerate("ABCDE", 2)]
    res = await env.run_paste([_current(i, r) for i, r in enumerate(rows, 1)], [("시트1", _paste_text(rows[:4]))])
    assert res.missing_marked == 1 and _missing_update(env)[0][0] == [5]


@pytest.mark.asyncio
async def test_paste_partial_copy_does_not_flood_review_queue(env):
    """시트 일부만 복사한 실수(기존 10개 중 7개 빠짐) — 사라짐 표시 대신 경고. 붙여넣은 정책 자체는 정상 반영."""
    rows = [_row(f"P{i}", i + 2) for i in range(10)]
    res = await env.run_paste([_current(i + 1, r) for i, r in enumerate(rows)], [("시트1", _paste_text(rows[:3]))])
    assert res.missing_marked == 0 and not _missing_update(env)
    assert len(res.warnings) == 1 and "'시트1' 시트: 기존 정책 10개 중 7개" in res.warnings[0]
    assert res.sheets[0].unchanged_skipped == 3
