"""정책서 화면의 수정 제안 + 정정 요청 [직접 수정](v0 재구축 2단계, 2026-10-21).

지키는 약속:
- 정책 화면의 직접 수정은 받은 원문 그대로 원장에 1건(AI 호출 없음), AI 제안은 한 줄 요청 → AI 초안. 정책 테이블엔 아무것도 안 쓴다.
- 같은 정책에 대기 중 정정이 있으면 새 제안을 만들지 않고 409(그 정정 번호를 같이).
- [직접 수정] 저장은 승인과 같은 검사(필수 칸·【확인 필요】·원문과 같음) + 대기 중만 + 누가 고쳤는지 기록.
- 비고만 바꾼 안도 "바뀐 안"이고, 승인하면 새 버전 비고가 바뀐다(수정안에 비고가 없으면 그대로).
conftest가 service 패키지를 MagicMock으로 바꿔 두므로 파일 경로로 로드한다(test_improvement.py와 같은 방식).
"""
import importlib.util as _ilu
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_backend = Path(__file__).resolve().parent.parent
sys.modules.setdefault("service.improvement", MagicMock())


def _load(name, rel):
    spec = _ilu.spec_from_file_location(name, str(_backend / rel))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


draft = _load("service.improvement.draft", "service/improvement/draft.py")
sys.modules["service.improvement"].draft = draft
svc = _load("service.improvement.service", "service/improvement/service.py")
_load("service.improvement.schemas", "service/improvement/schemas.py")
sys.modules["service.improvement"].service = svc
router = _load("service.improvement.router", "service/improvement/router.py")
schemas = sys.modules["service.improvement.schemas"]

USER = {"id": 9, "username": "담당", "role": "user", "part_id": 1}
WHOLE = svc.PIPELINE_WHOLE


def _conn():
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.transaction = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
    c.execute = AsyncMock(return_value="UPDATE 1")
    return c


class _PolicyDB:
    """정책 1건(v0, 조각 1개)과 원장을 흉내 — SQL 글로 어느 조회인지 가른다."""

    def __init__(self, pending=None, raw="반품은 7일 이내", remark="비고 A", whole=True):
        self.pending, self.raw, self.remark, self.whole = pending, raw, remark, whole
        self.gone = False
        self.inserted = None
        self.conn = _conn()
        self.conn.fetchrow = AsyncMock(side_effect=self.fetchrow)
        self.conn.fetchval = AsyncMock(side_effect=self.fetchval)

    async def fetchrow(self, sql, *args):
        if "AS chunk_id FROM policy_item" in sql:
            return {"id": args[0], "pipeline_version": WHOLE if self.whole else "r1-x", "chunk_id": 200}
        if "FROM policy_item" in sql:
            return {"id": args[0], "policy_name": "반품 기한", "category_path": ["반품"], "raw_body": self.raw,
                    "remark": self.remark}
        if "FROM policy_chunk" in sql:
            return {"chunk_text": self.raw}
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        if "FROM ops_improvement_item o WHERE o.status = 'pending'" in sql:
            return self.pending
        if "INSERT INTO ops_improvement_item" in sql:
            self.inserted = args
            return 55
        if "SELECT NOT EXISTS (SELECT 1 FROM policy_item" in sql:   # 잠금 안 재확인 — 아직 검색 대상
            return self.gone
        raise AssertionError(sql)


def _patched(db, drafted=None):
    return (patch.object(svc, "get_conn", return_value=db.conn),
            patch.object(svc, "resolve_namespace_id", AsyncMock(return_value=1)),
            patch.object(svc.draft_mod, "draft_correction", AsyncMock(return_value=drafted)))


class TestDirectEdit:
    @pytest.mark.asyncio
    async def test_creates_ledger_item_with_given_proposal_and_no_ai(self):
        db = _PolicyDB()
        p1, p2, p3 = _patched(db)
        with p1, p2, p3 as ai:
            out = await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_direct",
                                                   proposed={"raw_body": "반품은 14일 이내", "remark": "비고 B", "x": "버림"},
                                                   user_input=None)
        ai.assert_not_awaited()
        assert out["id"] == 55 and out["source"] == "policy_direct" and out["ai_failed"] is False
        v = db.inserted   # (kind, source, ns, message, reporter, target_type, target_id, sub_id, user_input, original, proposed, cands, verdict)
        assert v[:9] == ("correction", "policy_direct", 1, None, 9, "policy_narrative", 10, 200, None)
        assert json.loads(v[10]) == {"chunk_text": "반품은 14일 이내", "raw_body": "반품은 14일 이내", "remark": "비고 B"}
        assert json.loads(v[9])["remark"] == "비고 A"          # 원문 스냅샷에 비고도
        # 정책 테이블엔 쓰지 않는다 — execute는 잠금뿐
        assert all("policy_item" not in c.args[0] for c in db.conn.execute.await_args_list)
        assert any("pg_advisory_xact_lock" in c.args[0] and c.args[2] == 10 for c in db.conn.execute.await_args_list)

    @pytest.mark.asyncio
    async def test_duplicate_pending_is_conflict_with_existing_id(self):
        db = _PolicyDB(pending=31)
        p1, p2, p3 = _patched(db)
        with p1, p2, p3, pytest.raises(svc.ConflictError) as e:
            await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_direct",
                                             proposed={"raw_body": "새 원문"}, user_input=None)
        assert e.value.existing_id == 31 and "#31" in str(e.value)
        assert db.inserted is None

    @pytest.mark.asyncio
    async def test_unchanged_rejected(self):
        db = _PolicyDB()
        p1, p2, p3 = _patched(db)
        with p1, p2, p3, pytest.raises(ValueError, match="바뀐 내용"):
            await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_direct",
                                             proposed={"raw_body": "반품은 7일 이내", "remark": "비고 A "}, user_input=None)
        assert db.inserted is None

    @pytest.mark.asyncio
    async def test_remark_only_change_is_a_change(self):
        db = _PolicyDB()
        p1, p2, p3 = _patched(db)
        with p1, p2, p3:
            await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_direct",
                                             proposed={"raw_body": "반품은 7일 이내", "remark": ""}, user_input=None)
        assert json.loads(db.inserted[10])["remark"] == ""

    @pytest.mark.asyncio
    async def test_empty_body_rejected(self):
        db = _PolicyDB()
        p1, p2, p3 = _patched(db)
        with p1, p2, p3, pytest.raises(ValueError, match="정책 원문"):
            await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_direct",
                                             proposed={"raw_body": "  "}, user_input=None)


