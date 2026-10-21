"""정책서 화면 조회(service/policy/catalog.py + /api/policy/policies·ref-tables·overviews, v0 재구축 2단계 2026-10-21).

모양(화면이 쓰는 칸)과 조회 규칙(현행만·문서 순서·검색어 글자 그대로·용어 연결 키 = 정책 logical_id), 권한(조회 검사를 먼저)을 본다.
"""
import importlib.util as _ilu
import json
import sys
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


catalog = _load("service.policy.catalog", "service/policy/catalog.py")
_pkg = sys.modules.setdefault("service.policy", MagicMock())
_load("service.policy.schemas", "service/policy/schemas.py")
_saved = getattr(_pkg, "catalog", None)
_pkg.catalog = catalog
prouter = _load("service.policy.router", "service/policy/router.py")

USER = {"id": 1, "role": "user", "part_id": 1}


def _conn(fetch=None, fetchrow=None):
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.fetch = AsyncMock(side_effect=fetch) if callable(fetch) else AsyncMock(return_value=fetch or [])
    c.fetchrow = AsyncMock(side_effect=fetchrow) if callable(fetchrow) else AsyncMock(return_value=fetchrow)
    return c


def _patch(conn, ns_id=5):
    return (patch.object(catalog, "get_conn", return_value=conn),
            patch.object(catalog, "resolve_namespace_id", AsyncMock(return_value=ns_id)))


class TestListPolicies:
    @pytest.mark.asyncio
    async def test_active_in_document_order_with_open_correction(self):
        conn = _conn(fetch=[{"item_id": 3, "logical_id": 30, "version": 2, "category_path": ("1. 가", "1-1. 나"),
                             "policy_name": "p", "has_remark": True, "open_correction_id": 12}])
        a, b = _patch(conn)
        with a, b:
            out = await catalog.list_policies("ns")
        assert out == [{"item_id": 3, "logical_id": 30, "version": 2, "category_path": ["1. 가", "1-1. 나"],
                        "policy_name": "p", "has_remark": True, "open_correction_id": 12}]
        sql, *args = conn.fetch.await_args.args
        assert "i.status = 'active'" in sql and sql.rstrip().endswith("ORDER BY i.source_file NULLS LAST, i.source_row NULLS LAST, i.id")
        assert "o.status = 'pending'" in sql and args == [5, ["policy_param", "policy_narrative"]]

    @pytest.mark.asyncio
    async def test_query_matches_name_or_body_literally(self):
        conn = _conn(fetch=[])
        a, b = _patch(conn)
        with a, b:
            await catalog.list_policies("ns", " 50%_할인 ")
        sql, *args = conn.fetch.await_args.args
        assert "policy_name ILIKE $3" in sql and "raw_body ILIKE $3" in sql
        assert args[2] == "%50\\%\\_할인%"            # %·_는 글자 그대로

    @pytest.mark.asyncio
    async def test_unknown_namespace(self):
        a, b = _patch(_conn(), ns_id=None)
        with a, b, pytest.raises(ValueError):
            await catalog.list_policies("없는 영역")


class TestGetPolicy:
    @pytest.mark.asyncio
    async def test_shape_terms_versions_open_correction(self):
        cur = {"item_id": 11, "chunk_id": 200, "logical_id": 30, "version": 2, "status": "active", "category_path": ["가"],
               "policy_name": "반품 기한", "raw_body": "14일", "remark": None, "source_file": "doc.json"}
        at = datetime(2026, 10, 21, tzinfo=timezone.utc)

        async def fetchrow(sql, *args):
            if "FROM policy_item i" in sql:
                assert args == (5, 30)
                return cur
            assert "status = 'pending'" in sql and args[0] == 11     # 검토 중 정정은 현행 항목 id 기준
            return {"id": 41, "source": "policy_direct"}

        async def fetch(sql, *args):
            if "FROM policy_param" in sql:
                return [{"id": 1, "name": "기한", "condition": None, "value": "14", "unit": "일", "external_source": None}]
            if "FROM ops_link" in sql:
                assert args == ("30",) and "l.relation = 'uses'" in sql   # from_key = 정책 logical_id(문자열)
                return [{"id": 7, "term": "반품", "description": "돌려보냄"}]
            assert "logical_id = $2" in sql
            return [{"item_id": 11, "version": 2, "status": "active", "created_at": at, "correction_id": 9},
                    {"item_id": 10, "version": 1, "status": "deprecated", "created_at": at, "correction_id": None}]

        conn = _conn(fetch=fetch, fetchrow=fetchrow)
        a, b = _patch(conn)
        with a, b:
            out = await catalog.get_policy("ns", 30)
        assert out["item_id"] == 11 and out["chunk_id"] == 200 and out["raw_body"] == "14일" and out["remark"] is None
        assert out["params"][0]["value"] == "14" and out["terms"] == [{"id": 7, "term": "반품", "description": "돌려보냄"}]
        assert [(v["version"], v["origin"]) for v in out["versions"]] == [(2, "정정 #9"), (1, "적재")]
        assert out["versions"][0]["created_at"] == at.isoformat()
        assert out["open_correction"] == {"id": 41, "source": "policy_direct"}

    @pytest.mark.asyncio
    async def test_no_active_version_is_none(self):
        conn = _conn(fetchrow=None)
        a, b = _patch(conn)
        with a, b:
            assert await catalog.get_policy("ns", 30) is None
        conn.fetch.assert_not_awaited()


