"""정책 자동 통과(service/policy/auto_review.py) — 표본 결정론, 사람 승인과 구분, 규칙 정지, 되돌리기, 큐 크기."""
import importlib.util as _ilu
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = _ilu.spec_from_file_location(name, str(_backend / rel))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sys.modules["service.policy"] = MagicMock()
risk = _load("service.policy.risk", "service/policy/risk.py")
sys.modules["service.policy"].risk = risk
ar = _load("service.policy.auto_review", "service/policy/auto_review.py")


def _row(id_, parse_status="parsed", chunks=1, sample=False, prior=False, ns="A"):
    return {"id": id_, "logical_id": id_, "namespace_id": 1, "parse_status": parse_status, "review_sample": sample,
            "review_rule": None, "namespace": ns, "chunk_count": chunks, "unresolved_count": 0, "prior_reject": prior}


def _conn(rows=None, config=None):
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.transaction = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
    c.fetch = AsyncMock(return_value=rows or [])
    c.fetchval = AsyncMock(return_value=json.dumps(config) if config else None)
    c.execute = AsyncMock()
    c.executemany = AsyncMock()
    return c


def _no_sample_id(start=1):
    i = start
    while ar.is_sample(i, 0.1):
        i += 1
    return i


def _sample_id():
    return next(i for i in range(1, 1000) if ar.is_sample(i, 0.1))


class TestSample:
    def test_deterministic_and_about_rate(self):
        picks = [ar.is_sample(i, 0.1) for i in range(1, 5001)]
        assert picks == [ar.is_sample(i, 0.1) for i in range(1, 5001)]  # 몇 번 돌려도 같은 표본
        assert 0.08 < sum(picks) / len(picks) < 0.12
        assert not any(ar.is_sample(i, 0.0) for i in range(1, 200))


class TestRun:
    @pytest.mark.asyncio
    async def test_low_auto_approved_as_rule_not_human_and_sample_kept(self):
        keep, sample = _no_sample_id(), _sample_id()
        rows = [_row(keep), _row(sample), _row(900001, "unresolved"), _row(900002, chunks=3)]
        conn = _conn(rows)
        with patch.object(ar, "get_conn", return_value=conn):
            out = await ar.run(None, actor_id=None, dry_run=False)
        assert out["auto_approved"] == 1 and out["sampled"] == 1
        assert out["by_level"] == {"high": 1, "medium": 1, "low": 2}
        assert out["human_queue_after"] == 3  # 높음·중간·표본만 사람 큐에
        upd = [c.args for c in conn.execute.await_args_list]
        approve_sql = next(a for a in upd if "status = 'active'" in a[0])
        assert "review_source = 'auto_rule'" in approve_sql[0] and "reviewed_by = NULL" in approve_sql[0]
        assert approve_sql[1] == [keep]
        assert next(a for a in upd if "review_sample = TRUE" in a[0])[1] == [sample]
        actions = [c.args[1][0][3] for c in conn.executemany.await_args_list]
        assert actions == ["auto_approved", "sampled"]
        assert "FOR UPDATE OF p SKIP LOCKED" in conn.fetch.await_args.args[0]

    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing(self):
        conn = _conn([_row(_no_sample_id())])
        with patch.object(ar, "get_conn", return_value=conn):
            out = await ar.run("A", actor_id=1, dry_run=True)
        assert out["auto_approved"] == 1 and out["run_id"] is None
        conn.execute.assert_not_awaited()
        conn.executemany.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_paused_rule_auto_approves_nothing(self):
        cfg = {"rules": {risk.LOW_RISK_RULE: {"enabled": True, "paused_at": "2026-10-01", "paused_reason": "x"}}}
        conn = _conn([_row(_no_sample_id())], cfg)
        with patch.object(ar, "get_conn", return_value=conn):
            out = await ar.run(None, actor_id=None, dry_run=False)
        assert out["auto_approved"] == 0 and out["human_queue_after"] == 1
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_already_sampled_and_prior_reject_stay_for_humans(self):
        conn = _conn([_row(_no_sample_id(), sample=True), _row(_no_sample_id(500), prior=True)])
        with patch.object(ar, "get_conn", return_value=conn):
            out = await ar.run(None, actor_id=None, dry_run=False)
        assert out["auto_approved"] == 0 and out["sampled"] == 0

    @pytest.mark.asyncio
    async def test_after_import_failure_is_logged_not_raised(self):
        with patch.object(ar, "get_conn", side_effect=RuntimeError("db down")):
            assert await ar.run_after_import("A") is None


