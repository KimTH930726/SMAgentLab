"""규칙 기반 한국어 조사/어미 제거가 RDB(param) 검색 hit@K를 실제로 올리는지 비교하는
일회성 실험 스크립트 — docs/tech/embedding-reranker-upgrade-plan.md 논의(2026-09-10)에서
"mecab/Kiwi 붙이기 전에 저비용 규칙 기반부터 실측해보자"는 결론에 따라 작성.

같은 골든셋(89문항)·같은 policy_param 데이터로 Track2의 RDB(param) 검색 부분만 떼어내
A(원문 그대로 tsquery) vs B(조사/어미 규칙 기반 제거 후 tsquery)를 비교한다. 벡터 쪽은
건드리지 않는다 — 지금 궁금한 건 "RDB 채널 자체를 개선했을 때 얼마나 나아지는가"이므로.

방법: PostgreSQL 임시 테이블에 policy_param+policy_item의 원문/제거문 버전을 둘 다 적재해
같은 to_tsquery/ts_rank 패턴으로 top-K를 뽑고, item-id 기준 hit@K로 채점한다(Track2와 동일
채점 방식). 임시 테이블이라 세션 종료 시 자동 삭제, 프로덕션 데이터 영향 없음.

실행: docker exec -it ops-backend python scripts/bench_suffix_stripping.py
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import asyncpg

from shared.embedding import embedding_service
from service.policy.korean_text import strip_text  # v2.71로 프로덕션(search.py)에 적용된 것과
# 동일한 규칙 — 이 스크립트가 따로 갖고 있던 사본은 제거하고 단일 출처를 재사용한다.

_GOLDEN_SET_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "golden_set" / "online_delivus_v1.jsonl"
_FILE_TO_NAMESPACE = [("온라인스토어", "온라인스토어 DB"), ("딜리버스", "딜리버스 DB")]
_TOP_K = 10


def _namespace_for_file(file_: str):
    for needle, ns in _FILE_TO_NAMESPACE:
        if needle in file_:
            return ns
    return None


async def _resolve_gold_ids(conn, entry, ns_id):
    qtype, src = entry["type"], entry["source"]
    if qtype in ("param", "narrative"):
        r = await conn.fetchrow(
            "SELECT id FROM policy_item WHERE source_file=$1 AND source_sheet=$2 AND source_row=$3 AND status != 'deprecated'",
            src["file"], src["sheet"], src["row"],
        )
        return {r["id"]} if r else set()
    elif qtype == "navigation":
        rows = await conn.fetch(
            "SELECT id FROM policy_item WHERE namespace_id=$1 AND status != 'deprecated' AND $2 = ANY(category_path)",
            ns_id, src["category"],
        )
        return {r["id"] for r in rows}
    else:
        rows = await conn.fetch(
            """SELECT DISTINCT p.policy_item_id AS id FROM policy_param p
               JOIN policy_item i ON i.id = p.policy_item_id
               WHERE i.namespace_id=$1 AND i.status != 'deprecated' AND p.condition = $2""",
            ns_id, src["condition"],
        )
        return {r["id"] for r in rows}


async def _search_param(conn, table: str, query_text: str, top_k: int) -> set[int]:
    """table: 'param_raw' 또는 'param_stripped' — 각각 원문/조사제거문 컬럼을 가진 임시 테이블."""
    rows = await conn.fetch(
        f"""
        SELECT item_id, ts_rank(to_tsvector('simple', name || ' ' || COALESCE(condition,'') || ' ' || policy_name), q.tsq) AS rank
        FROM {table}
        CROSS JOIN LATERAL (
            SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS tsq
            FROM (SELECT DISTINCT lexeme FROM unnest(to_tsvector('simple', $1))) t
            WHERE lexeme IS NOT NULL
        ) q
        WHERE to_tsvector('simple', name || ' ' || COALESCE(condition,'') || ' ' || policy_name) @@ q.tsq
        ORDER BY rank DESC LIMIT $2
        """,
        query_text, top_k,
    )
    return {r["item_id"] for r in rows}


async def _search_narrative(conn, ns_id: int, query_vec: list[float], top_k: int) -> set[int]:
    """service/policy/search.py의 narrative(벡터) 검색과 동일한 SQL — 양쪽 비교군이 공유하는
    벡터 채널이라 원문/조사제거 버전을 따로 안 만들고 그대로 재사용한다."""
    rows = await conn.fetch(
        """
        SELECT i.id AS item_id
        FROM policy_chunk c
        JOIN policy_item i ON i.id = c.policy_item_id
        WHERE i.namespace_id = $1 AND i.status != 'deprecated'
        ORDER BY c.embedding <=> $2::vector
        LIMIT $3
        """,
        ns_id, str(query_vec), top_k,
    )
    return {r["item_id"] for r in rows}


async def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://ops:ops1234@localhost:5432/opsdb")
    conn = await asyncpg.connect(db_url)
    embedding_service.load()

    with open(_GOLDEN_SET_PATH, encoding="utf-8") as f:
        golden = [json.loads(line) for line in f if line.strip()]

    ns_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM ops_namespace")}

    try:
        # ── 임시 테이블 2개(원문/조사제거) 구성 ──
        await conn.execute("""
            CREATE TEMP TABLE param_raw (item_id int, name text, condition text, policy_name text)
        """)
        await conn.execute("""
            CREATE TEMP TABLE param_stripped (item_id int, name text, condition text, policy_name text)
        """)
        rows = await conn.fetch(
            """SELECT p.policy_item_id AS item_id, p.name, p.condition, i.policy_name
               FROM policy_param p JOIN policy_item i ON i.id = p.policy_item_id
               WHERE i.status != 'deprecated'"""
        )
        raw_data = [(r["item_id"], r["name"] or "", r["condition"] or "", r["policy_name"] or "") for r in rows]
        stripped_data = [
            (item_id, strip_text(name), strip_text(cond), strip_text(pname))
            for item_id, name, cond, pname in raw_data
        ]
        await conn.copy_records_to_table("param_raw", records=raw_data, columns=["item_id", "name", "condition", "policy_name"])
        await conn.copy_records_to_table("param_stripped", records=stripped_data, columns=["item_id", "name", "condition", "policy_name"])
        await conn.execute("CREATE INDEX ON param_raw USING gin (to_tsvector('simple', name || ' ' || COALESCE(condition,'') || ' ' || policy_name))")
        await conn.execute("CREATE INDEX ON param_stripped USING gin (to_tsvector('simple', name || ' ' || COALESCE(condition,'') || ' ' || policy_name))")

        # ── 골든셋 채점 ──
        # RDB 채널 단독(param_rows) + 하이브리드 전체(hybrid_rows, RDB+벡터 결합 — 벡터쪽은
        # 원문/조사제거 양쪽에 동일하게 재사용) 둘 다 잰다. hit@K만 보면 "노이즈가 늘어서
        # 우연히 걸린 것"인지 구분이 안 되므로 precision@K도 같이 잰다(2026-09-08 원칙 재사용).
        param_rows: dict[str, list[tuple[bool, bool, float, float]]] = {}
        hybrid_rows: dict[str, list[tuple[bool, bool, float, float, bool, bool]]] = {}
        for entry in golden:
            qtype, query = entry["type"], entry["query"]
            src = entry["source"]
            ns_name = _namespace_for_file(src.get("file", ""))
            if ns_name is None or ns_name not in ns_ids:
                continue
            ns_id = ns_ids[ns_name]
            gold_ids = await _resolve_gold_ids(conn, entry, ns_id)
            if not gold_ids:
                continue

            raw_param_hits = await _search_param(conn, "param_raw", query, _TOP_K)
            stripped_param_hits = await _search_param(conn, "param_stripped", strip_text(query), _TOP_K)
            query_vec = await embedding_service.embed(query)
            narrative_hits = await _search_narrative(conn, ns_id, query_vec, _TOP_K)

            a_hit = bool(gold_ids & raw_param_hits)
            b_hit = bool(gold_ids & stripped_param_hits)
            a_prec = (len(gold_ids & raw_param_hits) / len(raw_param_hits)) if raw_param_hits else 0.0
            b_prec = (len(gold_ids & stripped_param_hits) / len(stripped_param_hits)) if stripped_param_hits else 0.0
            param_rows.setdefault(qtype, []).append((a_hit, b_hit, a_prec, b_prec))

            base_combined = raw_param_hits | narrative_hits
            strip_combined = stripped_param_hits | narrative_hits
            base_hit = bool(gold_ids & base_combined)
            strip_hit = bool(gold_ids & strip_combined)
            base_prec = (len(gold_ids & base_combined) / len(base_combined)) if base_combined else 0.0
            strip_prec = (len(gold_ids & strip_combined) / len(strip_combined)) if strip_combined else 0.0
            strip_via_rdb = bool(gold_ids & stripped_param_hits)
            strip_via_vec = bool(gold_ids & narrative_hits)
            hybrid_rows.setdefault(qtype, []).append(
                (base_hit, strip_hit, base_prec, strip_prec, strip_via_rdb, strip_via_vec)
            )
    finally:
        await conn.close()

    def _avg(rows_, idx):
        return sum(r[idx] for r in rows_) / len(rows_) if rows_ else 0.0

    print("\n=== ① RDB(param) 채널 단독 — 벡터 안 섞음 ===")
    print(f"{'유형':16}{'건수':>5}{'원문hit':>10}{'제거hit':>10}{'원문prec':>11}{'제거prec':>11}")
    all_p = []
    for t, rows_ in sorted(param_rows.items()):
        n = len(rows_)
        print(f"{t:16}{n:>5}{_avg(rows_,0)*100:>9.1f}%{_avg(rows_,1)*100:>9.1f}%{_avg(rows_,2)*100:>10.1f}%{_avg(rows_,3)*100:>10.1f}%")
        all_p += rows_
    n = len(all_p)
    print(f"{'전체':16}{n:>5}{_avg(all_p,0)*100:>9.1f}%{_avg(all_p,1)*100:>9.1f}%{_avg(all_p,2)*100:>10.1f}%{_avg(all_p,3)*100:>10.1f}%")

    print("\n=== ② 하이브리드 전체(RDB+벡터 결합) — 프로덕션과 동일 구조 ===")
    print(f"{'유형':16}{'건수':>5}{'기존hit':>10}{'조사제거hit':>13}{'기존prec':>11}{'조사제거prec':>14}")
    all_h = []
    for t, rows_ in sorted(hybrid_rows.items()):
        n = len(rows_)
        print(f"{t:16}{n:>5}{_avg(rows_,0)*100:>9.1f}%{_avg(rows_,1)*100:>12.1f}%{_avg(rows_,2)*100:>10.1f}%{_avg(rows_,3)*100:>13.1f}%")
        all_h += rows_
    n = len(all_h)
    print(f"{'전체':16}{n:>5}{_avg(all_h,0)*100:>9.1f}%{_avg(all_h,1)*100:>12.1f}%{_avg(all_h,2)*100:>10.1f}%{_avg(all_h,3)*100:>13.1f}%")

    print("\n=== ③ 조사제거 적용 후 — 하이브리드 안에서 RDB/벡터 기여도 재분해 ===")
    rdb_only = sum(1 for r in all_h if r[4] and not r[5])
    vec_only = sum(1 for r in all_h if r[5] and not r[4])
    both = sum(1 for r in all_h if r[4] and r[5])
    n = len(all_h)
    print(f"RDB만: {rdb_only/n*100:.1f}%  벡터만: {vec_only/n*100:.1f}%  둘다: {both/n*100:.1f}%  (§9 조사제거 전: RDB만 13.5%/벡터만 50.6%/둘다 13.5%)")


if __name__ == "__main__":
    asyncio.run(main())
