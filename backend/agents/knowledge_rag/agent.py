"""지식베이스 RAG 에이전트 — AgentBase 구현."""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from agents.base import AgentBase
from core.database import get_conn
from core.config import settings
from service.chat import memory
from service.chat.helpers import (
    LLM_UNAVAILABLE_MSG, LLM_EMPTY_MSG, NO_KNOWLEDGE_MARKER, is_llm_failure,
    results_to_json, results_to_payload,
    update_assistant_message, update_inhouse_conv_id,
    create_query_log, post_save_tasks,
)
from agents.knowledge_rag.knowledge import retrieval, glossary_terms
from service.policy import search as policy_search
from service.refdata import service as refdata_search
from service.llm.base import REPLACE_PREFIX, resolve_system_prompt
from service.llm.factory import get_llm_provider
from shared.embedding import embedding_service
from shared import cache as sem_cache
from shared import reranker as reranker_svc
from shared.rrf import rrf_score

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 20

# 정책 검색 후보 수(param·서술 각각) — 5에서 10으로(2026-09-28, scripts/diagnose_no_knowledge.py
# 골든셋 89문항 실측): 정답이 6~20위에 있어 잘리던 문항이 8→2건, 정상 답변 69→75건(77.5%→84.3%),
# 컨텍스트가 길어져도 "정답 있는데 거절"은 5→5건으로 늘지 않음. 평가 스크립트도 이 상수를 참조해
# 운영과 측정 조건이 어긋나지 않게 한다.
POLICY_CONTEXT_TOP_K = 10


async def _safe_post_save(conv_id: int, namespace: str) -> None:
    try:
        await post_save_tasks(conv_id, namespace)
    except Exception as e:
        logger.warning("post_save_tasks 실패: %s", e)


