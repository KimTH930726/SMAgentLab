"""정책서 v0 재구축 3단계(2026-10-21) — 원문 확인(service/policy/source_check.py) + JSON 내려받기·가져오기(service/policy/json_io.py).

지키는 약속:
- 원문 확인은 정정이 아니다 — 정정 원장을 kind='source_check'로 재사용하되 정정 요청 목록·배지·내 신고·정정 처리 API에서 빠진다.
- [해결]/[닫기]는 메모 필수, 처리 전 건만, 담당 파트·관리자만. 제안 연결은 suggested인 것만 active/rejected로.
- JSON 가져오기는 정책 테이블에 직접 쓰지 않는다 — 바뀐 정책마다 정정 제안(source json_import)만. 같음·바뀜·파일에만·DB에만·검토 중 분류는
  전체로 맞아야 하고(내려받은 것을 그대로 넣으면 바뀜 0), 미리보기 뒤 파일·정책이 바뀌면 409.
conftest가 service 패키지를 MagicMock으로 바꿔 두므로 파일 경로로 로드한다.
"""
import importlib.util as _ilu
import inspect
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_backend = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = _ilu.spec_from_file_location(name, str(_backend / rel))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _real(name, rel):
    """이미 다른 테스트 파일이 실제 모듈을 올려 뒀으면 그걸 쓴다(같은 이름을 두 번 올리면 그쪽 참조가 어긋난다)."""
    m = sys.modules.get(name)
    return m if isinstance(m, types.ModuleType) else _load(name, rel)


source_check = _load("service.policy.source_check", "service/policy/source_check.py")
_load("service.policy.v0_convert", "service/policy/v0_convert.py")
json_io = _load("service.policy.json_io", "service/policy/json_io.py")
_pkg = sys.modules.setdefault("service.policy", MagicMock())
_load("service.policy.schemas", "service/policy/schemas.py")
_saved = {k: getattr(_pkg, k, None) for k in ("source_check", "json_io")}
_pkg.source_check, _pkg.json_io = source_check, json_io
prouter = _load("service.policy.router", "service/policy/router.py")

sys.modules.setdefault("service.improvement", MagicMock())
draft = _real("service.improvement.draft", "service/improvement/draft.py")
sys.modules["service.improvement"].draft = draft
svc = _real("service.improvement.service", "service/improvement/service.py")

USER = {"id": 9, "username": "담당", "role": "user", "part_id": 1}
ADMIN = {"id": 1, "username": "admin", "role": "admin"}


def _conn(fetch=None, fetchrow=None, fetchval=None, execute="UPDATE 1"):
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.transaction = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
    c.fetch = AsyncMock(side_effect=fetch) if callable(fetch) else AsyncMock(return_value=fetch or [])
    c.fetchrow = AsyncMock(side_effect=fetchrow) if callable(fetchrow) else AsyncMock(return_value=fetchrow)
    c.fetchval = AsyncMock(side_effect=fetchval) if callable(fetchval) else AsyncMock(return_value=fetchval)
    c.execute = AsyncMock(return_value=execute)
    return c


def _sql_texts(mock):
    return [c.args[0] for c in mock.await_args_list]


# ── 원문 확인: 씨앗 ──────────────────────────────────────────────────────────

