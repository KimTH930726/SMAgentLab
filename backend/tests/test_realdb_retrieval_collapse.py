"""검색 같은 글 접기(11/09) 실 DB 시험 — 다른 페이지에 똑같이 실린 글은 페이지마다 저장하고 검색에서만 한 건으로.

트랜잭션 안에서 만들고 되돌린다(tests/realdb.py). DB가 없으면 건너뛴다.
"""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))   # tests/ 공용 장치
import importlib.util  # noqa: E402

from realdb import ROOT, knowledge, load, ns, run  # noqa: E402

database = load("_rdb_real_database_collapse", "core/database.py")   # 단위 테스트 쪽 가짜 core.database 대신 실 범위 SQL

# realdb.load는 sys.modules에 안 올려 @dataclass(RetrievalResult)가 모듈을 못 찾는다 — 여기서만 올려서 실 모듈을 쓴다
_spec = importlib.util.spec_from_file_location("_rdb_real_retrieval", ROOT / "agents/knowledge_rag/knowledge/retrieval.py")
retrieval = importlib.util.module_from_spec(_spec)
sys.modules["_rdb_real_retrieval"] = retrieval
_spec.loader.exec_module(retrieval)
retrieval.namespace_scope_ids = database.namespace_scope_ids
retrieval.settings = __import__("types").SimpleNamespace(
    freshness_decay_halflife_days=0, knowledge_collapse_contained=True, glossary_min_similarity=0.5,
    knowledge_min_score=0.35, knowledge_high_score=0.8, knowledge_mid_score=0.55, duplicate_min_similarity=0.88)   # 단위 테스트의 가짜 설정 대신 — 감쇠 없음, 임계값은 config 기본값
UNIT = "[" + ",".join(["1"] + ["0"] * 1023) + "]"
QUERY = [1.0] + [0.0] * 1023


def test_identical_text_collapses_to_one_result_and_survives_removal():
    """성질: 같은 글(공백만 달라도) n개 → 결과에 1개 · 다른 글은 그대로 · 대표가 내려가면 남은 사본이 그 자리를 잇는다(유실 0)."""
    async def scenario(conn):
        s = await ns(conn, "시험_접기 시스템")
        a = await knowledge(conn, s, "주문 취소는 결제 후 30분 안에 가능", confluence_page_id="P1")
        b = await knowledge(conn, s, "주문 취소는  결제 후 30분 안에 가능\n", confluence_page_id="P2")
        c = await knowledge(conn, s, "환불은 영업일 3일 안에 처리", confluence_page_id="P3")
        await conn.execute("UPDATE rag_knowledge SET embedding = $2::vector WHERE id = ANY($1::int[])", [a, b, c], UNIT)
        await conn.execute("SET LOCAL enable_indexscan = off")   # HNSW는 전체 상위 후보 뒤에 영역 필터 — 시험 행 3개가 전체 상위에 안 들 수 있음
        name = await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", s)
        first = [r.id for r in await retrieval.search_knowledge(name, QUERY, "주문 취소", top_k=10)]
        await conn.execute("UPDATE rag_knowledge SET status = 'deleted' WHERE id = $1", a)
        after = [r.id for r in await retrieval.search_knowledge(name, QUERY, "주문 취소", top_k=10)]
        return a, b, c, first, after
    a, b, c, first, after = run(scenario, [retrieval])
    assert sorted(first) == [a, c]          # 같은 글은 id 작은 쪽 하나(점수 동점 → id 순)
    assert sorted(after) == [b, c]          # 대표가 지워지면 다른 페이지 사본이 이어받음


