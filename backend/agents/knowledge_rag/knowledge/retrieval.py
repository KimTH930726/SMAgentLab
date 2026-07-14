"""2단계 하이브리드 검색 파이프라인."""
from __future__ import annotations

import math
import datetime
from dataclasses import dataclass, field
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from core.config import settings
from shared.embedding import embedding_service


# ── Runtime threshold overrides ──────────────────────────────────────────────
_runtime_thresholds: dict[str, float] = {}


def get_thresholds() -> dict[str, float]:
    return {
        "glossary_min_similarity": _runtime_thresholds.get("glossary_min_similarity", settings.glossary_min_similarity),
        "fewshot_min_similarity": _runtime_thresholds.get("fewshot_min_similarity", settings.fewshot_min_similarity),
        "knowledge_min_score": _runtime_thresholds.get("knowledge_min_score", settings.knowledge_min_score),
        "knowledge_high_score": _runtime_thresholds.get("knowledge_high_score", settings.knowledge_high_score),
        "knowledge_mid_score": _runtime_thresholds.get("knowledge_mid_score", settings.knowledge_mid_score),
    }


def set_thresholds(updates: dict[str, float]) -> dict[str, float]:
    for k, v in updates.items():
        if k in ("glossary_min_similarity", "fewshot_min_similarity", "knowledge_min_score", "knowledge_high_score", "knowledge_mid_score"):
            _runtime_thresholds[k] = v
    return get_thresholds()


# ── Runtime search defaults overrides ────────────────────────────────────────
_runtime_search_defaults: dict[str, float] = {}

_SEARCH_DEFAULT_KEYS = ("default_top_k", "default_w_vector", "default_w_keyword")


def get_search_defaults() -> dict[str, float]:
    return {
        "default_top_k": _runtime_search_defaults.get("default_top_k", settings.default_top_k),
        "default_w_vector": _runtime_search_defaults.get("default_w_vector", settings.default_w_vector),
        "default_w_keyword": _runtime_search_defaults.get("default_w_keyword", settings.default_w_keyword),
    }


def set_search_defaults(updates: dict[str, float]) -> dict[str, float]:
    for k, v in updates.items():
        if k in _SEARCH_DEFAULT_KEYS:
            _runtime_search_defaults[k] = v
    return get_search_defaults()


@dataclass
class GlossaryMatch:
    term: str
    description: str
    similarity: float


@dataclass
class RetrievalResult:
    id: int
    namespace: str
    container_name: Optional[str]
    target_tables: Optional[list[str]]
    content: str
    query_template: Optional[str]
    base_weight: float
    final_score: float
    v_score: float = field(default=0.0)
    k_score: float = field(default=0.0)
    category: Optional[str] = field(default=None)


async def map_glossary_term(
    namespace: str, query_vec: list[float]
) -> Optional[GlossaryMatch]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return None
        row = await conn.fetchrow(
            """
            SELECT term, description,
                   1 - (embedding <=> $2::vector) AS similarity
            FROM rag_glossary
            WHERE namespace_id = $1
              AND embedding IS NOT NULL
            ORDER BY embedding <=> $2::vector
            LIMIT 1
            """,
            ns_id, str(query_vec),
        )
    if row and float(row["similarity"]) >= get_thresholds()["glossary_min_similarity"]:
        return GlossaryMatch(
            term=row["term"], description=row["description"],
            similarity=float(row["similarity"]),
        )
    return None


_VECTOR_CANDIDATE_LIMIT = 300  # top_k/reranker_candidates(기본 20)보다 넉넉한 후보 풀 —
# 벡터 CTE를 ORDER BY + LIMIT로 유계화해 정렬 비용을 줄인다(테이블이 커지면 HNSW
# 인덱스도 이 형태에서만 자동으로 쓰이기 시작함).


