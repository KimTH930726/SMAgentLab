"""VOC 이메일 정책 → 시스템 컬렉션 이동 스크립트(11/05) — 실 DB, 트랜잭션 안에서 만들고 되돌린다(tests/realdb.py).
불변식: 옮기기 = 받는 쪽 후 = 전 + 옮긴 수 · 보내는 쪽 0 · 되돌리기 = 원래 자리(유실·중복 0) · 받는 쪽에 같은 메일함·메일이 있으면 멈춤."""
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))   # tests/ 공용 장치
from realdb import load, ns, run  # noqa: E402

move = load("_mvs_real_move_voc", "scripts/move_voc_to_system.py")


async def _voc(conn, ns_id, part_id, mailbox="voc@example.com", msgs=("m1", "m2")):
    r = await conn.fetchval("INSERT INTO ops_voc_routing (namespace_id, mailbox_upn, part_id) VALUES ($1, $2, $3) RETURNING id", ns_id, mailbox, part_id)
    c = await conn.fetchval("INSERT INTO ops_voc_cluster (namespace_id, first_seen_at, last_seen_at) VALUES ($1, NOW(), NOW()) RETURNING id", ns_id)
    for m in msgs:
        await conn.execute("INSERT INTO ops_email_analysis (namespace_id, source_message_id, mailbox_upn, routing_id, voc_cluster_id) "
                           "VALUES ($1, $2, $3, $4, $5)", ns_id, m, mailbox, r, c)
    return r, c


async def _part(conn):
    return await conn.fetchval("SELECT id FROM ops_part ORDER BY id LIMIT 1")


def test_move_and_revert_conserve_rows(tmp_path):
    log = str(tmp_path / "move.json")

    async def scenario(conn):
        part = await _part(conn)
        if part is None:
            pytest.skip("파트 없음")
        p, s = await ns(conn, "시험_VOC 정책", "policy"), await ns(conn, "시험_VOC 시스템", "system")
        await _voc(conn, p, part)
        src, dst = await move.ns_row(conn, "시험_VOC 정책"), await move.ns_row(conn, "시험_VOC 시스템")
        await move.move(conn, src, dst, apply=True, log=log)
        moved = (await move.counts(conn, p), await move.counts(conn, s))
        # 옮긴 뒤에도 메일 ↔ 라우팅 ↔ 묶음 연결이 그대로(같은 id를 가리킴)
        linked = await conn.fetchval("SELECT count(*) FROM ops_email_analysis a JOIN ops_voc_routing r ON r.id = a.routing_id "
                                     "JOIN ops_voc_cluster c ON c.id = a.voc_cluster_id WHERE a.namespace_id = $1 AND r.namespace_id = $1 "
                                     "AND c.namespace_id = $1", s)
        await move.revert(conn, log)
        return moved, linked, (await move.counts(conn, p), await move.counts(conn, s))
    (left, got), linked, (back, gone) = run(scenario)
    assert left == {t: 0 for t in move.TABLES} and got == {"ops_voc_routing": 1, "ops_email_analysis": 2, "ops_voc_cluster": 1}
    assert linked == 2
    assert back == got and gone == {t: 0 for t in move.TABLES}


def test_stops_when_target_has_same_mailbox_or_mail():
    async def scenario(conn):
        part = await _part(conn)
        if part is None:
            pytest.skip("파트 없음")
        p, s = await ns(conn, "시험_VOC 충돌 정책", "policy"), await ns(conn, "시험_VOC 충돌 시스템", "system")
        await _voc(conn, p, part)
        await _voc(conn, s, part, msgs=("m2",))
        src, dst = await move.ns_row(conn, "시험_VOC 충돌 정책"), await move.ns_row(conn, "시험_VOC 충돌 시스템")
        why = await move.check(conn, src, dst)
        with pytest.raises(SystemExit):
            await move.move(conn, src, dst, apply=True, log=None)
        return why, await move.counts(conn, p)
    why, left = run(scenario)
    assert why == ["받는 쪽에 같은 메일함 라우팅이 이미 있음", "받는 쪽에 같은 메일 분석이 이미 있음"]
    assert left == {"ops_voc_routing": 1, "ops_email_analysis": 2, "ops_voc_cluster": 1}   # 하나도 안 옮김


def test_rejects_wrong_direction():
    async def scenario(conn):
        p, s = await ns(conn, "시험_VOC 방향 정책", "policy"), await ns(conn, "시험_VOC 방향 시스템", "system")
        return await move.check(conn, await move.ns_row(conn, "시험_VOC 방향 시스템"), await move.ns_row(conn, "시험_VOC 방향 정책"))
    assert run(scenario) == ["보내는 쪽이 정책 컬렉션이 아님", "받는 쪽이 시스템 컬렉션이 아님"]
