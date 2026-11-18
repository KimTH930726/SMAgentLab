"""게시 전 골든셋 확인(ingestion/golden_check.py, 11/17) — 실 DB(트랜잭션 롤백). 벡터는 손으로 정해 순위를 안다.

불변식:
- 같은 내용으로 다시 넣기(조각 번호만 바뀜) → 나빠짐 0 · 정답 사라짐 0 (닻이 새 조각을 정답으로 찾는다)
- 정답 줄이 바뀐 내용으로 교체 → 정답 사라짐(나빠짐과 따로)
- 새 조각이 정답을 상위 3 밖으로 밀어냄 → 나빠짐, 대신 나오는 것에 "이번 배치" 표시
- 게시 때 내려갈 옛 버전 = held_plan replacing_pages(_switch_staged와 같은 조건) · 강제 승인 대기 작업 조각은 검색에 안 든다
- 다른 컬렉션 문항·범위 밖 문항·닻 없는 문항은 세지 않는다 · 두 번 잡기(claim)는 하나만
"""
import asyncio
import json
import math
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from realdb import load, ns, run  # noqa: E402

gc = load("_rdb_golden_check", "agents/knowledge_rag/ingestion/golden_check.py")
from trustkit.eval.anchors import anchors_for  # noqa: E402

NS, OTHER = "시험_골든확인 시스템", "시험_골든확인 다른"


def vec(*pairs) -> str:
    """(축, 값) → 단위 벡터 문자열(1024차원)."""
    v = [0.0] * 1024
    for i, x in pairs:
        v[i] = x
    n = math.sqrt(sum(x * x for x in v)) or 1
    return "[" + ",".join(f"{x / n:.6f}" for x in v) + "]"


Q = [float(x) for x in vec((0, 1)).strip("[]").split(",")]   # 질문 = 0번 축
GOLD_V = vec((0, 0.8), (1, 0.6))                              # 질문과 유사도 0.8
NEAR_V = vec((0, 0.95), (2, 0.31))                            # 정답보다 가까움


async def chunk(conn, n, content, v, *, page=None, status="active", job=None):
    return await conn.fetchval(
        "INSERT INTO rag_knowledge (namespace_id, content, embedding, status, confluence_page_id, ingestion_job_id, source_file) "
        "VALUES ($1, $2, $3::vector, $4, $5, $6, '시험_골든확인') RETURNING id", n, content, v, status, page, job)


async def job(conn, n, plan):
    return await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, source_file, source_type, status, held_plan) "
                               "VALUES ($1, '시험_골든확인', 'confluence', 'held', $2::jsonb) RETURNING id", n, json.dumps(plan))


GOLD_TEXT = "시험_정답 페이지 고유 첫 줄입니다\n시험_정답 페이지 고유 둘째 줄입니다"


async def base(conn):
    """정답 조각(페이지 P1, 1위) + 먼 조각 3개. 문항 하나(닻) + 세지 않을 문항들."""
    n = await ns(conn, NS)
    o = await ns(conn, OTHER)
    g = await chunk(conn, n, GOLD_TEXT, GOLD_V, page="P1")
    for i in range(3):
        await chunk(conn, n, f"시험_먼 조각 {i} 본문 줄입니다", vec((0, 0.3), (10 + i, 0.95)), page=f"F{i}")
    live = [dict(r) for r in await conn.fetch("SELECT id, content, confluence_page_id, content_norm_md5 FROM rag_knowledge WHERE namespace_id = $1", n)]
    anchors = [anchors_for([g], live)[g]]
    items = [{"qid": "A", "ns": NS, "question": "시험_정답 질문?", "gold_anchors": anchors},
             {"qid": "B", "ns": OTHER, "question": "시험_다른 컬렉션 질문?", "gold_anchors": anchors},   # 배치가 안 닿는 컬렉션
             {"qid": "C", "ns": NS, "question": "시험_범위 밖?", "expect": "abstain"},
             {"qid": "D", "ns": NS, "question": "시험_옛 id 정답?", "gold_ids": [g]}]
    return n, o, g, items


async def embed(texts):
    return [Q for _ in texts]