def _build_rrf_context(
    results: list[retrieval.RetrievalResult], policy_result: policy_search.PolicySearchResult,
    common_codes: Optional[list[dict]] = None, db_columns: Optional[list[dict]] = None,
    parent_expansions: Optional[list[dict]] = None,
) -> str:
    """일반지식(코사인)·정책 파라미터(RDB ts_rank)·정책 서술(코사인)·구조화 참조데이터
    (공통코드/DB스키마, ts_rank)를 RRF로 합쳐 하나의 LLM 컨텍스트로 만든다(2026-09-18,
    참조데이터 축은 09-18 추가). 기존엔 retrieval.build_context() + policy_search.
    build_policy_context()를 그냥 이어붙였는데(항상 "일반지식 먼저, 정책 나중"), 축마다
    점수 스케일이 달라(코사인 0~1 vs ts_rank 0~5+) 실제로 더 관련성 높은 쪽이 항상 뒤에
    깔리는 구조적 문제가 있었음. 평가 게이트 즉석 질의(UnifiedAdhocSearch, 프론트에서
    RRF 적용)와 별개 백엔드 검증 스크립트(scripts/compare_rrf_context.py)로 실제 질문
    3건씩 두 라운드 비교한 결과 사실관계 왜곡은 없었고, 이 방식이 실제 순위 구조를 더
    정확히 반영해 채팅에도 동일하게 적용.

    참조데이터 축 추가 배경: rag_knowledge의 "DB"/"공통코드" 카테고리(_KEYWORD_ONLY_
    CATEGORIES)는 벡터 채널과 같은 테이블 안에서 final_score로 경쟁하다 보니, ts_rank
    스케일이 코사인보다 항상 작아 ORDER BY ... LIMIT top_k 단계에서부터 후보 풀에도 못
    들어가는 문제가 실측 확인됐다("DS14가 뭐야?"가 자기 매칭 대상인 id=20을 top_k=5
    후보에서 완전히 배제 — 27건 중 27등). CMDB 데이터도 앞으로 같은 성격(정확 조회용
    RDB 데이터)이라 이 문제가 반복될 것으로 예상돼, rag_knowledge 안에서 SQL을 더
    복잡하게 만드는 대신 원래 이 목적으로 만들어졌던(§table-definition.md #43) 별도
    구조화 테이블(ref_common_code/ref_db_column, 이미 구현돼 있었지만 chat에서 호출을
    안 하고 있었음)로 완전히 분리 — 처음부터 독립된 RRF 축으로 둬서 스케일 경쟁 자체를
    피한다. id=20은 이 축으로 이전(마이그레이션 스크립트, rag_knowledge 쪽은 deprecated).

    retrieval.build_context()/policy_search.build_policy_context()는 각각 디버그
    검색(service/chat/router.py)·이메일 VOC 파이프라인·기존 정책 편입 로직 등 다른
    화면에서 그대로 쓰이고 있어(테스트도 그 출력 형식에 매여 있음) 건드리지 않고,
    여기서만 항목 단위로 다시 포맷한다 — 포맷 문자열이 일부 중복되지만 공유 함수를
    바꿔 여러 화면에 영향이 번지는 것보다 안전하다."""
    th = retrieval.get_thresholds()
    relevant = [r for r in results if retrieval.is_adopted(r, th)]

    items: list[tuple[float, str]] = []
    for i, r in enumerate(relevant):
        rel = retrieval.relevance_score(r)
        confidence = "정확 매칭" if retrieval.is_keyword_only_category(r.category) else (
            "높음" if rel >= th["knowledge_high_score"]
            else "보통" if rel >= th["knowledge_mid_score"]
            else "낮음"
        )
        heading_str = f"\n상위 맥락: {' > '.join(r.heading_path)}" if r.heading_path else ""
        # 문서 식별자를 반드시 포함 — 없으면 시스템 프롬프트의 "📎 문서 N 참고" 지시를
        # 따를 근거가 컨텍스트에 없어, LLM이 옆에 보이는 점수(float)를 N으로 오인해
        # "문서 0.3925 참고"처럼 잘못 인용하는 실사고가 있었다(2026-09-22). #{id} 포맷은
        # 프론트 SearchResultCard.tsx의 `문서 #${result.id}` 표기와 동일해 사용자가
        # 위쪽 검색 결과 패널과 답변 하단 인용을 바로 대조할 수 있다.
        items.append((rrf_score(i), f"--- 문서 #{r.id} (점수: {rel:.4f}, 신뢰도: {confidence}) ---{heading_str}\n내용:\n{r.content}"))
    for i, p in enumerate(policy_result.params):
        value_str = f"{p.value}{p.unit or ''}" if p.value else "값 없음"
        condition_str = f" ({p.condition})" if p.condition else ""
        category_str = f" [분류: {' > '.join(p.category_path)}]" if p.category_path else ""
        items.append((rrf_score(i), f"[정책 파라미터 · 정확 일치: {p.policy_name}{condition_str}]{category_str} {p.param_name} = {value_str}"))
    for i, n in enumerate(policy_result.narratives):
        confidence = "높음" if n.score >= 0.6 else "보통" if n.score >= 0.4 else "낮음"
        category_str = f" [분류: {' > '.join(n.category_path)}]" if n.category_path else ""
        # chunk_text 대신 raw_body(원문 전체) 사용 이유는 service/policy/search.py의
        # build_policy_context()와 동일 — 여러 조각으로 쪼개진 항목의 적용범위 조건 유실 방지.
        body = n.raw_body or n.chunk_text
        items.append((rrf_score(i), f"[정책 서술: {n.policy_name}]{category_str} (신뢰도: {confidence})\n{body}"))
    for i, c in enumerate(common_codes or []):
        items.append((rrf_score(i), f"[공통코드 · 정확 매칭: {c['group_code_name']}] {c['code_id']} = {c['code_name']}"))
    for i, d in enumerate(db_columns or []):
        items.append((rrf_score(i), f"[DB 스키마 · 정확 매칭: {d['table_name']}.{d['column_name']}] {d.get('column_comment') or ''} ({d.get('data_type') or ''})"))

    items.sort(key=lambda x: x[0], reverse=True)
    blocks = [text for _, text in items]
    # 부모 섹션 보충(retrieval.expand_parent_sections) — 순위 경쟁에 넣지 않고 맨 뒤에, 보충임을 표시해서
    # 붙인다. 실측에서 확장 컨텍스트가 단일 질문 4/51건에서 정답과 어긋난 답을 만들어(이웃 내용 혼입 추정),
    # 검색된 문서를 우선 근거로 삼도록 구분한다.
    for e in parent_expansions or []:
        heading_str = f"\n상위 맥락: {' > '.join(e['heading_path'])}" if e.get("heading_path") else ""
        blocks.append(f"--- 문서 #{e['id']} (같은 상위 섹션 보충 — 위 문서를 우선 근거로) ---{heading_str}\n내용:\n{e['content']}")
    return "\n\n".join(blocks)


