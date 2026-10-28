"""지식 코어 API(10/28 VOC 레이어 분리) — 창구가 쓰는 지식 경로. 가장 가까운 지식: 한 쿼리·채팅과 같은 범위(영역 + 직계 하위)·사용 중만·키와 값 짝 유지."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.knowledge_rag.knowledge import api


@pytest.mark.asyncio
async def test_nearest_empty_skips_db():
    conn = MagicMock(fetch=AsyncMock())
    assert await api.nearest(conn, [], snippet_len=100) == {}
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_nearest_one_query_scope_and_pairs():
    conn = MagicMock(fetch=AsyncMock(return_value=[
        {"key": 7, "knowledge_id": 42, "similarity": 0.8, "snippet": "s", "content": "c"},
        {"key": 9, "knowledge_id": None, "similarity": None, "snippet": None, "content": None}]))
    out = await api.nearest(conn, [(7, 1, "[0.1]"), (9, 2, None)], snippet_len=200)
    sql, keys, ns, embs, n = conn.fetch.call_args.args
    assert conn.fetch.await_count == 1                              # 클러스터 수만큼 부르지 않는다
    assert (keys, ns, embs, n) == ([7, 9], [1, 2], ["[0.1]", None], 200)   # 키·영역·벡터 짝이 같은 순서
    assert "s.parent_id = c.namespace_id" in sql and "status = 'active'" in sql and "LIMIT 1" in sql
    assert "AS MATERIALIZED" in sql and "<=> c.v" in sql and "c.emb::vector" not in sql   # 변환은 키마다 한 번(리뷰 실측 10배)
    assert out[7]["knowledge_id"] == 42 and out[9]["similarity"] is None


def _patch_conn(monkeypatch, ns_id, rows):
    conn = MagicMock(fetch=AsyncMock(return_value=rows))
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(api, "get_conn", MagicMock(return_value=conn))
    monkeypatch.setattr(api, "resolve_namespace_id", AsyncMock(return_value=ns_id))
    return conn


@pytest.mark.asyncio
async def test_contents_empty_skips_db(monkeypatch):
    conn = _patch_conn(monkeypatch, 42, [])
    assert await api.contents("딜리버스", []) == []
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_contents_unknown_namespace(monkeypatch):
    conn = _patch_conn(monkeypatch, None, [])
    assert await api.contents("없는곳", [1]) == []
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_contents_scoped_to_namespace(monkeypatch):
    """다른 영역 지식이 VOC 근거로 섞이지 않게 — 그 영역 id로 거른다(옮긴 VOC 시험, 10/28 리뷰)."""
    conn = _patch_conn(monkeypatch, 42, [{"id": 1, "content": "내용1"}])
    assert await api.contents("딜리버스", [1, 999]) == [{"id": 1, "content": "내용1"}]
    sql, ns, ids = conn.fetch.call_args.args
    assert "namespace_id = $1" in sql and (ns, ids) == (42, [1, 999])
