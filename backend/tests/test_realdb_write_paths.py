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


def test_rollback_releases_duplicates_whose_only_match_was_rolled_back():
    """롤백(11/06 D4): 지운 조각만 상대로 가리키던 게시 전 승인 대기(staging_review)는 staging으로 풀린다(비교 지움).
    승인 대기(pending_review)는 담당 승인 대기와 구분이 안 돼 그대로(리뷰 중간) · 살아 있는 상대가 남은 것 · 읽기 제한 · 상관없는 것도 그대로."""
    ti = load("_rdb_real_team_import", "agents/knowledge_rag/ingestion/team_import.py")

    async def scenario(conn):
        s = await ns(conn, "시험_롤백해제 시스템")
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        job = await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks) VALUES ($1,'confluence','completed',1) RETURNING id", s)
        gone = await knowledge(conn, s, "되돌릴 조각", ingestion_job_id=job)
        alive = await knowledge(conn, s, "남는 기존 지식")
        k = {name: await knowledge(conn, s, name, status=st, **extra) for name, st, extra in [
            ("승인 대기", "pending_review", {}), ("게시 전 풀림", "staging_review", {}), ("상대 남음", "staging_review", {}),
            ("읽기 제한", "staging_review", {"confluence_page_id": "RP1"}), ("상관없음", "staging_review", {})]}
        await conn.execute("INSERT INTO rag_source_document (namespace_id, source_type, source_ref, title, raw_format, raw_content, content_hash, status) "
                           "VALUES ($1, 'confluence', 'RP1', 'RP1', 'html', 'x', md5('x'), 'restricted')", s)
        pairs = [(k["승인 대기"], gone), (k["게시 전 풀림"], gone), (k["상대 남음"], gone), (k["상대 남음"], alive),
                 (k["읽기 제한"], gone), (k["상관없음"], alive)]
        await conn.executemany("INSERT INTO rag_knowledge_duplicate_match (new_knowledge_id, matched_knowledge_id, similarity) VALUES ($1, $2, 0.9)", pairs)
        b = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, status, sampling_required, total_pages, done_pages, job_ids, loaded_at) "
                                "VALUES ($1, 'https://confl.sinc.co.kr/display/RR', 'loaded', FALSE, 1, 1, $2::int[], NOW()) RETURNING id", team, [job])
        out = await ti.rollback_batch(conn, b)
        st = {r["id"]: r["status"] for r in await conn.fetch("SELECT id, status FROM rag_knowledge WHERE id = ANY($1::int[])", list(k.values()))}
        n = {r["new_knowledge_id"]: r["n"] for r in await conn.fetch(
            "SELECT new_knowledge_id, count(*) AS n FROM rag_knowledge_duplicate_match WHERE new_knowledge_id = ANY($1::int[]) GROUP BY 1", list(k.values()))}
        return out["released"], {name: (st[i], n.get(i, 0)) for name, i in k.items()}
    released, after = run(scenario)
    assert released == 1
    assert after == {"승인 대기": ("pending_review", 1), "게시 전 풀림": ("staging", 0), "상대 남음": ("staging_review", 2),
                     "읽기 제한": ("staging_review", 1), "상관없음": ("staging_review", 1)}


def test_boot_cleanup_keeps_duplicate_matches_of_unfinished_chunks():
    """재기동 정리(#65): 끝난 지식의 중복 비교만 지운다 — 승인 대기(pending_review)·게시 전 승인 대기(staging_review)는 남긴다
    (11/05 실사고: staging 상태가 생긴 뒤 조건을 안 고쳐 재시작마다 게시 전 비교 30건이 지워졌다)."""
    import ast
    import logging
    # main.py 통째 import는 다른 시험의 가짜 core와 부딪힌다 — 이 함수만 실제 소스에서 꺼내 실행(조건을 바꾸면 그대로 걸린다)
    src = (__import__("pathlib").Path(__file__).parent.parent / "main.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if getattr(n, "name", "") == "_migrate_drop_orphans_2026_10_06")
    env = {"logger": logging.getLogger("test")}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "main.py", "exec"), env)
    cleanup = env["_migrate_drop_orphans_2026_10_06"]

    async def scenario(conn):
        s = await ns(conn, "시험_정리 시스템")
        base = await knowledge(conn, s, "기존 지식")
        by_status = {st: await knowledge(conn, s, f"{st} 조각", status=st) for st in ("pending_review", "staging_review", "active", "deleted")}
        await conn.executemany("INSERT INTO rag_knowledge_duplicate_match (new_knowledge_id, matched_knowledge_id, similarity) VALUES ($1, $2, 0.9)",
                               [(k, base) for k in by_status.values()])
        await cleanup(conn)
        left = {r["new_knowledge_id"] for r in await conn.fetch(
            "SELECT new_knowledge_id FROM rag_knowledge_duplicate_match WHERE new_knowledge_id = ANY($1::int[])", list(by_status.values()))}
        return {st for st, k in by_status.items() if k in left}
    assert run(scenario) == {"pending_review", "staging_review"}


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
        # 실데이터의 다른 판단은 감사한 것으로(트랜잭션 안 — 되돌림) — 오늘 같은 대량 적재(11/19 판단 8천 줄)면 무작위 30건에 시험 줄이 안 뽑혔다
        await conn.execute("UPDATE ops_ai_decision SET spot_result = 'accepted' WHERE spot_result IS NULL AND id <> ALL($1::int[])", list(mine))
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


# ── 사용자 피드백 컬렉션 필터(11/05) ─────────────────────────────────────────

