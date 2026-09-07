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
from service.llm.base import resolve_system_prompt
from service.llm.factory import get_llm_provider
from shared.embedding import embedding_service
from shared import cache as sem_cache
from shared import reranker as reranker_svc

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 20


async def _safe_post_save(conv_id: int, namespace: str) -> None:
    try:
        await post_save_tasks(conv_id, namespace)
    except Exception as e:
        logger.warning("post_save_tasks 실패: %s", e)


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
            cache_vec = await embedding_service.embed(sem_cache.normalize_query(search_question))

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
                await create_query_log(namespace, query, cached["answer"], bool(cached.get("results")), cached.get("mapped_term"), msg_id, had_context=bool(cached.get("results")))
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
            results_raw, fewshots, policy_available = await asyncio.gather(
                retrieval.search_knowledge(namespace, query_vec, enriched_query, w_vector, w_keyword, candidate_k, categories),
                retrieval.fetch_fewshots(namespace, query_vec),
                policy_search.has_policy_data(namespace),
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
            policy_context = ""
            policy_result = policy_search.PolicySearchResult()
            policy_citations: list[dict] = []
            if policy_available:
                try:
                    policy_result = await policy_search.search_policy(
                        namespace, enriched_query, top_k=5, query_vec=query_vec,
                    )
                    policy_context = policy_search.build_policy_context(policy_result)
                except Exception as e:
                    logger.warning("정책 검색 실패(채팅 흐름은 계속 진행): %s", e)

            fs_section = retrieval.build_fewshot_section(fewshots)
            doc_context = retrieval.build_context(results)
            if policy_context:
                doc_context = f"{doc_context}\n\n{policy_context}" if doc_context else policy_context
            llm_context = f"{fs_section}\n\n{doc_context}" if fs_section else doc_context
            has_results = len(results) > 0 or bool(policy_context)
            had_context = bool(doc_context.strip())

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
            await create_query_log(namespace, query, final_answer, has_results, mapped_term, msg_id, had_context=had_context)

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