class TestSeed:
    def test_hash_ignores_description_and_order_but_not_id(self):
        h = source_check.seed_hash("golden_set", "conflict", ["b", "a"], [])
        assert h == source_check.seed_hash("golden_set", "conflict", ["a", "b"], [])
        assert h != source_check.seed_hash("v0_load_check", "conflict", ["a", "b"], [])
        # 같은 표의 다른 문제(종류·대상 같음)는 씨앗 id로 갈린다
        assert source_check.seed_hash("golden_set", "ref_error", [], ["t"], "x") != source_check.seed_hash("golden_set", "ref_error", [], ["t"], "y")

    @pytest.mark.parametrize("rec, ok", [
        ({"source": "golden_set", "type": "conflict", "policies": ["a", "b"], "description": "d"}, True),
        ({"source": "golden_set", "type": "conflict", "policies": ["a"], "description": "d"}, False),       # 어긋남은 둘 이상
        ({"source": "golden_set", "type": "ref_error", "ref_tables": ["t"], "description": " "}, False),    # 설명 필수
        ({"source": "chat", "type": "question", "policies": ["a"], "description": "d"}, False),             # 모르는 출처
        ({"source": "v0_load_check", "type": "question", "description": "d"}, False),                       # 대상 없음
    ])
    def test_validate(self, rec, ok):
        assert (source_check.validate_seed(rec) == []) is ok


# ── 원문 확인: 조회·처리 ─────────────────────────────────────────────────────

def _check_row(**kw):
    return {"id": 70, "source": "golden_set", "target_type": "policy", "target_id": 11, "user_input": "두 정책이 다르게 말함",
            "original": json.dumps({"type": "conflict", "policies": [11, 12], "ref_tables": []}), "status": "pending",
            "ai_verdict": None, "created_at": datetime(2026, 10, 21, tzinfo=timezone.utc), "decided_at": None, "decided_by": None} | kw


class TestListChecks:
    @pytest.mark.asyncio
    async def test_shape_and_open_filter(self):
        async def fetch(sql, *args):
            if "FROM ops_improvement_item" in sql:
                return [_check_row(), _check_row(id=71, target_type="ref_table", target_id=3, status="approved",
                                                 original=json.dumps({"type": "ref_error", "policies": [], "ref_tables": [3]}),
                                                 ai_verdict=json.dumps({"resolution": "resolved", "note": "원본 고침"}))]
            if "FROM policy_item" in sql:
                return [{"logical_id": 11, "policy_name": "가", "category_path": ("1. 가",)}]   # 12는 현행 없음
            if "FROM ref_table" in sql:
                return [{"id": 30, "logical_id": 3, "title": "표"}]
            if "FROM ops_link" in sql:
                return [{"id": 5, "from_key": "11", "relation": "refers", "to_type": "section", "to_key": "534|5. 결제 > 5-1. 가",
                         "status": "suggested", "policy_name": "가", "category_path": ("1. 가",)}]
            if "FROM ops_namespace" in sql:
                return [{"id": 534, "name": "POS"}]
            raise AssertionError(sql)
        c = _conn(fetch=fetch)
        with patch.object(source_check, "get_conn", return_value=c), \
             patch.object(source_check, "resolve_namespace_id", AsyncMock(return_value=2)):
            out = await source_check.list_checks("ns", "open")
        ledger_sql, *args = c.fetch.await_args_list[0].args
        assert "i.kind = $2" in ledger_sql and args[1] == "source_check" and args[2] == ["pending"]
        link_sql, *largs = next(x.args for x in c.fetch.await_args_list if "FROM ops_link" in x.args[0])
        assert largs[1:] == ["v0_rule", ["suggested"]]
        a, b = out["checks"]
        assert a["status"] == "open" and a["type"] == "conflict" and a["description"] == "두 정책이 다르게 말함"
        assert a["policies"] == [{"logical_id": 11, "policy_name": "가", "category_path": ["1. 가"]},
                                 {"logical_id": 12, "policy_name": None, "category_path": []}]
        assert b["status"] == "resolved" and b["note"] == "원본 고침" and b["ref_tables"] == [{"logical_id": 3, "id": 30, "title": "표"}]
        assert out["links"] == [{"id": 5, "status": "suggested", "relation": "refers",
                                 "policy": {"logical_id": 11, "policy_name": "가", "category_path": ["1. 가"]},
                                 "target": {"type": "section", "namespace": "POS", "path": ["5. 결제", "5-1. 가"]}}]

    @pytest.mark.asyncio
    async def test_done_filter(self):
        c = _conn(fetch=[])
        with patch.object(source_check, "get_conn", return_value=c), \
             patch.object(source_check, "resolve_namespace_id", AsyncMock(return_value=2)):
            await source_check.list_checks("ns", "done")
        assert c.fetch.await_args_list[0].args[3] == ["approved", "rejected"]
        assert c.fetch.await_args_list[-1].args[3] == ["active", "rejected"]


