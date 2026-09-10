"""정책 데이터 검색 — docs/policy-doc-pipeline-plan.md §4 쿼리 유형 4종 중
파라미터 조회(RDB 정확 조회)와 서술 Q&A(벡터 검색)를 하나의 엔드포인트로 묶는다.

Track 2(저장 전략 실험실, §4) 실행 결과(2026-09-04) — 벡터 폴백 보완 후 하이브리드 스키마(B)가
지식-only(A) 대비 4개 질의 유형 전부에서 우세로 확정됐다(§4-3/§4-4). 이 결과로 `agent.py`
채팅 흐름 편입을 막던 이유가 해소돼, `build_policy_context()`를 통해 편입 1단계(텍스트
컨텍스트만 합치고 인용 카드 UI는 아직 안 건드림)가 시작됐다 — `/api/policy/search` 전용
엔드포인트는 그대로 유지(디버깅/직접 조회용).

편입 2단계(2026-09-06)로 인용 카드 UI(`build_policy_citations()`)가 추가됐는데, `search_policy()`
가 param/narrative 각각 top_k(기본 5)씩 총 최대 10건을 반환해 채팅 화면에 근거가 너무 많이
보인다는 후속 피드백이 나왔다("원문 정책 1건만 보여달라" + "10개를 참조해서 답변 만든 거냐" —
실제로 그랬다). 처음엔 검색 직후 벡터 점수 1위 하나만 골라 LLM 컨텍스트/화면 카드 양쪽에
쓰려 했으나(`select_top_policy_hit`, 바로 폐기) 실측에서 문제가 드러남 — "장바구니 최대
개수" 질문에서 벡터 점수 1위 narrative는 실제로 무관한 "배송지"였고, LLM 컨텍스트를 그거
하나로 줄이자 정답이었던 "장바구니 최대 보관 수량" 항목이 통째로 빠져 "관련 지식을 찾지
못했습니다"로 답변 자체가 실패했다. 그래서 **LLM 컨텍스트는 원래대로 여러 후보를 유지해
재현율을 지키고(`build_policy_context()`는 안 바뀜), 화면에 보여줄 근거 1건은 답변이 생성된
"뒤에" `select_cited_hit()`으로 역추적**한다 — 실제 답변 텍스트와 원문(raw_body)의 토큰
겹침이 가장 큰 후보를 고른다(2026-09-07). `agent.py`는 이 때문에 정책 인용을 LLM 스트리밍이
끝난 뒤 두 번째 `meta` SSE 이벤트로 늦게 내려보낸다.

v1엔 검토/승인 UI가 없어(브리프 §2-4) 데이터가 전부 status='pending_review'로 쌓인다 —
검색은 '검토 대기' 상태도 포함한다(안 그러면 아무것도 안 나옴), 대신 결과에 status를 노출해
호출측이 "미검토" 표시를 할 수 있게 한다.
"""
from __future__ import annotations

import re
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
    raw_body: str = ""  # 원문 전체 — 채팅 인용 카드의 "근거" 표시용(2026-09-06)
    score: float = 0.0  # ts_rank 관련도 — 예전엔 i.id DESC(최신순)로만 정렬해 무의미했음,
    # select_top_policy_hit()에서 narrative가 없을 때 최선의 param 1건을 고르는 데 씀(2026-09-07)


@dataclass
class NarrativeHit:
    item_id: int
    logical_id: int
    policy_name: str
    category_path: list[str]
    status: str
    chunk_text: str
    score: float
    raw_body: str = ""  # 원문 전체 — 채팅 인용 카드의 "근거" 표시용(2026-09-06)


@dataclass
class PolicySearchResult:
    params: list[ParamHit] = field(default_factory=list)
    narratives: list[NarrativeHit] = field(default_factory=list)


