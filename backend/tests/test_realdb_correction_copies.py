"""같은 글 사본까지 정정(v2.228, work-os 검수 11/10) 실 DB 시험 — 검색은 본문이 같은(content_norm_md5) 사본을 한 자리로 합쳐 근거·[답변 틀림]
대상이 그 1건뿐이라, 정정하면 사본이 옛 글로 남아 다음 검색에 따로 한 자리를 받았다.
불변식: 정정 승인 뒤 "옛 글을 가진 사용 중 사본 = 0"(명시적으로 남긴 것·고칠 권한이 없는 컬렉션 것만 예외). 트랜잭션 안에서 만들고 되돌린다.
"""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))   # tests/ 공용 장치
from realdb import knowledge, load, ns, run  # noqa: E402

# conftest가 service·core를 가짜로 바꿔 둔다 — 정정 서비스가 import 때 쓰는 범위 SQL(core.database)은 실제 것으로(test_improvement과 같은 방식)
sys.modules.setdefault("service.improvement", MagicMock())
database = load("_rdb_real_database_copies", "core/database.py")
with patch.dict(sys.modules, {"core.database": database}):
    sys.modules["service.improvement"].draft = load("_rdb_real_draft_copies", "service/improvement/draft.py")
    svc = load("_rdb_real_improvement_copies", "service/improvement/service.py")
    deps_real = load("_rdb_real_dependencies_copies", "core/dependencies.py")
OLD = "결제 취소는 30분 안에 할 수 있다."
NEW = "결제 취소는 1시간 안에 할 수 있다."


async def _setup(conn):
    mine = await ns(conn, "시험_사본정정 시스템")
    other = await ns(conn, "시험_사본정정 다른 시스템")
    part = await conn.fetchval("INSERT INTO ops_part (name) VALUES ('시험_사본정정 파트') RETURNING id")
    await conn.execute("INSERT INTO ops_namespace_owner (namespace_id, part_id) VALUES ($1, $2)", mine, part)
    target = await knowledge(conn, mine, OLD, confluence_page_id="P1", heading_path=["페이지 A"])
    same_ns = await knowledge(conn, mine, OLD + "  ", confluence_page_id="P2", heading_path=["페이지 B"])   # 공백만 다른 같은 글
    other_ns = await knowledge(conn, other, OLD, confluence_page_id="P3", heading_path=["다른 시스템 페이지"])   # 권한 없는 컬렉션
    differ = await knowledge(conn, mine, OLD + " 단, 예약 상품은 안 된다.", confluence_page_id="P4")              # 같은 글 아님
    item = await conn.fetchval(
        "INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_input, original, proposed) "
        "VALUES ('correction', 'chat', $1, 'knowledge', $2, '시간이 바뀜', $3::jsonb, $4::jsonb) RETURNING id",
        mine, target, '{"content": "%s"}' % OLD, '{"content": "%s"}' % NEW)
    return dict(mine=mine, part=part, target=target, same_ns=same_ns, other_ns=other_ns, differ=differ, item=item)


def _approve(exclude=None, role="user", exclude_of=None, extra=None):
    """exclude_of = ids 키 이름 목록(셋업 뒤에야 id를 안다) · extra(conn, ids) = 승인 전 더 할 일(다른 대기 신고 등)."""
    async def scenario(conn):
        ids = await _setup(conn)
        if extra:
            await extra(conn, ids)
        user = {"id": None, "role": role, "part_id": ids["part"], "username": "시험"}
        ex = exclude if exclude_of is None else [ids[k] for k in exclude_of]
        with patch.object(svc, "embedding_service", SimpleNamespace(embed=AsyncMock(return_value=[0.0] * 1024))), \
             patch.object(svc, "settings", SimpleNamespace(embedding_model="시험")), \
             patch.dict(sys.modules, {"core.dependencies": deps_real}):
            listed = await svc.list_items("시험_사본정정 시스템", "pending", user=user)
            out = await svc.approve(ids["item"], user, {"content": NEW}, exclude_copy_ids=ex)
        old_md5 = await conn.fetchval("SELECT content_norm_md5 FROM rag_knowledge WHERE id = $1", ids["target"])
        left = [r["id"] for r in await conn.fetch(
            "SELECT id FROM rag_knowledge WHERE status = 'active' AND content_norm_md5 = $1 ORDER BY id", old_md5)]
        ids["after"] = {r["id"]: (r["status"], r["ai_verdict"]) for r in await conn.fetch(
            "SELECT id, status, ai_verdict FROM ops_improvement_item WHERE namespace_id = $1", ids["mine"])}
        return ids, listed, out, left
    return run(scenario, [svc])


def test_listing_shows_copies_with_permission():
    ids, listed, _out, _left = _approve()
    card = next(c for c in listed if c["id"] == ids["item"])
    assert {(c["id"], c["editable"]) for c in card["copies"]} == {(ids["same_ns"], True), (ids["other_ns"], False)}