def test_same_content_reloaded_is_not_worse():
    async def scenario(conn):
        n, _o, _g, items = await base(conn)
        j = await job(conn, n, {"replacing_pages": ["P1"]})
        await chunk(conn, n, GOLD_TEXT, GOLD_V, page="P1", status="staging", job=j)   # 같은 내용, 새 조각 번호
        return await gc.run_check(conn, [j], items, embed)
    out = run(scenario)
    assert out["items"] == 1                                    # B(다른 컬렉션)·C(범위 밖)·D(닻 없음) 제외
    assert (out["worse"], out["gold_lost"], out["better"]) == (0, 0, 0)


def test_changed_gold_lines_is_gold_lost():
    async def scenario(conn):
        n, _o, _g, items = await base(conn)
        j = await job(conn, n, {"replacing_pages": ["P1"]})
        await chunk(conn, n, "시험_완전히 바뀐 내용 첫 줄\n시험_완전히 바뀐 내용 둘째 줄", GOLD_V, page="P1", status="staging", job=j)
        return await gc.run_check(conn, [j], items, embed)
    out = run(scenario)
    assert (out["gold_lost"], out["worse"]) == (1, 0) and out["details"][0]["gold_lost"] and out["details"][0]["before"] == 1


def test_new_chunks_push_gold_out_of_top3_is_worse():
    async def scenario(conn):
        n, _o, _g, items = await base(conn)
        j = await job(conn, n, {"replacing_pages": ["N0", "N1", "N2"]})
        for i in range(3):
            await chunk(conn, n, f"시험_새 조각 {i} 본문 줄입니다", NEAR_V, page=f"N{i}", status="staging", job=j)
        return await gc.run_check(conn, [j], items, embed)
    out = run(scenario)
    d = out["details"][0]
    assert (out["worse"], out["gold_lost"]) == (1, 0)
    assert (d["before"], d["after"]) == (1, 4) and all(g["new"] for g in d["got"])


def test_force_pending_job_chunks_do_not_count():
    async def scenario(conn):
        n, _o, _g, items = await base(conn)
        j = await job(conn, n, {"replacing_pages": [], "force_pending": True})
        for i in range(3):
            await chunk(conn, n, f"시험_새 조각 {i} 본문 줄입니다", NEAR_V, page=f"N{i}", status="staging", job=j)
        return await gc.run_check(conn, [j], items, embed)
    assert run(scenario)["worse"] == 0


def test_replacing_page_retires_old_version_only_in_after():
    """정답 페이지를 내리기만 하고(새 조각은 다른 페이지) → 지금 1위 · 게시하면 정답 없음."""
    async def scenario(conn):
        n, _o, _g, items = await base(conn)
        j = await job(conn, n, {"replacing_pages": ["P1"]})
        await chunk(conn, n, "시험_다른 페이지 새 조각 줄입니다", vec((5, 1)), page="X", status="staging", job=j)
        return await gc.run_check(conn, [j], items, embed)
    out = run(scenario)
    assert (out["gold_lost"], out["worse"]) == (1, 0) and out["details"][0]["after"] is None


def test_claim_once():
    async def scenario(conn):
        n = await ns(conn, NS)
        b = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, status, job_ids) "
                                "VALUES ((SELECT id FROM ops_team LIMIT 1), 'https://x/시험', 'loaded', '{}') RETURNING id")
        return [await gc.claim(conn, b), await gc.claim(conn, b)]
    assert run(scenario) == [True, False]


def test_verdict_rules():
    assert gc.verdict(1, 4) == "worse" and gc.verdict(None, 2) == "better"
    assert gc.verdict(1, 3) == "down" and gc.verdict(2, 1) == "same" and gc.verdict(None, None) == "same"
    assert gc.usable([{"ns": "a", "question": " q ", "gold_anchors": [1]}, {"ns": "a", "question": "q", "gold_anchors": [2]}]) == \
        [{"ns": "a", "question": " q ", "gold_anchors": [1]}]


svc = load("_rdb_knowledge_service_gc", "agents/knowledge_rag/knowledge/service.py")