def test_higher_scored_copy_wins():
    """같은 글 중 점수가 높은 쪽(가중치)이 남는다 — id 순은 동점일 때만."""
    async def scenario(conn):
        s = await ns(conn, "시험_접기점수 시스템")
        a = await knowledge(conn, s, "같은 안내 문장", confluence_page_id="P1")
        b = await knowledge(conn, s, "같은 안내 문장", confluence_page_id="P2")
        await conn.execute("UPDATE rag_knowledge SET embedding = $2::vector WHERE id = ANY($1::int[])", [a, b], UNIT)
        await conn.execute("UPDATE rag_knowledge SET base_weight = 2.0 WHERE id = $1", b)
        await conn.execute("SET LOCAL enable_indexscan = off")
        name = await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", s)
        return b, [r.id for r in await retrieval.search_knowledge(name, QUERY, "안내", top_k=10)]
    b, got = run(scenario, [retrieval])
    assert got == [b]


def test_db_same_text_rule_matches_python_norm_text():
    """성질(리뷰 L2): DB의 같은 글 칸(content_norm_md5)과 적재의 norm_text가 같은 글을 같게 본다 — 비ASCII 공백(U+202F·U+3000·U+0085 등) 포함 무작위."""
    import hashlib
    import random
    from trustkit.ingest.dedupe import norm_text
    ws = [c for c in map(chr, range(0x3001)) if c.isspace()]
    rnd = random.Random(1109)
    texts = ["".join(rnd.choice(["가", "a", "1", "."] + ws) for _ in range(rnd.randint(0, 12))) for _ in range(300)]

    async def scenario(conn):
        s = await ns(conn, "시험_정규화 시스템")
        ids = [await knowledge(conn, s, t) for t in texts]
        got = {r["id"]: r["h"] for r in await conn.fetch("SELECT id, content_norm_md5 h FROM rag_knowledge WHERE id = ANY($1::int[])", ids)}
        return [got[i] for i in ids]
    db = run(scenario, [])
    assert db == [hashlib.md5(norm_text(t).encode()).hexdigest() for t in texts]


def test_contained_chunk_collapses_into_longer_one_and_setting_turns_it_off():
    """포함 접기(11/09): 문장이 다른 페이지 조각에 통째로 들어 있는 조각은 긴 쪽 한 자리로 · 일부만 겹치면 둘 다 · 설정을 끄면 전부.
    빈자리는 채우지 않는다(top_k보다 적게 나올 수 있음)."""
    async def scenario(conn):
        s = await ns(conn, "시험_포함접기 시스템")
        short = await knowledge(conn, s, "주문 취소는 결제 후 30분 안에 가능하다.", confluence_page_id="P1")
        long = await knowledge(conn, s, "주문 취소는 결제 후 30분 안에 가능하다. 그 뒤에는 고객센터로 문의한다.", confluence_page_id="P2")
        part = await knowledge(conn, s, "주문 취소는 결제 후 30분 안에 가능하다. 단, 예약 상품은 취소할 수 없다.", confluence_page_id="P3")
        await conn.execute("UPDATE rag_knowledge SET embedding = $2::vector WHERE id = ANY($1::int[])", [short, long, part], UNIT)
        await conn.execute("SET LOCAL enable_indexscan = off")
        name = await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", s)
        on = [r.id for r in await retrieval.search_knowledge(name, QUERY, "주문 취소", top_k=3)]
        retrieval.settings.knowledge_collapse_contained = False
        try:
            off = [r.id for r in await retrieval.search_knowledge(name, QUERY, "주문 취소", top_k=3)]
        finally:
            retrieval.settings.knowledge_collapse_contained = True
        return short, long, part, on, off
    short, long, part, on, off = run(scenario, [retrieval])
    assert sorted(on) == sorted([long, part])           # short ⊂ long·part — 하나로 접히고, long과 part는 서로 포함이 아니라 둘 다
    assert short not in on and len(on) == 2              # 빈자리 안 채움
    assert sorted(off) == sorted([short, long, part])


