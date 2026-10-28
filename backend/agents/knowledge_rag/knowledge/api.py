"""지식 코어 API — 바깥 창구(VOC 메일 등)가 지식을 쓰는 유일한 길(10/28 VOC 레이어 분리, docs/tech/post-demo-backlog.md §0-3).

막힌 일: VOC(`service/email_voc`)가 지식 검색 내부(retrieval·glossary_terms)를 직접 import하고 지식 표(rag_knowledge)에 SQL을 직접 날렸다 —
코어(검색·저장·신뢰도)를 바꾸면 VOC가 조용히 깨지고, VOC를 별도 에이전트로 떼려면 어디가 엮였는지부터 찾아야 했다.
업계(모듈러 모놀리스 · 포트와 어댑터)처럼 같은 프로젝트 안에서 경계만 긋는다: 창구는 이 모듈만 import, 코어 내부는 이 모듈 뒤로.
경계는 `tests/test_voc_boundary.py`가 정적 검사로 막는다. 별도 에이전트(HTTP)로 떼는 조건은 백로그 §0-3(두 번째 창구 · VOC 부하가 채팅에 영향 · 다른 팀 사용).
여기 함수는 옮겨 온 것 — 동작은 VOC가 쓰던 것과 같다(eval_out/voc_boundary_snapshot.py 전/후 비교).
"""
from __future__ import annotations

from dataclasses import dataclass

from agents.knowledge_rag.knowledge import glossary_terms, retrieval
from agents.knowledge_rag.knowledge.retrieval import RetrievalResult
from core.database import get_conn, resolve_namespace_id, scope_ids_subquery

__all__ = ["RetrievalResult", "Search", "search", "min_score", "nearest", "contents"]


@dataclass
class Search:
    results: list[RetrievalResult]
    context: str
    mapped_term: str | None
    w_vector: float     # 이번 검색에 쓴 가중치 — 창구가 "가중합 원점수"(base_weight 뺀 관련도)를 직접 계산할 때
    w_keyword: float


async def search(namespace: str, query_text: str, query_vec: list[float]) -> Search:
    """지식 검색 — 채팅 지식 검색과 같은 설정(용어 보강 방식 · 기본 가중치 · top_k)으로. 정책·참조데이터는 안 본다(지식만)."""
    _, mapped_term, enriched = await glossary_terms.resolve_query_terms(namespace, query_text, query_vec)
    d = retrieval.get_search_defaults()
    w_vector, w_keyword = d["default_w_vector"], d["default_w_keyword"]
    results = await retrieval.search_knowledge(namespace, query_vec, enriched, w_vector, w_keyword, int(d["default_top_k"]))
    return Search(results=results, context=retrieval.build_context(results), mapped_term=mapped_term, w_vector=w_vector, w_keyword=w_keyword)


def min_score() -> float:
    """지식을 근거로 쓰는 점수 하한(채팅 채택 기준 knowledge_min_score — final_score와 비교)."""
    return retrieval.get_thresholds()["knowledge_min_score"]


# 벡터 글자 → vector 변환은 키마다 한 번(MATERIALIZED) — LATERAL 안에서 c.emb::vector로 쓰면 지식 행마다 12KB 글자를 다시 파싱해
# 10배 느렸다(10/28 리뷰 실측 EXPLAIN: 5.5ms → 53ms, 고친 뒤 7.5ms). MATERIALIZED가 없으면 planner가 펼쳐 같은 문제가 난다.
_NEAREST_SQL = f"""
    WITH c AS MATERIALIZED (
        SELECT u.key, u.namespace_id, u.emb::vector AS v FROM unnest($1::bigint[], $2::int[], $3::text[]) AS u(key, namespace_id, emb)
    )
    SELECT c.key, best.knowledge_id, best.similarity, best.snippet, best.content
    FROM c
    LEFT JOIN LATERAL (
        SELECT k.id AS knowledge_id, 1 - (k.embedding <=> c.v) AS similarity, LEFT(k.content, $4) AS snippet, k.content
        FROM rag_knowledge k
        -- 영역 + 직계 하위 영역(채팅 지식 검색과 같은 범위 규칙)
        WHERE k.namespace_id IN ({scope_ids_subquery("c.namespace_id")})
          AND (k.status IS NULL OR k.status = 'active') AND k.embedding IS NOT NULL
        ORDER BY k.embedding <=> c.v
        LIMIT 1
    ) best ON true"""


async def nearest(conn, items: list[tuple[int, int, str | None]], snippet_len: int) -> dict[int, dict]:
    """(키, 영역 id, 벡터 글자 '[0.1,…]') 여러 개 → 키마다 가장 가까운 사용 중 지식 하나 {knowledge_id, similarity, snippet, content}.
    한 쿼리로(창구가 키 수만큼 부르지 않게). 벡터가 없으면 similarity None. conn = 창구가 쓰는 연결(같은 트랜잭션 안에서)."""
    if not items:
        return {}
    rows = await conn.fetch(_NEAREST_SQL, [k for k, _, _ in items], [n for _, n, _ in items], [e for _, _, e in items], snippet_len)
    return {r["key"]: {"knowledge_id": r["knowledge_id"], "similarity": r["similarity"], "snippet": r["snippet"], "content": r["content"]}
            for r in rows}


async def contents(namespace: str, ids: list[int]) -> list[dict]:
    """지식 id들 → [{id, content}] — 그 영역 것만(지금 다른 영역으로 옮겨졌거나 지워진 id는 빠짐)."""
    if not ids:
        return []
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch("SELECT id, content FROM rag_knowledge WHERE namespace_id = $1 AND id = ANY($2::int[])", ns_id, ids)
    return [dict(r) for r in rows]
