"""대표 용어(locator.load_profiles) — 컬렉션별 쿼리·표본 상한·컬렉션별 캐시(11/19 최종 적재: 전수 집계가 DB 60초 제한을 넘어 미리보기 실패)."""
import asyncio

import pytest

from agents.knowledge_rag.ingestion import locator


class FakeConn:
    def __init__(self, rows_by_ns):
        self.rows_by_ns = rows_by_ns
        self.calls = []

    async def fetch(self, sql, ns, limit):
        self.calls.append((ns, limit))
        return [{"term": t, "hits": h, "first_id": f} for t, h, f in self.rows_by_ns.get(ns, [])]


@pytest.fixture(autouse=True)
def _clear_cache():
    locator._profile_cache.clear()
    yield
    locator._profile_cache.clear()


def run(coro):
    return asyncio.run(coro)


def test_one_query_per_collection_with_sample_cap():
    conn = FakeConn({1: [("a", 3, 1)], 2: [("b", 1, 5)]})
    out = run(locator.load_profiles(conn, [1, 2, 1]))   # 중복 id는 한 번만
    assert out == {1: ["a"], 2: ["b"]}
    assert conn.calls == [(1, locator.PROFILE_SAMPLE), (2, locator.PROFILE_SAMPLE)]


def test_sql_samples_active_chunks_of_that_collection_only():
    # FakeConn은 SQL을 실행하지 않는다 — 표본의 뜻(사용 중만·컬렉션별 상한·0건 용어 유지·같은 컬렉션 용어)이 SQL에서 빠지면 잡는다(리뷰 L4)
    sql = " ".join(locator.PROFILE_SQL.split())
    assert "status = 'active'" in sql and "namespace_id = $1 AND status" in sql
    assert "ORDER BY id DESC LIMIT $2)" in sql            # 상한은 CTE 안(컬렉션별 표본)
    assert "LEFT JOIN k" in sql and "WHERE g.namespace_id = $1" in sql


def test_order_by_hits_then_registration_and_top_n():
    rows = [(f"t{i}", 0, i) for i in range(15)] + [("hot", 9, 99), ("warm", 2, 50)]
    out = run(locator.load_profiles(FakeConn({7: rows}), [7]))[7]
    assert out[:2] == ["hot", "warm"]                       # 많이 나온 순
    assert out[2:] == [f"t{i}" for i in range(8)]           # 같으면 먼저 등록된 순
    assert len(out) == locator.PROFILE_TERMS


def test_cache_is_per_collection_other_team_does_not_evict():
    conn = FakeConn({1: [("a", 1, 1)], 2: [("b", 1, 2)], 3: [("c", 1, 3)]})
    run(locator.load_profiles(conn, [1, 2]))
    run(locator.load_profiles(conn, [3]))       # 다른 팀 후보 — 예전엔 캐시를 통째로 덮어 1·2를 다시 셌다
    run(locator.load_profiles(conn, [1, 2, 3]))
    assert [ns for ns, _ in conn.calls] == [1, 2, 3]


def test_cache_expires(monkeypatch):
    conn = FakeConn({1: [("a", 1, 1)]})
    run(locator.load_profiles(conn, [1]))
    at, terms = locator._profile_cache[1]
    locator._profile_cache[1] = (at - locator.PROFILE_TTL - 1, terms)
    run(locator.load_profiles(conn, [1]))
    assert len(conn.calls) == 2


def test_collection_without_terms_is_empty_list():
    out = run(locator.load_profiles(FakeConn({}), [5]))
    assert out == {5: []}