def test_after_pool_equals_real_publish_random():
    """불변식: '게시했다면' 쪽 active 집합 = 실제 _switch_staged를 돌린 뒤 active 집합(무작위 배치 40개, 세이브포인트에서 돌리고 되돌림).
    페이지 교체 · 한 컬렉션에 작업 둘 · staging_review(→ 승인 대기) · 강제 승인 대기 작업 · 교체 안 하는 작업."""
    import random

    async def scenario(conn):
        n = await ns(conn, NS)
        rng = random.Random(1117)
        bad = []
        for case in range(40):
            for p in range(6):   # 페이지마다 active 0~2개
                for _ in range(rng.randint(0, 2)):
                    await chunk(conn, n, f"시험_옛 {case}-{p}-{rng.random()}", GOLD_V, page=f"C{case}P{p}")
            jobs = []
            for _ in range(rng.randint(1, 2)):
                pages = rng.sample([f"C{case}P{p}" for p in range(6)], rng.randint(0, 3))
                plan = {"replacing_pages": pages, "confirm_replace": True, "force_pending": rng.random() < 0.2}
                jid = await job(conn, n, plan)
                for pg in pages or [f"C{case}N"]:
                    await chunk(conn, n, f"시험_새 {case}-{pg}-{rng.random()}", GOLD_V, page=pg, status="staging", job=jid)
                    if rng.random() < 0.3:
                        await chunk(conn, n, f"시험_검토 {case}-{pg}-{rng.random()}", GOLD_V, page=pg, status="staging_review", job=jid)
                jobs.append((jid, plan))
            rows = await conn.fetch("SELECT j.id, j.namespace_id, n.name, j.held_plan FROM rag_ingestion_job j JOIN ops_namespace n ON n.id = j.namespace_id "
                                    "WHERE j.id = ANY($1::int[])", [j for j, _ in jobs])
            excluded, new_jobs = await gc.publish_effect(conn, rows)
            active = {r["id"] for r in await conn.fetch("SELECT id FROM rag_knowledge WHERE namespace_id = $1 AND status = 'active'", n)}
            staged = {r["id"] for r in await conn.fetch("SELECT id FROM rag_knowledge WHERE ingestion_job_id = ANY($1::int[]) AND status = 'staging'", new_jobs)}
            expected = (active | staged) - excluded
            sp = conn.transaction()
            await sp.start()
            for jid, plan in sorted(jobs):   # 게시 순서 = 작업 id 순(publish_batch)
                await svc._switch_staged(conn, job_id=jid, ns_id=n, source_doc_ids=[], partial_doc_ids=[], replacing_pages=plan["replacing_pages"],
                                         replacing_docs=[], confirm_replace=True, force_pending=plan["force_pending"])
            actual = {r["id"] for r in await conn.fetch("SELECT id FROM rag_knowledge WHERE namespace_id = $1 AND status = 'active'", n)}
            await sp.rollback()
            if actual != expected:
                bad.append((case, sorted(actual ^ expected)))
            await conn.execute("UPDATE rag_knowledge SET status = 'deleted' WHERE namespace_id = $1", n)   # 다음 배치는 빈 컬렉션에서
        return bad
    assert run(scenario) == []



def test_exact_copy_of_found_gold_is_gold():
    """사본 규칙(측정 로더와 같음): 닻으로 찾은 정답과 글자까지 같은 조각은 다른 페이지여도 정답. 닻으로 못 찾았으면 사본도 없다
    (닻은 정답 페이지 안에서만 찾는다 — 정답 페이지가 바뀌면 다른 페이지 사본이 있어도 정답 사라짐, 로더와 같다)."""
    from trustkit.eval.anchors import anchor_hashes
    texts = {1: GOLD_TEXT, 2: GOLD_TEXT, 3: "시험_다른 내용 줄입니다"}
    chunks = [{"id": 1, "page": "P1", "md5": "m"}, {"id": 2, "page": "COPY", "md5": "m"}, {"id": 3, "page": "P1", "md5": "x"}]
    hashed = {i: set(anchor_hashes(t)) for i, t in texts.items()}
    anchors = [{"page": "P1", "lines": sorted(hashed[1]), "need": 1}]
    assert gc._Side(chunks, hashed).gold(anchors) == {1, 2}
    assert gc._Side([chunks[1], chunks[2]], hashed).gold(anchors) == set()