def test_feedback_namespace_filter_partitions_rows():
    """컬렉션 필터 — 이름 = 그 컬렉션만, '-' = 미지정만, 없음 = 전체. 세 갈래 합 = 전체, 건수(counts) = 그 필터의 줄 수."""
    fb = load("_rdb_real_user_feedback", "service/user_feedback/router.py")

    async def scenario(conn):
        await conn.execute("DELETE FROM ops_user_feedback")   # 트랜잭션 안 — 끝나면 되돌린다
        a, b = await ns(conn, "시험_피드백 가"), await ns(conn, "시험_피드백 나")
        for n_id in (a, a, b, None):
            await conn.execute("INSERT INTO ops_user_feedback (kind, body, namespace_id) VALUES ('idea', '시험', $1)", n_id)
        all_ = await fb.list_all(status=None, namespace=None, admin={})
        only_a = await fb.list_all(status=None, namespace="시험_피드백 가", admin={})
        none = await fb.list_all(status=None, namespace="-", admin={})
        new_a = await fb.list_all(status="new", namespace="시험_피드백 가", admin={})
        return all_, only_a, none, new_a

    all_, only_a, none, new_a = run(scenario, modules=[fb])
    assert len(all_["items"]) == 4 and sum(all_["counts"].values()) == 4
    assert [i["namespace"] for i in only_a["items"]] == ["시험_피드백 가"] * 2 and sum(only_a["counts"].values()) == 2
    assert [i["namespace"] for i in none["items"]] == [None] and sum(none["counts"].values()) == 1
    assert len(new_a["items"]) == 2 and new_a["counts"]["new"] == 2   # 상태 + 컬렉션 같이
    assert sorted((x["namespace"] or "", x["n"]) for x in all_["namespaces"]) == [("", 1), ("시험_피드백 가", 2), ("시험_피드백 나", 1)]


# ── 원문 페이지 제목(11/05) ─────────────────────────────────────────────────

def test_review_and_page_urls_use_page_title_not_batch_name():
    """공간 일괄 적재 조각은 source_file이 묶음 이름 — 검토 필요·승인 대기 원문 링크 제목은 적재 원장의 페이지 제목이어야 한다.
    원장에 없는 페이지는 첫 제목 경로, 그것도 없으면 원본 이름."""
    import datetime
    _OLD = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)   # 기한 지남(기본 90일)
    review = load("_rdb_real_review", "agents/knowledge_rag/knowledge/review.py")

    async def scenario(conn):
        s = await ns(conn, "시험_제목 시스템")
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        b = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, status, sampling_required, total_pages, done_pages) "
                                "VALUES ($1, 'https://confl.sinc.co.kr/display/X', 'preview', FALSE, 1, 1) RETURNING id", team)
        await conn.execute("INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, status) VALUES ($1, '시험p1', '송장 재발행 안내', 'b', 1, 'auto')", b)
        batch_name = "Confluence 16개 페이지(파일럿 진단 · 그림 켬)"
        k1 = await knowledge(conn, s, "조각 1", confluence_page_id="시험p1", source_file=batch_name, created_at=_OLD, image_count=3)
        k2 = await knowledge(conn, s, "조각 2", confluence_page_id="시험p2", source_file=batch_name, heading_path=["배치 재기동"],
                             created_at=_OLD)
        for k in (k1, k2):   # 목록에 오르게 — 신고(내용이 낡음, 대기 중)는 기한과 상관없이 올라온다(11/05 사건 기반)
            await conn.execute("INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_reason, status) "
                               "VALUES ('correction', 'chat', $1, 'knowledge', $2, 'stale', 'pending')", s, k)
        due = await review.review_due("시험_제목 시스템")
        urls = await ksvc.page_urls([k1, k2])
        return {d["key"]: d["title"] for d in due["due"]}, {i: (u["title"], u["image_count"]) for i, u in urls.items()}, k1, k2

    titles, url_titles, k1, k2 = run(scenario, modules=[review, ksvc])
    assert titles == {"p:시험p1": "송장 재발행 안내", "p:시험p2": "배치 재기동"}   # 둘 다 신고로 올라옴
    assert url_titles == {k1: ("송장 재발행 안내", 3), k2: ("배치 재기동", 0)}   # 그림 수(11/06) — 안 셈(NULL)은 0


def test_activate_records_image_count_per_chunk_section():
    """원문 활성화(11/06): 조각마다 앞뒤 문맥 그림 수가 image_count로 — 그림 바로 앞 본문이 든 조각만 >0, 합 = 원문 그림 수,
    컨플 아닌 조각은 안 셈(NULL)."""
    sl = load("_rdb_real_ledger", "agents/knowledge_rag/ingestion/source_ledger.py")
    raw = ("<h2>주문 흐름</h2><p>" + "가나다라 첫째 문단 " * 200 + "</p>" '<ac:image><ri:attachment ri:filename="a.png"/></ac:image>'
           '<ac:image><ri:attachment ri:filename="b.png"/></ac:image>' "<h2>다음</h2><p>" + "마바사 둘째 문단 " * 200 + "</p>")

    async def scenario(conn):
        s = await ns(conn, "시험_그림 시스템")
        doc = await conn.fetchval("INSERT INTO rag_source_document (namespace_id, source_type, source_ref, title, raw_format, raw_content, content_hash, status) "
                                  "VALUES ($1, 'confluence', 'IMG1', '그림 문서', 'html', $2, md5($2), 'draft') RETURNING id", s, raw)
        row = await conn.fetchrow(f"SELECT {sl._DOC_COLS} FROM rag_source_document WHERE id = $1", doc)
        items = sl.rechunk_items(row)
        old_job = await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks) VALUES ($1,'confluence','completed',1) RETURNING id", s)
        new_job = await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks) VALUES ($1,'confluence','completed',1) RETURNING id", s)
        # 같은 원문의 옛 등록(다른 분할) — 다시 분할하면 같은 원문 행에 옛 조각이 남아 있다(리뷰 중간: 섞여 세지 않게)
        old = [await knowledge(conn, s, "첫째 문단 옛 조각 " * 30, source_document_id=doc, source_chunk_idx=0, ingestion_job_id=old_job)]
        ks = [await knowledge(conn, s, x["content"], status="staging", source_document_id=doc, source_chunk_idx=i, ingestion_job_id=new_job)
              for i, x in enumerate(items)]
        later_job = await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks) VALUES ($1,'confluence','held',1) RETURNING id", s)
        # 같은 원문을 든 더 늦은 보류 작업(재리뷰 낮음) — 확정되는 작업(new_job)이 max가 아니어도 그 작업 조각을 센다
        later = [await knowledge(conn, s, items[0]["content"], status="staging", source_document_id=doc, source_chunk_idx=0, ingestion_job_id=later_job)]
        paste = await knowledge(conn, s, "붙여넣기 조각")
        await sl.activate(conn, s, [doc], [], [], job_id=new_job)
        got = {r["id"]: r["image_count"] for r in await conn.fetch("SELECT id, image_count FROM rag_knowledge WHERE id = ANY($1::int[])", ks + old + later + [paste])}
        return [got[k] for k in ks], [x["content"] for x in items], got[paste], (got[old[0]], got[later[0]])
    per_chunk, texts, paste, old = run(scenario)   # 원장 함수는 연결을 인자로 받는다
    assert len(per_chunk) >= 2 and sum(per_chunk) == 2
    last_first = max(i for i, t in enumerate(texts) if "첫째" in t)      # 그림 바로 앞 본문(첫째 문단 끝)이 든 조각
    assert per_chunk[last_first] == 2 and all(n == 0 for i, n in enumerate(per_chunk) if i != last_first)
    assert paste is None and old == (None, None)   # 옛 등록·다른 보류 작업 조각은 안 셈(확정되는 작업 조각만)