class TestAiSuggestion:
    @pytest.mark.asyncio
    async def test_drafts_with_one_line_and_aligns_chunk_to_body(self):
        db = _PolicyDB()
        p1, p2, p3 = _patched(db, drafted={"chunk_text": "다른 조각", "raw_body": "반품은 14일 이내"})
        with p1, p2, p3 as ai:
            out = await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_ai", proposed=None,
                                                   user_input=" 기한은 14일 ")
        assert ai.await_args.args[0] == "policy_narrative" and ai.await_args.args[2] == "기한은 14일"
        assert db.inserted[1] == "policy_ai" and db.inserted[8] == "기한은 14일" and db.inserted[10] is None
        assert out["proposed"] == {"chunk_text": "반품은 14일 이내", "raw_body": "반품은 14일 이내"}   # v0: 조각 = 원문
        upd = db.conn.execute.await_args_list[-1].args
        assert "SET proposed" in upd[0] and json.loads(upd[1]) == out["proposed"] and upd[2] == 55

    @pytest.mark.asyncio
    async def test_chunk_only_change_becomes_new_body(self):
        db = _PolicyDB()
        p1, p2, p3 = _patched(db, drafted={"chunk_text": "반품은 10일 이내", "raw_body": "반품은 7일 이내"})
        with p1, p2, p3:
            out = await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_ai", proposed=None, user_input="x")
        assert out["proposed"] == {"chunk_text": "반품은 10일 이내", "raw_body": "반품은 10일 이내"}

    @pytest.mark.asyncio
    async def test_late_ai_draft_does_not_overwrite_human_edit(self):
        """AI가 쓰는 사이 담당자가 [직접 수정]으로 안을 넣었으면 늦게 온 초안은 버린다(리뷰 M2)."""
        db = _PolicyDB()
        db.conn.execute = AsyncMock(side_effect=lambda sql, *a: "UPDATE 0" if "SET proposed" in sql else "SELECT 1")
        p1, p2, p3 = _patched(db, drafted={"chunk_text": "반품은 14일 이내", "raw_body": "반품은 14일 이내"})
        with p1, p2, p3:
            out = await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_ai", proposed=None, user_input="x")
        upd = next(c.args[0] for c in db.conn.execute.await_args_list if "SET proposed" in c.args[0])
        assert "proposed IS NULL" in upd and out["proposed"] is None

    @pytest.mark.asyncio
    async def test_ai_failure_keeps_proposal_and_reports(self):
        db = _PolicyDB()
        p1, p2, p3 = _patched(db, drafted=None)
        with p1, p2, p3:
            out = await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_ai", proposed=None, user_input="x")
        assert out["id"] == 55 and out["ai_failed"] is True and out["proposed"] is None

    @pytest.mark.asyncio
    async def test_requires_one_line(self):
        with pytest.raises(ValueError):
            await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_ai", proposed=None, user_input=" ")


class TestCreateRouter:
    @pytest.mark.asyncio
    async def test_policy_source_requires_ownership(self):
        create = AsyncMock()
        with patch.object(router, "check_namespace_ownership", AsyncMock(side_effect=HTTPException(403, "no"))), \
             patch.object(router.service, "create_policy_proposal", create):
            with pytest.raises(HTTPException) as e:
                await router.create_correction(schemas.CorrectionCreate(
                    namespace="ns", target_type="policy_narrative", target_id=10, source="policy_direct",
                    proposed={"raw_body": "x"}), user=USER)
        assert e.value.status_code == 403
        create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_is_409_with_existing_id(self):
        with patch.object(router, "check_namespace_ownership", AsyncMock()), \
             patch.object(router.service, "create_policy_proposal",
                          AsyncMock(side_effect=svc.ConflictError("이미 #31", existing_id=31))):
            with pytest.raises(HTTPException) as e:
                await router.create_correction(schemas.CorrectionCreate(
                    namespace="ns", target_type="policy_narrative", target_id=10, source="policy_ai", user_input="x"), user=USER)
        assert e.value.status_code == 409 and e.value.detail == {"message": "이미 #31", "existing_id": 31}

    @pytest.mark.asyncio
    async def test_chat_report_still_needs_one_line_and_no_ownership(self):
        owner = AsyncMock()
        with patch.object(router, "check_namespace_ownership", owner), \
             patch.object(router.service, "create_item", AsyncMock(return_value={"id": 1})) as create:
            with pytest.raises(HTTPException) as e:
                await router.create_correction(schemas.CorrectionCreate(namespace="ns", target_type="auto", message_id=3), user=USER)
            assert e.value.status_code == 400
            await router.create_correction(schemas.CorrectionCreate(
                namespace="ns", target_type="auto", message_id=3, user_input=" 틀림 "), user=USER)
        owner.assert_not_awaited()                          # 채팅 신고는 누구나(신호일 뿐)
        assert create.await_args.kwargs["user_input"] == "틀림"