@dataclass
class ChatContext:
    """채팅 한 턴의 검색·문맥 조립 결과 — 채팅과 측정 스크립트(scripts/eval_chat_retrieval.py)가 같은 함수를 써서
    "측정한 경로 = 실제 채팅 경로"가 되게 한다(v2.128: 예전 측정은 용어 매핑을 안 거쳐 채팅과 달랐다)."""
    results: list
    policy_result: policy_search.PolicySearchResult
    common_codes: list
    db_columns: list
    llm_context: str
    enriched_query: str
    mapped_term: Optional[str]
    term_matches: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    abstain: bool = False


def should_abstain(signals: dict, min_score: Optional[float], min_param_rank: float) -> bool:
    """근거 없음 즉시 판정 — 지식 채택 0·공통코드/DB 0·정책 파라미터 약함·정책 서술 최고점 < 하한. min_score가 None이면 끔.
    실측(2026-10-06, 채팅 경로·새 용어 방식, 골든 88 vs 답 없는 질문 60): (서술 0.50, 파라미터 0.05)에서 골든 거짓 거절 0,
    답 없음 35/60 차단 — 점수만으론 다 못 거르므로(분포가 겹침) 나머지는 LLM 거절에 맡긴다. 정답 질문의 파라미터 rank도 대부분
    0.05 미만이라 파라미터 하한은 사실상 "강하게 걸린 파라미터"만 근거로 친다. 배포 후 다시 재서 켠다(그래서 기본 꺼짐)."""
    if min_score is None:
        return False
    return (signals.get("adopted", 0) == 0 and signals.get("codes", 0) == 0 and signals.get("columns", 0) == 0
            and signals.get("max_param_rank", 0.0) <= min_param_rank
            and signals.get("top_narrative", 0.0) < min_score)