class TestDecide:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("resolution, status", [("resolved", "approved"), ("dismissed", "rejected")])
    async def test_marks_with_note_only_pending_source_check(self, resolution, status):
        c = _conn()
        with patch.object(source_check, "get_conn", return_value=c):
            out = await source_check.decide_check(70, USER, resolution, " 메모 ")
        sql, *args = c.execute.await_args.args
        assert "status = 'pending'" in sql and "kind = $5" in sql and args[4] == "source_check"
        assert args[1] == status and args[2] == 9
        assert json.loads(args[3]) == {"resolution": resolution, "note": "메모", "by": 9, "by_name": "담당"}
        assert out == {"id": 70, "status": resolution}

    @pytest.mark.asyncio
    async def test_note_required_and_already_done(self):
        with pytest.raises(ValueError, match="이유"):
            await source_check.decide_check(70, USER, "dismissed", "  ")
        c = _conn(execute="UPDATE 0")
        with patch.object(source_check, "get_conn", return_value=c), pytest.raises(ValueError, match="이미 처리"):
            await source_check.decide_check(70, USER, "resolved", "x")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("confirm, status", [(True, "active"), (False, "rejected")])
    async def test_link_confirm_reject_only_suggested(self, confirm, status):
        c = _conn()
        with patch.object(source_check, "get_conn", return_value=c):
            out = await source_check.decide_link(5, USER, confirm)
        sql, *args = c.execute.await_args.args
        assert "status = 'suggested'" in sql and args == [5, status, "v0_rule"]
        assert out == {"id": 5, "status": status}
        c0 = _conn(execute="UPDATE 0")
        with patch.object(source_check, "get_conn", return_value=c0), pytest.raises(ValueError):
            await source_check.decide_link(5, USER, confirm)


class TestSourceCheckRouter:
    @pytest.mark.asyncio
    async def test_list_checks_read_permission_first(self):
        with patch.object(prouter, "check_knowledge_read", AsyncMock(side_effect=HTTPException(403, "no"))), \
             patch.object(prouter.source_check, "list_checks", AsyncMock()) as lst:
            with pytest.raises(HTTPException) as e:
                await prouter.list_source_checks(namespace="ns", status=None, user=USER)
        assert e.value.status_code == 403
        lst.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resolve_needs_owner_of_items_domain(self):
        owner = AsyncMock(side_effect=HTTPException(403, "no"))
        with patch.object(prouter.source_check, "check_namespace", AsyncMock(return_value="회원")), \
             patch.object(prouter, "check_namespace_ownership", owner), \
             patch.object(prouter.source_check, "decide_check", AsyncMock()) as decide:
            with pytest.raises(HTTPException) as e:
                await prouter.resolve_source_check(70, prouter.SourceCheckDecision(note="x"), user=USER)
        assert e.value.status_code == 403 and owner.await_args.args == ("회원", USER)
        decide.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_or_correction_item_is_404(self):
        with patch.object(prouter.source_check, "check_namespace", AsyncMock(return_value=None)), \
             patch.object(prouter.source_check, "decide_check", AsyncMock()) as decide:
            with pytest.raises(HTTPException) as e:
                await prouter.dismiss_source_check(70, prouter.SourceCheckDecision(note="x"), user=USER)
        assert e.value.status_code == 404
        decide.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dismiss_and_link_confirm_call_through(self):
        with patch.object(prouter.source_check, "check_namespace", AsyncMock(return_value="회원")), \
             patch.object(prouter, "check_namespace_ownership", AsyncMock()), \
             patch.object(prouter.source_check, "decide_check", AsyncMock(return_value={"id": 70})) as decide:
            await prouter.dismiss_source_check(70, prouter.SourceCheckDecision(note="중복"), user=USER)
        assert decide.await_args.args == (70, USER, "dismissed", "중복")
        with patch.object(prouter.source_check, "link_namespace", AsyncMock(return_value="POS")), \
             patch.object(prouter, "check_namespace_ownership", AsyncMock()) as owner, \
             patch.object(prouter.source_check, "decide_link", AsyncMock(return_value={"id": 5})) as dl:
            await prouter.confirm_link(5, user=USER)
            await prouter.reject_link(5, user=USER)
        assert owner.await_args.args == ("POS", USER)
        assert [c.args for c in dl.await_args_list] == [(5, USER, True), (5, USER, False)]

    def test_note_required_by_schema(self):
        with pytest.raises(Exception):
            prouter.SourceCheckDecision(note="")


