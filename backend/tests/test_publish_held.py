"""검수 후 게시(11/03 결정 가) — 실제 Postgres 불변식. 트랜잭션 안에서 만들고 되돌린다(데이터 안 남김). DB가 없으면 건너뜀.

불변식:
  게시 전: 새 조각은 staging(검색에 안 나옴) · 같은 페이지 옛 조각은 active 그대로(내용이 비는 구간 없음)
  게시 후: 새 조각 active · 옛 조각 deprecated · 작업 completed · 조각 수 그대로(유실·중복 0)
  보류가 아닌 작업을 게시하면 거부(아무것도 안 바뀜)
"""
import asyncio
import importlib.util
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_held_real_service", ROOT / "agents/knowledge_rag/knowledge/service.py")
svc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(svc)

VEC = "[" + ",".join(["0"] * 1024) + "]"


def _run(scenario):
    asyncpg = pytest.importorskip("asyncpg")
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL 없음")

    async def go():
        conn = await asyncpg.connect(url)
        tx = conn.transaction()
        await tx.start()
        try:
            ns = await conn.fetchval("INSERT INTO ops_namespace (name, kind, area) VALUES ('시험_게시 시스템', 'domain', 'system') RETURNING id")

            @asynccontextmanager
            async def get_conn():
                yield conn
            with patch.object(svc, "get_conn", get_conn):
                return await scenario(conn, ns)
        finally:
            await tx.rollback()
            await conn.close()
    try:
        return asyncio.run(go())
    except OSError:
        pytest.skip("DB 연결 안 됨")


async def _seed(conn, ns: int, *, job_status: str = "held") -> dict:
    old = await conn.fetchval("INSERT INTO rag_knowledge (namespace_id, content, embedding, status, confluence_page_id) "
                              "VALUES ($1, '옛 내용', $2::vector, 'active', 'p-1') RETURNING id", ns, VEC)
    plan = {"source_doc_ids": [], "partial_doc_ids": [], "replacing_pages": ["p-1"], "replacing_docs": [],
            "confirm_replace": False, "force_pending": False}
    job = await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks, created_chunks, held_plan) "
                              "VALUES ($1, 'confluence', $2, 2, 2, $3::jsonb) RETURNING id", ns, job_status, json.dumps(plan))
    new = [await conn.fetchval("INSERT INTO rag_knowledge (namespace_id, content, embedding, status, confluence_page_id, ingestion_job_id) "
                               "VALUES ($1, $2, $3::vector, 'staging', 'p-1', $4) RETURNING id", ns, f"새 내용 {i}", VEC, job) for i in range(2)]
    return {"old": old, "job": job, "new": new}


async def _statuses(conn, ids):
    return {r["id"]: r["status"] for r in await conn.fetch("SELECT id, status FROM rag_knowledge WHERE id = ANY($1::int[])", ids)}


def test_held_job_keeps_old_version_until_publish_then_swaps():
    async def scenario(conn, ns):
        ids = await _seed(conn, ns)
        all_ids = [ids["old"], *ids["new"]]
        before = await _statuses(conn, all_ids)
        searchable_before = await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1 AND status = 'active'", ns)
        await svc.publish_held_job(ids["job"])
        after = await _statuses(conn, all_ids)
        job = await conn.fetchval("SELECT status FROM rag_ingestion_job WHERE id = $1", ids["job"])
        total = await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1", ns)
        return ids, before, searchable_before, after, job, total
    ids, before, searchable_before, after, job, total = _run(scenario)
    assert before[ids["old"]] == "active" and all(before[i] == "staging" for i in ids["new"])
    assert searchable_before == 1                                  # 게시 전엔 옛 내용만 검색된다(빈 구간 없음)
    assert after[ids["old"]] == "deprecated" and all(after[i] == "active" for i in ids["new"])
    assert job == "completed" and total == 3                       # 유실·중복 0


def test_publish_refuses_non_held_job():
    async def scenario(conn, ns):
        ids = await _seed(conn, ns, job_status="completed")
        with pytest.raises(ValueError, match="보류 작업이 아닙니다"):
            await svc.publish_held_job(ids["job"])
        return ids, await _statuses(conn, [ids["old"], *ids["new"]])
    ids, st = _run(scenario)
    assert st[ids["old"]] == "active" and all(st[i] == "staging" for i in ids["new"])   # 아무것도 안 바뀜


def test_publish_refuses_held_job_without_chunks():
    """조각이 없는 보류 작업을 게시하면 옛 버전만 내려 그 페이지가 검색에서 빠진다 — 거부하고 옛 버전 그대로(재리뷰 H)."""
    async def scenario(conn, ns):
        ids = await _seed(conn, ns)
        await conn.execute("DELETE FROM rag_knowledge WHERE ingestion_job_id = $1", ids["job"])
        with pytest.raises(ValueError, match="게시할 조각이 없습니다"):
            await svc.publish_held_job(ids["job"])
        return ids, await _statuses(conn, [ids["old"]]), await conn.fetchval("SELECT status FROM rag_ingestion_job WHERE id = $1", ids["job"])
    ids, st, job = _run(scenario)
    assert st[ids["old"]] == "active" and job == "held"