def test_default_corrects_every_editable_copy_and_leaves_no_old_text():
    """불변식: 기본(전부 함께) 승인 뒤 옛 글 사용 중 행 = 권한 없는 컬렉션 사본뿐 · 같은 글 아닌 조각은 그대로 · 원장에 한 일 기록."""
    import json
    ids, _listed, out, left = _approve()
    assert left == [ids["other_ns"]]
    assert [c["id"] for c in out["copies_applied"]] == [ids["same_ns"]] and out["copies_left"] == [ids["other_ns"]]
    assert ids["differ"] not in [c["id"] for c in out["copies_applied"]]
    verdict = ids["after"][ids["item"]][1]
    rec = (json.loads(verdict) if isinstance(verdict, str) else verdict)["copies"]
    assert [c["id"] for c in rec["applied"]] == [ids["same_ns"]] and rec["left"] == [ids["other_ns"]]


def test_unchecked_copy_stays_old_and_only_it():
    """불변식의 예외는 체크를 푼 것뿐 — 푼 사본만 옛 글, 모르는 id를 빼 달라고 해도 해 없음."""
    ids, _listed, out, left = _approve(exclude_of=["same_ns"])
    assert left == sorted([ids["same_ns"], ids["other_ns"]]) and out["copies_applied"] == []
    _ids, _l, out2, _left2 = _approve(exclude=[0])
    assert [c["id"] for c in out2["copies_applied"]] == [_ids["same_ns"]]


def test_pending_report_on_a_corrected_copy_is_closed():
    """같이 정정한 사본을 겨눈 다른 대기 신고는 자동 종료(대상이 현행 아님 — 리뷰 L2)."""
    async def other_report(conn, ids):
        ids["other_item"] = await conn.fetchval(
            "INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_input, original, proposed) "
            "VALUES ('correction', 'chat', $1, 'knowledge', $2, '사본 쪽 신고', '{}'::jsonb, '{}'::jsonb) RETURNING id", ids["mine"], ids["same_ns"])
    ids, _listed, _out, _left = _approve(extra=other_report)
    assert ids["after"][ids["other_item"]][0] == "rejected"


def test_copy_changed_after_listing_is_refused_atomically():
    """사본을 찾은 뒤 잠그기 전에 본문이 바뀐 경우(병합 등) — 바뀐 글을 덮어쓰지 않고 아무것도 반영 안 함(409, 리뷰 L3)."""
    real = svc.knowledge_copies

    async def stale(conn, kid, user):   # 찾을 땐 같은 글이던 '다른 글' 조각이 사본 목록에 끼어 있는 상황
        rows = await real(conn, kid, user)
        differ = await conn.fetchrow("SELECT id, namespace_id FROM rag_knowledge WHERE content LIKE '%예약 상품은 안 된다.'")
        return rows + [{"id": differ["id"], "namespace_id": differ["namespace_id"], "namespace": "x", "heading_path": [], "editable": True}]
    with patch.object(svc, "knowledge_copies", stale), pytest.raises(svc.ConflictError):
        _approve()


def test_admin_default_corrects_copies_in_every_collection():
    """관리자는 모든 컬렉션을 고칠 수 있다 — 기본 승인 뒤 옛 글 사용 중 행 = 0."""
    ids, _listed, out, left = _approve(role="admin")
    assert left == [] and sorted(c["id"] for c in out["copies_applied"]) == sorted([ids["same_ns"], ids["other_ns"]])


def test_golden_gold_follows_corrected_version_and_its_copies():
    """정답 id가 정정으로 폐기되면 골든셋은 새 버전과 그 사본을 정답으로 본다(독립 점검 M6 — md5만 보면 사본까지 같이 고친 뒤 정답 0개)."""
    import json as _json
    kes = load("_rdb_real_eval_set_copies", "scripts/knowledge_eval_set.py")

    async def scenario(conn):
        ids = await _setup(conn)
        user = {"id": None, "role": "admin", "part_id": ids["part"], "username": "시험"}
        with patch.object(svc, "embedding_service", SimpleNamespace(embed=AsyncMock(return_value=[0.0] * 1024))), \
             patch.object(svc, "settings", SimpleNamespace(embedding_model="시험")), \
             patch.dict(sys.modules, {"core.dependencies": deps_real}):
            out = await svc.approve(ids["item"], user, {"content": NEW})
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(_json.dumps({"qid": "q1", "ns": "시험_사본정정 시스템", "gold_ids": [ids["target"]], "question": "q"}, ensure_ascii=False))
        try:
            items = await kes.load_items(conn, f.name)
        finally:
            os.unlink(f.name)
        return out, items
    out, items = run(scenario, [svc])
    want = {out["applied_target_id"], *(c["new_id"] for c in out["copies_applied"])}
    assert want <= set(items[0]["gold_ids"])