def _ledger_item(**kw):
    return {"id": 7, "status": "pending", "target_type": "policy_narrative", "target_id": 10, "target_sub_id": 200,
            "original": json.dumps({"chunk_text": "7일", "raw_body": "7일", "remark": "a"}),
            "proposed": json.dumps({"chunk_text": "14일", "raw_body": "14일"}), "ai_verdict": None} | kw


class TestUpdateProposed:
    def _db(self, item, update_result="UPDATE 1"):
        c = _conn()
        c.fetchrow = AsyncMock(return_value=item)
        c.fetchval = AsyncMock(return_value=WHOLE)
        c.execute = AsyncMock(return_value=update_result)
        return c

    @pytest.mark.asyncio
    async def test_stores_cleaned_and_logs_editor(self):
        c = self._db(_ledger_item(ai_verdict=json.dumps({"verdict": "evidence"})))
        with patch.object(svc, "get_conn", return_value=c):
            out = await svc.update_proposed(7, USER, {"raw_body": "10일", "remark": "b", "junk": 1})
        assert out["proposed"] == {"chunk_text": "10일", "raw_body": "10일", "remark": "b"}
        sql, *args = c.execute.await_args.args
        assert "status = 'pending'" in sql and "target_sub_id IS NOT DISTINCT FROM $6" in sql
        assert json.loads(args[1]) == out["proposed"]
        verdict = json.loads(args[2])
        assert verdict["verdict"] == "evidence" and verdict["edits"][0]["by"] == 9 and verdict["edits"][0]["at"]

    @pytest.mark.parametrize("proposed, msg", [
        ({"raw_body": ""}, "정책 원문"),
        ({"raw_body": "7일【확인 필요: 기한】"}, "확인 필요"),
        ({"raw_body": "7일", "remark": "a"}, "기존 내용과 같아요"),
    ])
    @pytest.mark.asyncio
    async def test_validation_like_approve(self, proposed, msg):
        c = self._db(_ledger_item())
        with patch.object(svc, "get_conn", return_value=c), pytest.raises(ValueError, match=msg):
            await svc.update_proposed(7, USER, proposed)
        c.execute.assert_not_awaited()

    @pytest.mark.parametrize("item", [_ledger_item(status="approved"), _ledger_item(target_type="answer"), None])
    @pytest.mark.asyncio
    async def test_only_pending_with_target(self, item):
        c = self._db(item)
        with patch.object(svc, "get_conn", return_value=c), pytest.raises(ValueError):
            await svc.update_proposed(7, USER, {"raw_body": "10일"})
        c.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_changed_meanwhile_is_conflict(self):
        c = self._db(_ledger_item(), update_result="UPDATE 0")
        with patch.object(svc, "get_conn", return_value=c), pytest.raises(svc.ConflictError):
            await svc.update_proposed(7, USER, {"raw_body": "10일"})

    @pytest.mark.asyncio
    async def test_remark_dropped_when_snapshot_has_no_remark(self):
        """옛 신고(원문 스냅샷에 비고 없음)에 비고 칸을 받으면 버린다 — 모르는 비고를 승인 때 지우지 않게."""
        c = self._db(_ledger_item(original=json.dumps({"chunk_text": "7일", "raw_body": "7일"})))
        with patch.object(svc, "get_conn", return_value=c):
            out = await svc.update_proposed(7, USER, {"raw_body": "10일", "remark": ""})
        assert "remark" not in out["proposed"]

    @pytest.mark.asyncio
    async def test_knowledge_target(self):
        c = self._db(_ledger_item(target_type="knowledge", target_sub_id=None, original=json.dumps({"content": "옛"})))
        with patch.object(svc, "get_conn", return_value=c):
            out = await svc.update_proposed(7, USER, {"content": "새", "remark": "지식엔 없음"})
        assert out["proposed"] == {"content": "새"}
        c.fetchval.assert_not_awaited()                 # 정책이 아니면 v0 여부를 안 본다

    @pytest.mark.asyncio
    async def test_router_checks_owner_first(self):
        update = AsyncMock()
        with patch.object(router.service, "item_namespace", AsyncMock(return_value="남의 영역")), \
             patch.object(router, "check_namespace_ownership", AsyncMock(side_effect=HTTPException(403, "no"))), \
             patch.object(router.service, "update_proposed", update):
            with pytest.raises(HTTPException) as e:
                await router.update_correction_proposed(7, schemas.ProposedBody(proposed={"raw_body": "x"}), user=USER)
        assert e.value.status_code == 403
        update.assert_not_awaited()


