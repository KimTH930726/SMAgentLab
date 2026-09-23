"""지식베이스 RAG 에이전트 — AgentBase 구현."""
import asyncio
import logging
from typing import AsyncIterator, Optional

from agents.base import AgentBase
from core.database import get_conn
from core.config import settings
from service.chat import memory
from service.chat.helpers import (
    LLM_UNAVAILABLE_MSG,
    results_to_json, results_to_payload,
    update_assistant_message, update_inhouse_conv_id,
    create_query_log, post_save_tasks,
)
from agents.knowledge_rag.knowledge import retrieval
from service.policy import search as policy_search
from service.refdata import service as refdata_search
from service.llm.base import resolve_system_prompt
from service.llm.factory import get_llm_provider
from shared.embedding import embedding_service
from shared import cache as sem_cache
from shared import reranker as reranker_svc
from shared.rrf import rrf_score

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 20


async def _safe_post_save(conv_id: int, namespace: str) -> None:
    try:
        await post_save_tasks(conv_id, namespace)
    except Exception as e:
        logger.warning("post_save_tasks 실패: %s", e)


def _build_rrf_context(
    results: list[retrieval.RetrievalResult], policy_result: policy_search.PolicySearchResult,
    common_codes: Optional[list[dict]] = None, db_columns: Optional[list[dict]] = None,
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
    return "\n\n".join(text for _, text in items)


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
        has_results = False
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
                await create_query_log(namespace, query, cached["answer"], bool(cached.get("results")), cached.get("mapped_term"), msg_id, had_context=bool(cached.get("results")), user_id=user.get("id"))
                yield {"type": "done", "message_id": msg_id, "status": "completed"}
                return

            yield {"type": "status", "step": "context", "message": "용어 매핑 및 대화 맥락 검색 중..."}
            glossary_match, history = await asyncio.gather(
                retrieval.map_glossary_term(namespace, query_vec),
                memory.build_context_history(conversation_id, query_vec),
            )
            mapped_term = glossary_match.term if glossary_match else None
            enriched_query = f"{search_question} {mapped_term}" if mapped_term else search_question

            yield {"type": "status", "step": "search", "message": "관련 문서 검색 중..."}
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
            # 근거 1건 선별(2026-09-07): "근거가 너무 많이 보인다, 원문 1건만" 피드백에 검색
            # 직후 벡터 점수 1위 하나로 LLM 컨텍스트까지 줄여봤다가, 그 1위가 실제로 무관한
            # 후보라 정답이 컨텍스트에서 빠져 "관련 지식을 찾지 못했습니다"로 답변이 실패하는
            # 걸 실측으로 발견 — 즉시 되돌림. 그래서 LLM 컨텍스트(policy_context)는 원래대로
            # top_k=5 다중 후보를 그대로 유지해 재현율을 지키고, 화면에 보여줄 근거 1건은
            # 답변이 다 나온 "뒤에" select_cited_hit()으로 역추적한다(아래, final_answer 계산
            # 직후). 그 전까지 policy_citations는 비워두고, 두 번째 meta 이벤트로 늦게 채운다.
            policy_result = policy_search.PolicySearchResult()
            policy_citations: list[dict] = []
            if policy_available:
                try:
                    policy_result = await policy_search.search_policy(
                        namespace, enriched_query, top_k=5, query_vec=query_vec,
                    )
                except Exception as e:
                    logger.warning("정책 검색 실패(채팅 흐름은 계속 진행): %s", e)

            # 구조화 참조데이터(공통코드/DB스키마) 병행 검색(2026-09-18) — 정책과 같은
            # 이유로 게이트(refdata_available)를 먼저 확인해 데이터 없는 네임스페이스의
            # 낭비를 피한다. ref_common_code/ref_db_column은 정확 조회 전용이라 임베딩이
            # 없어 벡터 검색 자체가 불가능 — 항상 키워드(ts_rank)로만 찾는다.
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

            llm_context = _build_rrf_context(results, policy_result, common_codes, db_columns)
            has_results = len(results) > 0 or bool(policy_result.params or policy_result.narratives) or bool(common_codes or db_columns)
            had_context = bool(llm_context.strip())

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
            await create_query_log(namespace, query, final_answer, has_results, mapped_term, msg_id, had_context=had_context, user_id=user.get("id"))

            # ── Semantic Cache 저장 (LLM 정상 응답 시만, 결과 유무 무관) ──
            if final_answer != LLM_UNAVAILABLE_MSG:
                await sem_cache.set_cached(namespace, "knowledge_rag", cache_vec, {
                    "answer": final_answer,
                    "mapped_term": mapped_term,
                    "results": results_to_payload(results),
                    "policy_citations": policy_citations,
                    "query": query,
                })

            yield {"type": "done", "message_id": msg_id, "status": msg_status}

        except Exception as e:
            logger.error("KnowledgeRagAgent 에러: %s", e, exc_info=True)
            if not full_answer:
                full_answer = LLM_UNAVAILABLE_MSG
            await update_assistant_message(msg_id, full_answer, "completed")
        finally:
            asyncio.create_task(_safe_post_save(conversation_id, namespace))