# ── 정정 요청에서 원문 확인 빼기 ─────────────────────────────────────────────

class TestCorrectionQueueExcludesSourceChecks:
    @pytest.mark.asyncio
    async def test_list_items_badge_mine_and_seen(self):
        c = _conn(fetch=[])
        c.execute = AsyncMock(return_value="UPDATE 0")
        with patch.object(svc, "get_conn", return_value=c):
            await svc.list_items(None, None)
            await svc.list_items("ns", "pending")
            await svc.pending_count()
            await svc.list_mine(9, False)
            await svc.mark_seen(9, [1])
        for call in c.fetch.await_args_list:
            sql, *args = call.args
            assert "kind <> 'source_check'" in sql or ("kind <> $" in sql and "source_check" in args), sql
        sql, *args = c.execute.await_args.args
        assert "kind <> $3" in sql and args[2] == "source_check"

    @pytest.mark.asyncio
    async def test_correction_endpoints_cannot_pick_source_check(self):
        c = _conn(fetchval=None)
        with patch.object(svc, "get_conn", return_value=c):
            assert await svc.item_namespace(70) is None
        sql, *args = c.fetchval.await_args.args
        assert "i.kind <> $2" in sql and args == [70, "source_check"]

    def test_policy_open_correction_ignores_source_checks(self):
        # 정책 화면 "정정 #N 검토 중"·중복 제안 막기는 정책 정정 대상 종류만 본다 — 원문 확인의 target_type('policy')은 거기 없다
        assert "policy" not in svc.POLICY_TARGETS and "ref_table" not in svc.POLICY_TARGETS


# ── JSON: 내려받기·읽기·비교 ────────────────────────────────────────────────

def _cur(sk, lid, body, remark=None, open_id=None, name=None, path=("1. 가",)):
    return {"item_id": lid * 10, "logical_id": lid, "source_key": sk, "policy_name": name or f"정책{lid}",
            "category_path": path, "raw_body": body, "remark": remark, "open_correction_id": open_id}


CURRENT = [_cur("pol_a", 1, "원문 A", "비고 A"), _cur("pol_b", 2, "원문 B"), _cur("pol_c", 3, "원문 C"),
           _cur("pol_d", 4, "원문 D", open_id=88)]


def _thin(policies, domain="ns"):
    return json.dumps({"schema": json_io.SCHEMA, "domain": domain, "exported_at": "x", "policies": policies}, ensure_ascii=False)


def _export_of(rows):
    return [{"source_key": r["source_key"], "category_path": list(r["category_path"]), "policy_name": r["policy_name"],
             "raw_body": r["raw_body"], "remark": r["remark"], "order": i + 1, "version": 1} for i, r in enumerate(rows)]