class TestRemarkOnApprove:
    @staticmethod
    def _old(**kw):
        return {"id": 10, "status": "active", "system_key": "", "category_path": [], "policy_name": "p", "raw_body": "7일",
                "remark": "옛 비고", "source_file": None, "source_sheet": None, "source_row": 3, "content_hash": "h",
                "logical_id": 10, "version": 1, "parse_status": "parsed", "unresolved_segments": None,
                "pipeline_version": WHOLE, "source_key": "pol_x"} | kw

    async def _insert_args(self, proposed):
        conn = _conn()
        conn.fetchrow = AsyncMock(return_value=self._old())
        conn.fetch = AsyncMock(side_effect=[[{"id": 200, "chunk_text": "7일", "chunk_idx": 0}], []])
        conn.fetchval = AsyncMock(side_effect=[2, 11])
        item = {"target_id": 10, "target_type": "policy_narrative", "target_sub_id": 200}
        rewrites = svc._policy_chunk_rewrites(self._old(), [{"id": 200, "chunk_text": "7일", "chunk_idx": 0}], item, proposed)
        await svc._apply_policy(conn, 1, item, proposed, {"id": 1}, {c: (t, [0.1]) for c, t in rewrites.items()})
        return next(c.args for c in conn.fetchval.await_args_list if "INSERT INTO policy_item" in c.args[0])

    @pytest.mark.asyncio
    async def test_remark_in_proposal_is_applied(self):
        ins = await self._insert_args({"chunk_text": "7일", "raw_body": "7일", "remark": " 새 비고 "})
        assert ins[5] == "7일" and ins[6] == "새 비고"

    @pytest.mark.asyncio
    async def test_empty_remark_clears(self):
        ins = await self._insert_args({"chunk_text": "14일", "raw_body": "14일", "remark": ""})
        assert ins[5] == "14일" and ins[6] is None

    @pytest.mark.asyncio
    async def test_no_remark_key_keeps_old(self):
        ins = await self._insert_args({"chunk_text": "14일", "raw_body": "14일"})
        assert ins[6] == "옛 비고"

    @pytest.mark.asyncio
    async def test_approve_accepts_remark_only_change(self):
        item = _ledger_item(proposed=json.dumps({"chunk_text": "7일", "raw_body": "7일", "remark": "b"}), namespace_id=1)
        locked = {k: item[k] for k in ("status", "proposed", "target_type", "target_id", "target_sub_id")}
        c = _conn()
        c.fetchrow = AsyncMock(side_effect=[item, locked])
        c.fetchval = AsyncMock(return_value="ns")
        apply = AsyncMock(return_value=99)
        with patch.object(svc, "get_conn", return_value=c), \
             patch.object(svc, "_prepare_policy", AsyncMock(return_value={})), \
             patch.object(svc, "_apply_policy", apply), \
             patch.object(svc, "_close_superseded", AsyncMock(return_value=0)):
            out = await svc.approve(7, USER, json.loads(item["proposed"]))
        assert out["status"] == "approved" and apply.await_args.args[3]["remark"] == "b"


class TestRetargetSameTargetWithoutCandidates:
    @pytest.mark.asyncio
    async def test_ai_draft_again_for_policy_proposal(self):
        """정책 화면 제안은 답변 근거 후보가 없다 — AI 초안이 실패했을 때 [AI 초안 만들기](같은 대상 다시)가 돼야 한다."""
        c = _conn()
        c.fetchrow = AsyncMock(return_value={"status": "pending", "namespace_id": 1, "message_id": None, "candidates": "[]",
                                             "user_input": "기한은 14일", "ai_verdict": None, "target_type": "policy_narrative",
                                             "target_id": 10, "target_sub_id": 200})
        loaded = AsyncMock(side_effect=lambda conn, ns, t, tid, sub, msg: {"raw_body": "7일"} if t == "policy_narrative" else {})
        drafted = AsyncMock(return_value={"chunk_text": "14일", "raw_body": "14일"})
        with patch.object(svc, "get_conn", return_value=c), patch.object(svc, "_load_original", loaded), \
             patch.object(svc.draft_mod, "draft_correction", drafted):
            out = await svc.retarget(7, USER, "pn-200")
        assert out["target_type"] == "policy_narrative" and out["proposed"]["raw_body"] == "14일"
        assert drafted.await_args.args[2] == "기한은 14일"


# ── 정책당 대기 정정 1건(10/21 리뷰 M1) — 정책 화면 제안·채팅 신고(직접/자동)·AI 추정·대상 변경 모든 길 ──
# 유니크 인덱스는 "같은 신고자" 단위라, 다른 길로 같은 정책에 두 번째 대기 건이 생기면 하나를 승인할 때 다른 하나(사람이 쓴 수정안)가
# _close_superseded로 말없이 닫혔다. 사례 하나가 아니라 "어떤 순서로 어떤 길을 타도 정책당 대기 정정 ≤ 1"을 무작위 순서로 확인한다.

_POL = {10: 101, 20: 201}   # 정책 항목 id → 서술 조각 id
_CANDS_ALL = [{"key": f"pn-{sub}", "target_type": "policy_narrative", "target_id": pid, "target_sub_id": sub,
               "label": f"정책 · {pid}", "original": {"policy_name": str(pid), "chunk_text": "x", "raw_body": "x", "remark": None}}
              for pid, sub in _POL.items()] + [
              {"key": "k-1", "target_type": "knowledge", "target_id": 1, "target_sub_id": None, "label": "문서 #1",
               "original": {"content": "c"}}]


