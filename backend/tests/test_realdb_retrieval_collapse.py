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
retrieval.settings = __import__("types").SimpleNamespace(freshness_decay_halflife_days=0)   # 단위 테스트의 가짜 설정 대신 — 감쇠 없음
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