def test_reported_card_clears_after_review():
    """신고 카드(리뷰 M1) — 신고 뒤 [검토 완료]하면 정정 요청이 대기 중이어도 목록에서 빠진다."""
    review = load("_rdb_real_review2", "agents/knowledge_rag/knowledge/review.py")

    async def scenario(conn):
        s = await ns(conn, "시험_신고 시스템")
        k = await knowledge(conn, s, "신고된 조각")
        await conn.execute("INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_reason, status) "
                           "VALUES ('correction', 'chat', $1, 'knowledge', $2, 'stale', 'pending')", s, k)
        before = [d["reason"] for d in (await review.review_due("시험_신고 시스템"))["due"]]
        await review.mark_reviewed("시험_신고 시스템", [k], "admin")
        after = [d["reason"] for d in (await review.review_due("시험_신고 시스템"))["due"]]
        return before, after

    before, after = run(scenario, modules=[review])
    assert before == ["reported"] and after == []



# ── 읽기 제한 포함 적재(11/05) ─────────────────────────────────────────────

def test_include_restricted_batch_records_who_and_sync_follows_only_loaded():
    """묶음 만들기 SQL(실 DB — 파라미터 타입 충돌로 500이 났던 것) · 켠 사람·시각은 켰을 때만 ·
    동기화는 같은 주소의 '적재 끝난' 제한 포함 묶음만 따른다(미리보기·다른 사람의 끈 묶음은 결정을 바꾸지 않음)."""
    router = load("_rdb_real_team_import_router", "agents/knowledge_rag/knowledge/team_import_router.py")
    sync = load("_rdb_real_confluence_sync", "agents/knowledge_rag/ingestion/confluence_sync.py")

    async def scenario(conn):
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        admin = await conn.fetchval("SELECT id FROM ops_user ORDER BY id LIMIT 1")
        url = "https://confl.sinc.co.kr/display/시험제한"
        await conn.execute("UPDATE rag_import_batch SET status = 'cancelled' WHERE team_id = $1 AND status IN ('previewing', 'loading')", team)
        on = await conn.fetchval(router.INSERT_BATCH_SQL, team, url, admin, None, True, None)
        row_on = await conn.fetchrow("SELECT include_restricted_at IS NOT NULL a, include_restricted_by b FROM rag_import_batch WHERE id = $1", on)
        before_load = await sync.include_restricted_for(conn, team, url)          # 미리보기(previewing)는 아직 안 따름
        await conn.execute("UPDATE rag_import_batch SET status = 'loaded' WHERE id = $1", on)
        off = await conn.fetchval(router.INSERT_BATCH_SQL, team, url, admin, None, False, None)   # 다른 사람이 끄고 새 미리보기
        row_off = await conn.fetchrow("SELECT include_restricted_at IS NOT NULL a, include_restricted_by b FROM rag_import_batch WHERE id = $1", off)
        after = await sync.include_restricted_for(conn, team, url)
        return (row_on["a"], row_on["b"] == admin), (row_off["a"], row_off["b"]), before_load, after

    (on_a, on_by), (off_a, off_by), before_load, after = run(scenario)
    assert (on_a, on_by) == (True, True) and (off_a, off_by) == (False, None)
    assert before_load is False and after is True