class _Ledger:
    """ops_improvement_item을 메모리로 흉내 — 서비스가 보내는 SQL 글로 어느 동작인지 가르고, 유니크 인덱스(같은 신고자·같은 대상)도 지킨다."""

    _COLS = ("kind", "source", "namespace_id", "message_id", "reporter_user_id", "target_type", "target_id",
             "target_sub_id", "user_input", "original", "proposed", "candidates", "ai_verdict")

    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.next_id = 1
        self.events: list[tuple] = []   # 잠금·쓰기 순서 기록 — ("tx", n) 트랜잭션 시작, ("row", id) 행 잠금, ("adv", 정책 id), ("write", id)
        self.tx = 0
        self.conn = _conn()
        self.conn.fetchval = AsyncMock(side_effect=self.fetchval)
        self.conn.fetchrow = AsyncMock(side_effect=self.fetchrow)
        self.conn.execute = AsyncMock(side_effect=self.execute)
        led = self

        class _Tx:
            async def __aenter__(self):
                led.tx += 1
                led.events.append(("tx", led.tx))

            async def __aexit__(self, *exc):
                led.events.append(("tx_end", led.tx))
                return False
        self.conn.transaction = MagicMock(side_effect=lambda: _Tx())

    def add(self, **kw) -> int:
        rid = self.next_id
        self.next_id += 1
        cands = [{k: c[k] for k in ("key", "target_type", "target_id", "target_sub_id", "label")} for c in _CANDS_ALL]
        self.rows[rid] = {"id": rid, "status": "pending", "kind": "answer_signal", "source": "chat_answer_wrong",
                          "namespace_id": 1, "message_id": None, "reporter_user_id": None, "target_type": "auto",
                          "target_id": None, "target_sub_id": None, "user_input": None, "original": "{}",
                          "proposed": None, "candidates": json.dumps(cands, ensure_ascii=False), "ai_verdict": None} | kw
        self._check_unique(rid)
        return rid

    def _check_unique(self, rid):
        r = self.rows[rid]
        if r["status"] != "pending" or r["kind"] != "correction":
            return
        key = (r["reporter_user_id"], r["target_type"], r["target_id"], r["target_sub_id"] or 0)
        for o in self.rows.values():
            if o["id"] != rid and o["status"] == "pending" and o["kind"] == "correction" and \
                    (o["reporter_user_id"], o["target_type"], o["target_id"], o["target_sub_id"] or 0) == key:
                raise svc.asyncpg.UniqueViolationError("uq_improvement_pending_correction")

    def _set(self, rid, **kw):
        before = dict(self.rows[rid])
        self.rows[rid].update(kw)
        try:
            self._check_unique(rid)
        except svc.asyncpg.UniqueViolationError:
            self.rows[rid] = before
            raise

    def pending_per_policy(self) -> dict:
        out: dict = {}
        for r in self.rows.values():
            if r["status"] == "pending" and r["target_type"] in svc._POLICY_TARGETS:
                out[r["target_id"]] = out.get(r["target_id"], 0) + 1
        return out

    async def fetchval(self, sql, *a):
        if "FROM ops_improvement_item o WHERE o.status = 'pending'" in sql:
            item_id, targets, exclude = a
            ids = [r["id"] for r in self.rows.values() if r["status"] == "pending" and r["target_type"] in targets
                   and r["target_id"] == item_id and r["id"] != exclude]
            return min(ids) if ids else None
        if "SELECT EXISTS" in sql:
            return True
        if "SELECT NOT EXISTS (SELECT 1 FROM policy_item" in sql:   # 정책은 계속 검색 대상
            return False
        if "SELECT name FROM ops_namespace" in sql:
            return "ns"
        if "kind = 'answer_signal' AND status = 'pending'" in sql:
            ids = [r["id"] for r in self.rows.values() if r["message_id"] == a[0] and r["reporter_user_id"] == a[1]
                   and r["kind"] == "answer_signal" and r["status"] == "pending"]
            if ids and "FOR UPDATE" in sql:
                self.events.append(("row", max(ids)))
            return max(ids) if ids else None
        if "INSERT INTO ops_improvement_item" in sql:
            rid = self.next_id
            self.rows[rid] = {"id": rid, "status": "pending"} | dict(zip(self._COLS, a))
            self.next_id += 1
            try:
                self._check_unique(rid)
            except svc.asyncpg.UniqueViolationError:
                del self.rows[rid]
                raise
            self.events.append(("write", rid))
            return rid
        if "UPDATE ops_improvement_item" in sql and "RETURNING id" in sql:   # 신호 건 채우기
            self._set(a[-1], **dict(zip(self._COLS, a[:-1])))
            self.events.append(("write", a[-1]))
            return a[-1]
        raise AssertionError(sql)

    async def fetchrow(self, sql, *a):
        if "AS chunk_id FROM policy_item" in sql:
            return {"id": a[0], "pipeline_version": WHOLE, "chunk_id": _POL[a[0]]}
        if "FROM ops_improvement_item WHERE id = $1" in sql:   # SELECT * / … FOR UPDATE
            r = self.rows.get(a[0])
            if r and "FOR UPDATE" in sql:
                self.events.append(("row", a[0]))
            return dict(r) if r else None
        raise AssertionError(sql)

    async def execute(self, sql, *a):
        if "pg_advisory_xact_lock" in sql:
            assert a[0] == svc._POLICY_LOCK_KEY
            self.events.append(("adv", a[1]))
            return "SELECT 1"
        if "DELETE FROM ops_improvement_item" in sql:
            self.rows.pop(a[0], None)
            return "DELETE 1"
        if "SET kind = $2, target_type = $3" in sql:          # retarget — 처음 읽은 대상 그대로일 때만
            r = self.rows[a[0]]
            if r["status"] != "pending" or (r["target_type"], r["target_id"], r["target_sub_id"]) != tuple(a[8:11]):
                return "UPDATE 0"
            self._set(a[0], kind=a[1], target_type=a[2], target_id=a[3], target_sub_id=a[4], original=a[5],
                      proposed=a[6], ai_verdict=a[7])
            self.events.append(("write", a[0]))
            return "UPDATE 1"
        if "SET kind = 'answer_signal', target_type = $2" in sql:   # analyze_signal
            r = self.rows[a[0]]
            if r["status"] != "pending" or r["user_input"] is not None or r["ai_verdict"] is not None:
                return "UPDATE 0"
            self._set(a[0], target_type=a[1], target_id=a[2], target_sub_id=a[3], original=a[4], proposed=a[5],
                      ai_verdict=a[6])
            self.events.append(("write", a[0]))
            return "UPDATE 1"
        if "SET proposed = $1::jsonb WHERE id = $2" in sql:
            if a[1] in self.rows:
                self.rows[a[1]]["proposed"] = a[0]
            return "UPDATE 1"
        if "SET status = 'approved'" in sql:                 # 승인 반영
            self.rows[a[0]].update(status="approved", proposed=a[1], applied_target_id=a[2])
            return "UPDATE 1"
        if "SET status = 'rejected', decided_by = $4" in sql:   # _close_superseded(진짜 함수가 보낸 SQL)
            item_id, target_id, types = a[0], a[1], a[2]
            hit = [r for r in self.rows.values() if r["status"] == "pending" and r["id"] != item_id
                   and r["target_id"] == target_id and r["target_type"] in types]
            for r in hit:
                r["status"] = "rejected"
            return f"UPDATE {len(hit)}"
        raise AssertionError(sql)


