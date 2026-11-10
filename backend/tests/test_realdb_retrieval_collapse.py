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
    rng = random.Random(1110)
    words = [f"문장{i}." for i in range(5)]
    for _ in range(300):
        rs = [R(id=i, namespace="n", content=" ".join(rng.sample(words, rng.randint(1, 3))), base_weight=0.0,
                final_score=rng.choice([0.2, 0.5])) for i in range(rng.randint(1, 7))]
        out = retrieval.collapse_adopted(rs, th)
        kept_adopted = [r for r in out if r.final_score >= 0.35]
        for r in rs:
            if r.final_score >= 0.35:
                assert any(_within(sentences(r.content), sentences(k.content)) for k in kept_adopted)
        assert [r.id for r in out if r.final_score < 0.35] == [r.id for r in rs if r.final_score < 0.35]
