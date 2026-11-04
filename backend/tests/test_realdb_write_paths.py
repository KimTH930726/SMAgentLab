"""쓰기 경로 실 DB 시험(11/03 표준 확대) — 가짜 연결 테스트가 못 보는 SQL 의미(조건·연결 방향·제약)를 실제 Postgres에서 불변식으로.

각 시험은 트랜잭션 안에서 만들고 되돌린다(tests/realdb.py). DB가 없으면 건너뛴다.
"""
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))   # tests/ 공용 장치
from realdb import knowledge, load, ns, run  # noqa: E402

database = load("_rdb_real_database", "core/database.py")
cache = load("_rdb_real_cache", "shared/cache.py")
ksvc = load("_rdb_real_knowledge_service", "agents/knowledge_rag/knowledge/service.py")
move = load("_rdb_real_move", "scripts/move_knowledge_to_system.py")


# ── 지식 삭제 ─────────────────────────────────────────────────────────────

def test_delete_reopens_gap_and_is_soft():
    """지식 삭제 = 상태만 deleted(행은 남음) · 그 지식으로 메운 공백은 다시 공백 · 다른 공백은 그대로."""
    async def scenario(conn):
        s = await ns(conn, "시험_삭제 시스템")
        k, other = await knowledge(conn, s), await knowledge(conn, s, "다른 지식")
        gap = await conn.fetchval("INSERT INTO ops_query_log (namespace_id, question, status, resolved_knowledge_id, resolved_at) "
                                  "VALUES ($1, '질문', 'no_knowledge', $2, NOW()) RETURNING id", s, k)
        keep = await conn.fetchval("INSERT INTO ops_query_log (namespace_id, question, status, resolved_knowledge_id, resolved_at) "
                                   "VALUES ($1, '다른 질문', 'no_knowledge', $2, NOW()) RETURNING id", s, other)
        assert await ksvc.delete_knowledge(k) is True
        assert await ksvc.delete_knowledge(k) is False            # 두 번째는 바뀐 것 없음
        return (await conn.fetchval("SELECT status FROM rag_knowledge WHERE id = $1", k),
                await conn.fetchval("SELECT resolved_knowledge_id FROM ops_query_log WHERE id = $1", gap),
                await conn.fetchval("SELECT resolved_knowledge_id FROM ops_query_log WHERE id = $1", keep), other)
    st, gap_ref, keep_ref, other = run(scenario, [ksvc])
    assert st == "deleted" and gap_ref is None and keep_ref == other


def test_bulk_delete_counts_only_changed():
    async def scenario(conn):
        s = await ns(conn, "시험_묶음삭제 시스템")
        ids = [await knowledge(conn, s, f"지식 {i}") for i in range(3)]
        await conn.execute("UPDATE rag_knowledge SET status = 'deleted' WHERE id = $1", ids[0])
        n = await ksvc.bulk_delete_knowledge(ids)
        left = await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE id = ANY($1::int[]) AND status <> 'deleted'", ids)
        return n, left
    assert run(scenario, [ksvc]) == (2, 0)


# ── 운영 지식 → 시스템 이동 스크립트 ────────────────────────────────────────

def test_move_and_revert_conserve_rows():
    """옮기기 = 받는 쪽 후 = 전 + 옮긴 수 · 보내는 쪽 0 · 되돌리기 = 원래 자리, 원문·조각이 갈리지 않음(유실·중복 0)."""
    import tempfile

    async def scenario(conn):
        p = await ns(conn, "시험_이동 정책", "policy")
        s = await ns(conn, "시험_이동 시스템", "system")
        doc = await conn.fetchval("INSERT INTO rag_source_document (namespace_id, source_type, title, raw_content, raw_format, content_hash, status) "
                                  "VALUES ($1, 'paste', '원문', '본문', 'text', md5('본문'), 'active') RETURNING id", p)
        ks = [await knowledge(conn, p, f"조각 {i}", source_document_id=doc) for i in range(3)]
        before_dst = await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1", s)
        moving = await move.ids_of(conn, p)
        await move.set_ns(conn, moving, s)
        after = (await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1", s),
                 await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1", p),
                 await conn.fetchval("SELECT namespace_id FROM rag_source_document WHERE id = $1", doc))
        log = {"src": "시험_이동 정책", "src_id": p, "dst": "시험_이동 시스템", "dst_id": s, "moved": moving}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(log, f)
        await move.revert(conn, f.name)
        back = (await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1 AND id = ANY($2::int[])", p, ks),
                await conn.fetchval("SELECT namespace_id FROM rag_source_document WHERE id = $1", doc))
        return before_dst, after, back, p, s
    before_dst, after, back, p, s = run(scenario)   # 이동 함수는 연결을 인자로 받는다
    assert after == (before_dst + 3, 0, s)
    assert back == (3, p)


