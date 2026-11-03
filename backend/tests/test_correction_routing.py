"""정정 라우팅 경우의 수(11/03 M2 — "정정은 근거가 속한 컬렉션 담당에게", 태훈 "컬렉션 연결이 복잡해져 다양한 케이스 테스트") — 실제 Postgres.

가짜 연결로 "이 SQL이 불렸다"만 보면 연결 방향(supports from=시스템 → to=정책)·상위 정책 상속이 틀려도 통과한다. 그래서 실제 DB에
트랜잭션을 열고 시험 컬렉션·연결·지식·정책을 넣은 뒤 끝나면 되돌린다(데이터 안 남김). DB가 없으면 건너뜀.

경우(물은 곳 × 근거가 있는 곳):
  정책 P에서 물음 · 근거 = P의 지식/정책            → 받음, 처리할 곳 P(예전과 같음)
  정책 P에서 물음 · 근거 = P를 받치는 시스템 S 지식   → 받음, 처리할 곳 S
  정책 P의 하위 Pc에서 물음 · 근거 = S 지식           → 받음(상위 연결 상속), 처리할 곳 S
  시스템 S에서 물음 · 근거 = S의 관련 정책 P          → 받음, 처리할 곳 P
  정책 P에서 물음 · 근거 = 연결 없는 시스템 S2 지식   → 안 받음(범위 밖)
  연결 범위 없이(reach=False, 정책 화면 제안 등)      → 연결된 S 지식 안 받음(예전 범위 그대로)
  연결이 꺼짐(status≠active)                          → 안 받음
승인 쪽: 처리할 곳 S로 S 지식 정정 = 됨 / 물은 곳 P로는 범위 밖이라 안 됨(그래서 라우팅이 필요) · 정책 컬렉션 "빠진 내용" 승인 = 막힘(지식 수 그대로).
"""
import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sys.modules.setdefault("service.improvement", MagicMock())
if "service.improvement.draft" not in sys.modules:
    sys.modules["service.improvement.draft"] = _load("service.improvement.draft", "service/improvement/draft.py")
sys.modules["service.improvement"].draft = sys.modules["service.improvement.draft"]
svc = _load("_routing_real_improvement", "service/improvement/service.py")
real_db = _load("_routing_real_database", "core/database.py")
# 범위 SQL은 실제 조각으로(conftest 가짜면 MagicMock 글자가 SQL에 박힌다) — 모듈 상수는 로드 때 이미 실제 조각으로 만들어졌다
assert "MagicMock" not in svc._IN_REACH_2
svc.settings = MagicMock(embedding_model="kure-test")   # 이 파일 전용 사본 — conftest 가짜 settings는 SQL 인자로 못 넘긴다

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
            ids = await _seed(conn)
            return await scenario(conn, ids)
        finally:
            await tx.rollback()   # 시험 데이터를 남기지 않는다
            await conn.close()
    try:
        return asyncio.run(go())
    except OSError:
        pytest.skip("DB 연결 안 됨")


async def _seed(conn) -> dict:
    async def ns(name, area, parent=None):
        return await conn.fetchval("INSERT INTO ops_namespace (name, kind, area, parent_id) VALUES ($1, 'domain', $2, $3) RETURNING id",
                                   name, area, parent)
    p = await ns("시험_정책P", "policy")
    pc = await ns("시험_정책P_하위", "policy", p)
    s = await ns("시험_시스템S 시스템", "system")
    s2 = await ns("시험_시스템S2 시스템", "system")
    await conn.execute("INSERT INTO ops_link (from_type, from_key, relation, to_type, to_key, source) "
                       "VALUES ('domain', $1, 'supports', 'domain', $2, 'manual')", str(s), str(p))

    async def know(ns_id):
        return await conn.fetchval("INSERT INTO rag_knowledge (namespace_id, content, embedding, status) VALUES ($1, '시험 지식', $2::vector, 'active') "
                                   "RETURNING id", ns_id, VEC)

    async def policy(ns_id):
        return await conn.fetchval("INSERT INTO policy_item (namespace_id, content_hash, policy_name, raw_body, status) "
                                   "VALUES ($1, md5(random()::text), '시험 정책', '원문', 'active') RETURNING id", ns_id)
    return {"P": p, "Pc": pc, "S": s, "S2": s2, "kP": await know(p), "kS": await know(s), "kS2": await know(s2),
            "pP": await policy(p)}


