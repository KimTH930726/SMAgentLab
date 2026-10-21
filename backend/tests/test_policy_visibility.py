"""정책 검색 대상 조건 한 곳(visibility.searchable_sql) + 정책 캐시 비우기 + 마이그레이션 #80 (2026-10-21, v0 재구축 1단계).

막힌 일: v0(원문 통째)에서 사람이 고친 새 버전(pending_review)이 승인 전에 검색·답변에 섞였다 — 조건이 여러 파일에
`status NOT IN ('deprecated', 'rejected')`로 흩어져 있었다. 여기서는
  1) 조건의 뜻을 실제 SQL 엔진(sqlite — IS DISTINCT FROM 지원)으로 상태×파이프라인 전 조합에 대해 확인하고,
  2) 검색 대상을 고르는 모든 쿼리가 그 조건을 쓰는지(옛 조건이 남지 않았는지) 확인한다.
"""
import ast
import asyncio
import importlib.util
import itertools
import logging
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_b = Path(__file__).resolve().parent.parent
sys.modules.setdefault("service.policy", MagicMock())
vis = sys.modules["service.policy.visibility"]       # conftest가 실제 파일로 등록
caches = sys.modules["service.policy.caches"]

OLD_FILTER = "NOT IN ('deprecated', 'rejected')"
STATUSES = ["active", "pending_review", "deprecated", "rejected"]
PIPELINES = ["v0_whole", None, "r1-e6ec734cbd", "v0_system"]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(_b / rel))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def _conn(fetch_return=None):
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.fetch = AsyncMock(return_value=fetch_return or [])
    c.fetchval = AsyncMock(return_value=None)
    c.execute = AsyncMock()
    c.transaction = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
    return c


# ── 1) 조건의 뜻 ───────────────────────────────────────────────────────────────

def _expected(status, pv):
    if status == "active":
        return True
    return status == "pending_review" and pv != "v0_whole"


