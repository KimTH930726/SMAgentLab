"""2단계 하이브리드 검색 파이프라인."""
from __future__ import annotations

import math
import datetime
import logging
from dataclasses import dataclass, field
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from core.config import settings
from shared.embedding import embedding_service

logger = logging.getLogger(__name__)

# ── Runtime threshold overrides ──────────────────────────────────────────────
_runtime_thresholds: dict[str, float] = {}


_THRESHOLD_KEYS = (
    "glossary_min_similarity",
    "knowledge_min_score", "knowledge_high_score", "knowledge_mid_score",
    "duplicate_min_similarity",
)


def get_thresholds() -> dict[str, float]:
    return {k: _runtime_thresholds.get(k, getattr(settings, k)) for k in _THRESHOLD_KEYS}


def set_thresholds(updates: dict[str, float]) -> dict[str, float]:
    for k, v in updates.items():
        if k in _THRESHOLD_KEYS:
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


# ── 관리자 설정 영속화 (2026-09-28) ───────────────────────────────────────────
# 위 두 오버라이드는 원래 메모리에만 있어 재시작·재배포마다 조용히 기본값으로 돌아갔다(관리자
# 화면에선 저장된 것처럼 보임). VOC 폴링 설정·시맨틱 캐시 설정과 같은 ops_system_config에
# 접두사를 붙여 저장하고, 기동 시 load_runtime_overrides_from_db()로 되살린다.
_CONFIG_PREFIX = "retrieval_"