class TestExport:
    @pytest.mark.asyncio
    async def test_thin_format_active_with_key_only(self):
        rows = [{"source_key": "pol_a", "category_path": ("1. 가",), "policy_name": "가", "raw_body": "본문", "remark": None, "version": 2},
                {"source_key": None, "category_path": (), "policy_name": "키 없음", "raw_body": "x", "remark": None, "version": 1}]
        c = _conn(fetch=rows)
        with patch.object(json_io, "get_conn", return_value=c), patch.object(json_io, "resolve_namespace_id", AsyncMock(return_value=2)):
            out = await json_io.export_domain("ns")
        assert "status = 'active'" in c.fetch.await_args.args[0]
        assert out["schema"] == "opslens/policy-v0/1" and out["domain"] == "ns" and out["exported_at"]
        assert out["policies"] == [{"source_key": "pol_a", "category_path": ["1. 가"], "policy_name": "가", "raw_body": "본문",
                                    "remark": None, "order": 1, "version": 2}]


class TestClassify:
    def test_roundtrip_export_is_all_unchanged(self):
        out = json_io.classify("ns", json_io.parse_import(_thin(_export_of(CURRENT))), CURRENT)
        assert (out["unchanged"], out["changed"], out["added"], out["missing"], out["blocked"], out["ignored_fields"]) == (4, [], [], [], [], [])

    def test_every_record_lands_in_exactly_one_bucket(self):
        recs = _export_of(CURRENT[:2] + CURRENT[3:])                  # pol_c 빠짐 → missing
        recs[0]["raw_body"] = "원문 A 개정"                            # 바뀜(원문)
        recs[1]["remark"] = "새 비고"                                  # 바뀜(비고만)
        recs[2]["raw_body"] = "원문 D 개정"                            # 검토 중 → blocked
        recs.append({"source_key": "pol_new", "policy_name": "새 정책", "raw_body": "새 원문", "remark": None})   # 파일에만
        recs[0]["policy_name"] = "이름 바꿈"                           # 이름 변경은 가져오지 않음(보고만)
        out = json_io.classify("ns", json_io.parse_import(_thin(recs)), CURRENT)
        assert [c["source_key"] for c in out["changed"]] == ["pol_a", "pol_b"]
        assert out["changed"][0]["body_changed"] and not out["changed"][0]["remark_changed"]
        assert out["changed"][1]["remark_changed"] and not out["changed"][1]["body_changed"]
        assert out["changed"][1]["_proposed"] == {"raw_body": "원문 B", "remark": "새 비고"}
        assert [(b["source_key"], b["correction_id"]) for b in out["blocked"]] == [("pol_d", 88)]
        assert [a["source_key"] for a in out["added"]] == ["pol_new"]
        assert [m["source_key"] for m in out["missing"]] == ["pol_c"]
        assert out["ignored_fields"] == [{"source_key": "pol_a", "logical_id": 1, "fields": ["policy_name"]}]
        # 유실·중복 0: 파일 항목 수 = 같음 + 바뀜 + 검토 중 + 파일에만, DB 현행 수 = 같음 + 바뀜 + 검토 중 + DB에만
        n_file, n_db = len(recs), len(CURRENT)
        mid = out["unchanged"] + len(out["changed"]) + len(out["blocked"])
        assert mid + len(out["added"]) == n_file and mid + len(out["missing"]) == n_db
        assert out["changed"][0]["before_hash"] != out["changed"][0]["after_hash"]

    def test_remark_key_absent_keeps_db_remark_and_crlf_is_same(self):
        recs = [{"source_key": "pol_a", "raw_body": "원문 A"}]     # 비고 키 없음 = 지금 비고 그대로
        out = json_io.classify("ns", json_io.parse_import(_thin(recs)), [_cur("pol_a", 1, "원문 A", "비고 A")])
        assert out["unchanged"] == 1
        out = json_io.classify("ns", json_io.parse_import(_thin([{"source_key": "pol_a", "raw_body": "줄1\r\n줄2", "remark": "비고 A"}])),
                               [_cur("pol_a", 1, "줄1\n줄2", "비고 A")])
        assert out["unchanged"] == 1

    def test_whitespace_only_edit_is_unchanged_like_the_ledger(self):
        """편집기가 줄 끝 공백·빈 줄만 바꾼 파일 — 원장의 같음 판정(_unmarked_equal)과 같아야 한다. 다르면 미리보기는 "바뀜"인데
        반영 땐 전부 "바뀐 내용 없음"으로 실패했다(10/21 리뷰 L1)."""
        cur = [_cur("pol_a", 1, "줄1 값 A\n줄2", "비고 A")]
        content = _thin([{"source_key": "pol_a", "raw_body": "줄1  값 A   \n\n줄2\n", "remark": " 비고 A "}])
        out = json_io.classify("ns", json_io.parse_import(content), cur)
        assert out["unchanged"] == 1 and out["changed"] == []
        # 원장 판정도 같은 결론
        assert svc._unchanged("policy_narrative", {"raw_body": "줄1 값 A\n줄2", "chunk_text": "줄1 값 A\n줄2", "remark": "비고 A"},
                              {"raw_body": "줄1  값 A   \n\n줄2\n", "chunk_text": "줄1  값 A   \n\n줄2\n", "remark": " 비고 A "})
        # 줄 구조가 바뀌면(두 줄 → 한 줄) 바뀐 것
        out = json_io.classify("ns", json_io.parse_import(_thin([{"source_key": "pol_a", "raw_body": "줄1 값 A 줄2", "remark": "비고 A"}])), cur)
        assert len(out["changed"]) == 1

    def test_single_line_team_file_is_team_format(self):
        line = json.dumps({"policy_id": "pol_a", "document_id": "doc_01", "domain": "ns", "section_path": ["1. 가"], "title": "t",
                           "source_ref": {}, "source_text": "원문 A", "source_notes": "비고 A"}, ensure_ascii=False)
        p = json_io.parse_import(line)
        assert p["format"] == "team" and [r["source_key"] for r in p["records"]] == ["pol_a"]

    def test_apply_twice_plan_hash_moves(self):
        """첫 반영으로 바뀐 정책에 대기 정정이 생기면 두 번째 반영의 계획이 달라져 409가 된다(같은 안 두 번 안 올라감)."""
        recs = _export_of(CURRENT)
        recs[0]["raw_body"] = "개정"
        p = json_io.parse_import(_thin(recs))
        h1 = json_io.classify("ns", p, CURRENT)["plan_hash"]
        after = [_cur("pol_a", 1, "원문 A", "비고 A", open_id=501)] + CURRENT[1:]
        out = json_io.classify("ns", p, after)
        assert out["plan_hash"] != h1 and out["changed"] == [] and out["blocked"][0]["source_key"] == "pol_a"

    def test_plan_hash_changes_when_db_changes(self):
        recs = _export_of(CURRENT)
        recs[0]["raw_body"] = "개정"
        p = json_io.parse_import(_thin(recs))
        h1 = json_io.classify("ns", p, CURRENT)["plan_hash"]
        moved = [_cur("pol_a", 1, "다른 사람이 승인한 새 원문", "비고 A")] + CURRENT[1:]
        assert json_io.classify("ns", p, moved)["plan_hash"] != h1
        assert json_io.classify("ns", p, CURRENT)["plan_hash"] == h1

    @pytest.mark.parametrize("content, msg", [
        ("", "비어"),
        (_thin([{"source_key": "pol_a", "raw_body": "x"}, {"source_key": "pol_a", "raw_body": "y"}]), "두 번"),
        (_thin([{"source_key": "pol_a", "raw_body": "  "}]), "원문이 비어"),
        (json.dumps({"schema": "other/1", "policies": []}), "모르는 형식"),
        ("not json\n", "JSON이 아니"),
        (json.dumps({"foo": 1}) + "\n", "형식"),
    ])
    def test_bad_files(self, content, msg):
        with pytest.raises(ValueError, match=msg):
            json_io.parse_import(content)

    def test_domain_mismatch(self):
        with pytest.raises(ValueError, match="업무 영역"):
            json_io.classify("ns", json_io.parse_import(_thin([{"source_key": "pol_a", "raw_body": "x"}], domain="다른")), CURRENT)

    def test_team_policies_jsonl_uses_v0_conversion_and_filters_domain(self):
        def team(pid, doc, domain, text, notes=""):
            return json.dumps({"policy_id": pid, "document_id": doc, "domain": domain, "section_path": ["1. 가"], "title": "t",
                               "source_ref": {"body_block": 1, "row": 1}, "source_text": text, "source_notes": notes}, ensure_ascii=False)
        content = "\n".join([team("pol_a", "doc_01", "ns (설명)", "원문 A", " 비고 A "), team("pol_b", "doc_01", "ns", "원문 B 개정"),
                             team("pol_x", "doc_02", "다른", "다른 문서")])
        p = json_io.parse_import(content)
        assert p["format"] == "team"
        out = json_io.classify("ns", p, CURRENT[:3])
        assert out["other_domain"] == 1 and out["unchanged"] == 1                # 비고 공백 정리 = 적재 규칙(v0_convert)
        assert [c["source_key"] for c in out["changed"]] == ["pol_b"] and [m["source_key"] for m in out["missing"]] == ["pol_c"]


