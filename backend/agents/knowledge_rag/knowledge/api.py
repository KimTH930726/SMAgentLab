"""지식 코어 API — 바깥 창구(VOC 메일 등)가 지식을 쓰는 유일한 길(10/28 VOC 레이어 분리, docs/backlog.md §0-3).

막힌 일: VOC(`service/email_voc`)가 지식 검색 내부(retrieval·glossary_terms)를 직접 import하고 지식 표(rag_knowledge)에 SQL을 직접 날렸다 —
코어(검색·저장·신뢰도)를 바꾸면 VOC가 조용히 깨지고, VOC를 별도 에이전트로 떼려면 어디가 엮였는지부터 찾아야 했다.
업계(모듈러 모놀리스 · 포트와 어댑터)처럼 같은 프로젝트 안에서 경계만 긋는다: 창구는 이 모듈만 import, 코어 내부는 이 모듈 뒤로.
경계는 `tests/test_voc_boundary.py`가 정적 검사로 막는다. 별도 에이전트(HTTP)로 떼는 조건은 백로그 §0-3(두 번째 창구 · VOC 부하가 채팅에 영향 · 다른 팀 사용).
여기 함수는 옮겨 온 것 — 동작은 VOC가 쓰던 것과 같다(eval_out/voc_boundary_snapshot.py 전/후 비교).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from agents.knowledge_rag.knowledge import glossary_terms, retrieval
from agents.knowledge_rag.knowledge.retrieval import RetrievalResult
from core.database import get_conn, resolve_namespace_id, scope_ids_subquery

logger = logging.getLogger(__name__)

__all__ = ["RetrievalResult", "Search", "search", "ChatSearch", "chat_context", "min_score", "nearest", "contents"]


@dataclass
class Search:
    results: list[RetrievalResult]
    context: str
    mapped_term: str | None
    w_vector: float     # 이번 검색에 쓴 가중치 — 창구가 "가중합 원점수"(base_weight 뺀 관련도)를 직접 계산할 때
    w_keyword: float


async def search(namespace: str, query_text: str, query_vec: list[float]) -> Search:
    """지식 검색 — 채팅 지식 검색과 같은 설정(용어 보강 방식 · 기본 가중치 · top_k)으로. 정책·참조데이터는 안 본다(지식만).
    VOC는 11/05부터 chat_context(채팅 경로 그대로)를 쓴다 — 이건 지식만 보는 가벼운 길(전/후 측정의 "전" 경로)로 남긴다."""
    _, mapped_term, enriched = await glossary_terms.resolve_query_terms(namespace, query_text, query_vec)
    d = retrieval.get_search_defaults()
    w_vector, w_keyword = d["default_w_vector"], d["default_w_keyword"]
    results = await retrieval.search_knowledge(namespace, query_vec, enriched, w_vector, w_keyword, int(d["default_top_k"]))
    return Search(results=results, context=retrieval.build_context(results), mapped_term=mapped_term, w_vector=w_vector, w_keyword=w_keyword)


# 정책 근거가 "있다" — 채팅 근거 없음 판정(agent.should_abstain)의 실측 값(10/06: 서술 0.50 · 파라미터 0.05에서 골든 거짓 거절 0)
_POLICY_MIN_NARRATIVE = 0.50
_POLICY_MIN_PARAM_RANK = 0.05


@dataclass
class ChatSearch:
    """채팅과 같은 검색 경로(agent.build_chat_context) 결과 중 창구가 쓰는 것 — 정책 객체는 dict로 풀어 창구가 정책 모듈을 몰라도 되게."""
    results: list[RetrievalResult]
    context: str                 # 채팅과 같은 RRF 문맥(지식 + 정책 + 참조데이터 + 용어 설명)
    mapped_term: str | None
    w_vector: float
    w_keyword: float
    policy_refs: list[dict]      # [{name, snippet, score, collection}] 하한을 넘은 정책 근거만 — 파라미터(정확 일치) 먼저, 서술 다음, 각자 검색 순
    policy_found: bool           # 정책 근거가 하한을 넘었나(_POLICY_MIN_*)


async def chat_context(namespace: str, query_text: str, query_vec: list[float], *, knowledge_scope: str | None,
                       policy_collections: list[str] | None = None) -> ChatSearch:
    """채팅과 같은 검색·문맥 조립(11/05 — VOC도 채팅 파이프라인 그대로, 나중엔 이 기능을 API로 제공).
    knowledge_scope: 지식 검색 범위(core.database.KNOWLEDGE_SCOPES). policy_collections: 정책서 범위를 이 컬렉션 이름들로 덮어쓰기
    (None = 채팅 규칙 — 시스템이면 받침 연결된 정책 컬렉션). 없는 이름은 빠지고 경고 로그(오타로 정책이 통째로 빠지는 걸 보이게)."""
    from agents.knowledge_rag import agent   # 채팅 에이전트(무거운 의존)는 쓸 때만
    policy_ns_ids = None
    if policy_collections is not None:
        async with get_conn() as conn:
            rows = await conn.fetch("SELECT id, name FROM ops_namespace WHERE name = ANY($1::text[]) ORDER BY array_position($1::text[], name)",
                                    policy_collections)
        policy_ns_ids = [r["id"] for r in rows]
        missing = [n for n in policy_collections if n not in {r["name"] for r in rows}]
        if missing:
            logger.warning("정책서 범위 덮어쓰기에 없는 컬렉션 이름 — 빼고 진행: %s", missing)
    d = retrieval.get_search_defaults()
    w_vector, w_keyword = d["default_w_vector"], d["default_w_keyword"]
    cc = await agent.build_chat_context(namespace, query_text, query_vec, top_k=int(d["default_top_k"]), w_vector=w_vector,
                                        w_keyword=w_keyword, knowledge_scope=knowledge_scope, policy_ns_ids=policy_ns_ids)
    pol = cc.policy_result
    # 근거로 내보내는 건 하한을 넘은 것만(리뷰 11/05 M1) — 서술은 벡터 검색이라 거의 늘 10개가 오고, 약한 서술이 앞을 차지하면
    # 판정 근거였던 파라미터가 잘려 안 보였다. 파라미터(정확 일치)를 먼저
    refs = [{"name": p.policy_name, "snippet": f"{p.param_name} = {p.value or ''}{p.unit or ''}" + (f" ({p.condition})" if p.condition else ""),
             "score": p.score, "collection": p.part} for p in pol.params if p.score > _POLICY_MIN_PARAM_RANK]
    refs += [{"name": n.policy_name, "snippet": n.chunk_text, "score": n.score, "collection": n.part}
             for n in pol.narratives if n.score >= _POLICY_MIN_NARRATIVE]
    found = bool(refs)
    return ChatSearch(results=cc.results, context=cc.llm_context, mapped_term=cc.mapped_term, w_vector=w_vector, w_keyword=w_keyword,
                      policy_refs=refs, policy_found=found)


def min_score() -> float:
    """지식을 근거로 쓰는 점수 하한(채팅 채택 기준 knowledge_min_score — final_score와 비교)."""
    return retrieval.get_thresholds()["knowledge_min_score"]


# 시스템 컬렉션 전체(with_systems) — VOC는 메일함 라우팅 영역과 실제 문의 시스템이 달라 찾은 지식이 다른 시스템에 있다(11/05)
_SYSTEM_IDS_SQL = "SELECT s.id FROM ops_namespace s WHERE s.kind = 'domain' AND s.area = 'system'"

# 벡터 글자 → vector 변환은 키마다 한 번(MATERIALIZED) — LATERAL 안에서 c.emb::vector로 쓰면 지식 행마다 12KB 글자를 다시 파싱해
# 10배 느렸다(10/28 리뷰 실측 EXPLAIN: 5.5ms → 53ms, 고친 뒤 7.5ms). MATERIALIZED가 없으면 planner가 펼쳐 같은 문제가 난다.
_NEAREST_SQL_TMPL = f"""
    WITH c AS MATERIALIZED (
        SELECT u.key, u.namespace_id, u.emb::vector AS v FROM unnest($1::bigint[], $2::int[], $3::text[]) AS u(key, namespace_id, emb)
    )
    SELECT c.key, best.knowledge_id, best.similarity, best.snippet, best.content
    FROM c
    LEFT JOIN LATERAL (
        SELECT k.id AS knowledge_id, 1 - (k.embedding <=> c.v) AS similarity, LEFT(k.content, $4) AS snippet, k.content
        FROM rag_knowledge k
        -- 영역 + 직계 하위 영역(채팅 지식 검색과 같은 범위 규칙)
        WHERE (k.namespace_id IN ({scope_ids_subquery("c.namespace_id")})/*systems*/)
          AND (k.status IS NULL OR k.status = 'active') AND k.embedding IS NOT NULL
        ORDER BY k.embedding <=> c.v
        LIMIT 1
    ) best ON true"""
# 자리표시는 글자 치환 — .format은 범위 조각에 중괄호가 생기면 import 순간 깨진다(리뷰 L7)
_NEAREST_SQL = _NEAREST_SQL_TMPL.replace("/*systems*/", "")
_NEAREST_WITH_SYSTEMS_SQL = _NEAREST_SQL_TMPL.replace("/*systems*/", f" OR k.namespace_id IN ({_SYSTEM_IDS_SQL})")


async def nearest(conn, items: list[tuple[int, int, str | None]], snippet_len: int, *, with_systems: bool = False) -> dict[int, dict]:
    """(키, 영역 id, 벡터 글자 '[0.1,…]') 여러 개 → 키마다 가장 가까운 사용 중 지식 하나 {knowledge_id, similarity, snippet, content}.
    한 쿼리로(창구가 키 수만큼 부르지 않게). 벡터가 없으면 similarity None. conn = 창구가 쓰는 연결(같은 트랜잭션 안에서).
    with_systems: 영역에 더해 시스템 컬렉션 전체에서 — 가장 가까운 한 조각은 search(auto_system)가 고르는 1위 시스템 안에 있다."""
    if not items:
        return {}
    rows = await conn.fetch(_NEAREST_WITH_SYSTEMS_SQL if with_systems else _NEAREST_SQL, [k for k, _, _ in items], [n for _, n, _ in items], [e for _, _, e in items], snippet_len)
    return {r["key"]: {"knowledge_id": r["knowledge_id"], "similarity": r["similarity"], "snippet": r["snippet"], "content": r["content"]}
            for r in rows}


async def contents(namespace: str, ids: list[int], *, with_systems: bool = False) -> list[dict]:
    """지식 id들 → [{id, content}] — 그 영역 것만(지금 다른 영역으로 옮겨졌거나 지워진 id는 빠짐). with_systems: 시스템 컬렉션 것도."""
    if not ids:
        return []
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        systems = f" OR namespace_id IN ({_SYSTEM_IDS_SQL})" if with_systems else ""
        rows = await conn.fetch(f"SELECT id, content FROM rag_knowledge WHERE (namespace_id = $1{systems}) AND id = ANY($2::int[])", ns_id, ids)
    return [dict(r) for r in rows]