# ── 컬렉션 삭제 빈 자리 정리 ───────────────────────────────────────────────

def test_release_refs_nulls_and_requeues_import_rows():
    """컬렉션 삭제 직전 정리: 넣을 곳이 이 컬렉션인 자동 줄은 검토로 · FK 없는 id 칸 NULL · 연결 줄 삭제 · 다른 컬렉션 줄은 그대로."""
    async def scenario(conn):
        s = await ns(conn, "시험_정리 시스템")
        other = await ns(conn, "시험_정리 다른 시스템")
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        b = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, status, sampling_required, total_pages, done_pages, default_namespace_id) "
                                "VALUES ($1, 'https://confl.sinc.co.kr/display/X', 'preview', FALSE, 2, 2, $2) RETURNING id", team, s)
        pg = await conn.fetchval("INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, status, final_namespace_id, ai_namespace_id) "
                                 "VALUES ($1, 'p1', 't', 'b', 1, 'auto', $2, $2) RETURNING id", b, s)
        keep = await conn.fetchval("INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, status, final_namespace_id) "
                                   "VALUES ($1, 'p2', 't', 'b', 1, 'auto', $2) RETURNING id", b, other)
        await conn.execute("INSERT INTO ops_link (from_type, from_key, relation, to_type, to_key, source) VALUES ('domain', $1, 'supports', 'domain', $2, 'manual')",
                           str(s), str(other))
        await database.release_namespace_refs(conn, s)
        r = dict(await conn.fetchrow("SELECT status, final_namespace_id, ai_namespace_id FROM rag_import_page WHERE id = $1", pg))
        k = dict(await conn.fetchrow("SELECT status, final_namespace_id FROM rag_import_page WHERE id = $1", keep))
        return (r, k, await conn.fetchval("SELECT default_namespace_id FROM rag_import_batch WHERE id = $1", b),
                await conn.fetchval("SELECT count(*) FROM ops_link WHERE from_key = $1 OR to_key = $1", str(s)), other)
    r, k, default, links, other = run(scenario)
    assert r == {"status": "review", "final_namespace_id": None, "ai_namespace_id": None}
    assert k == {"status": "auto", "final_namespace_id": other}
    assert default is None and links == 0


# ── 답변 캐시: 연결된 컬렉션 이름 ──────────────────────────────────────────

def test_cache_linked_names_both_directions_and_children():
    """캐시 비우기 대상 = 관련 정책 연결 양방향 + 연결된 정책의 하위(채팅 linked 범위와 같게) · 꺼진 연결은 빼고."""
    async def scenario(conn):
        p = await ns(conn, "시험_캐시 정책", "policy")
        pc = await ns(conn, "시험_캐시 정책 하위", "policy", p)
        s = await ns(conn, "시험_캐시 시스템", "system")
        s2 = await ns(conn, "시험_캐시 꺼진 시스템", "system")
        await conn.execute("INSERT INTO ops_link (from_type, from_key, relation, to_type, to_key, source) VALUES ('domain', $1, 'supports', 'domain', $2, 'manual')",
                           str(s), str(p))
        await conn.execute("INSERT INTO ops_link (from_type, from_key, relation, to_type, to_key, source, status) VALUES ('domain', $1, 'supports', 'domain', $2, 'manual', 'inactive')",
                           str(s2), str(p))
        from_sys = sorted(r["name"] for r in await conn.fetch(cache._LINKED_NAMES_SQL, ["시험_캐시 시스템"]))
        from_child = sorted(r["name"] for r in await conn.fetch(cache._LINKED_NAMES_SQL, ["시험_캐시 정책 하위"]))
        return from_sys, from_child
    from_sys, from_child = run(scenario)
    assert from_sys == ["시험_캐시 정책", "시험_캐시 정책 하위"]   # 정책 + 그 하위(하위에서 물은 답에도 이 시스템 지식이 든다)
    assert from_child == ["시험_캐시 시스템"]                       # 하위 정책은 상위의 연결을 물려받는다 · 꺼진 연결 제외