def test_default_space_batch_gets_sync_connection_and_rules():
    """기본 위치 배치 → 증분 연결(11/19, 리뷰 M2·M3·L1 반영) — 묶음 규칙: 넣은 곳 하나면 그곳 · 여러 곳이면 '규칙 없음' · 통째로 뺀 묶음은 제외 ·
    새 묶음 '*' = 기본 위치. 다음 배치가 자동 규칙을 갱신하고, 롤백은 그 배치 자동 규칙을 지운다. 사람이 연결한 공간엔 자동 규칙을 안 더한다."""
    ti = load("_rdb_real_team_import3", "agents/knowledge_rag/ingestion/team_import.py")
    router = load("_rdb_real_team_import_router3", "agents/knowledge_rag/knowledge/team_import_router.py")

    async def scenario(conn):
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        admin = await conn.fetchval("SELECT id FROM ops_user ORDER BY id LIMIT 1")
        a, b2 = [r["id"] for r in await conn.fetch("SELECT id FROM ops_namespace ORDER BY id LIMIT 2")]
        await conn.execute("UPDATE rag_import_batch SET status = 'cancelled' WHERE team_id = $1 AND status IN ('previewing', 'loading')", team)
        url = "https://c/x/conn-probe"

        async def batch(default_ns, pages, root=url):
            bid = await conn.fetchval(router.INSERT_BATCH_SQL, team, root, admin, default_ns, False, None)
            await conn.execute("UPDATE rag_import_batch SET status = 'loaded' WHERE id = $1", bid)
            for i, (bundle, st, ns) in enumerate(pages):
                await conn.execute("INSERT INTO rag_import_page (batch_id, page_id, status, bundle, final_namespace_id) VALUES ($1, $2, $3, $4, $5)",
                                   bid, f"cp{bid}-{i}", st, bundle, ns)
            return bid

        async def rules_of(root=url):
            return {r["bundle"]: (r["namespace_id"], r["no_rule"]) for r in await conn.fetch(
                "SELECT bundle, namespace_id, no_rule FROM rag_import_rule WHERE team_id = $1 AND root_url = $2", team, root)}

        b1 = await batch(a, [("한곳", "loaded", a), ("한곳", "loaded", a), ("섞임", "loaded", a), ("섞임", "loaded", b2),
                             ("뺀묶음", "excluded", None), ("뺀묶음", "held", None)])
        cid = await ti.connect_default_space(conn, b1, admin)
        r1 = await rules_of()
        conn_row = await conn.fetchrow("SELECT status, last_incremental_at IS NOT NULL s FROM rag_source_connection WHERE id = $1", cid)
        b2id = await batch(b2, [("한곳", "loaded", b2)])          # 같은 공간을 기본 위치 B로 다시 — 자동 규칙이 따라간다(M2)
        await ti.connect_default_space(conn, b2id, admin)
        r2 = await rules_of()
        await conn.execute("UPDATE rag_import_batch SET status = 'loaded', publishing_at = NULL WHERE id = $1", b2id)
        await ti.rollback_batch(conn, b2id)                         # 롤백 — 그 배치가 만든 자동 규칙 지움(M2)
        r3 = await rules_of()
        h = url + "/human"                                           # 사람이 묶음 연결한 공간 — 자동 규칙 안 더함(L1)
        await conn.execute("INSERT INTO rag_import_rule (team_id, root_url, bundle, grp, namespace_id, created_by_user_id) "
                           "VALUES ($1, $2, '사람묶음', NULL, $3, $4)", team, h, b2, admin)
        hb = await batch(a, [("사람묶음", "loaded", a), ("다른묶음", "loaded", a)], root=h)
        await ti.connect_default_space(conn, hb, admin)
        rh = await rules_of(h)
        nodef = await batch(None, [("x", "loaded", a)], root=url + "2")
        none = await ti.connect_default_space(conn, nodef, admin)
        return a, b2, r1, r2, r3, rh, dict(conn_row), none

    a, b2, r1, r2, r3, rh, conn_row, none = run(scenario)
    assert r1 == {"한곳": (a, False), "섞임": (None, True), "뺀묶음": (None, False), "*": (a, False)}
    assert r2["*"] == (b2, False) and r2["한곳"] == (b2, False)
    assert "*" not in r3 and "한곳" not in r3          # b2 자동 규칙이 지워짐(b1 것은 b2가 덮어썼으므로 함께 사라진다 — 증분은 no_rule로 사람에게)
    assert rh == {"사람묶음": (b2, False)}
    assert conn_row["status"] == "ok" and conn_row["s"]
    assert none is None
    assert ti_connect_target_default_ok()


def test_human_connect_after_auto_rules_takes_over_space():
    """자동 규칙 뒤 사람이 [묶음 연결](11/19 재리뷰 중간 1) — 사람 규칙이 되고(자동 표시 지움) 남은 자동 규칙('*'·규칙 없음)은 지워진다.
    옛 배치 롤백이 사람 규칙을 안 지우고, 다음 기본 위치 배치도 덮지 않는다. 롤백된(적재 상태 아닌) 배치는 연결 안 함(낮음 3)."""
    ti = load("_rdb_real_team_import4", "agents/knowledge_rag/ingestion/team_import.py")
    router = load("_rdb_real_team_import_router4", "agents/knowledge_rag/knowledge/team_import_router.py")

    async def scenario(conn):
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        admin = await conn.fetchval("SELECT id FROM ops_user ORDER BY id LIMIT 1")
        a, b2 = [r["id"] for r in await conn.fetch("SELECT id FROM ops_namespace ORDER BY id LIMIT 2")]
        await conn.execute("UPDATE rag_import_batch SET status = 'cancelled' WHERE team_id = $1 AND status IN ('previewing', 'loading')", team)
        url = "https://c/x/human-takeover"

        async def batch(pages):
            bid = await conn.fetchval(router.INSERT_BATCH_SQL, team, url, admin, a, False, None)
            await conn.execute("UPDATE rag_import_batch SET status = 'loaded' WHERE id = $1", bid)
            for i, (bundle, st, ns) in enumerate(pages):
                await conn.execute("INSERT INTO rag_import_page (batch_id, page_id, status, bundle, final_namespace_id) VALUES ($1, $2, $3, $4, $5)",
                                   bid, f"ht{bid}-{i}", st, bundle, ns)
            return bid

        async def rules():
            return {r["bundle"]: (r["namespace_id"], r["no_rule"], r["auto_from_batch"] is not None) for r in await conn.fetch(
                "SELECT bundle, namespace_id, no_rule, auto_from_batch FROM rag_import_rule WHERE team_id = $1 AND root_url = $2", team, url)}

        b1 = await batch([("한곳", "loaded", a), ("섞임", "loaded", a), ("섞임", "loaded", b2)])
        await ti.connect_default_space(conn, b1, admin)
        before = await rules()
        await ti.save_human_rules(conn, team, url, admin, [("섞임", b2, [])])        # 사람이 '섞임'만 연결
        after = await rules()
        await conn.execute("UPDATE rag_import_batch SET publishing_at = NULL WHERE id = $1", b1)
        await ti.rollback_batch(conn, b1)
        after_rb = await rules()
        b3 = await batch([("새묶음", "loaded", a)])
        await ti.connect_default_space(conn, b3, admin)
        after_next = await rules()
        b4 = await batch([("x", "loaded", a)])
        await conn.execute("UPDATE rag_import_batch SET status = 'rolled_back' WHERE id = $1", b4)
        none = await ti.connect_default_space(conn, b4, admin)
        return a, b2, before, after, after_rb, after_next, none

    a, b2, before, after, after_rb, after_next, none = run(scenario)
    assert before == {"한곳": (a, False, True), "섞임": (None, True, True), "*": (a, False, True)}
    assert after == {"섞임": (b2, False, False)}          # 사람 규칙 · 자동 '*'·'한곳' 지움(한곳 새 페이지 → 규칙 없음 → 사람)
    assert after_rb == after and after_next == after      # 옛 배치 롤백·다음 기본 위치 배치가 사람 규칙을 안 건드림
    assert none is None


def ti_connect_target_default_ok() -> bool:
    from agents.knowledge_rag.ingestion import team_import as t
    rules = {"한곳": {"ns": 1, "groups": {}}, "섞임": {"ns": None, "groups": {}, "no_rule": True}, "*": {"ns": 9, "groups": {}}}
    return (t.connect_target({"bundle": "한곳"}, rules) == (True, 1) and t.connect_target({"bundle": "새것"}, rules) == (True, 9)
            and t.connect_target({"bundle": "섞임"}, rules) == (False, None)
            and t.connect_target({"bundle": "새것"}, {"한곳": {"ns": 1, "groups": {}}}) == (False, None))