def test_startup_cleanup_keeps_held_chunks():
    """기동 정리(main._cleanup_orphaned_ingestion_jobs)의 staging 삭제가 보류 작업 조각을 남기는지 — 같은 SQL을 실제 DB에서(재리뷰 H)."""
    import re
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    fn = src[src.index("async def _cleanup_orphaned_ingestion_jobs"):]
    sql = re.search(r'staged = await conn.execute\(\s*((?:"[^"]*"\s*)+)\)', fn).group(1)
    sql = "".join(re.findall(r'"([^"]*)"', sql))
    assert "held" in sql

    async def scenario(conn, ns):
        held = await _seed(conn, ns)
        dead = await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks) VALUES ($1, 'file', 'failed', 1) "
                                   "RETURNING id", ns)
        orphan = await conn.fetchval("INSERT INTO rag_knowledge (namespace_id, content, embedding, status, ingestion_job_id) "
                                     "VALUES ($1, '죽은 작업 찌꺼기', $2::vector, 'staging', $3) RETURNING id", ns, VEC, dead)
        await conn.execute(sql)
        left = {r["id"] for r in await conn.fetch("SELECT id FROM rag_knowledge WHERE namespace_id = $1", ns)}
        return held, orphan, left
    held, orphan, left = _run(scenario)
    assert set(held["new"]) <= left and orphan not in left


def test_publish_batch_claim_partial_failure_and_release():
    """배치 게시: 두 번째 게시는 게시 중이라 거부 · 한 작업이 실패하면 게시 시각은 비고 오류 기록 · 끝나면 게시 중 표시가 풀린다 ·
    남은 작업을 다시 게시하면 그때 게시 시각(재리뷰 테스트 공백)."""
    ti_spec = importlib.util.spec_from_file_location("_held_real_team_import", ROOT / "agents/knowledge_rag/ingestion/team_import.py")
    ti = importlib.util.module_from_spec(ti_spec)
    ti_spec.loader.exec_module(ti)
    import sys
    from unittest.mock import AsyncMock, MagicMock

    async def scenario(conn, ns):
        ok = await _seed(conn, ns)
        bad = await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks, held_plan) "
                                  "VALUES ($1, 'confluence', 'held', 0, '{}'::jsonb) RETURNING id", ns)   # 조각 없는 보류 작업 → 게시 실패
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        bid = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, status, sampling_required, total_pages, done_pages, job_ids, loaded_at) "
                                  "VALUES ($1, 'https://confl.sinc.co.kr/display/T', 'loaded', FALSE, 1, 1, $2::int[], NOW()) RETURNING id",
                                  team, [ok["job"], bad])

        @asynccontextmanager
        async def get_conn():
            yield conn
        router = MagicMock(AUTO_EXTRACT_SOURCE="auto", _run_auto_glossary=AsyncMock(return_value=0))
        import agents.knowledge_rag.knowledge as kpkg   # `from …knowledge import service`는 패키지 속성을 먼저 본다 — 둘 다 바꿔 끼운다
        with patch.dict(sys.modules, {"agents.knowledge_rag.knowledge.router": router,
                                      "agents.knowledge_rag.knowledge.service": svc, "core.database": MagicMock(get_conn=get_conn)}),                 patch.object(kpkg, "service", svc, create=True), patch.object(kpkg, "router", router, create=True):
            await conn.execute("UPDATE rag_import_batch SET publishing_at = NOW() WHERE id = $1", bid)
            with pytest.raises(ValueError, match="게시 중"):
                await ti.publish_batch(bid, {"id": None})
            await conn.execute("UPDATE rag_import_batch SET publishing_at = NULL WHERE id = $1", bid)
            first = await ti.publish_batch(bid, {"id": None})
            after_first = dict(await conn.fetchrow("SELECT published_at, publishing_at, error FROM rag_import_batch WHERE id = $1", bid))
            await conn.execute("UPDATE rag_ingestion_job SET status = 'cancelled' WHERE id = $1", bad)   # 실패 작업을 정리했다고 치고
            second = await ti.publish_batch(bid, {"id": None})
            after_second = await conn.fetchval("SELECT published_at IS NOT NULL FROM rag_import_batch WHERE id = $1", bid)
        return first, after_first, second, after_second
    first, after_first, second, after_second = _run(scenario)
    assert first["published"] == 1 and len(first["failed"]) == 1
    assert after_first["published_at"] is None and after_first["publishing_at"] is None and "게시 실패 1건" in after_first["error"]
    assert second == {"published": 0, "failed": []} and after_second is True