async def _reach(conn, asked, target_type, target_id, reach=True) -> bool:
    try:
        await svc._load_original(conn, asked, target_type, target_id, None if target_type == "knowledge" else 0, None, reach=reach)
        return True
    except ValueError as e:
        return "파라미터" in str(e) or "서술" in str(e)   # 정책 행 자체는 찾았고 하위(파라미터·서술) 단계에서 멈춤 = 범위 안


@pytest.mark.parametrize("asked,target,expect_reach,expect_home", [
    ("P", ("knowledge", "kP"), True, "P"),
    ("P", ("knowledge", "kS"), True, "S"),
    ("Pc", ("knowledge", "kS"), True, "S"),
    ("S", ("knowledge", "kS"), True, "S"),
    ("S", ("policy_narrative", "pP"), True, "P"),
    ("P", ("knowledge", "kS2"), False, None),
    ("S2", ("knowledge", "kS"), False, None),
])
def test_routing_matrix(asked, target, expect_reach, expect_home):
    async def scenario(conn, ids):
        tt, key = target
        got_reach = await _reach(conn, ids[asked], tt, ids[key])
        home = await svc._home_ns(conn, tt, ids[key], ids[asked]) if got_reach else None
        return got_reach, home, ids
    got_reach, home, ids = _run(scenario)
    assert got_reach is expect_reach
    if expect_home:
        assert home == ids[expect_home]


def test_without_reach_keeps_old_scope():
    """정책 화면 제안·평가 게이트 표시처럼 답변 근거가 아닌 길은 예전 범위 — 연결된 시스템 지식을 받지 않는다."""
    async def scenario(conn, ids):
        return await _reach(conn, ids["P"], "knowledge", ids["kS"], reach=False), await _reach(conn, ids["P"], "knowledge", ids["kP"], reach=False)
    assert _run(scenario) == (False, True)


def test_inactive_link_not_reached():
    async def scenario(conn, ids):
        await conn.execute("UPDATE ops_link SET status = 'inactive' WHERE relation = 'supports' AND from_key = $1", str(ids["S"]))
        return await _reach(conn, ids["P"], "knowledge", ids["kS"])
    assert _run(scenario) is False


def test_missing_and_answer_stay_with_asked():
    async def scenario(conn, ids):
        return [await svc._home_ns(conn, t, None, ids["P"]) == ids["P"] for t in ("missing", "answer", "auto")]
    assert all(_run(scenario))


def test_apply_needs_routed_home():
    """승인은 처리할 곳 범위로 원본을 다시 잡는다 — 물은 곳(P)로는 S 지식이 범위 밖이라 실패, 라우팅된 S로는 새 버전이 S에 생긴다."""
    async def scenario(conn, ids):
        with pytest.raises(ValueError):
            async with conn.transaction():
                await svc._apply_knowledge(conn, ids["P"], ids["kS"], "고친 내용", [0.0] * 1024)
        new_id = await svc._apply_knowledge(conn, ids["S"], ids["kS"], "고친 내용", [0.0] * 1024)
        return await conn.fetchval("SELECT namespace_id FROM rag_knowledge WHERE id = $1", new_id), ids
    ns_id, ids = _run(scenario)
    assert ns_id == ids["S"]


def test_missing_in_policy_collection_blocked_count_unchanged():
    async def scenario(conn, ids):
        before = await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1", ids["P"])
        with pytest.raises(ValueError, match="정책 컬렉션엔 운영 지식"):
            await svc._apply_missing(conn, ids["P"], "빠진 내용", [0.0] * 1024, {"id": None, "username": "t"})
        after = await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1", ids["P"])
        made = await svc._apply_missing(conn, ids["S"], "빠진 내용", [0.0] * 1024, {"id": None, "username": "t"})
        return before, after, await conn.fetchval("SELECT namespace_id FROM rag_knowledge WHERE id = $1", made), ids
    before, after, made_ns, ids = _run(scenario)
    assert before == after
    assert made_ns == ids["S"]


def test_every_direct_knowledge_insert_is_guarded():
    """경계 테스트(재리뷰 제안) — rag_knowledge에 직접 INSERT하는 앱 코드 목록을 고정한다. 새 경로가 생기면 여기서 걸려
    "정책 컬렉션엔 운영 지식 금지" 검사를 붙였는지 보게 된다(스크립트·테스트는 제외)."""
    allowed = {
        "agents/knowledge_rag/knowledge/service.py",   # create_knowledge·bulk_create_knowledge — resolve_knowledge_namespace_id
        "service/improvement/service.py",              # _apply_knowledge(원래 행의 컬렉션) · _apply_missing(정책이면 막음)
        "service/policy/track2.py",                    # 옛 비교 실행 — 시험 컬렉션(kind=policy_test)에만
    }
    found = set()
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "scripts/", "eval_out/", "libs/")):
            continue
        if "INSERT INTO rag_knowledge\n" in path.read_text(encoding="utf-8") or "INSERT INTO rag_knowledge " in path.read_text(encoding="utf-8"):
            found.add(rel)
    assert found == allowed, found ^ allowed