# ── JSON: 반영 ───────────────────────────────────────────────────────────────

def _fake_improvement(side_effects):
    m = types.SimpleNamespace()
    m.ConflictError = svc.ConflictError
    m.create_policy_proposal = AsyncMock(side_effect=side_effects)
    return m


class TestApply:
    def _content(self):
        recs = _export_of(CURRENT)
        recs[0]["raw_body"] = "원문 A 개정"
        recs[1]["raw_body"] = "원문 B 개정"
        return _thin(recs)

    def _patches(self, current=CURRENT):
        c = _conn(fetch=current)
        return c, (patch.object(json_io, "get_conn", return_value=c),
                   patch.object(json_io, "resolve_namespace_id", AsyncMock(return_value=2)))

    @pytest.mark.asyncio
    async def test_creates_json_import_proposals_only(self):
        content = self._content()
        c, (p1, p2) = self._patches()
        with p1, p2:
            prev = await json_io.preview("ns", content)
        assert [x["source_key"] for x in prev["changed"]] == ["pol_a", "pol_b"] and "_proposed" not in prev["changed"][0]
        fake = _fake_improvement([{"id": 501}, svc.ConflictError("이미", existing_id=77)])
        c, (p1, p2) = self._patches()
        with p1, p2, patch.object(sys.modules["service.improvement"], "service", fake, create=True):
            out = await json_io.apply("ns", ADMIN, content, prev["doc_hash"], prev["plan_hash"], "rev.json")
        calls = fake.create_policy_proposal.await_args_list
        assert [k.kwargs["mode"] for k in calls] == ["json_import", "json_import"]
        assert calls[0].kwargs["item_id"] == 10 and calls[0].kwargs["proposed"] == {"raw_body": "원문 A 개정", "remark": "비고 A"}
        assert calls[0].kwargs["user_input"] == "JSON 가져오기 — rev.json"
        assert out["created"] == [{"source_key": "pol_a", "logical_id": 1, "correction_id": 501}]
        assert {"source_key": "pol_b", "logical_id": 2, "correction_id": 77} in out["blocked"]
        # 정책 테이블엔 직접 안 쓴다 — 가져오기 모듈은 읽기만
        c.execute.assert_not_awaited()
        assert all(s.lstrip().upper().startswith("SELECT") for s in _sql_texts(c.fetch))

    @pytest.mark.asyncio
    async def test_file_changed_since_preview_is_conflict(self):
        content = self._content()
        c, (p1, p2) = self._patches()
        with p1, p2:
            prev = await json_io.preview("ns", content)
        fake = _fake_improvement([])
        with patch.object(sys.modules["service.improvement"], "service", fake, create=True):
            with pytest.raises(json_io.ImportConflict):
                await json_io.apply("ns", ADMIN, content + " ", prev["doc_hash"], prev["plan_hash"])
            moved = [_cur("pol_a", 1, "그 사이 승인된 원문", "비고 A")] + CURRENT[1:]
            c, (p1, p2) = self._patches(moved)
            with p1, p2, pytest.raises(json_io.ImportConflict):
                await json_io.apply("ns", ADMIN, content, prev["doc_hash"], prev["plan_hash"])
        fake.create_policy_proposal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_router_maps_conflict_to_409_and_routes_are_admin_only(self):
        with patch.object(prouter.json_io, "apply", AsyncMock(side_effect=json_io.ImportConflict("바뀜"))):
            with pytest.raises(HTTPException) as e:
                await prouter.apply_policy_json(prouter.JsonApplyBody(namespace="ns", content="x", doc_hash="a", plan_hash="b"), user=ADMIN)
        assert e.value.status_code == 409
        for fn in (prouter.export_policy_json, prouter.preview_policy_json, prouter.apply_policy_json):
            assert inspect.signature(fn).parameters["user"].default.dependency is prouter.get_current_admin