# ── 기법 스위치 저장·읽기 ──────────────────────────────────────────────────

def test_technique_switch_roundtrip_in_db():
    """스위치를 쓰면 같은 줄이 DB에 남고(updated_at 갱신), 새 프로세스처럼 비운 뒤 읽어도 같은 값 · 다른 컬렉션 제안은 옛 줄이 기준."""
    flags = load("_rdb_real_flags", "core/technique_flags.py")

    async def scenario(conn):
        await flags.set_switch(conn, "context_budget", False, "시험")
        await flags.set_switch(conn, "collection_suggest", True, "시험")
        await conn.execute("UPDATE ops_system_config SET value = 'false' WHERE key = 'chat.collection_suggest'")   # SQL로 옛 줄만 바꿈
        flags._state.clear()
        await flags.load(conn)
        row = await conn.fetchrow("SELECT value, updated_at > NOW() - interval '1 minute' AS fresh FROM ops_system_config "
                                  "WHERE key = 'technique.context_budget'")
        return flags.is_on("context_budget"), flags.is_on("collection_suggest"), json.loads(row["value"])["by"], row["fresh"]
    out = run(scenario)
    flags._state.clear()
    assert out == (False, False, "시험", True)


# ── 정책 컬렉션 지식 거부(서비스 길목) ─────────────────────────────────────

def test_create_knowledge_rejects_policy_and_accepts_system():
    """등록 길목: 정책 컬렉션은 ValueError(행 0) · 시스템은 active 1행."""
    async def scenario(conn):
        p = await ns(conn, "시험_등록 정책", "policy")
        s = await ns(conn, "시험_등록 시스템", "system")
        with patch.object(ksvc, "resolve_knowledge_namespace_id", database.resolve_knowledge_namespace_id), \
                patch.object(ksvc, "_EMBEDDING_MODEL_NAME", "kure-test"), \
                patch.object(ksvc.embedding_service, "embed", AsyncMock(return_value=[0.0] * 1024)), \
                patch.object(ksvc, "find_similar_active_knowledge", AsyncMock(return_value=[])):
            with pytest.raises(ValueError, match="정책 컬렉션엔 운영 지식"):
                await ksvc.create_knowledge("시험_등록 정책", "정책에 넣으려는 운영 지식")
            row = await ksvc.create_knowledge("시험_등록 시스템", "시스템에 넣는 운영 지식")
        return (await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1", p),
                await conn.fetchval("SELECT status FROM rag_knowledge WHERE id = $1", row["id"]))
    assert run(scenario, [ksvc]) == (0, "active")


def test_create_knowledge_dedupe_two_stage():
    """단건 등록 중복 판정 2단(11/04): 공백만 다른 같은 글 = 거부(행 0) · 글자도 비슷 = 승인 대기 + 매칭 1행 · 의미만 비슷 = 사용 중, 매칭 0."""
    base = "## 배치 점검\n재기동 전 상태 확인\n담당 운영팀"

    async def scenario(conn):
        s = await ns(conn, "시험_중복2단 시스템")
        k1 = await knowledge(conn, s, base)
        near = lambda sim: AsyncMock(return_value=[{"id": k1, "content": base, "similarity": sim}])   # noqa: E731
        out = {}
        with patch.object(ksvc, "resolve_knowledge_namespace_id", database.resolve_knowledge_namespace_id), \
                patch.object(ksvc, "_EMBEDDING_MODEL_NAME", "kure-test"), \
                patch.object(ksvc.embedding_service, "embed", AsyncMock(return_value=[0.0] * 1024)):
            with patch.object(ksvc, "find_similar_active_knowledge", near(0.9999)):
                with pytest.raises(ValueError, match=f"같은 글이 이미 있어요\\(#{k1}\\)"):
                    await ksvc.create_knowledge("시험_중복2단 시스템", "  ## 배치 점검\n\n재기동 전 상태 확인 \n담당 운영팀")
            out["after_exact"] = await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1", s)
            with patch.object(ksvc, "find_similar_active_knowledge", near(0.97)):
                r = await ksvc.create_knowledge("시험_중복2단 시스템", "## 배치 점검\n재기동 전 상태 꼭 확인\n담당 운영팀")
            out["near"] = (r["status"], r["pending_review"],
                           await conn.fetchval("SELECT count(*) FROM rag_knowledge_duplicate_match WHERE new_knowledge_id = $1", r["id"]))
            with patch.object(ksvc, "find_similar_active_knowledge", near(0.95)):
                r = await ksvc.create_knowledge("시험_중복2단 시스템", "## 결제 대사\n매일 02시 정산 파일 비교\n담당 정산팀")
            out["sibling"] = (r["status"], r["pending_review"],
                              await conn.fetchval("SELECT count(*) FROM rag_knowledge_duplicate_match WHERE new_knowledge_id = $1", r["id"]))
        return out
    out = run(scenario, [ksvc])
    assert out == {"after_exact": 1, "near": ("pending_review", True, 1), "sibling": ("active", False, 0)}