def test_explicit_collapse_false_shows_everything():
    """지식 검색 화면(collapse=False)은 설정이 켜져 있어도 접힌 조각까지 보여 준다 — 고치려고 찾는 화면."""
    async def scenario(conn):
        s = await ns(conn, "시험_화면접기 시스템")
        short = await knowledge(conn, s, "주문 취소는 결제 후 30분 안에 가능하다.", confluence_page_id="P1")
        long = await knowledge(conn, s, "주문 취소는 결제 후 30분 안에 가능하다. 그 뒤에는 고객센터로 문의한다.", confluence_page_id="P2")
        await conn.execute("UPDATE rag_knowledge SET embedding = $2::vector WHERE id = ANY($1::int[])", [short, long], UNIT)
        await conn.execute("SET LOCAL enable_indexscan = off")
        name = await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", s)
        return short, long, [r.id for r in await retrieval.search_knowledge(name, QUERY, "주문 취소", top_k=3, collapse=False)]
    short, long, got = run(scenario, [retrieval])
    assert sorted(got) == sorted([short, long])


def test_collapse_only_among_adopted_keeps_adopted_content_and_positions():
    """독립 리뷰(11/10): 문맥에 안 쓰는(채택 미달) 큰 조각이 쓰는 작은 조각을 지우면 안 된다 — 채택 조각끼리만 접고 채택 미달은 제자리.
    무작위 성질: 채택 조각의 문장은 모두 남은 채택 조각 어딘가에 · 채택 미달 조각은 전부 같은 상대 순서로 남음."""
    import random
    from trustkit.retrieval.fusion import _within, sentences
    th = {"knowledge_min_score": 0.35}
    R = retrieval.RetrievalResult
    a = R(id=1, namespace="n", content="가. 나. 다.", base_weight=2.0, final_score=0.9)   # 관련도 0.3 — 채택 미달인데 앞
    c = R(id=2, namespace="n", content="가. 나.", base_weight=1.0, final_score=0.8)       # 관련도 0.4 — 채택, a에 포함
    assert [r.id for r in retrieval.collapse_adopted([a, c], th)] == [1, 2]
    # 소속(상위 맥락)이 다르면 문장이 같아도 안 접는다 — AI가 보는 글(맥락 줄 + 본문)로 비교(11/10 외부 검수)
    silver = R(id=3, namespace="n", content="적립 3%. 무료배송.", base_weight=0.0, final_score=0.6, heading_path=["혜택", "실버"])
    gold = R(id=4, namespace="n", content="적립 3%. 무료배송. 라운지.", base_weight=0.0, final_score=0.6, heading_path=["혜택", "골드"])
    gold2 = R(id=5, namespace="n", content="적립 3%. 무료배송. 라운지.", base_weight=0.0, final_score=0.6, heading_path=["혜택", "실버"])
    bare = R(id=6, namespace="n", content="적립 3%. 무료배송.", base_weight=0.0, final_score=0.6)
    assert [r.id for r in retrieval.collapse_adopted([silver, gold], th)] == [3, 4]
    assert [r.id for r in retrieval.collapse_adopted([silver, gold2], th)] == [5]     # 같은 소속이면 긴 쪽
    assert [r.id for r in retrieval.collapse_adopted([bare, gold], th)] == [4]        # 맥락 없는 짧은 쪽은 긴 쪽에 다 있음
    assert [r.id for r in retrieval.collapse_adopted([silver, bare], th)] == [3]      # 맥락 있는 쪽이 더 담음
    rng = random.Random(1110)
    words = [f"문장{i}." for i in range(5)]
    heads = [[], ["가"], ["나"], ["가", "다"]]
    for _ in range(300):
        rs = [R(id=i, namespace="n", content=" ".join(rng.sample(words, rng.randint(1, 3))), base_weight=0.0,
                final_score=rng.choice([0.2, 0.5]), heading_path=list(rng.choice(heads))) for i in range(rng.randint(1, 7))]
        out = retrieval.collapse_adopted(rs, th)
        kept_adopted = [r for r in out if r.final_score >= 0.35]
        for r in rs:   # AI가 보던 글(맥락 + 본문)이 남은 채택 조각 어딘가에 그대로 — 소속까지 사라지지 않음
            if r.final_score >= 0.35:
                assert any(_within(sentences(retrieval.seen_text(r)), sentences(retrieval.seen_text(k))) for k in kept_adopted)
        assert [r.id for r in out if r.final_score < 0.35] == [r.id for r in rs if r.final_score < 0.35]


