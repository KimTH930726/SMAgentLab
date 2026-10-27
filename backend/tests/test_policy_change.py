"""정책 추가·폐기·분류 옮기기 제안(C3 2단계, 10/27) — 검사 규칙, 골든 영향 세기, 승인 반영의 불변식.
옮기기: 조각 유실·중복 0(원래 조각 수 = 새 조각 수, 글 그대로, 임베딩만 새것), 원천 키·logical_id 그대로, 원본을 먼저 내림.
추가: 조각 1개 = 원문, 원천 키 sys:<logical_id>. 폐기: 상태만(지우지 않음). conftest가 service 패키지를 가짜로 바꿔 두므로 파일 경로로 로드."""
import importlib.util as _ilu
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).resolve().parent.parent
sys.modules.setdefault("service.improvement", MagicMock())


def _load(name, rel):
    spec = _ilu.spec_from_file_location(name, str(_backend / rel))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


if "service.improvement.service" not in sys.modules or isinstance(sys.modules["service.improvement.service"], MagicMock):
    draft = _load("service.improvement.draft", "service/improvement/draft.py")
    sys.modules["service.improvement"].draft = draft
    _load("service.improvement.service", "service/improvement/service.py")
svc = sys.modules["service.improvement.service"]
pc = _load("service.improvement.policy_change", "service/improvement/policy_change.py")


def _conn():
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.transaction = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
    return c


class TestCleanPath:
    def test_trims_and_drops_empty(self):
        assert pc.clean_path([" 배송 ", "", "  ", "기본 배송비"]) == ["배송", "기본 배송비"]

    @pytest.mark.parametrize("bad", [None, [], ["", " "], ["a", "b", "c", "d"], ["x" * 201]])
    def test_rejects(self, bad):
        with pytest.raises(ValueError):
            pc.clean_path(bad)


class TestGoldenUses:
    def test_counts_only_this_key(self, tmp_path):
        golden = {"A-1": {"gold": [{"source_key": "k1"}]}, "A-2": {"gold": [{"source_key": "k2"}, {"source_key": "k1"}]},
                  "A-3": {"gold": [{"source_key": "k2"}]}, "A-4": {"gold": []}}
        report = MagicMock(GOLDEN_DIR=tmp_path, _golden=lambda: golden)
        with patch.dict(sys.modules, {"service.eval.report": report}):
            assert pc.golden_uses("k1") == {"n": 2, "qids": ["A-1", "A-2"], "checked": True}
            assert pc.golden_uses(None) == {"n": 0, "qids": [], "checked": True}

    def test_no_golden_dir_is_not_checked(self, tmp_path):
        report = MagicMock(GOLDEN_DIR=tmp_path / "없음", _golden=lambda: {"X": {"gold": [{"source_key": "k1"}]}})
        with patch.dict(sys.modules, {"service.eval.report": report}):
            assert pc.golden_uses("k1") == {"n": 0, "qids": [], "checked": False}


class TestCreateValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kw, msg", [
        ({"change": "retire", "target_id": 1, "reason": " "}, "폐기 이유"),
        ({"change": "add", "policy_name": "", "raw_body": "본문", "category_path": ["a"]}, "이름과 원문"),
        ({"change": "add", "policy_name": "n", "raw_body": "본문", "category_path": []}, "분류"),
        ({"change": "nope"}, "알 수 없는"),
    ])
    async def test_rejects_before_writing(self, kw, msg):
        conn = _conn()
        conn.fetchval = AsyncMock(return_value=None)
        base = {"target_id": None, "policy_name": None, "category_path": None, "raw_body": None, "remark": None, "reason": None}
        with patch.object(pc, "get_conn", return_value=conn), patch.object(pc, "resolve_namespace_id", AsyncMock(return_value=3)), \
                patch.object(pc, "_write_item", AsyncMock()) as write:
            with pytest.raises(ValueError, match=msg):
                await pc.create("ns", {"id": 1}, **{**base, **kw})
        write.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_move_to_same_path_rejected(self):
        conn = _conn()
        conn.fetchrow = AsyncMock(return_value={"id": 5, "logical_id": 5, "policy_name": "p", "category_path": ["a", "b"],
                                                "version": 1, "source_key": "k"})
        with patch.object(pc, "get_conn", return_value=conn), patch.object(pc, "resolve_namespace_id", AsyncMock(return_value=3)):
            with pytest.raises(ValueError, match="지금 분류와 같아요"):
                await pc.create("ns", {"id": 1}, change="move", target_id=5, policy_name=None, category_path=[" a", "b "],
                                raw_body=None, remark=None, reason=None)

    @pytest.mark.asyncio
    async def test_pending_on_same_policy_is_conflict(self):
        """폐기 제안 — 그 정책에 대기 정정이 있으면 원장에 쓰지 않고 409(그 번호)."""
        conn = _conn()
        conn.fetchrow = AsyncMock(return_value={"id": 5, "logical_id": 5, "policy_name": "p", "category_path": ["a"],
                                                "version": 1, "source_key": None})
        conn.fetch = AsyncMock(return_value=[])
        with patch.object(pc, "get_conn", return_value=conn), patch.object(pc, "resolve_namespace_id", AsyncMock(return_value=3)), \
                patch.object(pc, "_claim_policy_slot", AsyncMock(return_value=77)), patch.object(pc, "_write_item", AsyncMock()) as write, \
                patch.object(pc, "golden_uses", return_value={"n": 0, "qids": [], "checked": True}):
            with pytest.raises(pc.ConflictError) as e:
                await pc.create("ns", {"id": 1}, change="retire", target_id=5, policy_name=None, category_path=None,
                                raw_body=None, remark=None, reason="중복")
        assert e.value.existing_id == 77
        write.assert_not_awaited()


class TestApproveGuards:
    @pytest.mark.asyncio
    async def test_shown_differs_from_stored_is_conflict(self):
        with pytest.raises(pc.ConflictError):
            await pc.approve({"id": 1, "target_type": pc.RETIRE, "namespace_id": 3, "target_id": 5}, {"id": 9},
                             {"retire": False}, {"retire": True})

    @pytest.mark.asyncio
    async def test_empty_stored_rejected(self):
        with pytest.raises(ValueError):
            await pc.approve({"id": 1, "target_type": pc.RETIRE, "namespace_id": 3, "target_id": 5}, {"id": 9}, None, None)


def _old(**kw):
    return {"id": 10, "namespace_id": 3, "system_key": "", "policy_name": "무료 배송 기준", "raw_body": "3만 원 이상 무료",
            "remark": "비고", "source_file": "f.docx", "source_sheet": None, "source_row": 7, "content_hash": "h", "logical_id": 4,
            "parse_status": "parsed", "unresolved_segments": None, "pipeline_version": "v0_whole", "source_key": "pol_x",
            "status": "active", **kw}


class TestApplyMove:
    @pytest.mark.asyncio
    async def test_invariants(self):
        """조각 유실·중복 0 · 글 그대로 · 임베딩은 새 분류로 만든 것 · logical_id·원천 키 유지 · 원본을 새 버전보다 먼저 내림."""
        conn = _conn()
        chunks = [{"id": 101, "chunk_text": "조각 A", "chunk_idx": 0}, {"id": 102, "chunk_text": "조각 B", "chunk_idx": 1}]
        conn.fetchrow = AsyncMock(return_value=_old())
        conn.fetch = AsyncMock(return_value=chunks)
        order: list[str] = []
        conn.execute = AsyncMock(side_effect=lambda sql, *a: order.append(sql.split()[0] + (" deprecate" if "deprecated" in sql else "")) or "OK")
        inserted: dict = {}

        async def fetchval(sql, *a):
            if "SELECT 1" in sql:
                return None                     # 같은 이름 없음
            if "MAX(version)" in sql:
                return 2
            order.append("INSERT item")
            inserted["args"] = a
            return 55
        conn.fetchval = AsyncMock(side_effect=fetchval)
        conn.executemany = AsyncMock()
        prepared = {101: ("조각 A", [0.1]), 102: ("조각 B", [0.2])}
        new_id = await pc._apply_move(conn, 3, 10, ["배송", "새 분류"], {"id": 9}, prepared)
        assert new_id == 55
        assert order.index("UPDATE deprecate") < order.index("INSERT item")
        a = inserted["args"]
        assert a[2] == ["배송", "새 분류"] and a[10] == 4 and a[11] == 2 and a[12] == 10 and a[-1] == "pol_x" and a[4] == "3만 원 이상 무료"
        rows = conn.executemany.await_args.args[1]
        assert sorted((r[1], r[3]) for r in rows) == [("조각 A", 0), ("조각 B", 1)]   # 원래 조각 그대로, 하나도 빠지거나 겹치지 않음
        assert {r[2] for r in rows} == {str([0.1]), str([0.2])} and all(r[0] == 55 for r in rows)

    @pytest.mark.asyncio
    async def test_chunks_changed_since_prepare_aborts(self):
        conn = _conn()
        conn.fetchrow = AsyncMock(return_value=_old())
        conn.fetch = AsyncMock(return_value=[{"id": 101, "chunk_text": "바뀐 조각", "chunk_idx": 0}])
        conn.execute = AsyncMock()
        with pytest.raises(ValueError, match="승인 준비 중"):
            await pc._apply_move(conn, 3, 10, ["x"], {"id": 9}, {101: ("조각 A", [0.1])})
        conn.execute.assert_not_awaited()   # 아무것도 안 내림

    @pytest.mark.asyncio
    async def test_not_current_aborts(self):
        conn = _conn()
        conn.fetchrow = AsyncMock(return_value=_old(status="deprecated"))
        with pytest.raises(ValueError, match="현행이 아니"):
            await pc._apply_move(conn, 3, 10, ["x"], {"id": 9}, {})