def test_restricted_decision_inherits_same_space_loaded_only():
    """④ 이어받기(11/19) — 같은 팀·같은 공간(원문 공간 키)·적재 끝난 제한 포함 배치만. 다른 공간·미리보기·끈 배치는 아님. 이어받은 배치 기록."""
    router = load("_rdb_real_team_import_router2", "agents/knowledge_rag/knowledge/team_import_router.py")
    sync = load("_rdb_real_confluence_sync2", "agents/knowledge_rag/ingestion/confluence_sync.py")

    async def scenario(conn):
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        admin = await conn.fetchval("SELECT id FROM ops_user ORDER BY id LIMIT 1")
        ns = await conn.fetchval("SELECT id FROM ops_namespace ORDER BY id LIMIT 1")
        await conn.execute("UPDATE rag_import_batch SET status = 'cancelled' WHERE team_id = $1 AND status IN ('previewing', 'loading')", team)

        async def batch(on: bool, status: str, key: str, from_id=None):
            b = await conn.fetchval(router.INSERT_BATCH_SQL, team, f"https://c/x/{key}", admin, None, on, from_id)
            await conn.execute("UPDATE rag_import_batch SET status = $2 WHERE id = $1", b, status)
            d = await conn.fetchval("INSERT INTO rag_source_document (namespace_id, source_type, source_ref, title, raw_content, raw_format, "
                                    "content_hash, status, media) VALUES ($1, 'confluence', $2, 't', '<p>x</p>', 'html', md5(random()::text), "
                                    "'superseded', $3::jsonb) RETURNING id", ns, f"p{b}", '{"page": {"space_key": "%s"}}' % key)
            await conn.execute("INSERT INTO rag_import_page (batch_id, page_id, status, source_document_id) VALUES ($1, $2, 'loaded', $3)",
                               b, f"p{b}", d)
            return b

        old = await batch(True, "loaded", "SPX")
        await batch(True, "preview", "SPX")        # 확정 안 한 켬 — 결정 아님
        other = await batch(True, "loaded", "SPY")
        got = await sync.restricted_decision(conn, team, "SPX")
        none = await sync.restricted_decision(conn, team, "SPZ")
        await batch(False, "loaded", "SPX")        # 나중에 일부러 끄고 적재 — 옛 켬이 뒤집지 않는다(리뷰 M1)
        after_off = await sync.restricted_decision(conn, team, "SPX")
        await batch(False, "preview", "SPX")       # 확정 안 한 끔은 결정 아님
        reon = await batch(True, "loaded", "SPX")
        after_reon = await sync.restricted_decision(conn, team, "SPX")
        child = await conn.fetchval(router.INSERT_BATCH_SQL, team, "https://c/x/new", admin, None, True, old)
        rec = await conn.fetchrow("SELECT include_restricted_from f FROM rag_import_batch WHERE id = $1", child)
        await conn.execute("UPDATE rag_import_batch SET status = 'cancelled' WHERE id = $1", child)   # 팀마다 진행 중 하나
        off_child = await conn.fetchval(router.INSERT_BATCH_SQL, team, "https://c/x/new2", admin, None, False, old)
        rec_off = await conn.fetchrow("SELECT include_restricted_from f, include_restricted_at a FROM rag_import_batch WHERE id = $1", off_child)
        return old, other, got, none, rec["f"], rec_off["f"], rec_off["a"], after_off, reon, after_reon

    old, other, got, none, f, f_off, a_off, after_off, reon, after_reon = run(scenario)
    assert got and got["id"] == old and got["id"] != other
    assert none is None
    assert after_off is None and after_reon and after_reon["id"] == reon
    assert f == old and f_off is None and a_off is None   # 끄면 출처도 안 남긴다


# ── 동기화로 바뀐 페이지(11/05) ─────────────────────────────────────────────

def test_sync_changes_scope_pairing_and_usage():
    """범위 = 이 연결이 넣은 (페이지, 컬렉션)만 — 같은 페이지의 다른 컬렉션 판은 안 섞임 · 내용 같은 새 판은 빠짐 ·
    처음 넣은 페이지(옛 판 없음)는 안 나옴 · 채팅 횟수는 옛 판 조각이 쓰인 것도 센다."""
    sc = load("_rdb_real_sync_changes", "agents/knowledge_rag/ingestion/sync_changes.py")

    async def scenario(conn):
        a, other = await ns(conn, "시험_변경 가"), await ns(conn, "시험_변경 나")
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        url = "https://confl.sinc.co.kr/display/시험변경"
        b = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, status, sampling_required, total_pages, done_pages) "
                                "VALUES ($1, $2, 'loaded', FALSE, 3, 3) RETURNING id", team, url)
        for pid in ("P1", "P2", "P3"):
            await conn.execute("INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, status, final_namespace_id) "
                               "VALUES ($1, $2, $2, 'b', 1, 'loaded', $3)", b, pid, a)

        async def doc(nsid, pid, raw, status, ago):
            return await conn.fetchval(
                "INSERT INTO rag_source_document (namespace_id, source_type, source_ref, title, raw_format, raw_content, content_hash, status, activated_at) "
                "VALUES ($1, 'confluence', $2, $2, 'html', $3, md5($3), $4, NOW() - make_interval(days => $5)) RETURNING id", nsid, pid, raw, status, ago)
        await doc(a, "P1", "<p>가</p><p>나</p>", "superseded", 5)
        new1 = await doc(a, "P1", "<p>가</p><p>나 고침</p><p>다</p>", "active", 1)          # 바뀜 +2 −1
        await doc(a, "P2", "<p>같음</p>", "superseded", 5)
        await doc(a, "P2", "<p>같음</p>", "active", 1)                                         # 내용 같음 → 빠짐
        await doc(a, "P3", "<p>처음</p>", "active", 1)                                         # 옛 판 없음 → 안 나옴
        await doc(other, "P1", "<p>남의 옛 판</p>", "superseded", 5)
        await doc(other, "P1", "<p>남의 새 판</p><p>더</p>", "active", 1)                      # 다른 컬렉션 → 안 섞임
        old_chunk = await knowledge(conn, a, "옛 판 조각", confluence_page_id="P1")
        cv = await conn.fetchval("INSERT INTO ops_conversation (title) VALUES ('시험') RETURNING id")
        await conn.execute("INSERT INTO ops_message (conversation_id, role, content, results) VALUES ($1, 'assistant', '답', $2::jsonb)",
                           cv, f'[{{"id": {old_chunk}}}]')
        c = {"team_id": team, "root_url": url, "retry_pages": []}
        lst = await sc.list_changes(c, 30)
        det = await sc.change_detail(c, new1)
        return lst, det, new1

    lst, det, new1 = run(scenario, modules=[sc])
    assert [(x["page_id"], x["namespace"], x["added"], x["removed"]) for x in lst["items"]] == [("P1", "시험_변경 가", 2, 1)]
    assert lst["items"][0]["new_id"] == new1 and lst["items"][0]["used"] == 1   # 옛 판 조각이 쓰인 것도 셈
    assert det["before"] == "가\n나" and det["after"] == "가\n나 고침\n다"