# ── 팀 적재 롤백(게시 전·후 공통) ─────────────────────────────────────────

def test_rollback_batch_removes_held_and_active_chunks_and_reopens_gaps():
    """배치 롤백: 게시된 조각·검수 대기 조각 모두 deleted · 보류 작업 cancelled · 그 조각으로 메운 공백은 다시 공백 · 게시 중이면 거부."""
    ti = load("_rdb_real_team_import", "agents/knowledge_rag/ingestion/team_import.py")

    async def scenario(conn):
        s = await ns(conn, "시험_롤백 시스템")
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        done = await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks) VALUES ($1,'confluence','completed',1) RETURNING id", s)
        held = await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks, held_plan) VALUES ($1,'confluence','held',1,'{}'::jsonb) RETURNING id", s)
        k_active = await knowledge(conn, s, "게시된 조각", ingestion_job_id=done)
        k_held = await knowledge(conn, s, "검수 대기 조각", status="staging", ingestion_job_id=held)
        gap = await conn.fetchval("INSERT INTO ops_query_log (namespace_id, question, status, resolved_knowledge_id, resolved_at) "
                                  "VALUES ($1, 'q', 'no_knowledge', $2, NOW()) RETURNING id", s, k_active)
        b = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, status, sampling_required, total_pages, done_pages, job_ids, loaded_at, publishing_at) "
                                "VALUES ($1, 'https://confl.sinc.co.kr/display/R', 'loaded', FALSE, 1, 1, $2::int[], NOW(), NOW()) RETURNING id", team, [done, held])
        blocked = await ti.rollback_batch(conn, b)                          # 게시 중 → None(거부)
        await conn.execute("UPDATE rag_import_batch SET publishing_at = NULL WHERE id = $1", b)
        out = await ti.rollback_batch(conn, b)
        st = {r["id"]: r["status"] for r in await conn.fetch("SELECT id, status FROM rag_knowledge WHERE id = ANY($1::int[])", [k_active, k_held])}
        return (blocked, out["chunks"], st[k_active], st[k_held],
                await conn.fetchval("SELECT status FROM rag_ingestion_job WHERE id = $1", held),
                await conn.fetchval("SELECT resolved_knowledge_id FROM ops_query_log WHERE id = $1", gap),
                await conn.fetchval("SELECT status FROM rag_import_batch WHERE id = $1", b))
    assert run(scenario) == (None, 2, "deleted", "deleted", "cancelled", None, "rolled_back")


# ── 관련 정책 연결 저장 ────────────────────────────────────────────────────