async def build_chat_context(
    namespace: str, search_question: str, query_vec: list[float], *,
    top_k: int, w_vector: float, w_keyword: float, categories: Optional[list[str]] = None,
    glossary_mode: Optional[str] = None, glossary_entries: Optional[list] = None,
    policy_top_k: int = POLICY_CONTEXT_TOP_K,
) -> ChatContext:
    """용어 → 지식/정책/참조데이터 검색 → RRF 문맥 조립. glossary_mode·glossary_entries는 측정용 덮어쓰기(기본은 설정값·DB)."""
    term_matches, mapped_term, enriched_query = await glossary_terms.resolve_query_terms(
        namespace, search_question, query_vec, mode=glossary_mode, entries=glossary_entries)

    # 리랭커 활성화 시 더 많은 후보를 가져온 뒤 CrossEncoder로 재정렬
    candidate_k = settings.reranker_candidates if settings.reranker_enabled else top_k
    results_raw, policy_available, refdata_available = await asyncio.gather(
        retrieval.search_knowledge(namespace, query_vec, enriched_query, w_vector, w_keyword, candidate_k, categories),
        policy_search.has_policy_data(namespace),
        refdata_search.has_refdata(namespace),
    )
    if settings.reranker_enabled and len(results_raw) > top_k:
        results = await reranker_svc.rerank(enriched_query, results_raw, top_k)
    else:
        results = results_raw[:top_k]

    # 정책서 데이터 편입(2026-09-04 1단계, 2026-09-06 2단계, 2026-09-07 근거 1건 선별)
    # — Track 2로 하이브리드 스키마 우세 확정 후 rag_knowledge 검색과 별개로 정책
    # 데이터도 doc_context에 텍스트로 얹는다(1단계). policy_available=False인
    # 네임스페이스(정책 데이터 없음)는 이 블록 자체를 건너뛰어 매 턴 불필요한 쿼리를
    # 피한다. 2단계: "정책에서 온 답인지 기준정보에서 온 답인지 구분이 안 되고 원문도
    # 안 보인다"는 사용자 피드백으로 policy_citations를 별도로 만들어 화면에 "정책 근거"
    # 카드로 노출(§4-2/§6) — 기존 results(rag_knowledge 인용) 배열엔 안 섞는다
    # (FeedbackSection이 results[0].id를 rag_knowledge id로 쓰는 것과 충돌 방지).
    # 근거 1건 선별(2026-09-07): LLM 컨텍스트는 top_k 다중 후보를 그대로 유지해 재현율을 지키고,
    # 화면에 보여줄 근거 1건은 답변이 다 나온 "뒤에" select_cited_hit()으로 역추적한다.
    policy_result = policy_search.PolicySearchResult()
    if policy_available:
        try:
            policy_result = await policy_search.search_policy(
                namespace, enriched_query, top_k=policy_top_k, query_vec=query_vec,
            )
        except Exception as e:
            logger.warning("정책 검색 실패(채팅 흐름은 계속 진행): %s", e)

    # 구조화 참조데이터(공통코드/DB스키마) 병행 검색(2026-09-18) — 정확 조회 전용이라 키워드(ts_rank)로만 찾는다.
    common_codes: list[dict] = []
    db_columns: list[dict] = []
    if refdata_available:
        try:
            common_codes, db_columns = await asyncio.gather(
                refdata_search.search_common_codes(namespace, enriched_query, top_k=5),
                refdata_search.search_db_columns(namespace, enriched_query, top_k=5),
            )
        except Exception as e:
            logger.warning("참조데이터 검색 실패(채팅 흐름은 계속 진행): %s", e)

    # 부모 섹션 확장 — 채택된 지식 청크의 같은 상위 섹션 이웃을 컨텍스트에 보충(실패해도 답변은 계속)
    th = retrieval.get_thresholds()
    adopted = [r for r in results if retrieval.is_adopted(r, th)]
    parent_expansions: list[dict] = []
    try:
        parent_expansions = await retrieval.expand_parent_sections(adopted)
    except Exception as e:
        logger.warning("부모 섹션 확장 실패(확장 없이 진행): %s", e)
    llm_context = _build_rrf_context(results, policy_result, common_codes, db_columns, parent_expansions)
    # 용어 설명은 근거가 있을 때만 앞에 — 근거 없이 설명만 있으면 LLM이 설명으로 답을 지어낼 수 있다
    definitions = glossary_terms.definitions_block(term_matches)
    if definitions and llm_context.strip():
        llm_context = f"{definitions}\n\n{llm_context}"

    signals = {
        "adopted": len(adopted), "codes": len(common_codes), "columns": len(db_columns),
        "params": len(policy_result.params),
        "max_param_rank": max((p.score for p in policy_result.params), default=0.0),
        "top_narrative": max((n.score for n in policy_result.narratives), default=0.0),
    }
    return ChatContext(
        results=results, policy_result=policy_result, common_codes=common_codes, db_columns=db_columns,
        llm_context=llm_context, enriched_query=enriched_query, mapped_term=mapped_term,
        term_matches=term_matches, signals=signals,
        abstain=should_abstain(signals, settings.policy_abstain_min_score, settings.policy_abstain_min_param_rank),
    )