# ── 조사 떼기 Python ↔ SQL 동등성(11/05 하드코딩 감사 — 두 벌 구현이 09/10 수작업 확인 한 번뿐이었다) ───────────────

def test_korean_suffix_strip_python_equals_sql():
    """trustkit.text.ko.strip_suffix/strip_text 와 DB 함수 policy_strip_ko_word/policy_strip_ko가 같은 결과 —
    조사 목록 전부 × 여러 어간 + 무작위 문장. 한쪽만 고치면 키워드 검색이 말없이 달라진다."""
    import random
    ko = load("_rdb_trustkit_ko", "libs/trustkit/trustkit/text/ko.py")
    stems = ["배송", "교환", "쿠폰", "정산배치", "앱", "가", "환불기준", "B2C몰", "2차인증", "회원"]
    words = [s + suf for s in stems for suf in ko.SUFFIXES] + stems + ["", "을", "에서", "에서도는"]
    rnd = random.Random(1105)
    texts = [" ".join(rnd.choice(words) for _ in range(rnd.randint(1, 8))) for _ in range(200)]

    async def scenario(conn):
        if not await conn.fetchval("SELECT 1 FROM pg_proc WHERE proname = 'policy_strip_ko_word'"):
            pytest.skip("policy_strip_ko 함수 없음")
        w = await conn.fetch("SELECT x, policy_strip_ko_word(x) AS y FROM unnest($1::text[]) AS x", words)
        t = await conn.fetch("SELECT x, policy_strip_ko(x) AS y FROM unnest($1::text[]) AS x", texts)
        return {r["x"]: r["y"] for r in w}, {r["x"]: r["y"] for r in t}

    sql_w, sql_t = run(scenario)
    bad_w = [x for x in words if (ko.strip_suffix(x) or "") != (sql_w[x] or "")]
    bad_t = [x for x in texts if (ko.strip_text(x) or "") != (sql_t[x] or "")]
    assert not bad_w, f"낱말 {len(bad_w)}개 다름: {bad_w[:5]}"
    assert not bad_t, f"문장 {len(bad_t)}개 다름: {bad_t[:3]}"


# ── 검토 필요 동작: 미루기 · 보관 · 되살리기(11/05, 리뷰 M3) ───────────────────────────

def test_review_snooze_archive_unarchive_scenarios():
    """미루기 → 목록에서 빠짐, 신고가 오면 다시 보임 · 보관 → 검색 대상 아님·공백 다시 열림·다른 컬렉션 id는 그대로 ·
    같은 페이지에 새 판이 생기면 되살리기 거부(옛 판·새 판 중복 방지) · 보관 조각 있으면 컬렉션 삭제가 막힘."""
    import datetime
    review = load("_rdb_real_review3", "agents/knowledge_rag/knowledge/review.py")
    old = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)

    async def scenario(conn):
        s, other = await ns(conn, "시험_보관 가"), await ns(conn, "시험_보관 나")
        a = await knowledge(conn, s, "보관 조각", confluence_page_id="시험A", created_at=old)
        b = await knowledge(conn, s, "미룰 조각", confluence_page_id="시험B", created_at=old)
        o = await knowledge(conn, other, "남의 조각", confluence_page_id="시험A")
        for k in (a, b):   # 두 페이지 다 신고로 목록에 오르게
            await conn.execute("INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_reason, status, created_at) "
                               "VALUES ('correction', 'chat', $1, 'knowledge', $2, 'stale', 'pending', NOW() - interval '1 day')", s, k)
        gap = await conn.fetchval("INSERT INTO ops_query_log (namespace_id, question, status, resolved_knowledge_id, resolved_at) "
                                  "VALUES ($1, '질문', 'no_knowledge', $2, NOW()) RETURNING id", s, a)
        keys0 = {d["key"] for d in (await review.review_due("시험_보관 가"))["due"]}
        # 미루기 — 신고가 있으면 미뤄도 보인다(리뷰 M2) → 신고를 정리한 뒤 미루면 빠진다
        await conn.execute("UPDATE ops_improvement_item SET status = 'rejected' WHERE target_id = $1", b)
        await conn.execute("UPDATE rag_knowledge SET reviewed_at = NULL WHERE id = $1", b)
        n_sn = await review.snooze("시험_보관 가", [b], 30)
        keys1 = {d["key"] for d in (await review.review_due("시험_보관 가"))["due"]}
        await conn.execute("INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, user_reason, status) "
                           "VALUES ('correction', 'chat', $1, 'knowledge', $2, 'stale', 'pending')", s, b)
        keys2 = {d["key"] for d in (await review.review_due("시험_보관 가"))["due"]}
        # 보관 — 다른 컬렉션 id가 섞여도 그 컬렉션 것만
        n_ar = await review.set_archived("시험_보관 가", [a, o], True)
        st = {r["id"]: r["status"] for r in await conn.fetch("SELECT id, status FROM rag_knowledge WHERE id = ANY($1::int[])", [a, o])}
        gap_open = await conn.fetchval("SELECT resolved_knowledge_id IS NULL FROM ops_query_log WHERE id = $1", gap)
        arch = await review.archived_docs("시험_보관 가")
        blocked = await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1 AND status IN ('active', 'pending_review', 'archived')", s)
        # 같은 페이지에 새 판(active)이 생기면 되살리기 거부
        await knowledge(conn, s, "새 판", confluence_page_id="시험A")
        n_un_block = await review.set_archived("시험_보관 가", [a], False)
        await conn.execute("UPDATE rag_knowledge SET status = 'deleted' WHERE namespace_id = $1 AND content = '새 판'", s)
        n_un = await review.set_archived("시험_보관 가", [a], False)
        st_a = await conn.fetchval("SELECT status FROM rag_knowledge WHERE id = $1", a)
        return keys0, n_sn, keys1, keys2, n_ar, st, gap_open, arch, blocked, n_un_block, n_un, st_a, a, o

    keys0, n_sn, keys1, keys2, n_ar, st, gap_open, arch, blocked, n_un_block, n_un, st_a, a, o = run(scenario, modules=[review])
    assert keys0 == {"p:시험A", "p:시험B"}
    assert n_sn == 1 and keys1 == {"p:시험A"}                # 미루면 빠짐
    assert keys2 == {"p:시험A", "p:시험B"}                   # 미뤄도 새 신고면 다시 보임
    assert n_ar == 1 and st == {a: "archived", o: "active"}  # 다른 컬렉션은 그대로
    assert gap_open is True and [d["key"] for d in arch] == ["p:시험A"]
    assert blocked >= 1                                        # 보관도 살아 있는 내용 — 컬렉션 삭제 판정에 셈
    assert n_un_block == 0 and n_un == 1 and st_a == "active"