class TestApplyAddRetire:
    @pytest.mark.asyncio
    async def test_add_one_chunk_and_system_key(self):
        conn = _conn()
        conn.fetchval = AsyncMock(side_effect=[None, 70])   # 같은 이름 없음 → 새 id
        conn.execute = AsyncMock(return_value="OK")
        p = {"policy_name": "교환 횟수", "category_path": ["교환"], "raw_body": "2회까지", "remark": None}
        assert await pc._apply_add(conn, 3, p, {"id": 9}, [0.3]) == 70
        sqls = [c.args for c in conn.execute.await_args_list if "advisory" not in c.args[0]]
        assert sqls[0][1:] == (70, "sys:")
        assert sqls[1][1:] == (70, "2회까지", str([0.3]))
        assert len([s for s in sqls if "policy_chunk" in s[0]]) == 1

    @pytest.mark.asyncio
    async def test_add_duplicate_name_aborts(self):
        conn = _conn()
        conn.fetchval = AsyncMock(return_value=1)
        conn.execute = AsyncMock()
        with pytest.raises(ValueError, match="같은 이름"):
            await pc._apply_add(conn, 3, {"policy_name": "n", "category_path": ["a"], "raw_body": "b"}, {"id": 9}, [0.1])
        assert all("advisory" in c.args[0] for c in conn.execute.await_args_list)   # 잠금 말고는 아무것도 안 씀

    @pytest.mark.asyncio
    async def test_retire_only_changes_status(self):
        conn = _conn()
        conn.fetchrow = AsyncMock(return_value=_old())
        conn.execute = AsyncMock(return_value="UPDATE 1")
        assert await pc._apply_retire(conn, 3, 10) == 10
        (sql, *args), = [c.args for c in conn.execute.await_args_list]
        assert sql.startswith("UPDATE policy_item SET status = 'deprecated'") and "DELETE" not in sql and args == [10]


class TestServiceWiring:
    def test_open_targets_include_retire_move_not_add(self):
        assert set(svc.POLICY_OPEN_TARGETS) == {"policy_param", "policy_narrative", "policy_retire", "policy_move"}
        assert all(svc.KIND_BY_TARGET[t] == "correction" for t in svc.POLICY_CHANGE_TARGETS)

    @pytest.mark.asyncio
    async def test_approve_routes_change_targets(self):
        conn = _conn()
        row = {"id": 1, "status": "pending", "target_type": "policy_retire", "proposed": json.dumps({"retire": True}),
               "namespace_id": 3, "target_id": 5}
        conn.fetchrow = AsyncMock(return_value=row)
        with patch.object(svc, "get_conn", return_value=conn), patch.object(pc, "approve", AsyncMock(return_value={"ok": 1})) as ap:
            assert await svc.approve(1, {"id": 9}, {"retire": True}) == {"ok": 1}
        assert ap.await_args.args[3] == {"retire": True}