def _orig(_c, _ns, tt, tid, sub, _m):
    if tt in svc._POLICY_TARGETS:
        return {"policy_name": str(tid), "category_path": [], "raw_body": "x", "remark": None, "chunk_text": "x"}
    if tt == "knowledge":
        return {"content": "c"}
    return {"question": "q", "answer": "a"}


def _ident_for(pid):
    async def ident(cands, ctx, user_input):
        c = next(c for c in cands if c["target_id"] == pid and c["target_type"] == "policy_narrative")
        return (c["target_type"], c["target_id"], c["target_sub_id"], c["original"], {"chunk_text": "y", "raw_body": "y"},
                {"verdict": "evidence", "method": "llm", "reason": "r", "key": c["key"], "label": c["label"]})
    return ident


def _no_opinion_for(pid):
    async def analyze(q, a, cands):
        idx = next(i for i, c in enumerate(cands) if c["target_id"] == pid and c["target_type"] == "policy_narrative")
        return {"verdict": "evidence", "index": idx, "reason": "r", "wrong_part": None, "fix_summary": None, "proposed": None}
    return analyze


class _Patched:
    """원장 흉내 + AI·원문 읽기를 가짜로(pid = AI가 고르는 정책)."""

    def __init__(self, led, pid=10):
        self.ps = [patch.object(svc, "get_conn", return_value=led.conn),
                   patch.object(svc, "resolve_namespace_id", AsyncMock(return_value=1)),
                   patch.object(svc, "_load_original", AsyncMock(side_effect=_orig)),
                   patch.object(svc, "_load_candidates", AsyncMock(return_value=_CANDS_ALL)),
                   patch.object(svc, "_identify", AsyncMock(side_effect=_ident_for(pid))),
                   patch.object(svc.draft_mod, "analyze_without_opinion", AsyncMock(side_effect=_no_opinion_for(pid))),
                   patch.object(svc.draft_mod, "draft_correction", AsyncMock(return_value={"chunk_text": "z", "raw_body": "z"})),
                   patch.object(svc.draft_mod, "draft_review_template", AsyncMock(return_value={"chunk_text": "z", "raw_body": "z"})),
                   # 승인은 진짜 approve·_close_superseded를 타고, 정책·지식 테이블 쓰기와 캐시만 가짜로
                   patch.object(svc, "_prepare_policy", AsyncMock(return_value={})),
                   patch.object(svc, "_apply_policy", AsyncMock(return_value=999)),
                   patch.object(svc, "_apply_knowledge", AsyncMock(return_value=998)),
                   patch.object(sys.modules["service.policy.caches"], "invalidate_policy_caches", AsyncMock()),
                   patch.dict(sys.modules, {"shared.cache": MagicMock(invalidate_namespace=AsyncMock())})]

    def __enter__(self):
        for p in self.ps:
            p.__enter__()

    def __exit__(self, *exc):
        for p in reversed(self.ps):
            p.__exit__(*exc)


async def _direct(pid=10):
    return await svc.create_policy_proposal("ns", USER, item_id=pid, mode="policy_direct",
                                            proposed={"raw_body": "사람이 고친 원문"}, user_input=None)