# ── 게시·검색 확인 진행(11/05 "멈춘 것처럼 보인다") ─────────────────────────────

def test_publish_claim_progress_and_interrupted_check():
    """게시 잡기: 한 번만(두 번째는 거부) · 진행 = 끝난 작업/전체, 다 끝나면 용어 추출 단계 · 기동 정리가 도는 중 검색 확인을 '끊김'으로."""
    ti = load("_rdb_real_team_import_p", "agents/knowledge_rag/ingestion/team_import.py")

    async def scenario(conn):
        s = await ns(conn, "시험_게시진행 시스템")
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        jobs = [await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks, held_plan) "
                                    "VALUES ($1,'confluence','held',1,'{}'::jsonb) RETURNING id", s) for _ in range(3)]
        b = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, status, sampling_required, total_pages, done_pages, job_ids, loaded_at, "
                                "publish_check) VALUES ($1, 'https://confl.sinc.co.kr/display/P', 'loaded', FALSE, 3, 3, $2::int[], NOW(), "
                                "'{\"running\": true, \"asked\": 2, \"total\": 10}'::jsonb) RETURNING id", team, jobs)
        got = await ti.claim_publish(conn, b)
        try:
            await ti.claim_publish(conn, b)
            second = "통과"
        except ValueError:
            second = "거부"
        row = dict(await conn.fetchrow("SELECT * FROM rag_import_batch WHERE id = $1", b))
        before = await ti.publish_progress(conn, row)
        await conn.execute("UPDATE rag_ingestion_job SET status = 'completed' WHERE id = $1", jobs[0])
        mid = await ti.publish_progress(conn, row)
        await conn.execute("UPDATE rag_ingestion_job SET status = 'completed' WHERE id = ANY($1::int[])", jobs)
        end = await ti.publish_progress(conn, row)
        running = ti.check_running(row)
        await ti.close_interrupted(conn)
        pc = json.loads(await conn.fetchval("SELECT publish_check::text FROM rag_import_batch WHERE id = $1", b))
        idle = await ti.publish_progress(conn, dict(await conn.fetchrow("SELECT * FROM rag_import_batch WHERE id = $1", b)))
        return (sorted(got) == sorted(jobs), second, (before["done"], before["total"], before["phase"]), (mid["done"], mid["phase"]),
                end["phase"], running, pc.get("running"), pc.get("interrupted"), pc.get("asked"), idle)
    assert run(scenario) == (True, "거부", (0, 3, "jobs"), (1, "jobs"), "glossary", True, None, True, 2, None)


def test_overlap_detail_finds_pairs_by_chunk_id_after_middle_removed():
    """겹침 상세는 조각 id로 쌍을 찾는다 — 기록 뒤 중간 조각이 빠져도 엉뚱한 쌍에 붙지 않고 '바뀜'으로 센다(리뷰 3).
    원문 연결이 없는 조각이 있으면 원문 대조 없이(예전 기준) 기록 · 본문 미기록."""
    ti = load("_rdb_real_team_import_o", "agents/knowledge_rag/ingestion/team_import.py")
    qm = load("_rdb_real_quality", "agents/knowledge_rag/ingestion/quality.py")
    row = "| 같은 행 | 값 | 비고 칸 |"

    async def scenario(conn):
        s = await ns(conn, "시험_겹침 시스템")
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        j = await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks) VALUES ($1,'confluence','held',4) RETURNING id", s)
        ks = [await knowledge(conn, s, c, status="staging", ingestion_job_id=j, source_chunk_idx=n)
              for n, c in enumerate([f"가 본문\n{row}", f"{row}\n나 본문입니다", "다 본문입니다 길게", f"{row}\n라 본문입니다"])]
        b = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, status, sampling_required, total_pages, done_pages, job_ids, loaded_at) "
                                "VALUES ($1, 'https://confl.sinc.co.kr/display/O', 'loaded', FALSE, 1, 1, $2::int[], NOW()) RETURNING id", team, [j])
        q = await qm.record_job_quality(conn, j)
        first = await ti.overlap_detail(conn, b, j)
        await conn.execute("UPDATE rag_knowledge SET status = 'deleted' WHERE id = $1", ks[0])   # 앞 쌍의 한쪽이 빠짐
        after = await ti.overlap_detail(conn, b, j)
        other = await ti.overlap_detail(conn, b, j + 999999)
        stored = await conn.fetchval("SELECT quality::text FROM rag_ingestion_job WHERE id = $1", j)
        return (q["source_checked"], q["adjacent_overlap"], [(p["a"], p["b"], p["a_id"] == ks[0]) for p in first["pairs"]],
                after["stale"], len(after["pairs"]), other, "같은 행" in stored)
    assert run(scenario) == (False, 1, [(1, 2, True)], 1, 0, None, False)