async def persist_runtime_overrides(updates: dict[str, float]) -> None:
    """관리자가 바꾼 임계치/검색 기본값을 DB에 저장. 실패하면 예외를 그대로 올린다 —
    호출부는 저장이 성공한 뒤에만 메모리 값을 바꿔야 "저장된 줄 알았는데 재시작하니 사라짐"이
    재발하지 않는다."""
    known = set(_THRESHOLD_KEYS) | set(_SEARCH_DEFAULT_KEYS)
    rows = [(f"{_CONFIG_PREFIX}{k}", str(v)) for k, v in updates.items() if k in known]
    if not rows:
        return
    async with get_conn() as conn:
        await conn.executemany(
            """INSERT INTO ops_system_config (key, value, updated_at) VALUES ($1, $2, NOW())
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
            rows,
        )


async def load_runtime_overrides_from_db(conn) -> None:
    """앱 시작 시 저장된 관리자 설정을 메모리 오버라이드로 복원. 실패하면 기본값으로 계속(로그만)."""
    try:
        rows = await conn.fetch(
            "SELECT key, value FROM ops_system_config WHERE key LIKE $1", f"{_CONFIG_PREFIX}%",
        )
        for row in rows:
            k = row["key"][len(_CONFIG_PREFIX):]
            if k in _THRESHOLD_KEYS:
                _runtime_thresholds[k] = float(row["value"])
            elif k == "default_top_k":
                _runtime_search_defaults[k] = int(float(row["value"]))
            elif k in _SEARCH_DEFAULT_KEYS:
                _runtime_search_defaults[k] = float(row["value"])
        if rows:
            logger.info("[Retrieval] 저장된 관리자 설정 복원: %s", {r["key"]: r["value"] for r in rows})
    except Exception:
        logger.warning("[Retrieval] 저장된 관리자 설정 복원 실패 — 기본값 사용", exc_info=True)


@dataclass
class GlossaryMatch:
    term: str
    description: str
    similarity: float


@dataclass
class RetrievalResult:
    id: int
    namespace: str
    content: str
    base_weight: float
    final_score: float
    v_score: float = field(default=0.0)
    k_score: float = field(default=0.0)
    category: Optional[str] = field(default=None)
    heading_path: list[str] = field(default_factory=list)
    source_type: Optional[str] = field(default=None)
    source_file: Optional[str] = field(default=None)
    created_at: Optional[datetime.datetime] = field(default=None)


def relevance_score(r: RetrievalResult) -> float:
    """base_weight(가중치 부스트)를 걷어낸 "진짜 관련성" 점수 (2026-09-18).

    실측(딜리버스 DB, 질문 6개): "오늘 날씨 어때?"처럼 지식베이스와 완전 무관한
    질문도 knowledge_min_score(0.35) 컷오프를 후보 20/20건 다 통과했다. 원인:
    final_score = (벡터+키워드 결합점수) × (1 + base_weight)인데 신규 지식의
    base_weight 기본값이 0이 아니라 1.0이라, 등록 시점부터 결합점수가 무조건 2배가
    돼(원점수 0.18만 넘어도 0.35 통과) 사실상 게이트가 거의 항상 열려 있었다.
    final_score를 그대로 게이트에 쓰면 "얼마나 관련 있는지"가 아니라 "피드백을
    얼마나 많이 받았는지"를 재는 꼴이라, (1+base_weight)로 나눠 원래의 결합점수로
    되돌린 뒤 그 값을 게이트/신뢰도 라벨에 쓴다. base_weight는 항상 0.0~5.0이라
    (1+base_weight)가 0이 될 일은 없다."""
    return r.final_score / (1 + r.base_weight)


def is_adopted(r: RetrievalResult, thresholds: dict[str, float]) -> bool:
    """이 결과를 실제 LLM 컨텍스트에 넣을지 최종 판단 (2026-09-18).

    `_KEYWORD_ONLY_CATEGORIES`(DB/공통코드) 문서는 벡터 점수 대신 ts_rank로만
    순위가 매겨지는데, ts_rank는 코사인 유사도(대략 0.2~0.9대)와 스케일 자체가 달라
    보통 0.01~0.1대다("DS14가 뭐야?" 실측: policy_strip_ko 조사 제거 버그를 고친 뒤에도
    k_score=0.0304). 코사인 스케일로 보정된 knowledge_min_score(0.35)를 그대로 대면
    "매칭은 됐지만 ts_rank가 작다"는 이유로 항상 걸러진다 — RRF가 풀었던 것과 똑같은
    스케일 불일치 문제가 단일 축 안에서 재현된 것. 이 카테고리의 원래 설계 의도
    (`_KEYWORD_ONLY_CATEGORIES` 주석 참고)는 "정확히 일치해야 의미 있다"는 이진 판단
    이라, ts_rank 크기가 아니라 "키워드 매칭이 됐는지"(final_score > 0 — 매칭 안 되면
    SQL의 keyword_scores CTE에 아예 없어 final_score가 0으로 떨어짐)만으로 채택 여부를
    가른다."""
    if r.category in _KEYWORD_ONLY_CATEGORIES:
        return r.final_score > 0
    return relevance_score(r) >= thresholds["knowledge_min_score"]


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


async def find_similar_active_knowledge(
    ns_id: int, embedding: list[float], *, limit: int = 3, staging_job_id: Optional[int] = None,
    replacing_confluence_pages: Optional[list[str]] = None,
) -> list[dict]:
    """같은 네임스페이스의 활성(active) 지식 중 embedding과 가장 유사한 상위 N건 조회.

    지식 등록 시 중복 의심 판정(승인 대기)에 사용 — 이미 pending_review/rejected인
    행은 후보에서 제외해 "대기 중인 것끼리" 비교되는 것을 막는다.

    `staging_job_id`: 벌크 수집 job이 자기 앞 배치와 비교할 때 넘긴다(2026-09-28). 벌크
    행은 job이 끝날 때까지 'staging'으로 숨겨져 있어(검색 노출 방지) 그냥 active만 보면
    같은 job 안의 앞 배치를 못 본다 — 예전엔 앞 배치가 즉시 active라 자연히 비교됐던
    동작을 그대로 유지하기 위해, 그 job의 비중복 스테이징 행까지 후보에 포함한다.

    `replacing_confluence_pages`: 이 등록이 교체할 컨플루언스 페이지 id들(2026-09-28). 그
    페이지의 기존 active 행은 곧 deprecated될 옛 버전이라 비교 대상에서 뺀다 — 빼지 않으면
    안 바뀐 청크가 옛 버전과 거의 같아 중복 판정(승인 대기)으로 빠지고, 뒤이어 옛 버전이
    폐기되면서 그 내용이 검색에서 통째로 사라진다.
    """
    staging_clause = ""
    replace_clause = ""
    args: list = [ns_id, str(embedding), limit]
    if staging_job_id is not None:
        args.append(staging_job_id)
        staging_clause = f"OR (status = 'staging' AND ingestion_job_id = ${len(args)})"
    if replacing_confluence_pages:
        args.append(list(replacing_confluence_pages))
        replace_clause = (
            f"AND NOT (status = 'active' AND confluence_page_id IS NOT NULL "
            f"AND confluence_page_id = ANY(${len(args)}::text[]))"
        )
    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, content, 1 - (embedding <=> $2::vector) AS similarity
            FROM rag_knowledge
            WHERE namespace_id = $1 AND (status = 'active' {staging_clause}) {replace_clause}
              AND embedding IS NOT NULL
            ORDER BY embedding <=> $2::vector
            LIMIT $3
            """,
            *args,
        )
    return [{"id": r["id"], "content": r["content"], "similarity": float(r["similarity"])} for r in rows]