class KnowledgeRagAgent(AgentBase):

    @property
    def agent_id(self) -> str:
        return "knowledge_rag"

    @property
    def metadata(self) -> dict:
        return {
            "display_name": "지식베이스 AI",
            "description": "운영 가이드 및 매뉴얼 기반 질의응답",
            "icon": "BookOpen",
            "color": "indigo",
            "output_type": "text",
            "welcome_message": "운영 관련 질문을 입력해주세요.",
            "supports_debug": True,
        }

    async def stream_chat(
        self,
        query: str,
        user: dict,
        conversation_id: int,
        context: dict,
    ) -> AsyncIterator[dict]:
        namespace: str = context["namespace"]
        msg_id: int = context["msg_id"]
        w_vector: float = context.get("w_vector", 0.7)
        w_keyword: float = context.get("w_keyword", 0.3)
        top_k: int = context.get("top_k", 5)
        user_credentials: Optional[dict] = context.get("user_credentials")
        inhouse_conv_id: Optional[str] = context.get("inhouse_conv_id")
        categories: Optional[list[str]] = context.get("categories")

        full_answer = ""
        token_count = 0
        mapped_term: Optional[str] = None
        llm_failed = False

        try:
            yield {"type": "status", "step": "embedding", "message": "질문 임베딩 생성 중..."}

            # ── 멀티턴 검색 보강: 직전 턴과 관련 있을 때만 결합 ──
            # user_msg_id(현재 질문) 이전 메시지만 봐야 함 — msg_id(답변 placeholder)로
            # 필터링하면 라우터가 미리 저장해둔 현재 질문까지 "직전 맥락"으로 잘못 포함됨
            user_msg_id = context.get("user_msg_id", msg_id)
            search_question, query_vec = await memory.augment_query_for_search(
                conversation_id, query, exclude_message_id=user_msg_id,
            )
            # 캐시 키는 반드시 "이번에 실제로 물은 질문"만으로 계산한다(2026-09-18 실사고
            # 수정) — search_question은 멀티턴 보강이 직전 턴을 붙인 결과라 검색 재현율엔
            # 도움이 되지만, 캐시 키로 쓰면 완전히 다른 두 질문이 "직전 턴이 같다"는 이유로
            # 캐시 벡터가 서로 가까워져 오답이 재사용될 수 있다(실측: "쿠폰 회수 정책
            # 알려줘" 다음에 전혀 무관한 "정책적으로 최대 재고 갯수가 몇개?"를 물었는데
            # MULTITURN_RELEVANCE_THRESHOLD(0.35)를 "정책"이라는 단어 하나로 넘겨 직전
            # 턴이 붙었고, 그 오염된 병합 텍스트로 계산한 캐시 벡터가 1턴째와 코사인
            # 0.9309로 나와 캐시 임계치를 넘어서 완전히 무관한 답이 그대로 재사용됨).
            cache_vec = await embedding_service.embed(sem_cache.normalize_query(query))

            # ── Semantic Cache 조회 ──
            cached = await sem_cache.get_cached(namespace, "knowledge_rag", cache_vec)
            if cached:
                cached_citations = cached.get("policy_citations", [])
                # 캐시 응답도 근거(results)를 메시지에 남긴다 — 안 남기면 다시 열었을 때 근거 카드가 사라지고,
                # "답변 틀림" 자동 식별이 그 답변의 근거를 못 찾아 "빠진 내용"으로 잘못 분류된다(2026-10-01 실측).
                async with get_conn() as conn:
                    await conn.execute(
                        "UPDATE ops_message SET mapped_term = $1, results = $2::jsonb WHERE id = $3",
                        cached.get("mapped_term"), json.dumps(cached.get("results", []), ensure_ascii=False), msg_id,
                    )
                await update_assistant_message(
                    msg_id, cached["answer"], "completed",
                    metadata={"policy_citations": cached_citations} if cached_citations else None,
                )
                yield {
                    "type": "meta", "conversation_id": conversation_id, "message_id": msg_id,
                    "mapped_term": cached.get("mapped_term"),
                    "results": cached.get("results", []),
                    "policy_citations": cached_citations,
                }
                yield {"type": "token", "data": cached["answer"]}
                # 근거 유무는 캐시 저장 시점 값으로 — 예전엔 results(지식)만 봐서 정책·공통코드만으로 답한 캐시 응답이
                # 공백으로 잘못 세졌다(2026-10-02 실측: 정책 답변 3건). 옛 캐시 항목엔 값이 없어 지식·정책 근거로 대신 판단.
                had_context = cached.get("had_context", bool(cached.get("results") or cached_citations))
                await create_query_log(namespace, query, cached["answer"], cached.get("mapped_term"), msg_id,
                                       user_id=user.get("id"), had_context=had_context)
                yield {"type": "done", "message_id": msg_id, "status": "completed"}
                return

            yield {"type": "status", "step": "context", "message": "용어 확인 및 관련 문서 검색 중..."}
            cc, history = await asyncio.gather(
                build_chat_context(namespace, search_question, query_vec, top_k=top_k, w_vector=w_vector,
                                   w_keyword=w_keyword, categories=categories),
                memory.build_context_history(conversation_id, query_vec),
            )
            results, policy_result, llm_context, mapped_term = cc.results, cc.policy_result, cc.llm_context, cc.mapped_term
            policy_citations: list[dict] = []

            async with get_conn() as conn:
                await conn.execute(
                    "UPDATE ops_message SET mapped_term = $1, results = $2::jsonb WHERE id = $3",
                    mapped_term, results_to_json(results), msg_id,
                )

            yield {
                "type": "meta", "conversation_id": conversation_id, "message_id": msg_id,
                "mapped_term": mapped_term, "results": results_to_payload(results),
                "policy_citations": policy_citations,
            }

            # 근거 없음 즉시 판정(v2.128, 설정으로 켬) — LLM을 안 불러 지어낼 기회도, 게이트웨이 대기(100초+)도 없다.
            # 캐시에는 넣지 않는다(지식이 새로 들어오면 바로 답해야 하는데 고정 거절이 TTL 동안 남는다).
            if cc.abstain:
                await update_assistant_message(msg_id, NO_KNOWLEDGE_MARKER, "completed")
                yield {"type": "token", "data": NO_KNOWLEDGE_MARKER}
                await create_query_log(namespace, query, NO_KNOWLEDGE_MARKER, mapped_term, msg_id,
                                       user_id=user.get("id"), had_context=False)
                yield {"type": "done", "message_id": msg_id, "status": "completed"}
                return

            yield {"type": "status", "step": "llm", "message": "AI 답변 생성 중..."}

            new_inhouse_conv_id: Optional[str] = None

            def _capture_inhouse_conv_id(cid: str) -> None:
                nonlocal new_inhouse_conv_id
                new_inhouse_conv_id = cid

            chat_prompt = await resolve_system_prompt()

            try:
                async for token in get_llm_provider().generate_stream(
                    llm_context, query, history,
                    user_credentials=user_credentials,
                    ext_conversation_id=inhouse_conv_id,
                    on_ext_conversation_id=_capture_inhouse_conv_id,
                    system_prompt=chat_prompt,
                ):
                    if isinstance(token, str) and token.startswith(REPLACE_PREFIX):
                        # 게이트웨이 검열이 답을 통째로 교체 — 저장·캐시·화면 모두 교체문으로(지금까지 보낸 토큰은 버림)
                        full_answer = token[len(REPLACE_PREFIX):]
                        logger.warning("게이트웨이가 답변을 교체함(message_replace) — 교체문 %d자", len(full_answer))
                        await update_assistant_message(msg_id, full_answer)
                        yield {"type": "replace", "data": full_answer}
                        continue
                    full_answer += token
                    token_count += 1
                    if token_count == 1 or token_count % _FLUSH_INTERVAL == 0:
                        await update_assistant_message(msg_id, full_answer)
                    yield {"type": "token", "data": token}
            except Exception as e:
                logger.warning("LLM 스트리밍 실패: %s", e)
                llm_failed = True
                full_answer = LLM_UNAVAILABLE_MSG
                yield {"type": "token", "data": LLM_UNAVAILABLE_MSG}

            if not llm_failed and not full_answer.strip():   # 공백만 온 것도 빈 응답(리뷰 2026-10-07)
                # 예외 없이 토큰 0개로 끝남(2026-10-06 실측: 0.4초 만에 빈 응답 4건) — 예전엔 화면에 아무것도 안 보내 빈 말풍선만
                # 남았고 로그도 없었다. 게이트웨이 쪽 상세(받은 이벤트 종류·상태)는 provider가 경고로 남긴다.
                logger.warning("LLM 빈 응답(토큰 0개, 예외 없음): namespace=%s msg_id=%s context=%d자",
                               namespace, msg_id, len(llm_context))
                llm_failed = True
                full_answer = LLM_EMPTY_MSG
                yield {"type": "token", "data": LLM_EMPTY_MSG}

            final_answer = full_answer or LLM_UNAVAILABLE_MSG
            msg_status = "failed" if llm_failed else "completed"

            # 정책 근거 1건 역추적(2026-09-07) — 답변이 실제로 나온 뒤에야 어떤 후보를
            # 참고했는지 알 수 있다(위 policy_result 정의부 주석 참고).
            if policy_result.params or policy_result.narratives:
                try:
                    cited = policy_search.select_cited_hit(policy_result, final_answer)
                    policy_citations = policy_search.build_policy_citations(cited)
                except Exception as e:
                    logger.warning("정책 근거 선택 실패(답변엔 영향 없음): %s", e)

            await update_assistant_message(
                msg_id, final_answer, msg_status,
                metadata={"policy_citations": policy_citations} if policy_citations else None,
            )
            if policy_citations:
                yield {
                    "type": "meta", "conversation_id": conversation_id, "message_id": msg_id,
                    "mapped_term": mapped_term, "results": results_to_payload(results),
                    "policy_citations": policy_citations,
                }
            if new_inhouse_conv_id and new_inhouse_conv_id != inhouse_conv_id:
                await update_inhouse_conv_id(conversation_id, new_inhouse_conv_id)
            had_context = bool(llm_context.strip())
            await create_query_log(namespace, query, final_answer, mapped_term, msg_id, user_id=user.get("id"),
                                   had_context=had_context)

            # ── Semantic Cache 저장 (LLM 정상 응답 시만, 결과 유무 무관 — 연결 실패·게이트웨이 거부는 제외) ──
            if not is_llm_failure(final_answer):
                await sem_cache.set_cached(namespace, "knowledge_rag", cache_vec, {
                    "answer": final_answer,
                    "mapped_term": mapped_term,
                    "results": results_to_payload(results),
                    "policy_citations": policy_citations,
                    "query": query,
                    "had_context": had_context,
                })

            yield {"type": "done", "message_id": msg_id, "status": msg_status}

        except Exception as e:
            logger.error("KnowledgeRagAgent 에러: %s", e, exc_info=True)
            if not full_answer:
                full_answer = LLM_UNAVAILABLE_MSG
            await update_assistant_message(msg_id, full_answer, "completed")
        finally:
            asyncio.create_task(_safe_post_save(conversation_id, namespace))
