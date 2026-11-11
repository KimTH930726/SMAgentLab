"""대상이 사라진 대기 정정 자동 반려(#127, 11/11 밤샘 점검 M4) 실 DB 시험 — 폐기된 정책을 겨눈 정정 #122·#189가 승인도 못 하고 대기로 남았다.
불변식(전체 개수로): 커밋 뒤 "대상이 현행(사용 중·검토 대기)이 아니고 이어받을 현행도 없는 대기 정정 = 0" · 이어받을 현행이 있으면 대기 그대로 +
목록에 [대상 폐기] · 원문 확인 카드(source_check)는 건드리지 않음. 트리거는 커밋 때 돈다 — 시험은 SET CONSTRAINTS ALL IMMEDIATE로 그 시점을 당긴다.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))   # tests/ 공용 장치
from realdb import knowledge, load, ns, run  # noqa: E402

sys.modules.setdefault("service.improvement", MagicMock())
database = load("_rdb_real_database_orphaned", "core/database.py")
with patch.dict(sys.modules, {"core.database": database}):
    sys.modules["service.improvement"].draft = load("_rdb_real_draft_orphaned", "service/improvement/draft.py")
    svc = load("_rdb_real_improvement_orphaned", "service/improvement/service.py")
NS = "시험_대상폐기 시스템"

# 불변식 — 시험 컬렉션의 대기 정정 중 대상이 현행이 아니고 이어받을 현행도 없는 것(트리거 규칙과 따로 쓴 같은 정의)
ORPHANS_SQL = """
SELECT i.id FROM ops_improvement_item i JOIN ops_namespace n ON n.id = i.namespace_id AND n.name = $1
 LEFT JOIN rag_knowledge k ON i.target_type = 'knowledge' AND k.id = i.target_id
 LEFT JOIN policy_item p ON i.target_type IN ('policy_param', 'policy_narrative') AND p.id = i.target_id
 WHERE i.status = 'pending' AND i.kind <> 'source_check' AND (
   (i.target_type = 'knowledge' AND coalesce(k.status, 'gone') NOT IN ('active', 'pending_review') AND NOT EXISTS (
      SELECT 1 FROM rag_knowledge s WHERE s.status IN ('active', 'pending_review') AND s.id <> i.target_id AND k.id IS NOT NULL
        AND (s.content_norm_md5 = k.content_norm_md5 OR coalesce(s.logical_document_id, s.id) = coalesce(k.logical_document_id, k.id)
             OR (s.confluence_page_id = k.confluence_page_id AND s.namespace_id = k.namespace_id)
             OR s.source_document_id IN (WITH RECURSIVE c(id) AS (SELECT k.source_document_id UNION
                                           SELECT d.superseded_by FROM rag_source_document d JOIN c ON d.id = c.id WHERE d.superseded_by IS NOT NULL)
                                         SELECT id FROM c))))
   OR (i.target_type IN ('policy_param', 'policy_narrative') AND coalesce(p.status, 'gone') NOT IN ('active', 'pending_review')
       AND NOT EXISTS (SELECT 1 FROM policy_item s WHERE s.logical_id = p.logical_id AND s.id <> p.id
                         AND s.status IN ('active', 'pending_review'))))