# ── 부모 섹션 확장 (parent-child, 2026-10-01) ─────────────────────────────────
# 검색은 작은 청크로 정확히 하고, LLM에는 그 청크가 속한 상위 섹션의 이웃 청크까지 넘긴다(small-to-big).
# 실측(scripts/eval_confluence_routing.py 등, 컨플루언스 51+21문항, 로컬 LLM 핵심항목 판정): 여러 섹션을
# 봐야 하는 통합형 질문의 핵심항목 재현율 76%→92%, 단일 질문 78%→86%, 응답시간 차이 없음(4.6s→4.3s),
# 조건부 라우팅은 단일 질문에서도 82%가 켜져 구분력이 없어 항상 켠다. 근사(같은 상위 문자열 + id 근접)가
# 아니라 적재 때 남은 구조로 경계를 복원한다 — 같은 등록 묶음(ingestion_job_id) 안에서 직계 상위
# (heading_path[:-1], 1단계면 페이지)가 같은 청크를 문서 순서(source_chunk_idx)상 가까운 것부터.
PARENT_EXPANSION_CHAR_BUDGET = 6000  # 검색 청크 하나당 덧붙일 최대 글자 수


async def expand_parent_sections(
    hits: list["RetrievalResult"], *, char_budget: int = PARENT_EXPANSION_CHAR_BUDGET,
) -> list[dict]:
    """채택된 검색 결과의 부모 섹션 이웃 청크(검색 결과 자신과 중복 제외). 구조 정보(등록 묶음·
    heading_path)가 없는 청크는 확장하지 않는다 — 추정으로 경계를 만들지 않는다."""
    if not hits:
        return []
    async with get_conn() as conn:
        info = {r["id"]: r for r in await conn.fetch(
            "SELECT id, ingestion_job_id, source_chunk_idx, heading_path FROM rag_knowledge WHERE id = ANY($1::int[])",
            [h.id for h in hits])}
        seen = {h.id for h in hits}
        out: list[dict] = []
        for h in hits:
            m = info.get(h.id)
            if not m or m["ingestion_job_id"] is None or not m["heading_path"]:
                continue
            path = list(m["heading_path"])
            parent = path[:-1] if len(path) > 1 else path
            # 정렬은 문서 순서상 가까운 순만 쓴다. "같은 섹션 먼저 + 다른 섹션은 '참고용' 표시"를 실측했으나
            # (2026-10-01) 단일 질문 모순 3→3으로 줄지 않고 통합형 핵심항목 재현율 90%→86%로 오히려 떨어져
            # 채택하지 않음 — 통합형 질문은 원래 다른 섹션 내용이 필요해서, 그걸 '참고용'으로 낮추면 손해.
            rows = await conn.fetch("""
                SELECT id, content, heading_path FROM rag_knowledge
                WHERE ingestion_job_id = $1 AND status = 'active'
                  AND heading_path[1:$2] = $3::text[] AND id <> ALL($4::int[])
                ORDER BY abs(source_chunk_idx - $5), source_chunk_idx
            """, m["ingestion_job_id"], len(parent), parent, list(seen), m["source_chunk_idx"] or 0)
            budget = char_budget
            for r in rows:
                if len(r["content"]) > budget:
                    break
                budget -= len(r["content"])
                seen.add(r["id"])
                out.append({"id": r["id"], "content": r["content"], "heading_path": list(r["heading_path"] or [])})
    return out


_VECTOR_CANDIDATE_LIMIT = 300  # top_k/reranker_candidates(기본 20)보다 넉넉한 후보 풀 —
# 벡터 CTE를 ORDER BY + LIMIT로 유계화해 정렬 비용을 줄인다(테이블이 커지면 HNSW
# 인덱스도 이 형태에서만 자동으로 쓰이기 시작함).