async def search_knowledge(
    namespace: str, query_vec: list[float], enriched_query: str,
    w_vector: float = 0.7, w_keyword: float = 0.3, top_k: int = 5,
    categories: Optional[list[str]] = None,
) -> list[RetrievalResult]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        category_filter = "AND k.category = ANY($8)" if categories else ""
        params = [str(query_vec), ns_id, enriched_query, w_vector, w_keyword, top_k, _VECTOR_CANDIDATE_LIMIT]
        if categories:
            params.append(categories)
        rows = await conn.fetch(
            f"""
            WITH vector_scores AS (
                SELECT id, 1 - (embedding <=> $1::vector) AS v_score
                FROM rag_knowledge WHERE namespace_id = $2 AND embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT $7
            ),
            keyword_scores AS (
                SELECT k.id, ts_rank(to_tsvector('simple', k.content), q.tsq) AS k_score
                FROM rag_knowledge k
                CROSS JOIN LATERAL (
                    SELECT to_tsquery('simple', string_agg(lexeme, ' | ')) AS tsq
                    FROM (SELECT DISTINCT lexeme FROM unnest(to_tsvector('simple', $3))) t
                    WHERE lexeme IS NOT NULL
                ) q
                WHERE k.namespace_id = $2
                  AND to_tsvector('simple', k.content) @@ q.tsq
            )
            SELECT k.id, n.name AS namespace, k.container_name, k.target_tables,
                   k.content, k.query_template, k.base_weight, k.category,
                   COALESCE(vs.v_score, 0.0) AS v_score,
                   COALESCE(ks.k_score, 0.0) AS k_score,
                   ($4 * COALESCE(vs.v_score, 0.0) + $5 * COALESCE(ks.k_score, 0.0))
                     * (1.0 + k.base_weight) AS final_score,
                   k.updated_at
            FROM rag_knowledge k
            JOIN ops_namespace n ON k.namespace_id = n.id
            LEFT JOIN vector_scores vs ON k.id = vs.id
            LEFT JOIN keyword_scores ks ON k.id = ks.id
            WHERE k.namespace_id = $2
              AND (vs.v_score IS NOT NULL OR ks.k_score IS NOT NULL)
              {category_filter}
            ORDER BY final_score DESC LIMIT $6
            """,
            *params,
        )

    halflife = settings.freshness_decay_halflife_days
    now = datetime.datetime.now(tz=datetime.timezone.utc)

    results = []
    for r in rows:
        score = float(r["final_score"])
        if halflife > 0 and r["updated_at"]:
            updated = r["updated_at"]
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=datetime.timezone.utc)
            age_days = (now - updated).total_seconds() / 86400.0
            # 반감기 halflife_days 기준 지수 decay; 최소 50%
            decay = max(0.5, math.pow(0.5, age_days / halflife))
            score *= decay
        results.append(RetrievalResult(
            id=r["id"], namespace=r["namespace"],
            container_name=r["container_name"],
            target_tables=list(r["target_tables"]) if r["target_tables"] else [],
            content=r["content"], query_template=r["query_template"],
            base_weight=r["base_weight"],
            v_score=float(r["v_score"]), k_score=float(r["k_score"]),
            final_score=score, category=r["category"],
        ))
    return results


async def fetch_fewshots(
    namespace: str, query_vec: list[float], limit: int = 2,
    *, min_similarity: float | None = None,
) -> list[dict]:
    min_sim = min_similarity if min_similarity is not None else get_thresholds()["fewshot_min_similarity"]
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT question, answer,
                   1 - (embedding <=> $2::vector) AS similarity
            FROM rag_fewshot
            WHERE namespace_id = $1
              AND (status IS NULL OR status = 'active')
              AND 1 - (embedding <=> $2::vector) >= $4
            ORDER BY embedding <=> $2::vector
            LIMIT $3
            """,
            ns_id, str(query_vec), limit, min_sim,
        )
    return [
        {"question": r["question"], "answer": r["answer"], "similarity": float(r["similarity"])}
        for r in rows
    ]


def build_fewshot_section(fewshots: list[dict]) -> str:
    if not fewshots:
        return ""
    examples = "\n\n".join(f"Q: {fs['question']}\nA: {fs['answer']}" for fs in fewshots)
    return f"[과거 유사 질문 답변 사례 — 참고용]\n{examples}"


def build_context(results: list[RetrievalResult]) -> str:
    th = get_thresholds()
    relevant = [r for r in results if r.final_score >= th["knowledge_min_score"]]
    if not relevant:
        return ""

    parts = []
    for i, r in enumerate(relevant, 1):
        confidence = "높음" if r.final_score >= th["knowledge_high_score"] else "보통" if r.final_score >= th["knowledge_mid_score"] else "낮음"
        part = [f"--- 문서 {i} (점수: {r.final_score:.4f}, 신뢰도: {confidence}) ---"]
        if r.container_name:
            part.append(f"컨테이너: {r.container_name}")
        if r.target_tables:
            part.append(f"관련 테이블: {', '.join(r.target_tables)}")
        part.append(f"내용:\n{r.content}")
        if r.query_template:
            part.append(f"SQL:\n{r.query_template}")
        parts.append("\n".join(part))

    return "\n\n".join(parts)