class TestPauseOnSampleReject:
    @pytest.mark.asyncio
    async def test_rejected_sample_pauses_rule_and_logs(self):
        conn = _conn()
        conn.fetchval = AsyncMock(side_effect=[None, MagicMock(isoformat=lambda: "2026-10-01T00:00:00")])
        row = _row(7, sample=True) | {"review_rule": risk.LOW_RISK_RULE}
        assert await ar.on_human_reject(conn, row, risk.classify_row(row), actor_id=3) is True
        saved = json.loads(conn.execute.await_args.args[2])
        assert saved["rules"][risk.LOW_RISK_RULE]["paused_at"] and "표본 #7" in saved["rules"][risk.LOW_RISK_RULE]["paused_reason"]
        assert conn.executemany.await_args.args[1][0][3] == "rule_paused"

    @pytest.mark.asyncio
    async def test_non_sample_reject_does_not_pause(self):
        conn = _conn()
        row = _row(7)
        assert await ar.on_human_reject(conn, row, risk.classify_row(row), actor_id=3) is False
        conn.execute.assert_not_awaited()


class TestRevert:
    @pytest.mark.asyncio
    async def test_revert_only_auto_approved(self):
        conn = _conn()
        conn.fetchval = AsyncMock(return_value=1)
        conn.fetch = AsyncMock(return_value=[])  # 자동 통과 상태가 아님(사람 승인 등)
        with patch.object(ar, "get_conn", return_value=conn), \
             patch.object(ar, "resolve_namespace_id", AsyncMock(return_value=1)):
            with pytest.raises(ValueError, match="자동 통과된 항목만"):
                await ar.revert_item("A", 5, 3)
        sql = conn.fetch.await_args.args[0]
        assert "review_source = 'auto_rule'" in sql and "status = 'pending_review'" in sql

    @pytest.mark.asyncio
    async def test_bulk_revert_by_run_logs_each(self):
        conn = _conn()
        conn.fetch = AsyncMock(side_effect=[[{"policy_item_id": 1}, {"policy_item_id": 2}],
                                            [{"id": 1, "logical_id": 1, "namespace_id": 1},
                                             {"id": 2, "logical_id": 2, "namespace_id": 1}]])
        with patch.object(ar, "get_conn", return_value=conn):
            n = await ar.revert_bulk(run_id="r1", rule_key=None, actor_id=3)
        assert n == 2 and [x[3] for x in conn.executemany.await_args.args[1]] == ["auto_reverted"] * 2

    @pytest.mark.asyncio
    async def test_bulk_revert_by_rule_scoped_to_namespace(self):
        """화면은 현재 파트 건수를 보여주므로 되돌리는 범위도 그 파트로 — 다른 파트의 자동 통과는 안 건드린다."""
        conn = _conn()
        conn.fetch = AsyncMock(side_effect=[[{"id": 1}], [{"id": 1, "logical_id": 1, "namespace_id": 1}]])
        with patch.object(ar, "get_conn", return_value=conn):
            await ar.revert_bulk(run_id=None, rule_key=risk.LOW_RISK_RULE, actor_id=3, namespace="A")
        sql, *args = conn.fetch.await_args_list[0].args
        assert "n.name = $2" in sql and args == [risk.LOW_RISK_RULE, "A"]

    @pytest.mark.asyncio
    async def test_bulk_revert_requires_scope(self):
        with pytest.raises(ValueError):
            await ar.revert_bulk(run_id=None, rule_key=None, actor_id=3)


class TestSummary:
    @pytest.mark.asyncio
    async def test_human_queue_counts_low_only_when_rule_paused(self):
        rows = [_row(1, "unresolved"), _row(2, chunks=2), _row(3, sample=True), _row(4), _row(5)]
        conn = _conn()
        conn.fetch = AsyncMock(side_effect=[rows, [{"review_rule": risk.LOW_RISK_RULE, "n": 9}]])
        with patch.object(ar, "get_conn", return_value=conn):
            out = await ar.summary("A")
        assert out["queue"] == {"high": 1, "medium": 1, "sample": 1, "low_waiting": 2}
        assert out["human_queue"] == 3 and out["auto_approved"] == {risk.LOW_RISK_RULE: 9}

        cfg = {"rules": {risk.LOW_RISK_RULE: {"enabled": True, "paused_at": "t", "paused_reason": "r"}}}
        conn = _conn(config=cfg)
        conn.fetch = AsyncMock(side_effect=[rows, []])
        with patch.object(ar, "get_conn", return_value=conn):
            assert (await ar.summary("A"))["human_queue"] == 5  # 규칙이 멈추면 낮음도 사람이 봄