class TestOnePendingPerPolicyScenario:
    """리뷰가 짚은 그대로: 정책 화면 직접 수정이 검토 중인데 같은 정책으로 다른 길의 정정이 들어온다."""

    @pytest.mark.asyncio
    async def test_chat_auto_report_is_kept_but_not_a_second_policy_correction(self):
        led = _Ledger()
        with _Patched(led, pid=10):
            first = await _direct()
            out = await svc.create_item("ns", {"id": 50}, target_type="auto", target_id=None, target_sub_id=None,
                                        message_id=900, user_input="기한이 틀려요")
        assert led.pending_per_policy() == {10: 1}
        row = led.rows[out["id"]]
        assert row["target_type"] == "auto" and row["kind"] == "answer_signal" and row["user_input"] == "기한이 틀려요"
        verdict = json.loads(row["ai_verdict"])
        assert verdict["duplicate_of"] == first["id"] and f"#{first['id']}" in verdict["reason"]
        assert out["target_type"] == "auto" and out["proposed"] is None   # 대상이 없으니 초안도 안 만든다

    @pytest.mark.asyncio
    async def test_chat_card_report_is_409_with_existing_id_and_signal_kept(self):
        led = _Ledger()
        signal = led.add(message_id=900, reporter_user_id=50)
        with _Patched(led):
            first = await _direct()
            with pytest.raises(svc.ConflictError) as e:
                await svc.create_item("ns", {"id": 50}, target_type="policy_narrative", target_id=10, target_sub_id=101,
                                      message_id=900, user_input="틀려요")
        assert e.value.existing_id == first["id"]
        assert led.pending_per_policy() == {10: 1} and led.rows[signal]["target_type"] == "auto"   # "답변 틀림" 기록은 남음

    @pytest.mark.asyncio
    async def test_retarget_onto_busy_policy_is_409_before_drafting(self):
        led = _Ledger()
        other = led.add(message_id=901, reporter_user_id=51, user_input="u", target_type="missing", kind="missing_knowledge")
        with _Patched(led):
            first = await _direct()
            with pytest.raises(svc.ConflictError) as e:
                await svc.retarget(other, USER, "pn-101")
            svc.draft_mod.draft_correction.assert_not_awaited()   # AI 초안을 쓰기 전에 막는다
            await svc.retarget(other, USER, "pn-201")              # 비어 있는 다른 정책으로는 옮겨진다
        assert e.value.existing_id == first["id"]
        assert led.pending_per_policy() == {10: 1, 20: 1}

    @pytest.mark.asyncio
    async def test_no_opinion_analysis_parks_on_busy_policy(self):
        led = _Ledger()
        signal = led.add(message_id=902, reporter_user_id=52)
        with _Patched(led, pid=10):
            await _direct()
            assert await svc.analyze_signal(signal, 1, 902) is True
        assert led.pending_per_policy() == {10: 1}
        assert led.rows[signal]["target_type"] == "auto" and json.loads(led.rows[signal]["ai_verdict"])["duplicate_of"]

    @pytest.mark.asyncio
    async def test_retarget_within_same_policy_is_not_blocked_by_itself(self):
        led = _Ledger()
        with _Patched(led):
            first = await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_ai", proposed=None, user_input="14일")
            out = await svc.retarget(first["id"], USER, "pn-101")   # [AI 초안 만들기] — 같은 대상 다시
        assert out["target_type"] == "policy_narrative" and led.pending_per_policy() == {10: 1}

    @pytest.mark.asyncio
    async def test_signal_already_on_policy_is_filled_not_blocked_by_itself(self):
        """AI 추정이 이미 이 정책을 골라 둔 신호 건에 한 줄이 오면 그 건을 채운다 — 자기 자신 때문에 막히지 않는다."""
        led = _Ledger()
        signal = led.add(message_id=903, reporter_user_id=53, target_type="policy_narrative", target_id=10, target_sub_id=101)
        with _Patched(led, pid=10):
            out = await svc.create_item("ns", {"id": 53}, target_type="auto", target_id=None, target_sub_id=None,
                                        message_id=903, user_input="7일이 아니라 14일")
        assert out["id"] == signal and led.rows[signal]["target_type"] == "policy_narrative"
        assert led.pending_per_policy() == {10: 1}


class TestLockOrder:
    """정책을 대상으로 정하는 길은 모두 같은 트랜잭션 안에서 "자기 원장 행 잠금 → 정책 잠금 → 쓰기" 순서(10/21 재리뷰 1).
    create_item(신호 행 → 정책)과 analyze_signal·retarget(정책 → 자기 행)의 순서가 엇갈려, "답변 틀림" 직후 한 줄을 보내면
    서로 기다리는 교착으로 한 줄이 사라질 수 있었다. 정책 잠금을 빼면(가드 제거) 여기서도 실패한다."""

    @staticmethod
    def _assert_order(led, rid):
        ev = led.events
        adv = next(i for i, e in enumerate(ev) if e[0] == "adv")
        write = next(i for i, e in enumerate(ev) if e == ("write", rid))
        row = next(i for i, e in enumerate(ev) if e == ("row", rid))
        tx_start = max(i for i, e in enumerate(ev[:row]) if e[0] == "tx")
        assert row < adv < write                                              # 행 → 정책 잠금 → 쓰기
        assert not any(e[0] == "tx_end" for e in ev[tx_start:write])          # 셋이 한 트랜잭션 안

    @pytest.mark.asyncio
    async def test_create_item_fills_signal(self):
        led = _Ledger()
        signal = led.add(message_id=910, reporter_user_id=60)
        with _Patched(led, pid=10):
            await svc.create_item("ns", {"id": 60}, target_type="auto", target_id=None, target_sub_id=None,
                                  message_id=910, user_input="한 줄")
        self._assert_order(led, signal)

    @pytest.mark.asyncio
    async def test_analyze_signal(self):
        led = _Ledger()
        signal = led.add(message_id=911, reporter_user_id=61)
        with _Patched(led, pid=10):
            assert await svc.analyze_signal(signal, 1, 911) is True
        self._assert_order(led, signal)

    @pytest.mark.asyncio
    async def test_retarget(self):
        led = _Ledger()
        other = led.add(message_id=912, reporter_user_id=62, user_input="u", target_type="missing", kind="missing_knowledge")
        with _Patched(led):
            await svc.retarget(other, USER, "pn-101")
        self._assert_order(led, other)

    @pytest.mark.asyncio
    async def test_analyze_signal_skips_when_line_arrived_first(self):
        """한 줄이 먼저 들어와 행이 바뀌었으면 정책 잠금도 잡지 않고 손대지 않는다."""
        led = _Ledger()
        signal = led.add(message_id=913, reporter_user_id=63, user_input="먼저 온 한 줄")
        with _Patched(led, pid=10):
            assert await svc.analyze_signal(signal, 1, 913) is False
        assert not any(e[0] == "adv" for e in led.events) and led.rows[signal]["target_type"] == "auto"

    @pytest.mark.asyncio
    async def test_retarget_target_changed_meanwhile_is_conflict(self):
        """AI 초안을 쓰는 사이 다른 사람이 대상을 바꿨으면 낡은 계산으로 덮지 않는다(10/21 재리뷰 2)."""
        led = _Ledger()
        other = led.add(message_id=914, reporter_user_id=64, user_input="u", target_type="missing", kind="missing_knowledge")

        async def draft_and_meanwhile(*_a, **_k):
            led.rows[other].update(target_type="answer", kind="answer_quality")   # 다른 담당자가 그 사이 바꿈
            return {"chunk_text": "z", "raw_body": "z"}
        with _Patched(led):
            with patch.object(svc.draft_mod, "draft_correction", AsyncMock(side_effect=draft_and_meanwhile)), \
                 pytest.raises(svc.ConflictError, match="대상을 바꾼"):
                await svc.retarget(other, USER, "pn-101")
        assert led.rows[other]["target_type"] == "answer" and led.pending_per_policy() == {}

    @pytest.mark.asyncio
    async def test_policy_retired_before_lock_is_not_proposed(self):
        """정책 화면 제안: 잠금 전 확인 뒤 그 버전이 은퇴했으면(다른 승인) 잠금 안 재확인에서 막는다(10/21 재리뷰 3)."""
        db = _PolicyDB()
        db.gone = True
        p1, p2, p3 = _patched(db)
        with p1, p2, p3, pytest.raises(ValueError, match="그 사이 바뀌었어요"):
            await svc.create_policy_proposal("ns", USER, item_id=10, mode="policy_direct",
                                             proposed={"raw_body": "새 원문"}, user_input=None)
        assert db.inserted is None

    def test_lock_key_distinct_from_source_ledger(self):
        src = (_backend / "agents" / "knowledge_rag" / "ingestion" / "source_ledger.py").read_text(encoding="utf-8")
        assert f"pg_advisory_xact_lock({svc._POLICY_LOCK_KEY}," not in src   # 원문 원장 키와 겹치면 서로의 잠금을 기다린다

    @pytest.mark.asyncio
    async def test_parked_report_has_no_fix_summary(self):
        led = _Ledger()
        with _Patched(led, pid=10):
            await _direct()

            async def ident(cands, ctx, ui):
                return ("policy_narrative", 10, 101, cands[0]["original"], {"chunk_text": "y", "raw_body": "y"},
                        {"verdict": "evidence", "method": "llm", "reason": "r", "fix_summary": "14일로", "key": "pn-101"})
            with patch.object(svc, "_identify", AsyncMock(side_effect=ident)):
                out = await svc.create_item("ns", {"id": 65}, target_type="auto", target_id=None, target_sub_id=None,
                                            message_id=915, user_input="x")
        assert "fix_summary" not in out["ai_verdict"] and out["ai_verdict"]["duplicate_of"] and out["ai_verdict"]["reason"]