def test_same_text_copies_merge_into_one_slot_keeping_every_heading():
    """같은 글 접기(content_norm_md5) — 한 자리로 합치되 버리는 사본의 소속은 남는 조각 문맥 줄에 같이(11/10 외부 검수 중간 1 + 재리뷰 높음:
    소속까지 같아야 접게 하면 컨플은 첫 칸이 페이지 제목이라 다른 페이지끼리 하나도 안 접혔다). 같은 소속은 한 번만."""
    async def scenario(conn):
        s = await ns(conn, "시험_소속접기 시스템")
        silver = await knowledge(conn, s, "- 구매액 3% 적립.", confluence_page_id="P1", heading_path=["실버 혜택", "적립"])
        gold = await knowledge(conn, s, "- 구매액 3% 적립.", confluence_page_id="P2", heading_path=["골드 혜택", "적립"])
        copy = await knowledge(conn, s, "- 구매액  3% 적립.\n", confluence_page_id="P3", heading_path=["실버 혜택", "적립"])
        await conn.execute("UPDATE rag_knowledge SET embedding = $2::vector WHERE id = ANY($1::int[])", [silver, gold, copy], UNIT)
        await conn.execute("SET LOCAL enable_indexscan = off")
        name = await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", s)
        return silver, gold, copy, await retrieval.search_knowledge(name, QUERY, "적립", top_k=10)
    silver, gold, copy, got = run(scenario, [retrieval])
    assert [r.id for r in got] == [silver]                         # 세 벌이 한 자리(동점 → id 작은 쪽)
    assert retrieval.heading_line(got[0]) == "상위 맥락: 실버 혜택 > 적립 | 골드 혜택 > 적립"   # 골드 소속도 문맥에, 실버는 한 번


def test_review_only_filter_keeps_only_candidates_needing_judgment():
    """판단할 상대만 다시 보기(review_only_for, 11/10) — 실 SQL: 다른 원본 사용 중 글은 빼고, 원본 없는 글·같은 원본 글은 남긴다(needs_review와 같은 기준)."""
    async def scenario(conn):
        s = await ns(conn, "시험_판단후보 시스템")
        other = await knowledge(conn, s, "다른 페이지 글", confluence_page_id="Q1")
        manual = await knowledge(conn, s, "수기 글")
        same = await knowledge(conn, s, "같은 페이지 옛 글", confluence_page_id="P1")
        doc_other = await knowledge(conn, s, "다른 원문 글", source_document_id=None)
        await conn.execute("UPDATE rag_knowledge SET embedding = $2::vector WHERE id = ANY($1::int[])", [other, manual, same, doc_other], UNIT)
        await conn.execute("SET LOCAL enable_indexscan = off")
        allc = {r["id"] for r in await retrieval.find_similar_active_knowledge(s, QUERY, limit=10)}
        only = {r["id"] for r in await retrieval.find_similar_active_knowledge(s, QUERY, limit=10, review_only_for={"confluence_page_id": "P1"})}
        return other, manual, same, doc_other, allc, only
    other, manual, same, doc_other, allc, only = run(scenario, [retrieval])
    assert {other, manual, same, doc_other} <= allc
    assert only == {manual, same, doc_other}   # doc_other는 원본 표시 없음(원문 id 없음) → 판단할 상대