# "정확히 일치해야 의미 있는" 구조화 데이터(DB 스키마/코드표 덤프) — 코사인 유사도로
# 비교하면 다른 카테고리 지식과 어휘만 겹쳐도 오탐이 난다는 게 실측으로 확인됨
# (2026-08-28, VOC 반복 유형 커버리지 판정에서 완전히 무관한 코드표 항목이 매칭됨).
# 이 카테고리는 벡터 점수를 랭킹에서 배제하고 키워드(RDB 텍스트) 매칭만으로 순위를
# 매긴다 — 질문 자체를 분류하는 게 아니라, 이미 등록된 지식 행의 category 값으로
# 판단한다(등록 시점에 정해지는 값이라 별도 분류 로직 불필요). 관리자 설정으로
# 노출하지 않고 코드 상수로 고정 — 카테고리가 딱 2개뿐이라 지금은 설정 화면을
# 만들 정도의 규모가 아니라고 판단(few-shot 관리 UI를 일부러 안 만든 것과 같은 이유).
_KEYWORD_ONLY_CATEGORIES = ("DB", "공통코드")


def is_keyword_only_category(category: Optional[str]) -> bool:
    """다른 모듈(agent.py 등)이 private 상수(_KEYWORD_ONLY_CATEGORIES)를 직접 참조하지
    않고도 같은 판단을 할 수 있게 하는 공개 헬퍼."""
    return category in _KEYWORD_ONLY_CATEGORIES


