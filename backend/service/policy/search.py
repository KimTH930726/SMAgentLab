"""정책 데이터 검색 — docs/policy-doc-pipeline-plan.md §4 쿼리 유형 4종 중
파라미터 조회(RDB 정확 조회)와 서술 Q&A(벡터 검색)를 하나의 엔드포인트로 묶는다.

Track 2(저장 전략 실험실, §4)가 아직 실행되지 않아 "A: rag_knowledge에 얹기만" vs
"B: 이 하이브리드 스키마"의 우열이 숫자로 확정되지 않았다 — 그래서 기존 채팅 파이프라인
(`retrieval.py`/`agent.py`)은 건드리지 않고 이 모듈을 별도 엔드포인트로 둔다. 나중에 Track 2
결과로 B가 이긴다고 확정되면 그때 채팅 라우팅에 편입하는 게 안전하다(모든 네임스페이스가
공유하는 핵심 검색 경로를 검증 안 된 상태로 먼저 바꾸는 위험을 피함).

v1엔 검토/승인 UI가 없어(브리프 §2-4) 데이터가 전부 status='pending_review'로 쌓인다 —
검색은 '검토 대기' 상태도 포함한다(안 그러면 아무것도 안 나옴), 대신 결과에 status를 노출해
호출측이 "미검토" 표시를 할 수 있게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service


@dataclass
class ParamHit:
    item_id: int
    logical_id: int
    policy_name: str
    category_path: list[str]
    status: str
    param_name: str
    condition: Optional[str]
    value: Optional[str]
    unit: Optional[str]


@dataclass
class NarrativeHit:
    item_id: int
    logical_id: int
    policy_name: str
    category_path: list[str]
    status: str
    chunk_text: str
    score: float


@dataclass
class PolicySearchResult:
    params: list[ParamHit] = field(default_factory=list)
    narratives: list[NarrativeHit] = field(default_factory=list)


async def search_policy(
    namespace: str, query: str, category: Optional[str] = None, top_k: int = 10,
) -> PolicySearchResult:
    """파라미터(RDB ILIKE)와 서술(벡터) 두 갈래로 동시에 찾아 합쳐서 반환한다 — 어느
    쪽이 정답인지는 질문 유형에 달려있어(§4) 미리 하나로 안 좁히고 둘 다 보여준다."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")

        # 실측(2026-09-03): ILIKE 부분문자열 매칭은 "장바구니 개수" 질의가 실제 파라미터명
        # "장바구니 최대 메뉴 개수"(사이에 다른 단어가 낀 경우)를 못 찾는 게 실 API 테스트로
        # 확인됨 — 자연어 질의엔 너무 엄격하다. retrieval.py의 키워드 검색과 동일한
        # to_tsvector/to_tsquery 패턴(lexeme 단위 매칭, quote_literal로 특수문자 안전 처리)으로
        # 교체한다. LATERAL 서브쿼리라 lexeme이 하나도 안 남는 질의(공백뿐 등)에도 안전하게
        # tsq=NULL → 매칭 0건으로 처리됨(에러 없음).
        category_clause = "AND $4 = ANY(i.category_path)" if category else ""
        param_args = [ns_id, query, top_k] + ([category] if category else [])
        param_rows = await conn.fetch(
            f"""
            SELECT i.id AS item_id, i.logical_id, i.policy_name, i.category_path, i.status,
                   p.name AS param_name, p.condition, p.value, p.unit
            FROM policy_param p
            JOIN policy_item i ON i.id = p.policy_item_id
            CROSS JOIN LATERAL (
                SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS tsq
                FROM (SELECT DISTINCT lexeme FROM unnest(to_tsvector('simple', $2))) t
                WHERE lexeme IS NOT NULL
            ) q
            WHERE i.namespace_id = $1 AND i.status != 'deprecated'
              AND to_tsvector('simple', p.name || ' ' || COALESCE(p.condition, '') || ' ' || i.policy_name) @@ q.tsq
              {category_clause}
            ORDER BY i.id DESC
            LIMIT $3
            """,
            *param_args,
        )

        vec = await embedding_service.embed(query)
        chunk_args = [ns_id, str(vec), top_k] + ([category] if category else [])
        category_clause2 = "AND $4 = ANY(i.category_path)" if category else ""
        chunk_rows = await conn.fetch(
            f"""
            SELECT i.id AS item_id, i.logical_id, i.policy_name, i.category_path, i.status,
                   c.chunk_text, 1 - (c.embedding <=> $2::vector) AS score
            FROM policy_chunk c
            JOIN policy_item i ON i.id = c.policy_item_id
            WHERE i.namespace_id = $1 AND i.status != 'deprecated'
              {category_clause2}
            ORDER BY c.embedding <=> $2::vector
            LIMIT $3
            """,
            *chunk_args,
        )

    return PolicySearchResult(
        params=[ParamHit(
            item_id=r["item_id"], logical_id=r["logical_id"], policy_name=r["policy_name"],
            category_path=list(r["category_path"] or []), status=r["status"],
            param_name=r["param_name"], condition=r["condition"], value=r["value"], unit=r["unit"],
        ) for r in param_rows],
        narratives=[NarrativeHit(
            item_id=r["item_id"], logical_id=r["logical_id"], policy_name=r["policy_name"],
            category_path=list(r["category_path"] or []), status=r["status"],
            chunk_text=r["chunk_text"], score=float(r["score"]),
        ) for r in chunk_rows],
    )