"""


async def _policy(conn, ns_id, status="active", logical_id=None, version=1):
    return await conn.fetchval(
        "INSERT INTO policy_item (namespace_id, policy_name, raw_body, content_hash, status, logical_id, version) "
        "VALUES ($1, '시험_정책', '본문', md5(random()::text), $2, $3, $4) RETURNING id", ns_id, status, logical_id, version)


async def _item(conn, ns_id, target_type, target_id, kind="correction"):
    return await conn.fetchval(
        "INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, target_sub_id, user_input) "
        "VALUES ($1, 'chat', $2, $3, $4, 1, '시험') RETURNING id", kind, ns_id, target_type, target_id)


async def _commit_point(conn):
    """커밋 때 판정을 여기서 — 트랜잭션마다 한 번만 도는 표시를 지워 다음 단계에서 다시 돌게(같은 트랜잭션 안 여러 시점 시험용)."""
    await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    await conn.execute("SELECT set_config('opslens.orphan_sweep', '', true)")
    await conn.execute("SET CONSTRAINTS ALL DEFERRED")


def _scenario(body):
    async def go(conn):
        ns_id = await ns(conn, NS)
        out = await body(conn, ns_id)
        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        st = {r["id"]: (r["status"], r["reject_reason"], r["ai_verdict"] or "") for r in await conn.fetch(
            "SELECT id, status, reject_reason, ai_verdict::text FROM ops_improvement_item WHERE id = ANY($1::int[])", out["items"])}
        orphans = [r["id"] for r in await conn.fetch(ORPHANS_SQL, NS)]
        return out, st, await svc.list_items(NS, "pending", user=None), orphans
    return run(go, [svc])


def test_policy_gone_rejects_pending_and_leaves_others():
    async def body(conn, ns_id):
        gone, kept = await _policy(conn, ns_id), await _policy(conn, ns_id)
        a, b = await _item(conn, ns_id, "policy_narrative", gone), await _item(conn, ns_id, "policy_param", gone)
        other = await _item(conn, ns_id, "policy_narrative", kept)
        await conn.execute("UPDATE policy_item SET status = 'deprecated' WHERE id = $1", gone)
        return {"items": [a, b, other], "a": a, "b": b, "other": other}
    out, st, _listed, orphans = _scenario(body)
    for i in (out["a"], out["b"]):
        assert st[i][0] == "rejected" and st[i][1].startswith("대상 폐기") and "target_gone" in st[i][2]
    assert st[out["other"]][0] == "pending" and orphans == []


def test_policy_new_version_later_in_same_tx_keeps_pending_and_marks_stale():
    """옛 판을 먼저 내리고 새 판을 나중에 넣어도(같은 트랜잭션) 커밋 때 보면 이어받을 현행이 있다 — 대기로 두고 [대상 폐기] 표시."""
    async def body(conn, ns_id):
        old = await _policy(conn, ns_id)
        item = await _item(conn, ns_id, "policy_narrative", old)
        await conn.execute("UPDATE policy_item SET status = 'deprecated' WHERE id = $1", old)
        await _policy(conn, ns_id, logical_id=old, version=2)
        return {"items": [item], "item": item}
    out, st, listed, orphans = _scenario(body)
    assert st[out["item"]][0] == "pending" and orphans == []
    assert next(d for d in listed if d["id"] == out["item"])["target_stale"] is True


def test_knowledge_gone_rejects_but_successor_later_in_same_tx_keeps():
    async def body(conn, ns_id):
        gone = await knowledge(conn, ns_id, "시험 지운 글")
        rechunked = await knowledge(conn, ns_id, "시험 다시 나눈 글", confluence_page_id="시험P1")
        live = await knowledge(conn, ns_id, "시험 그대로인 글")
        a, b, c = (await _item(conn, ns_id, "knowledge", gone), await _item(conn, ns_id, "knowledge", rechunked),
                   await _item(conn, ns_id, "knowledge", live))
        await conn.execute("UPDATE rag_knowledge SET status = 'deleted' WHERE id = $1", gone)
        await conn.execute("UPDATE rag_knowledge SET status = 'deprecated' WHERE id = $1", rechunked)
        await knowledge(conn, ns_id, "시험 다시 나눈 글의 새 조각", confluence_page_id="시험P1")   # 폐기 뒤에 넣은 같은 페이지 현행
        return {"items": [a, b, c], "a": a, "b": b, "c": c}
    out, st, listed, orphans = _scenario(body)
    assert st[out["a"]][0] == "rejected"
    assert st[out["b"]][0] == "pending" and st[out["c"]][0] == "pending" and orphans == []
    stale = {d["id"]: d["target_stale"] for d in listed}
    assert stale[out["b"]] is True and stale[out["c"]] is False


def test_successor_later_gone_rejects_old_target_item():
    """리뷰 중간 1 — K1 → (같은 페이지) K2로 이어받아 대기로 둔 정정이, 그 뒤 K2도 지워지면(다른 트랜잭션 시점) 반려된다."""
    async def body(conn, ns_id):
        k1 = await knowledge(conn, ns_id, "시험 첫 판", confluence_page_id="시험P2")
        item = await _item(conn, ns_id, "knowledge", k1)
        await conn.execute("UPDATE rag_knowledge SET status = 'deprecated' WHERE id = $1", k1)
        k2 = await knowledge(conn, ns_id, "시험 둘째 판", confluence_page_id="시험P2")
        await _commit_point(conn)
        kept = await conn.fetchval("SELECT status FROM ops_improvement_item WHERE id = $1", item)
        await conn.execute("UPDATE rag_knowledge SET status = 'deleted' WHERE id = $1", k2)
        return {"items": [item], "item": item, "kept": kept}
    out, st, _listed, orphans = _scenario(body)
    assert out["kept"] == "pending"
    assert st[out["item"]][0] == "rejected" and orphans == []


def test_hold_to_pending_review_keeps_item():
    """리뷰 중간 2 — 읽기 제한 보류(사용 중 → 검토 대기)는 되돌아올 수 있다: 반려하지 않는다(카드엔 승인 불가 표시)."""
    async def body(conn, ns_id):
        k = await knowledge(conn, ns_id, "시험 보류될 글", confluence_page_id="시험P3")
        item = await _item(conn, ns_id, "knowledge", k)
        await conn.execute("UPDATE rag_knowledge SET status = 'pending_review' WHERE id = $1", k)
        return {"items": [item], "item": item}
    out, st, listed, orphans = _scenario(body)
    assert st[out["item"]][0] == "pending" and orphans == []
    assert next(d for d in listed if d["id"] == out["item"])["target_stale"] is True


def test_replaced_source_document_counts_as_successor():
    """리뷰 낮음 5 — 파일 재등록은 새 원문 + 옛 원문 superseded_by: 새 원문의 조각이 있으면 이어받은 것."""
    async def body(conn, ns_id):
        async def doc():
            return await conn.fetchval(
                "INSERT INTO rag_source_document (namespace_id, source_type, source_ref, title, raw_format, raw_content, content_hash, status) "
                "VALUES ($1, 'file', '시험.xlsx', '시험', 'text', '시험', md5(random()::text) || md5(random()::text), 'active') RETURNING id",
                ns_id)
        d1, d2, d3, e1, e2 = await doc(), await doc(), await doc(), await doc(), await doc()
        for old, new in ((d1, d2), (d2, d3), (e1, e2)):   # d: 두 번 교체(A→B→C, 조각은 C에만 — 재리뷰 테스트 보강) · e: 교체됐지만 새 판 조각 없음
            await conn.execute("UPDATE rag_source_document SET status = 'superseded', superseded_by = $2 WHERE id = $1", old, new)
        k1 = await knowledge(conn, ns_id, "시험 옛 파일 조각", source_document_id=d1)
        g1 = await knowledge(conn, ns_id, "시험 조각 없는 새 판의 옛 조각", source_document_id=e1)
        item, gone = await _item(conn, ns_id, "knowledge", k1), await _item(conn, ns_id, "knowledge", g1)
        await knowledge(conn, ns_id, "시험 새 파일 조각(내용 바뀜)", source_document_id=d3)
        await conn.execute("UPDATE rag_knowledge SET status = 'deprecated' WHERE id = ANY($1::int[])", [k1, g1])
        return {"items": [item, gone], "item": item, "gone": gone}
    out, st, _listed, orphans = _scenario(body)
    assert st[out["item"]][0] == "pending" and st[out["gone"]][0] == "rejected" and orphans == []


def test_source_check_card_untouched():
    """리뷰 중간 3 — 원문 확인 카드(target_id = 정책 logical_id)는 자기 처리 흐름이 있다: 정책이 사라져도 트리거가 닫지 않는다."""
    async def body(conn, ns_id):
        pol = await _policy(conn, ns_id)
        card = await _item(conn, ns_id, "policy", pol, kind="source_check")
        await conn.execute("UPDATE policy_item SET status = 'deprecated' WHERE id = $1", pol)
        return {"items": [card], "card": card}
    out, st, _listed, _orphans = _scenario(body)
    assert st[out["card"]][0] == "pending"