async def search_knowledge(
    namespace: str, query_vec: list[float], enriched_query: str,
    w_vector: float = 0.7, w_keyword: float = 0.3, top_k: int = 5,
    categories: Optional[list[str]] = None,
) -> list[RetrievalResult]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        category_filter = "AND k.category = ANY($9)" if categories else ""
        params = [
            str(query_vec), ns_id, enriched_query, w_vector, w_keyword, top_k, _VECTOR_CANDIDATE_LIMIT,
            list(_KEYWORD_ONLY_CATEGORIES),
        ]
        if categories:
            params.append(categories)
        rows = await conn.fetch(
            f"""
            WITH vector_scores AS (
                SELECT id, 1 - (embedding <=> $1::vector) AS v_score
                FROM rag_knowledge
                WHERE namespace_id = $2 AND embedding IS NOT NULL
                  AND (status IS NULL OR status = 'active')
                ORDER BY embedding <=> $1::vector
                LIMIT $7
            ),
            keyword_scores AS (
                -- policy_strip_ko()로 한국어 조사/어미를 걷어내고 매칭한다(2026-09-18) —
                -- to_tsvector('simple', ...)엔 형태소 분석이 없어 "DS14가"(조사 융합)와
                -- 문서 안의 깔끔한 "DS14"가 서로 다른 lexeme('ds14가' vs 'ds14')로 갈려
                -- 전혀 매칭이 안 되는 게 실측 확인됨("DS14가 뭐야?" 질문이 정확히 이 문제로
                -- 키워드 전용 카테고리 문서를 못 찾음). policy/search.py의 정책 파라미터
                -- 검색에 이미 같은 함수로 적용해 검증된 패턴(89문항 실측 hit@10 75.3%→79.8%,
                -- init/06-policy-strip-ko.sql) 재사용 — 함수 자체는 정책 전용이 아닌 범용
                -- 조사 제거기라 그대로 쓸 수 있다.
                SELECT k.id, ts_rank(to_tsvector('simple', policy_strip_ko(k.content)), q.tsq) AS k_score
                FROM rag_knowledge k
                CROSS JOIN LATERAL (
                    -- quote_literal로 각 lexeme를 감싸야 한다 — 감싸지 않으면 lexeme
                    -- 안에 tsquery 문법 특수문자(예: URL 토큰에 딸려온 짝 안 맞는
                    -- ")")가 섞였을 때 to_tsquery가 그 문자를 연산자로 해석해
                    -- "syntax error in tsquery"로 죽는다 — 실제 VOC 메일(광고성
                    -- 웨비나 메일의 URL) fetch 테스트에서 재현·확인된 버그.
                    SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS tsq
                    FROM (SELECT DISTINCT lexeme FROM unnest(to_tsvector('simple', policy_strip_ko($3)))) t
                    WHERE lexeme IS NOT NULL
                ) q
                WHERE k.namespace_id = $2
                  AND (k.status IS NULL OR k.status = 'active')
                  AND to_tsvector('simple', policy_strip_ko(k.content)) @@ q.tsq
            )
            SELECT k.id, n.name AS namespace,
                   k.content, k.base_weight, k.category, k.heading_path,
                   k.source_type, k.source_file, k.created_at,
                   COALESCE(vs.v_score, 0.0) AS v_score,
                   COALESCE(ks.k_score, 0.0) AS k_score,
                   -- 카테고리별로 RDB(키워드) 검색을 쓸지 벡터 검색을 쓸지 여기서 갈린다
                   -- ($8 = _KEYWORD_ONLY_CATEGORIES) — 구조화 코드/스키마 데이터는 벡터
                   -- 점수를 0으로 만들어 랭킹에서 배제하고 키워드 점수만으로 순위를 매김.
                   (CASE WHEN k.category = ANY($8::text[])
                         THEN COALESCE(ks.k_score, 0.0)
                         ELSE $4 * COALESCE(vs.v_score, 0.0) + $5 * COALESCE(ks.k_score, 0.0)
                    END) * (1.0 + k.base_weight) AS final_score,
                   k.updated_at
            FROM rag_knowledge k
            JOIN ops_namespace n ON k.namespace_id = n.id
            LEFT JOIN vector_scores vs ON k.id = vs.id
            LEFT JOIN keyword_scores ks ON k.id = ks.id
            WHERE k.namespace_id = $2
              AND (k.status IS NULL OR k.status = 'active')
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
            content=r["content"],
            base_weight=r["base_weight"],
            v_score=float(r["v_score"]), k_score=float(r["k_score"]),
            final_score=score, category=r["category"],
            heading_path=list(r["heading_path"] or []),
            source_type=r["source_type"], source_file=r["source_file"],
            created_at=r["created_at"],
        ))
    return results


def build_context(results: list[RetrievalResult]) -> str:
    """관련지식 게이트(2026-08-25 실측 감사): 실 질의로그 103건을 재현해보니
    resolved(실제 답변 성공)와 no_knowledge(지식 공백 판정) 두 그룹의 top score
    분포가 거의 겹쳤다(중앙값 0.603 vs 0.610, 표본 39건). 순수 코사인 점수만으론
    "진짜 관련 있는지"를 못 가른다는 게 VOC 커버리지 검증(pattern_detection.py
    _verify_coverage_with_llm 참고)과 같은 근본 원인으로 재확인된 셈이다.

    다만 VOC 커버리지와 달리 여기선 위험도가 다르다 — knowledge_min_score는
    "컨텍스트에 포함할지"만 결정하고, 그 컨텍스트가 실제로 질문에 답이 되는지는
    LLM이 답변을 생성하며 스스로 판단한다(no_knowledge 상태 자체도 LLM이 "관련
    지식을 찾지 못했습니다"라고 답한 문구를 사후에 감지해 매긴다, v2.32) — 즉 이미
    암묵적인 LLM 재확인 단계가 있어 VOC처럼 "검증이 아예 없는" 상태는 아니다.
    실제 리스크는 무관한 문서가 컨텍스트에 섞여 LLM이 가끔 그럴듯하게 오답을 지어낼
    가능성 쪽이라, VOC와 같은 명시적 LLM 재검증 게이트를 매 채팅 메시지마다 추가하는
    것보다는(비용·지연 커짐) 이미 준비된 CrossEncoder 리랭커(reranker_enabled,
    현재 huggingface.co 네트워크 제한으로 비활성) 도입을 기다리기로 결정함
    (2026-08-25). 표본도 39건뿐이라 값 자체를 지금 조정하지 않음.
    """
    th = get_thresholds()
    relevant = [r for r in results if is_adopted(r, th)]
    if not relevant:
        return ""

    parts = []
    for i, r in enumerate(relevant, 1):
        rel = relevance_score(r)
        # 키워드 전용 카테고리는 ts_rank가 코사인 스케일보다 항상 작아 mid/high 라벨이
        # 무의미(is_adopted에서 이미 매칭 여부로만 채택 판단) — "정확 매칭"으로 고정 표시
        confidence = "정확 매칭" if r.category in _KEYWORD_ONLY_CATEGORIES else (
            "높음" if rel >= th["knowledge_high_score"] else "보통" if rel >= th["knowledge_mid_score"] else "낮음"
        )
        part = [f"--- 문서 {i} (점수: {rel:.4f}, 신뢰도: {confidence}) ---"]
        if r.heading_path:
            part.append(f"상위 맥락: {' > '.join(r.heading_path)}")
        part.append(f"내용:\n{r.content}")
        parts.append("\n".join(part))

    return "\n\n".join(parts)