def test_write_item_both_shapes_record_asked():
    """_write_item — asked를 주면(답변 신고: 처리할 곳 ≠ 물은 곳일 수 있음) 그 값, 안 주면(정책 화면 제안·정책 변경) 처리할 곳과 같다.
    가짜 연결 테스트가 못 잡고 E2E에서 500으로 드러난 경로(11/03) — 실제 INSERT로 칸 수·값을 본다."""
    async def scenario(conn, ids):
        base = ("correction", "direct", ids["S"], None, None, "knowledge", ids["kS"], None, "의견", "{}", None, "[]", None)
        a = await svc._write_item(conn, base, None, asked=ids["P"])
        b = await svc._write_item(conn, (*base[:6], ids["kP"], *base[7:]), None)
        rows = {r["id"]: (r["namespace_id"], r["asked_namespace_id"]) for r in await conn.fetch(
            "SELECT id, namespace_id, asked_namespace_id FROM ops_improvement_item WHERE id = ANY($1::bigint[])", [a, b])}
        return rows[a], rows[b], ids
    a, b, ids = _run(scenario)
    assert a == (ids["S"], ids["P"])   # 정책 P에서 물었고 S 담당이 처리
    assert b == (ids["S"], ids["S"])


def test_retarget_to_linked_evidence_moves_home_keeps_asked():
    """정책 P에서 신고 → 담당이 대상을 연결된 시스템 S의 지식으로 바꿈 → 처리할 곳 S, 물은 곳 P 그대로(M2 리뷰 L4 — 가짜 연결 테스트는
    _home_ns를 항등으로 고정해 retarget이 무엇을 넘기는지 못 본다). 초안 생성(LLM)만 가짜."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, patch

    async def scenario(conn, ids):
        item_id = await conn.fetchval(
            "INSERT INTO ops_improvement_item (kind, source, namespace_id, asked_namespace_id, target_type, user_input, original, candidates) "
            "VALUES ('correction', 'auto', $1, $1, 'answer', '로그인 순서가 바뀜', '{}'::jsonb, $2::jsonb) RETURNING id",
            ids["P"], '[{"key": "k-%d", "target_type": "knowledge", "target_id": %d, "target_sub_id": null, "label": "x", "preview": "x"}]'
            % (ids["kS"], ids["kS"]))

        @asynccontextmanager
        async def get_conn():
            yield conn
        with patch.object(svc, "get_conn", get_conn),                 patch.object(svc.draft_mod, "draft_correction", AsyncMock(return_value={"content": "고친 내용"})):
            await svc.retarget(item_id, {"id": None}, f"k-{ids['kS']}")
        row = await conn.fetchrow("SELECT namespace_id, asked_namespace_id, target_id FROM ops_improvement_item WHERE id = $1", item_id)
        return dict(row), ids
    row, ids = _run(scenario)
    assert row == {"namespace_id": ids["S"], "asked_namespace_id": ids["P"], "target_id": ids["kS"]}


def test_delete_collection_with_content_is_blocked():
    """살아 있는 내용이 있는 컬렉션은 지울 수 없다(11/03 설계 리뷰 #1 — CASCADE로 원문·정책·용어까지 사라졌다). 지운 지식·기록만 남았으면 지운다."""
    async def scenario(conn, ids):
        counts = await real_db.namespace_content_counts(conn, ids["S"])
        await conn.execute("UPDATE rag_knowledge SET status = 'deleted' WHERE namespace_id = $1", ids["S"])
        still = await real_db.namespace_content_counts(conn, ids["S"])
        empty = await conn.fetchval("INSERT INTO ops_namespace (name, kind, area) VALUES ('시험_빈 시스템', 'domain', 'system') RETURNING id")
        return counts, still, await real_db.namespace_content_counts(conn, empty)
    counts, still, empty = _run(scenario)
    assert counts == {"운영 지식": 1}
    assert still == {}   # 지운 지식은 이력 — 막지 않는다(재리뷰 M1: 세면 한 번이라도 쓴 컬렉션은 화면에서 영영 못 지웠다)
    assert empty == {}


def _policy_change():
    """policy_change는 service.improvement.service에서 가져온다 — 이 파일의 실제 사본을 그 이름으로 걸고 읽는다."""
    saved = sys.modules.get("service.improvement.service")
    sys.modules["service.improvement.service"] = svc
    try:
        return _load("_routing_real_policy_change", "service/improvement/policy_change.py")
    finally:   # 원래 없었으면 지운다 — 뒤 테스트가 이 사본을 가짜 대신 집지 않게(리뷰)
        if saved is not None:
            sys.modules["service.improvement.service"] = saved
        else:
            del sys.modules["service.improvement.service"]


def test_forward_missing_in_policy_collection_to_policy_add():
    """정책 컬렉션 "빠진 내용" → 정책 추가 제안(11/03): 새 제안 1건(대기) + 원래 신고 처리됨(forwarded_to). 시스템 컬렉션 건은 거부."""
    import json as _json
    from contextlib import asynccontextmanager
    from unittest.mock import patch
    pc = _policy_change()

    async def scenario(conn, ids):
        async def missing(ns):
            return await conn.fetchval("INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, user_input, original) "
                                       "VALUES ('missing_knowledge', 'auto', $1, 'missing', '픽업 취소 기한이 없어요', '{}'::jsonb) RETURNING id", ns)
        a, b = await missing(ids["P"]), await missing(ids["S"])

        @asynccontextmanager
        async def get_conn():
            yield conn
        with patch.object(pc, "get_conn", get_conn):
            out = await pc.forward_missing(a, {"id": None}, policy_name="픽업 취소 기한", category_path=["주문", "취소"],
                                           raw_body="픽업 주문은 준비 전까지 취소할 수 있다.", remark=None)
            with pytest.raises(ValueError, match="정책 컬렉션의 신고만"):
                await pc.forward_missing(b, {"id": None}, policy_name="x", category_path=["x"], raw_body="x", remark=None)
            with pytest.raises(ValueError, match="대기 중인"):   # 이미 넘긴 건은 다시 못 넘긴다
                await pc.forward_missing(a, {"id": None}, policy_name="y", category_path=["y"], raw_body="y", remark=None)
        src = await conn.fetchrow("SELECT status, ai_verdict FROM ops_improvement_item WHERE id = $1", a)
        new = await conn.fetchrow("SELECT status, target_type, namespace_id, proposed FROM ops_improvement_item WHERE id = $1", out["forwarded_to"])
        return dict(src), dict(new), ids   # 되돌리므로 시험 신고·제안은 남지 않는다
    src, new, ids = _run(scenario)
    verdict = _json.loads(src["ai_verdict"]) if isinstance(src["ai_verdict"], str) else src["ai_verdict"]
    assert src["status"] == "approved" and verdict["forwarded_to"]
    assert (new["status"], new["target_type"], new["namespace_id"]) == ("pending", "policy_add", ids["P"])
    proposed = _json.loads(new["proposed"]) if isinstance(new["proposed"], str) else new["proposed"]
    assert proposed["policy_name"] == "픽업 취소 기한" and proposed["category_path"] == ["주문", "취소"]


def test_gap_filled_by_policy_add_and_reopened_on_reject():
    """정책 컬렉션 지식 공백 → 정책 추가 제안(query_log_id) → 그 공백은 메움, 제안 반려 → 다시 공백(11/03 리뷰 M)."""
    from contextlib import asynccontextmanager
    from unittest.mock import patch
    pc = _policy_change()

    async def scenario(conn, ids):
        log = await conn.fetchval("INSERT INTO ops_query_log (namespace_id, question, status) VALUES ($1, '픽업 취소 기한?', 'no_knowledge') "
                                  "RETURNING id", ids["P"])

        @asynccontextmanager
        async def get_conn():
            yield conn
        with patch.object(pc, "get_conn", get_conn), patch.object(svc, "get_conn", get_conn),                 patch.object(pc, "resolve_namespace_id", real_db.resolve_namespace_id):   # conftest 가짜는 늘 1을 준다
            out = await pc.create("시험_정책P", {"id": None}, change="add", target_id=None, policy_name="픽업 취소 기한",
                                  category_path=["주문"], raw_body="준비 전까지 취소 가능", remark=None, reason=None, query_log_id=log)
            after_add = await conn.fetchval("SELECT resolved_correction_id FROM ops_query_log WHERE id = $1", log)
            await svc.reject(out["id"], {"id": None}, "정책 아님")
        after_reject = await conn.fetchrow("SELECT resolved_correction_id, resolved_at FROM ops_query_log WHERE id = $1", log)
        return out["id"], after_add, dict(after_reject)
    new_id, after_add, after_reject = _run(scenario)
    assert after_add == new_id
    assert after_reject == {"resolved_correction_id": None, "resolved_at": None}