def test_dedupe_sees_other_held_jobs_staging_chunks():
    """중복 비교 대상: 사용 중 + 게시 전 검수(held) 작업의 비중복 조각 — 팀 적재는 페이지마다 작업이 따로이고 게시 전까지 staging이라
    같은 배치의 다른 페이지끼리 못 봤다(11/05 앱 공간 84조각). 끝난·취소된 작업의 staging, 중복 의심(staging_review)은 대상 아님."""
    from realdb import VEC
    rmod = sys.modules[ksvc.find_similar_active_knowledge.__module__]

    async def scenario(conn):
        s = await ns(conn, "시험_보류비교 시스템")
        job = lambda st: conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks) "   # noqa: E731
                                       "VALUES ($1,'confluence',$2,1) RETURNING id", s, st)
        held, cancelled, mine = await job("held"), await job("cancelled"), await job("processing")
        k_held = await knowledge(conn, s, "보류 페이지 조각", status="staging", ingestion_job_id=held)
        k_review = await knowledge(conn, s, "보류 중복 의심 조각", status="staging_review", ingestion_job_id=held)
        k_cancel = await knowledge(conn, s, "취소된 조각", status="staging", ingestion_job_id=cancelled)
        k_mine = await knowledge(conn, s, "이 작업 앞 조각", status="staging", ingestion_job_id=mine)
        k_active = await knowledge(conn, s, "사용 중 조각")
        got = {m["id"] for m in await rmod.find_similar_active_knowledge(s, VEC, limit=10, staging_job_id=mine)}
        return ({k_held, k_mine, k_active} <= got, k_review in got, k_cancel in got)
    assert run(scenario, [rmod]) == (True, False, False)


def test_publish_check_clears_running_when_it_dies_midway():
    """검색 확인이 중간에 죽어도(임베딩 오류) '도는 중'이 내려가고 사유·센 데까지가 남는다 — 안 그러면 버튼이 재시작 전까지 잠긴다(리뷰 M1)."""
    import types
    from contextlib import asynccontextmanager
    ti = load("_rdb_real_team_import_c", "agents/knowledge_rag/ingestion/team_import.py")

    async def scenario(conn):
        s = await ns(conn, "시험_검색확인 시스템")
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        j = await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_type, status, total_chunks) VALUES ($1,'confluence','held',2) RETURNING id", s)
        for n in range(2):
            await knowledge(conn, s, f"검색 확인 표본 조각 {n} — 길이를 사십 자 넘게 채우는 본문입니다 가나다라마바사", status="staging", ingestion_job_id=j)
        b = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, status, sampling_required, total_pages, done_pages, job_ids, loaded_at) "
                                "VALUES ($1, 'https://confl.sinc.co.kr/display/C', 'loaded', FALSE, 1, 1, $2::int[], NOW()) RETURNING id", team, [j])

        @asynccontextmanager
        async def get_conn():
            yield conn
        llm = MagicMock()
        llm.generate_once = AsyncMock(return_value="질문 하나?")
        emb = MagicMock()
        emb.embed = AsyncMock(side_effect=RuntimeError("임베딩 서버 끊김"))
        fakes = {"core.database": types.SimpleNamespace(get_conn=get_conn),
                 "service.llm.factory": types.SimpleNamespace(get_ingest_llm=lambda: (llm, False)),
                 "shared.embedding": types.SimpleNamespace(embedding_service=emb)}
        with patch.dict(sys.modules, fakes):
            out = await ti.publish_check(b)
        pc = json.loads(await conn.fetchval("SELECT publish_check::text FROM rag_import_batch WHERE id = $1", b))
        return ("running" in pc, "RuntimeError" in (pc.get("error") or ""), pc.get("asked"), out.get("asked"))
    assert run(scenario) == (False, True, 0, 0)


# ── 동기화 때 본문이 빈 페이지(11/11 밤샘 점검) ─────────────────────────────────────────────

def test_sync_empty_page_holds_old_chunks():
    """본문이 비게 된 페이지 — 예전엔 '빈 페이지'로 세기만 하고 옛 조각이 계속 현행으로 검색됐다. 불변식: 그 페이지·그 컬렉션의 사용 중 조각 0
    (승인 대기로 내림, 지우지 않음) · 다른 페이지 조각은 그대로."""
    from types import SimpleNamespace
    sync = load("_rdb_real_confluence_sync_empty", "agents/knowledge_rag/ingestion/confluence_sync.py")

    async def scenario(conn):
        s = await ns(conn, "시험_빈페이지 시스템")
        team = await conn.fetchval("SELECT id FROM ops_team ORDER BY id LIMIT 1")
        url = "https://confl.sinc.co.kr/display/시험빈페이지"
        b = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, status, sampling_required, total_pages, done_pages) "
                                "VALUES ($1, $2, 'loaded', FALSE, 1, 1) RETURNING id", team, url)
        await conn.execute("INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, status, final_namespace_id) "
                           "VALUES ($1, '시험E1', '시험', 'b', 1, 'loaded', $2)", b, s)
        emptied = [await knowledge(conn, s, f"시험 빈 페이지 옛 조각 {i}", confluence_page_id="시험E1") for i in range(2)]
        other = await knowledge(conn, s, "시험 다른 페이지 조각", confluence_page_id="시험E2")
        doc = SimpleNamespace(metadata={"raw_html": ""}, raw_text="", source_type="confluence", source_name="시험", )
        src = SimpleNamespace(root_id=None, page=AsyncMock(return_value=doc))
        out = {k: 0 for k in ("empty", "held", "unchanged", "denied", "restricted")}
        out["_touched"] = set()
        c = {"team_id": team, "root_url": url}
        with patch.object(sync, "_write_denied", AsyncMock(return_value=None)):
            res = await sync._reingest(src, c, {"page_id": "시험E1", "ancestors": []}, {}, {"id": None, "role": "admin"}, out)
        st = {r["id"]: r["status"] for r in await conn.fetch("SELECT id, status FROM rag_knowledge WHERE id = ANY($1::int[])", [*emptied, other])}
        return res, out, st, emptied, other, s

    res, out, st, emptied, other, s = run(scenario, modules=[sync])
    assert res is None and out["empty"] == 1 and out["held"] == 2 and out["_touched"] == {s}
    assert [st[i] for i in emptied] == ["pending_review", "pending_review"] and st[other] == "active"