class TestJsonImportProposal:
    """가져오기가 부르는 정정 제안 — 직접 수정처럼 받은 안 그대로(AI 없음), 출처 json_import, 정책 테이블엔 안 씀."""

    @pytest.mark.asyncio
    async def test_stored_as_is_with_source(self):
        inserted = {}

        async def fetchrow(sql, *args):
            if "AS chunk_id FROM policy_item" in sql:
                return {"id": args[0], "pipeline_version": svc.PIPELINE_WHOLE, "chunk_id": 200}
            if "FROM policy_item" in sql:
                return {"id": args[0], "policy_name": "p", "category_path": ["가"], "raw_body": "옛 원문", "remark": None}
            if "FROM policy_chunk" in sql:
                return {"chunk_text": "옛 원문"}
            raise AssertionError(sql)

        async def fetchval(sql, *args):
            if "FROM ops_improvement_item o WHERE o.status = 'pending'" in sql:
                return None
            if "INSERT INTO ops_improvement_item" in sql:
                inserted["v"] = args
                return 61
            if "SELECT NOT EXISTS" in sql:
                return False
            raise AssertionError(sql)

        c = _conn(fetchrow=fetchrow, fetchval=fetchval)
        with patch.object(svc, "get_conn", return_value=c), patch.object(svc, "resolve_namespace_id", AsyncMock(return_value=1)), \
             patch.object(svc.draft_mod, "draft_correction", AsyncMock()) as ai:
            out = await svc.create_policy_proposal("ns", ADMIN, item_id=10, mode="json_import",
                                                   proposed={"raw_body": "새 원문", "remark": ""}, user_input="JSON 가져오기")
        ai.assert_not_awaited()
        v = inserted["v"]
        assert v[:9] == ("correction", "json_import", 1, None, 1, "policy_narrative", 10, 200, "JSON 가져오기")
        assert json.loads(v[10]) == {"chunk_text": "새 원문", "raw_body": "새 원문", "remark": ""}
        assert out["source"] == "json_import" and out["id"] == 61
        assert all("policy_item" not in s for s in _sql_texts(c.execute))

    @pytest.mark.asyncio
    async def test_chat_create_schema_does_not_accept_json_import(self):
        schemas = _real("service.improvement.schemas", "service/improvement/schemas.py")
        with pytest.raises(Exception):
            schemas.CorrectionCreate(namespace="ns", target_type="policy_narrative", target_id=1, source="json_import",
                                     proposed={"raw_body": "x"})


def teardown_module(module):
    for k, v in _saved.items():
        if v is not None:
            setattr(_pkg, k, v)