class TestRefTablesAndOverviews:
    @pytest.mark.asyncio
    async def test_ref_table_rows_are_dicts_in_row_order(self):
        cols = [{"name": "유형", "role": "condition", "decisive": True}, {"name": "수량", "role": "result", "decisive": False}]
        t = {"id": 4, "logical_id": 4, "version": 1, "title": "표", "category_path": [], "columns": json.dumps(cols)}
        conn = _conn(fetch=[{"row_no": 1, "cells": json.dumps({"유형": "A", "수량": "1"})},
                            {"row_no": 2, "cells": {"유형": "B", "수량": "3"}}], fetchrow=t)
        a, b = _patch(conn)
        with a, b:
            out = await catalog.get_ref_table("ns", 4)
        assert out["columns"] == cols and out["rows"] == [{"유형": "A", "수량": "1"}, {"유형": "B", "수량": "3"}]
        assert "ORDER BY row_no" in conn.fetch.await_args.args[0]
        assert "namespace_id = $2" in conn.fetchrow.await_args.args[0] and conn.fetchrow.await_args.args[1:] == (4, 5)

    @pytest.mark.asyncio
    async def test_ref_table_other_namespace_is_none(self):
        conn = _conn(fetchrow=None)
        a, b = _patch(conn)
        with a, b:
            assert await catalog.get_ref_table("ns", 4) is None

    @pytest.mark.asyncio
    async def test_lists(self):
        conn = _conn(fetch=[{"id": 1, "logical_id": 1, "version": 1, "title": "표", "category_path": None,
                             "n_columns": 3, "n_rows": 10}])
        a, b = _patch(conn)
        with a, b:
            tables = await catalog.list_ref_tables("ns")
        assert tables[0]["category_path"] == [] and tables[0]["n_rows"] == 10
        conn = _conn(fetch=[{"id": 1, "title": "개요", "body": "b", "source_key": "doc_01", "source_file": "f"}])
        a, b = _patch(conn)
        with a, b:
            assert (await catalog.list_overviews("ns"))[0]["source_key"] == "doc_01"
        assert "status = 'active'" in conn.fetch.await_args.args[0]


class TestPolicyRouter:
    @pytest.mark.asyncio
    async def test_read_check_runs_before_query(self):
        q = AsyncMock()
        with patch.object(prouter, "check_knowledge_read", AsyncMock(side_effect=HTTPException(403, "no"))), \
             patch.object(prouter.catalog, "list_policies", q):
            with pytest.raises(HTTPException):
                await prouter.list_policies(namespace="ns", q=None, user=USER)
        q.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_detail_404_and_pass_through(self):
        read = AsyncMock()
        with patch.object(prouter, "check_knowledge_read", read), \
             patch.object(prouter.catalog, "get_policy", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as e:
                await prouter.get_policy(30, namespace="ns", user=USER)
        assert e.value.status_code == 404 and read.await_args.args == ("ns", USER)
        with patch.object(prouter, "check_knowledge_read", AsyncMock()), \
             patch.object(prouter.catalog, "get_ref_table", AsyncMock(return_value={"id": 4})):
            assert await prouter.get_ref_table(4, namespace="ns", user=USER) == {"id": 4}

    def test_removed_routes_are_gone(self):
        paths = {r.path for r in prouter.router.routes}
        for gone in ("/api/policy/import", "/api/policy/import-paste", "/api/policy/sources", "/api/policy/items",
                     "/api/policy/unresolved-summary", "/api/policy/review-summary", "/api/policy/auto-review/run",
                     "/api/policy/params/{param_id}", "/api/policy/narratives/{chunk_id}",
                     "/api/policy/items/{item_id}/approve", "/api/policy/items/{item_id}/reject"):
            assert gone not in paths, gone
        for kept in ("/api/policy/search", "/api/policy/policies", "/api/policy/policies/{logical_id}",
                     "/api/policy/ref-tables", "/api/policy/ref-tables/{table_id}", "/api/policy/overviews",
                     "/api/policy/track2/run", "/api/policy/answer-eval/history", "/api/policy/pipeline-stats"):
            assert kept in paths, kept


def teardown_module(_m):
    # 다른 테스트 파일이 가짜 service.policy 패키지의 catalog 속성을 기대하지 않게 되돌린다
    if _saved is None:
        try:
            del _pkg.catalog
        except AttributeError:
            pass
    else:
        _pkg.catalog = _saved
