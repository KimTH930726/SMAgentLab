"""캐시 답의 근거가 지금 DB에 있나(backlog 0-0h B·C②, 11/11 시연 녹화) — 실 DB(트랜잭션 롤백).

실례: 사본 DB를 새로 만들어도 Redis 캐시가 남아 DB에 없는 #65251을 근거로 한 답이 나왔다. 백업 복원·폐쇄망 반입·SQL 직접 변경도 같다.
불변식: 캐시 답을 내보내는 조건 = 근거 지식·정책이 전부 지금 사용 중(active). 하나라도 지워지거나(deleted·없음) 폐기(deprecated)·
검토 대기로 내려가면 False → 채팅은 캐시를 버리고 새로 답한다.
"""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))   # tests/ 공용 장치
from realdb import knowledge, load, ns, run  # noqa: E402

helpers = load("_rdb_real_chat_helpers", "service/chat/helpers.py")
NS = "시험_캐시근거 시스템"


async def _policy(conn, ns_id, status="active"):
    return await conn.fetchval(
        "INSERT INTO policy_item (namespace_id, policy_name, raw_body, content_hash, status, logical_id, version) "
        "VALUES ($1, '시험_정책', '본문', md5(random()::text), $2, NULL, 1) RETURNING id", ns_id, status)


def _payload(k_ids, p_ids):
    return {"answer": "a", "results": [{"id": i} for i in k_ids], "policy_citations": [{"kind": "narrative", "item_id": i} for i in p_ids]}


def test_alive_only_when_every_evidence_is_active():
    async def scenario(conn):
        n = await ns(conn, NS)
        k1, k2 = await knowledge(conn, n, "시험_근거1"), await knowledge(conn, n, "시험_근거2")
        p1 = await _policy(conn, n)
        out = {"all_active": await helpers.cached_evidence_alive(_payload([k1, k2], [p1]))}
        # 근거 없는 답(근거 없음·되묻기 아님) — 확인할 게 없다
        out["no_evidence"] = await helpers.cached_evidence_alive({"answer": "관련 지식을 찾지 못했습니다", "results": []})
        # 지식 하나 삭제 처리(지식 화면 삭제 = status deleted)
        await conn.execute("UPDATE rag_knowledge SET status = 'deleted' WHERE id = $1", k2)
        out["k_deleted"] = await helpers.cached_evidence_alive(_payload([k1, k2], [p1]))
        await conn.execute("UPDATE rag_knowledge SET status = 'active' WHERE id = $1", k2)
        # 행 자체가 없음(사본·백업으로 바꾼 DB엔 그 id가 아예 없다 — 시연 실례)
        out["k_missing"] = await helpers.cached_evidence_alive(_payload([k1, 2_000_000_000], [p1]))
        # 정책 폐기
        await conn.execute("UPDATE policy_item SET status = 'deprecated' WHERE id = $1", p1)
        out["p_deprecated"] = await helpers.cached_evidence_alive(_payload([k1, k2], [p1]))
        # 같은 id가 여러 번 들어 있어도(같은 문서 여러 문장 인용) 개수 비교가 틀리지 않게
        await conn.execute("UPDATE policy_item SET status = 'active' WHERE id = $1", p1)
        out["dup_ids"] = await helpers.cached_evidence_alive(_payload([k1, k1, k2], [p1, p1]))
        return out

    out = run(scenario, modules=[helpers])
    assert out == {"all_active": True, "no_evidence": True, "k_deleted": False, "k_missing": False,
                   "p_deprecated": False, "dup_ids": True}


def test_pending_review_evidence_is_not_alive():
    """검토 대기로 내려간 지식은 검색에 안 쓰인다 — 캐시 답도 그 근거로 내보내지 않는다."""
    async def scenario(conn):
        n = await ns(conn, NS)
        k = await knowledge(conn, n, "시험_근거", status="pending_review")
        return await helpers.cached_evidence_alive(_payload([k], []))

    assert run(scenario, modules=[helpers]) is False


def test_check_failure_drops_cache():
    """확인 쿼리 실패 = 버림(새로 답하는 쪽이 안전)."""
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import patch

    @asynccontextmanager
    async def broken():
        raise RuntimeError("db down")
        yield  # noqa: unreachable

    with patch.object(helpers, "get_conn", broken):
        assert asyncio.run(helpers.cached_evidence_alive(_payload([1], []))) is False