@pytest.mark.parametrize("alias", ["i", "policy_item"])
def test_searchable_sql_semantics_all_combinations(alias):
    """상태 4 × 파이프라인 4(NULL 포함) 전 조합 — active는 늘, 검토 대기는 v0가 아닐 때만, 폐기·반려는 절대."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE policy_item (id INTEGER PRIMARY KEY, status TEXT, pipeline_version TEXT)")
    combos = list(itertools.product(STATUSES, PIPELINES))
    db.executemany("INSERT INTO policy_item (status, pipeline_version) VALUES (?, ?)", combos)
    frm = "policy_item i" if alias == "i" else "policy_item"
    got = {r[0] for r in db.execute(f"SELECT id FROM {frm} WHERE {vis.searchable_sql(alias)}")}
    want = {n + 1 for n, (s, pv) in enumerate(combos) if _expected(s, pv)}
    assert got == want
    # 파이썬 판정(정정 대상 확인용)도 같은 규칙
    assert all(vis.is_searchable(s, pv) == _expected(s, pv) for s, pv in combos)


def test_v0_pending_excluded_legacy_pending_kept():
    assert not vis.is_searchable("pending_review", "v0_whole")
    assert vis.is_searchable("pending_review", None) and vis.is_searchable("pending_review", "r1-x")
    assert vis.is_searchable("active", "v0_whole")


# ── 2) 검색 대상을 고르는 쿼리가 모두 이 조건을 쓴다 ─────────────────────────────

@pytest.mark.parametrize("rel", [
    "service/policy/search.py", "service/policy/keyword_index.py", "service/policy/category_list.py",
    "service/policy/scope.py", "service/admin/service.py",
])
def test_no_old_filter_left(rel):
    src = (_b / rel).read_text(encoding="utf-8")
    assert OLD_FILTER not in src and "searchable_sql(" in src


def test_improvement_target_lookup_uses_rule():
    src = (_b / "service/improvement/service.py").read_text(encoding="utf-8")
    body = src[src.index("async def _load_original"):src.index("async def", src.index("async def _load_original") + 10)]
    assert "searchable_sql('policy_item')" in body and OLD_FILTER not in body


def test_scope_sql_uses_rule():
    scope = _load("_v0rev_scope", "service/policy/scope.py")
    assert vis.searchable_sql("i") in scope._SQL and OLD_FILTER not in scope._SQL


def test_keyword_index_query_uses_rule():
    K = _load("_v0rev_keyword_index", "service/policy/keyword_index.py")
    conn = _conn()
    K.get_conn = MagicMock(return_value=conn)
    asyncio.run(K.search([1, 2], "질문", 5))
    assert vis.searchable_sql("policy_item") in conn.fetch.await_args.args[0]


def test_category_index_query_uses_rule():
    cl = _load("_v0rev_category_list", "service/policy/category_list.py")
    conn = _conn()
    asyncio.run(cl._index(conn, (1,)))
    assert vis.searchable_sql("i") in conn.fetch.await_args.args[0]


def test_admin_policy_count_uses_rule():
    admin = sys.modules["service.admin.service"]
    conn = _conn()
    orig = admin.get_conn
    admin.get_conn = MagicMock(return_value=conn)
    try:
        asyncio.run(admin.list_namespaces_detail())
    finally:
        admin.get_conn = orig
    assert vis.searchable_sql("policy_item") in conn.fetch.await_args.args[0]


# ── 정책 캐시 비우기 ─────────────────────────────────────────────────────────────

def test_invalidate_policy_caches_clears_every_namespace(monkeypatch):
    """채팅은 정책서를 여러 업무 영역에 걸쳐 찾는다 — 고친 영역만이 아니라 모든 영역 답변 캐시 + 정책 색인."""
    inv = AsyncMock(return_value=0)
    monkeypatch.setitem(sys.modules, "shared.cache", MagicMock(invalidate_all=inv))   # 영역마다 훑지 않고 전체 한 번(리뷰 P1)
    kw, cat = MagicMock(), MagicMock()
    monkeypatch.setattr(sys.modules["service.policy"], "keyword_index", kw, raising=False)
    monkeypatch.setattr(sys.modules["service.policy"], "category_list", cat, raising=False)
    asyncio.run(caches.invalidate_policy_caches())
    inv.assert_awaited_once_with()
    kw.invalidate.assert_called_once()
    cat.invalidate.assert_called_once()


def test_invalidate_policy_caches_best_effort(monkeypatch, caplog):
    """캐시가 죽어도 예외를 올리지 않고(승인은 이미 반영) 경고 로그를 남긴다."""
    monkeypatch.setitem(sys.modules, "shared.cache", MagicMock(invalidate_all=AsyncMock(side_effect=RuntimeError("redis down"))))
    with caplog.at_level(logging.WARNING):
        asyncio.run(caches.invalidate_policy_caches())
    assert "답변 캐시 비우기 실패" in caplog.text


# ── 마이그레이션 #80 (main.py는 무거워 함수 소스만 떼어 실행) ─────────────────────

def _migration_fn():
    src = (_b / "main.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(src).body
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "_migrate_policy_v0_revisions")
    ns = {"logger": logging.getLogger("test_migrate_80")}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), ns)
    return ns["_migrate_policy_v0_revisions"]


OLD_DEF = ("CHECK (((action)::text = ANY ((ARRAY['approved'::character varying, 'resubmitted'::character varying, "
           "'rule_resumed'::character varying])::text[])))")


def _mig_conn(*, constraint_def, existing_indexes=(), dups=None):
    c = _conn()
    dups = dups or {}

    async def fetch(sql, *args):
        if "pg_constraint" in sql:
            return [{"conname": "policy_review_log_action_check", "def": constraint_def}]
        key = "pending" if "pending_review" in sql else "active"
        return dups.get(key, [])
    c.fetch = AsyncMock(side_effect=fetch)
    c.fetchval = AsyncMock(side_effect=lambda sql, name: 1 if name in existing_indexes else None)
    return c


def test_migration_first_run_adds_submitted_and_indexes():
    c = _mig_conn(constraint_def=OLD_DEF)
    asyncio.run(_migration_fn()(c))
    sqls = [a.args[0] for a in c.execute.await_args_list]
    assert any('DROP CONSTRAINT IF EXISTS "policy_review_log_action_check"' in s for s in sqls)
    add = next(s for s in sqls if "ADD CONSTRAINT" in s)
    assert "'submitted'" in add and "'resubmitted'" in add and "'rule_resumed'" in add   # 기존 값 유지 + 새 값
    one_pending = next(s for s in sqls if "uq_policy_one_pending_v0" in s)
    assert "UNIQUE INDEX IF NOT EXISTS" in one_pending and "pipeline_version = 'v0_whole'" in one_pending
    assert any("uq_policy_one_active" in s and "status = 'active'" in s for s in sqls)


def test_migration_second_run_is_noop():
    """이미 반영된 DB(제약에 'submitted', 인덱스 있음)엔 아무것도 바꾸지 않는다 — 'resubmitted'에 속지 않음."""
    c = _mig_conn(constraint_def=OLD_DEF.replace("'rule_resumed'", "'submitted'::character varying, 'rule_resumed'"),
                  existing_indexes=("uq_policy_one_pending_v0", "uq_policy_one_active"))
    asyncio.run(_migration_fn()(c))
    c.execute.assert_not_awaited()


def test_migration_skips_unique_index_when_duplicates(caplog):
    """이미 같은 정책에 active가 둘이면 인덱스를 건너뛰고 경고만(기동은 계속)."""
    c = _mig_conn(constraint_def=OLD_DEF, dups={"active": [{"logical_id": 7, "n": 2}]})
    with caplog.at_level(logging.WARNING):
        asyncio.run(_migration_fn()(c))
    sqls = [a.args[0] for a in c.execute.await_args_list]
    assert not any("uq_policy_one_active" in s for s in sqls)
    assert any("uq_policy_one_pending_v0" in s for s in sqls)
    assert "uq_policy_one_active" in caplog.text


def test_migration_registered_after_79():
    src = (_b / "main.py").read_text(encoding="utf-8")
    run = src[src.index("async def _run_migrations"):]
    assert run.index("_migrate_prompt_voc_type(conn)") < run.index("_migrate_policy_v0_revisions(conn)")