async def search_policy(
    namespace: str, query: str, category: Optional[str] = None, top_k: int = 10,
    query_vec: Optional[list[float]] = None,
) -> PolicySearchResult:
    """파라미터(RDB ILIKE)와 서술(벡터) 두 갈래로 동시에 찾아 합쳐서 반환한다 — 어느
    쪽이 정답인지는 질문 유형에 달려있어(§4) 미리 하나로 안 좁히고 둘 다 보여준다.

    `query_vec`을 넘기면 임베딩을 다시 계산하지 않는다 — `agent.py`가 이미 같은 질문을
    임베딩해뒀는데 여기서 또 계산하면 채팅 메인 경로에서 매 턴 중복 임베딩이 생긴다
    (2026-09-04, 편입 1단계에서 발견해 방어). `/api/policy/search` 전용 엔드포인트처럼
    호출측에 미리 계산된 벡터가 없으면 생략해도 되고, 그러면 기존처럼 내부에서 계산한다."""
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
        #
        # 조사/어미 제거(2026-09-10, v2.71): to_tsvector('simple', ...)엔 한국어 형태소 분석이
        # 없어 "담을"/"담기"처럼 같은 어간도 조사·어미가 다르면 lexeme이 갈려 매칭이 실패한다
        # (89문항 골든셋 실측 — RDB 자기 전문 분야인 param 질의에서조차 벡터가 RDB를 앞섬).
        # 규칙 기반 조사/어미 제거를 검증한 결과(backend/scripts/bench_suffix_stripping.py)
        # 하이브리드 전체 hit@10 75.3%→79.8%로 확인돼, 쿼리/콘텐츠 양쪽에 `policy_strip_ko()`
        # (init/06-policy-strip-ko.sql)를 적용한다 — 저장 컬럼 추가 없이 조회 시점 변환이라
        # policy_param/policy_item 데이터는 그대로 둔다(건수가 작아 성능 영향 없음).
        category_clause = "AND $4 = ANY(i.category_path)" if category else ""
        param_args = [ns_id, query, top_k] + ([category] if category else [])
        param_rows = await conn.fetch(
            f"""
            SELECT i.id AS item_id, i.logical_id, i.policy_name, i.category_path, i.status, i.raw_body,
                   p.name AS param_name, p.condition, p.value, p.unit,
                   ts_rank(to_tsvector('simple', policy_strip_ko(p.name || ' ' || COALESCE(p.condition, '') || ' ' || i.policy_name)), q.tsq) AS rank
            FROM policy_param p
            JOIN policy_item i ON i.id = p.policy_item_id
            CROSS JOIN LATERAL (
                SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS tsq
                FROM (SELECT DISTINCT lexeme FROM unnest(to_tsvector('simple', policy_strip_ko($2)))) t
                WHERE lexeme IS NOT NULL
            ) q
            WHERE i.namespace_id = $1 AND i.status != 'deprecated'
              AND to_tsvector('simple', policy_strip_ko(p.name || ' ' || COALESCE(p.condition, '') || ' ' || i.policy_name)) @@ q.tsq
              {category_clause}
            ORDER BY rank DESC
            LIMIT $3
            """,
            *param_args,
        )

        vec = query_vec if query_vec is not None else await embedding_service.embed(query)
        chunk_args = [ns_id, str(vec), top_k] + ([category] if category else [])
        category_clause2 = "AND $4 = ANY(i.category_path)" if category else ""
        chunk_rows = await conn.fetch(
            f"""
            SELECT i.id AS item_id, i.logical_id, i.policy_name, i.category_path, i.status, i.raw_body,
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
            raw_body=r["raw_body"], score=float(r["rank"]),
        ) for r in param_rows],
        narratives=[NarrativeHit(
            item_id=r["item_id"], logical_id=r["logical_id"], policy_name=r["policy_name"],
            category_path=list(r["category_path"] or []), status=r["status"],
            chunk_text=r["chunk_text"], score=float(r["score"]), raw_body=r["raw_body"],
        ) for r in chunk_rows],
    )


async def has_policy_data(namespace: str) -> bool:
    """이 네임스페이스에 정책 데이터가 있는지 가볍게 확인한다. `agent.py`(모든 네임스페이스가
    공유하는 채팅 메인 경로)가 정책 데이터 없는 네임스페이스에서까지 매 턴 `search_policy()`를
    부르는 낭비를 피하려고 둔 게이트 — 인덱스 탄 EXISTS 한 번뿐이라 비용은 작다."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return False
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM policy_item WHERE namespace_id = $1 AND status != 'deprecated')",
            ns_id,
        )
        return bool(exists)


_CITATION_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")


def _token_overlap_score(candidate_text: str, answer_text: str) -> int:
    """candidate_text에서 뽑은 토큰(2글자 이상 한글/영문/숫자 덩어리) 중 answer_text에
    그대로 등장하는 개수. 정교한 유사도 대신 단순 포함 카운트를 쓰는 이유: 정책 답변은
    보통 원문의 숫자·항목명을 거의 그대로 옮겨 적어서(예: "20개", "장바구니 최대 보관
    수량") 이 정도로도 실제 근거를 충분히 변별할 수 있었다(실측)."""
    if not candidate_text or not answer_text:
        return 0
    tokens = set(_CITATION_TOKEN_RE.findall(candidate_text))
    return sum(1 for t in tokens if t in answer_text)