class TestReviewFixes:
    """10/27 독립 리뷰 L1·L2 — 왜 처음 테스트가 못 잡았나: 추가는 잠금이 없는 경로라 '동시 승인'을 사례로 안 봤고, 대상 변경 API는 화면에 버튼이 없어 안 봤다."""

    @pytest.mark.asyncio
    async def test_add_locks_before_name_check(self):
        conn = _conn()
        calls: list[str] = []
        conn.execute = AsyncMock(side_effect=lambda sql, *a: calls.append("lock" if "advisory" in sql else "write") or "OK")

        async def fetchval(sql, *a):
            calls.append("check" if "SELECT 1" in sql else "insert")
            return None if "SELECT 1" in sql else 70
        conn.fetchval = AsyncMock(side_effect=fetchval)
        await pc._apply_add(conn, 3, {"policy_name": "n", "category_path": ["a"], "raw_body": "b"}, {"id": 9}, [0.1])
        assert calls[:2] == ["lock", "check"]   # 같은 (컬렉션·분류·이름) 잠금을 잡은 뒤에 "같은 이름 없음"을 본다
        assert conn.execute.await_args_list[0].args[1:] == (pc._ADD_LOCK_KEY, json.dumps([3, ["a"], "n"], ensure_ascii=False))

    @pytest.mark.asyncio
    async def test_pending_same_add_is_conflict(self):
        conn = _conn()
        conn.fetchval = AsyncMock(side_effect=[None, 41])   # 같은 이름 현행 없음 → 대기 중인 같은 추가 제안 #41
        with patch.object(pc, "get_conn", return_value=conn), patch.object(pc, "resolve_namespace_id", AsyncMock(return_value=3)), \
                patch.object(pc, "_write_item", AsyncMock()) as write:
            with pytest.raises(pc.ConflictError) as e:
                await pc.create("ns", {"id": 1}, change="add", target_id=None, policy_name="n", category_path=["a"],
                                raw_body="b", remark=None, reason=None)
        assert e.value.existing_id == 41
        write.assert_not_awaited()
        sql, *args = conn.fetchval.await_args_list[1].args   # 대기 중복 검사 — 종류·컬렉션·이름·분류(jsonb)로
        assert "target_type = $1" in sql and "proposed->'category_path' = $4::jsonb" in sql and "status = 'pending'" in sql
        assert args == [pc.ADD, 3, "n", '["a"]']

    @pytest.mark.asyncio
    @pytest.mark.parametrize("t", ["policy_add", "policy_retire", "policy_move"])
    async def test_retarget_refuses_change_items(self, t):
        conn = _conn()
        conn.fetchrow = AsyncMock(return_value={"id": 1, "status": "pending", "target_type": t, "namespace_id": 3, "message_id": None})
        with patch.object(svc, "get_conn", return_value=conn):
            with pytest.raises(ValueError, match="대상을 바꿀 수 없어요"):
                await svc.retarget(1, {"id": 9}, "missing")


    @pytest.mark.asyncio
    async def test_move_takes_slot_lock_with_destination(self):
        """옮기기도 옮길 자리(새 분류·이름)를 잠근 뒤 같은 이름을 본다 — 추가와 동시 승인돼 같은 자리에 2개가 생기지 않게(재리뷰)."""
        conn = _conn()
        conn.fetchrow = AsyncMock(return_value=_old())
        conn.fetch = AsyncMock(return_value=[{"id": 101, "chunk_text": "조각 A", "chunk_idx": 0}])
        conn.execute = AsyncMock(return_value="OK")
        conn.fetchval = AsyncMock(return_value=1)   # 같은 이름 있음 → 중단
        with pytest.raises(ValueError, match="같은 이름"):
            await pc._apply_move(conn, 3, 10, ["배송", "새"], {"id": 9}, {101: ("조각 A", [0.1])})
        assert conn.execute.await_args_list[0].args[1:] == (pc._ADD_LOCK_KEY, json.dumps([3, ["배송", "새"], "무료 배송 기준"], ensure_ascii=False))
        assert len(conn.execute.await_args_list) == 1   # 잠금 말고는 아무것도 안 씀(원본도 그대로)