def test_set_supports_replaces_links_and_rejects_wrong_kinds():
    """시스템의 관련 정책 = 통째 교체(중복 제거) · 정책 컬렉션에는 못 정함 · 대상이 시스템이면 거부 · 아무것도 안 바뀜."""
    admin = load("_rdb_real_admin_service", "service/admin/service.py")

    async def scenario(conn):
        s = await ns(conn, "시험_연결 시스템")
        p1, p2 = await ns(conn, "시험_연결 정책1", "policy"), await ns(conn, "시험_연결 정책2", "policy")
        other_sys = await ns(conn, "시험_연결 다른 시스템")
        with patch.object(admin, "_forget_cached_answers", AsyncMock()):
            assert await admin.set_supports("시험_연결 시스템", ["시험_연결 정책1", "시험_연결 정책1", "시험_연결 정책2"]) == ["시험_연결 정책1", "시험_연결 정책2"]
            assert await admin.set_supports("시험_연결 시스템", ["시험_연결 정책2"]) == ["시험_연결 정책2"]
            with pytest.raises(ValueError, match="시스템 컬렉션에만"):
                await admin.set_supports("시험_연결 정책1", ["시험_연결 정책2"])
            with pytest.raises(ValueError, match="정책 컬렉션이 아닙니다"):
                await admin.set_supports("시험_연결 시스템", ["시험_연결 다른 시스템"])
        rows = await conn.fetch("SELECT to_key FROM ops_link WHERE relation = 'supports' AND from_key = $1", str(s))
        return sorted(int(r["to_key"]) for r in rows), p2, other_sys
    links, p2, _ = run(scenario, [admin])
    assert links == [p2]


# ── 중복 의심 처리 → AI 판단 원장(11/03) ─────────────────────────────────────

def test_duplicate_resolution_writes_one_ledger_row_per_action():
    """승인 대기 처리마다 원장 한 줄: AI 값 = 가장 비슷한 기존 지식·유사도, 사람 값 = 처리 · 그대로 저장만 번복 · 매칭은 지워짐."""
    import sys
    from types import SimpleNamespace
    real_ledger = load("_rdb_real_ledger", "service/decision/ledger.py")

    async def scenario(conn):
        s = await ns(conn, "시험_중복 시스템")
        old = await knowledge(conn, s, "기존 지식")
        news = [await knowledge(conn, s, f"새 지식 {i}", status="pending_review") for i in range(2)]
        for n, sim in zip(news, (0.962, 0.991)):
            await conn.execute("INSERT INTO rag_knowledge_duplicate_match (new_knowledge_id, matched_knowledge_id, similarity) VALUES ($1, $2, $3)",
                               n, old, sim)
        before = await conn.fetchval("SELECT count(*) FROM ops_ai_decision WHERE kind = 'duplicate_check'")
        with __import__("unittest.mock").mock.patch.dict(sys.modules, {"service.decision": SimpleNamespace(ledger=real_ledger)}):
            await ksvc.resolve_duplicate(news[0], "approve", reviewer_username="admin")
            await ksvc.resolve_duplicate(news[1], "reject", reviewer_username="admin")
        rows = await conn.fetch("SELECT ai_value, final_value, human_changed, confident, target_ids FROM ops_ai_decision "
                                "WHERE kind = 'duplicate_check' ORDER BY id DESC LIMIT 2")
        left = await conn.fetchval("SELECT count(*) FROM rag_knowledge_duplicate_match WHERE new_knowledge_id = ANY($1::int[])", news)
        after = await conn.fetchval("SELECT count(*) FROM ops_ai_decision WHERE kind = 'duplicate_check'")
        return before, after, rows, left, news, old
    before, after, rows, left, news, old = run(scenario, [ksvc])
    import json
    rej, app = rows   # 최신 먼저
    assert after - before == 2 and left == 0
    assert json.loads(app["ai_value"]) == {"duplicate_of": old, "similarity": 0.962} and json.loads(app["final_value"])["action"] == "approve"
    assert app["human_changed"] is True and app["confident"] is False and list(app["target_ids"]) == [news[0]]
    assert json.loads(rej["final_value"])["action"] == "reject" and rej["human_changed"] is False and rej["confident"] is True