def select_cited_hit(result: PolicySearchResult, answer_text: str) -> PolicySearchResult:
    """검색 직후가 아니라 LLM이 실제로 생성한 답변(answer_text)이 나온 뒤에, 그 안에 등장하는
    내용과 원문(raw_body)이 가장 많이 겹치는 후보 1건을 역추적해서 "정책 근거" 카드로 쓴다
    (2026-09-07, 사용자 피드백: "근거가 너무 많이 보인다, 원문 정책 1건만 보여달라" + "10개를
    참조해서 답변 만든 거냐").

    검색 직후 벡터 점수만으로 미리 1건을 골라 LLM 컨텍스트까지 그걸로 줄여봤더니(구버전
    `select_top_policy_hit`) 실제로 무관한 후보가 뽑히고, 심지어 그 때문에 정답이 컨텍스트에서
    아예 빠져 "관련 지식을 찾지 못했습니다"로 답변이 실패하는 것까지 실측으로 확인됐다. 그래서
    LLM 컨텍스트(`build_policy_context()`)는 원래대로 여러 후보를 유지해 재현율을 지키고,
    화면에 보여줄 근거만 답변 생성 후 이 함수로 골라낸다 — 답변과 겹치는 게 하나도 없으면
    (LLM이 정책 데이터를 실제로 안 썼거나 "모른다"류 답변) 억지로 아무거나 보여주지 않고
    빈 결과를 반환한다."""
    candidates: list[tuple[int, str, object]] = []
    for p in result.params:
        candidates.append((_token_overlap_score(p.raw_body, answer_text), "param", p))
    for n in result.narratives:
        candidates.append((_token_overlap_score(n.raw_body or n.chunk_text, answer_text), "narrative", n))
    if not candidates:
        return PolicySearchResult()
    best_score, kind, hit = max(candidates, key=lambda c: c[0])
    if best_score <= 0:
        return PolicySearchResult()
    return PolicySearchResult(params=[hit]) if kind == "param" else PolicySearchResult(narratives=[hit])


def build_policy_context(result: PolicySearchResult) -> str:
    """search_policy() 결과를 채팅 LLM 컨텍스트용 텍스트로 변환 — `agent.py`의 `doc_context`에
    추가 섹션으로 이어붙인다(편입 1단계, 2026-09-04).

    retrieval.py의 build_context()와 다른 점: param은 RDB 정확 매칭이라 점수가 없어(있다/없다
    뿐) "정확 일치"로만 표시하고, narrative만 벡터 점수 기반 신뢰도 라벨을 붙인다."""
    if not result.params and not result.narratives:
        return ""

    parts = []
    for p in result.params:
        value_str = f"{p.value}{p.unit or ''}" if p.value else "값 없음"
        condition_str = f" ({p.condition})" if p.condition else ""
        parts.append(f"[정책 파라미터 · 정확 일치: {p.policy_name}{condition_str}] {p.param_name} = {value_str}")
    for n in result.narratives:
        confidence = "높음" if n.score >= 0.6 else "보통" if n.score >= 0.4 else "낮음"
        parts.append(f"[정책 서술: {n.policy_name}] (신뢰도: {confidence})\n{n.chunk_text}")

    return "--- 정책 데이터 ---\n" + "\n\n".join(parts)


def build_policy_citations(result: PolicySearchResult) -> list[dict]:
    """search_policy() 결과를 채팅 화면의 "정책 근거" 카드용 데이터로 변환 — 편입 2단계
    (2026-09-06, 사용자 피드백: "정책에서 온 답인지 기준정보에서 온 답인지 알기 어렵다,
    원문도 근거로 보여달라"). `build_policy_context()`(LLM 프롬프트용 텍스트)와 별개로,
    화면에 그대로 렌더링할 수 있는 구조화 데이터를 만든다.

    기존 `results`(rag_knowledge 인용 카드) 배열엔 안 섞는다 — `FeedbackSection`이
    `results[0].id`를 "이 답변이 참조한 rag_knowledge 항목"으로 써서(피드백→지식수정 연결),
    policy_param/policy_chunk의 id를 그 배열에 섞으면 다른 테이블의 id가 rag_knowledge id로
    오인될 위험이 있다. 그래서 SSE meta 이벤트에 `policy_citations`라는 별도 필드로 얹는다."""
    citations = []
    for p in result.params:
        value_str = f"{p.value}{p.unit or ''}" if p.value else "값 없음"
        detail = f"{p.param_name}" + (f" ({p.condition})" if p.condition else "") + f" = {value_str}"
        citations.append({
            "kind": "param", "policy_name": p.policy_name, "category_path": p.category_path,
            "detail": detail, "raw_body": p.raw_body,
        })
    for n in result.narratives:
        citations.append({
            "kind": "narrative", "policy_name": n.policy_name, "category_path": n.category_path,
            "detail": n.chunk_text, "raw_body": n.raw_body,
        })
    return citations