def test_review_only_filter_drops_same_batch_peer_staging():
    """같은 팀 적재 배치의 다른 페이지 작업(peer_jobs) 보류 조각은 다시 볼 후보에서 빠진다 — 판단할 상대가 아닌데 자리를 채워 그 뒤의
    수기 글을 가렸다(v2.230 리뷰 중간). 배치 밖 보류 작업 조각·수기 글은 남는다."""
    async def scenario(conn):
        s = await ns(conn, "시험_형제후보 시스템")
        peer, outside = [await conn.fetchval("INSERT INTO rag_ingestion_job (namespace_id, status) VALUES ($1, 'held') RETURNING id", s)
                         for _ in range(2)]
        sib = [await knowledge(conn, s, f"형제 페이지 양식 {i}", status="staging", confluence_page_id=f"S{i}", ingestion_job_id=peer)
               for i in range(12)]
        held_out = await knowledge(conn, s, "배치 밖 보류 글", status="staging", confluence_page_id="X1", ingestion_job_id=outside)
        manual = await knowledge(conn, s, "수기 글")
        await conn.execute("UPDATE rag_knowledge SET embedding = $2::vector WHERE id = ANY($1::int[])", [*sib, held_out, manual], UNIT)
        await conn.execute("SET LOCAL enable_indexscan = off")
        base = {"confluence_page_id": "P1"}
        before = {r["id"] for r in await retrieval.find_similar_active_knowledge(s, QUERY, limit=10, review_only_for=base)}
        after = {r["id"] for r in await retrieval.find_similar_active_knowledge(
            s, QUERY, limit=10, review_only_for={**base, "peer_jobs": [peer]})}
        return set(sib), held_out, manual, before, after
    sib, held_out, manual, before, after = run(scenario, [retrieval])
    assert len(before) == 10 and len(before & sib) >= 8             # 빼지 않으면 형제가 다시 볼 자리를 채운다(동점이라 순서는 정해지지 않음)
    assert after == {held_out, manual}


def test_copy_headings_are_score_ordered_and_capped_to_top_k():
    """사본 소속은 점수 순(실행마다 같은 문맥 줄) · top_k − 1개까지(합치지 않았어도 문맥엔 사본이 top_k개까지만 — 재리뷰 L1 11/10)."""
    async def scenario(conn):
        s = await ns(conn, "시험_사본상한 시스템")
        ids = [await knowledge(conn, s, "같은 안내 문장.", confluence_page_id=f"P{i}", heading_path=[f"페이지{i}"]) for i in range(5)]
        await conn.execute("UPDATE rag_knowledge SET embedding = $2::vector WHERE id = ANY($1::int[])", ids, UNIT)
        for i, k in enumerate(ids):   # 점수 = 가중치 순서(페이지4가 가장 높음)
            await conn.execute("UPDATE rag_knowledge SET base_weight = $2 WHERE id = $1", k, float(i))
        await conn.execute("SET LOCAL enable_indexscan = off")
        name = await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", s)
        return ids, await retrieval.search_knowledge(name, QUERY, "안내", top_k=3)
    ids, got = run(scenario, [retrieval])
    assert [r.id for r in got] == [ids[4]]
    assert got[0].other_headings == [["페이지3"], ["페이지2"]]   # 점수 순 · top_k − 1 = 2개


def test_copy_heading_cap_follows_context_size_not_candidate_size():
    """리랭커처럼 후보를 더 뽑아(top_k 크게) 나중에 자르는 쪽은 context_k를 넘긴다 — 사본 소속은 context_k − 1개까지(work-os 검수 11/10)."""
    async def scenario(conn):
        s = await ns(conn, "시험_사본상한후보 시스템")
        ids = [await knowledge(conn, s, "같은 안내 문장.", confluence_page_id=f"P{i}", heading_path=[f"페이지{i}"]) for i in range(6)]
        await conn.execute("UPDATE rag_knowledge SET embedding = $2::vector WHERE id = ANY($1::int[])", ids, UNIT)
        await conn.execute("SET LOCAL enable_indexscan = off")
        name = await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", s)
        wide = await retrieval.search_knowledge(name, QUERY, "안내", top_k=20, context_k=3)
        plain = await retrieval.search_knowledge(name, QUERY, "안내", top_k=20)
        return wide, plain
    wide, plain = run(scenario, [retrieval])
    assert len(wide[0].other_headings) == 2 and len(plain[0].other_headings) == 5