def test_duplicate_resolution_skips_unmatched_and_refuses_twice():
    """매칭 없는 승인 대기(읽기 제한 보류 등)는 원장에 안 남김 · 이미 처리한 건을 다시 처리하면 거부(줄이 더 안 생김) — 리뷰 M2."""
    import sys
    from types import SimpleNamespace
    real_ledger = load("_rdb_real_ledger2", "service/decision/ledger.py")

    async def scenario(conn):
        s = await ns(conn, "시험_중복2 시스템")
        old = await knowledge(conn, s, "기존 지식")
        lone = await knowledge(conn, s, "매칭 없는 대기", status="pending_review")
        dup = await knowledge(conn, s, "매칭 있는 대기", status="pending_review")
        await conn.execute("INSERT INTO rag_knowledge_duplicate_match (new_knowledge_id, matched_knowledge_id, similarity) VALUES ($1, $2, 0.95)", dup, old)
        before = await conn.fetchval("SELECT count(*) FROM ops_ai_decision WHERE kind = 'duplicate_check'")
        twice = None
        with __import__("unittest.mock").mock.patch.dict(sys.modules, {"service.decision": SimpleNamespace(ledger=real_ledger)}):
            await ksvc.resolve_duplicate(lone, "reject", reviewer_username="admin")
            await ksvc.resolve_duplicate(dup, "approve", reviewer_username="admin")
            try:
                await ksvc.resolve_duplicate(dup, "approve", reviewer_username="admin")
            except ValueError as e:
                twice = str(e)
        after = await conn.fetchval("SELECT count(*) FROM ops_ai_decision WHERE kind = 'duplicate_check'")
        return after - before, twice, await conn.fetchval("SELECT status FROM rag_knowledge WHERE id = $1", lone)
    added, twice, lone_status = run(scenario, [ksvc])
    assert added == 1                      # 매칭 있는 처리 한 번만
    assert twice and "이미 처리" in twice
    assert lone_status == "rejected"       # 기록은 안 해도 처리는 됨


# ── 사용자 피드백 단계 이동(11/04) ─────────────────────────────────────────

def test_feedback_status_moves_step_by_step():
    """다음 단계·한 단계 되돌리기만 — 건너뛰면 409이고 상태는 그대로(조건이 UPDATE 안이라 원자적)."""
    from fastapi import HTTPException
    from typing import Literal, Optional
    fb = load("_rdb_real_user_feedback", "service/user_feedback/router.py")
    fb.TriageIn.model_rebuild(_types_namespace={"Literal": Literal, "Optional": Optional})   # 경로로 읽은 모듈이라 타입 이름을 직접 넘김

    async def scenario(conn):
        admin = await conn.fetchval("SELECT id FROM ops_user ORDER BY id LIMIT 1")
        fid = await conn.fetchval("INSERT INTO ops_user_feedback (kind, body) VALUES ('idea', '단계 시험') RETURNING id")
        st = lambda: conn.fetchval("SELECT status FROM ops_user_feedback WHERE id = $1", fid)   # noqa: E731
        seen = []

        async def go(to, **kw):
            try:
                await fb.triage(fid, fb.TriageIn(status=to, **kw), {"id": admin})
                seen.append((to, "ok", await st()))
            except HTTPException as e:
                seen.append((to, e.status_code, await st()))
        await go("in_progress")               # 신규 → 진행 중: 선정 건너뜀
        await go("done", note="x")            # 신규 → 완료: 건너뜀
        await go("planned")
        await go("done", note="x")            # 선정 → 완료: 진행 중 건너뜀
        await go("in_progress")
        await go("planned")                   # 한 단계 되돌리기
        await go("in_progress")
        await go("done", note="반영")
        await go("new")                       # 완료 → 신규: 안 됨(다시 열기는 진행 중으로)
        await go("in_progress")               # 다시 열기
        return seen
    assert run(scenario, [fb]) == [
        ("in_progress", 409, "new"), ("done", 409, "new"), ("planned", "ok", "planned"), ("done", 409, "planned"),
        ("in_progress", "ok", "in_progress"), ("planned", "ok", "planned"), ("in_progress", "ok", "in_progress"),
        ("done", "ok", "done"), ("new", 409, "done"), ("in_progress", "ok", "in_progress")]