class TestOnePendingPerPolicyInvariant:
    """무작위 순서로 모든 생성 경로를 섞어 돌려도 "정책당 대기 정정 ≤ 1" — 그리고 받아 준 채팅 신고(한 줄)는 사라지지 않는다."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("seed", range(25))
    async def test_random_sequences(self, seed):
        import random
        rnd = random.Random(seed)
        led = _Ledger()
        msg = iter(range(1000, 2000))
        accepted_lines, peak = [], 0
        for _ in range(40):
            pid = rnd.choice(list(_POL))
            op = rnd.choice(["policy_direct", "policy_ai", "card", "auto", "signal", "retarget", "close"])
            with _Patched(led, pid=pid):
                try:
                    if op in ("policy_direct", "policy_ai"):
                        await svc.create_policy_proposal(
                            "ns", {"id": rnd.randint(1, 3)}, item_id=pid, mode=op,
                            proposed={"raw_body": f"새 원문 {rnd.random()}"} if op == "policy_direct" else None,
                            user_input="한 줄" if op == "policy_ai" else None)
                    elif op in ("card", "auto"):
                        m, reporter = next(msg), rnd.randint(1, 3)
                        if rnd.random() < 0.5:
                            led.add(message_id=m, reporter_user_id=reporter)   # "답변 틀림"을 먼저 누른 경우
                        line = f"의견 {m}"
                        card = op == "card"
                        out = await svc.create_item(
                            "ns", {"id": reporter}, target_type="policy_narrative" if card else "auto",
                            target_id=pid if card else None, target_sub_id=_POL[pid] if card else None,
                            message_id=m, user_input=line)
                        accepted_lines.append((out["id"], line))
                    elif op == "signal":
                        m = next(msg)
                        sid = led.add(message_id=m, reporter_user_id=rnd.randint(1, 3))
                        await svc.analyze_signal(sid, 1, m)
                    elif op == "retarget":
                        pend = [r["id"] for r in led.rows.values() if r["status"] == "pending"]
                        if pend:
                            await svc.retarget(rnd.choice(pend), USER, f"pn-{_POL[pid]}")
                    else:   # 승인·반려로 자리가 비는 경우 — 승인할 수 있는 건은 진짜 approve(같은 근거 다른 건 자동 종료 포함)로
                        pend = [r["id"] for r in led.rows.values() if r["status"] == "pending"]
                        if pend:
                            rid = rnd.choice(pend)
                            r = led.rows[rid]
                            try:
                                if r["target_type"] not in (*svc._POLICY_TARGETS, "knowledge") or not r["proposed"]:
                                    raise ValueError("승인할 수 없는 건")
                                await svc.approve(rid, USER, None)
                                assert led.rows[rid]["status"] == "approved" and r["target_id"] not in led.pending_per_policy()
                            except (svc.ConflictError, ValueError):
                                r["status"] = "rejected"
                except (svc.ConflictError, ValueError):
                    pass   # 막힌 것(409·잘못된 대상)도 정상 결과 — 불변식만 본다
            peak = max([peak, *led.pending_per_policy().values()])
            assert peak <= 1, (seed, op, led.pending_per_policy())
        for rid, line in accepted_lines:   # 받아 준 신고는 원장에 그 한 줄 그대로 남는다
            assert rid in led.rows and led.rows[rid]["user_input"] == line
        assert peak == 1     # 빈 원장으로 통과하는 약한 검증이 아니게 — 정책 정정이 실제로 생겼다