def test_feedback_next_same_in_screen_and_server():
    """화면(FB_NEXT)과 서버(NEXT)의 단계 이동 표가 같다 — 한쪽만 바뀌면 버튼은 보이는데 409가 난다. 두 소스를 글로 읽어 비교(호스트에서 돈다)."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    ts = root / "frontend-react/src/components/feedback/UserFeedback.tsx"
    if not ts.exists():
        pytest.skip("프론트 소스 없음(컨테이너)")
    py_body = re.search(r"^NEXT = \{(.*?)^\}", (root / "backend/service/user_feedback/router.py").read_text(encoding="utf-8"), re.S | re.M).group(1)
    server = {k: tuple(re.findall(r'"(\w+)"', v)) for k, v in re.findall(r'"(\w+)": \(([^)]*)\)', py_body)}
    ts_body = re.search(r"const FB_NEXT[^{]*\{(.*?)\n\};", ts.read_text(encoding="utf-8"), re.S).group(1)
    screen = {k: tuple(re.findall(r"'(\w+)'", v)) for k, v in re.findall(r"(\w+): \[([^\]]*)\]", ts_body)}
    assert server and screen == server


# ── AI 감사 리포트 — 사람 검토 결과 · 표본 감사(11/04) ─────────────────────

def test_spot_check_only_auto_and_accepted(monkeypatch):
    """표본 감사 대상 = 자동 적용·수용뿐(규칙 적용·검토 대기·번복 제외) · 다시 매기면 덮어씀 · 감사 로그 review 거르기가 같은 규칙."""
    from types import SimpleNamespace
    ovm = load("_rdb_real_override", "service/eval/override.py")
    monkeypatch.setitem(sys.modules, "service.eval.override", ovm)
    monkeypatch.setitem(sys.modules, "service.eval.improve", SimpleNamespace(LATEST_KNOWLEDGE_RUN_SQL="(SELECT NULL::int)"))
    dl = load("_rdb_real_decision_log", "service/eval/decision_log.py")

    async def scenario(conn):
        admin = await conn.fetchval("SELECT id FROM ops_user ORDER BY id LIMIT 1")
        k = "test_kind_1104"

        async def add(final, sig):
            return await conn.fetchval("INSERT INTO ops_ai_decision (kind, ai_value, signals, final_value) VALUES ('knowledge_location', $1::jsonb, $2::jsonb, $3::jsonb) "
                                       "RETURNING id", json.dumps({"namespace_id": 1, "t": k}), json.dumps(sig), None if final is None else json.dumps(final))
        ids = {"auto": await add({"namespace_id": 1, "choice": "team_import"}, {"assigned_by": "ai", "proposed_id": 1}),
               "accept": await add({"namespace_id": 1, "choice": "accept"}, {"proposed_id": 1}),
               "rule": await add({"namespace_id": 3, "choice": "team_import"}, {"preset_id": 3}),
               "pending": await add(None, {"proposed_id": 1}),
               "override": await add({"namespace_id": 2}, {"proposed_id": 1})}
        clarify = await conn.fetchval("INSERT INTO ops_ai_decision (kind, ai_value, signals, final_value) VALUES ('chat_clarify', '{}'::jsonb, '{}'::jsonb, "
                                      "'{\"picked\": \"a\"}'::jsonb) RETURNING id")   # 사용자 선택(수용) — 관리자 표본 감사 대상 아님
        mine = set(ids.values())
        all_sample = {it["id"] for it in (await dl.spot_sample(days=1, kind=None, n=30))["items"]}
        clarify_mark = await dl.spot_mark(clarify, result="accepted", reason=None, user_id=admin)
        sample = await dl.spot_sample(days=1, kind="knowledge_location", n=30)
        sampled = {it["id"] for it in sample["items"]} & mine
        marks = {name: await dl.spot_mark(i, result="overridden", reason="wrong", user_id=admin) for name, i in ids.items()}
        again = await dl.spot_mark(ids["auto"], result="accepted", reason="wrong", user_id=admin)   # 덮어쓰기 · 수용이면 사유 비움
        row = await conn.fetchrow("SELECT spot_result, spot_reason FROM ops_ai_decision WHERE id = $1", ids["auto"])
        by_review = {}
        for name in ids:
            page = await dl.decision_log(days=1, kind="knowledge_location", changed_only=False, limit=200, review=name)
            by_review[name] = {it["id"] for it in page["items"]} & mine
        return ids, sampled, marks, again, dict(row), by_review, (clarify in all_sample, clarify_mark)
    ids, sampled, marks, again, row, by_review, clar = run(scenario, [dl])
    assert clar == (False, False)
    assert sampled == {ids["auto"], ids["accept"]}
    assert marks == {"auto": True, "accept": True, "rule": False, "pending": False, "override": False}
    assert again and row == {"spot_result": "accepted", "spot_reason": None}
    assert by_review == {name: {i} for name, i in ids.items()}   # 다섯 갈래 거르기 — 각자 자기 줄만
