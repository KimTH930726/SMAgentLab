"""지식베이스 RAG 에이전트 — AgentBase 구현."""
import asyncio
import logging
from typing import AsyncIterator, Optional

from agents.base import AgentBase
from core.database import get_conn
from core.config import settings
from domain.chat import memory
from domain.chat.helpers import (
    LLM_UNAVAILABLE_MSG,
    results_to_json, results_to_payload,
    update_assistant_message, update_inhouse_conv_id,
    create_query_log, post_save_tasks,
)
from domain.knowledge import retrieval
from domain.llm.base import resolve_system_prompt
from domain.llm.factory import get_llm_provider
from shared.embedding import embedding_service
from shared import cache as sem_cache

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
        api_key: Optional[str] = context.get("api_key")
        inhouse_conv_id: Optional[str] = context.get("inhouse_conv_id")
        category: Optional[str] = context.get("category")

        full_answer = ""
        token_count = 0
        mapped_term: Optional[str] = None
        has_results = False
        llm_failed = False

        try:
            yield {"type": "status", "step": "embedding", "message": "질문 임베딩 생성 중..."}

            # ── 멀티턴 검색 보강: 직전 Q+A 결합 ──
            search_question = query
            async with get_conn() as conn:
                prev_pair = await conn.fetch(
                    """
                    SELECT role, content FROM (
                        SELECT role, content, created_at, id FROM ops_message
                        WHERE conversation_id = $1 AND id < $2
                        ORDER BY created_at DESC, id DESC LIMIT 2
                    ) sub ORDER BY sub.created_at ASC, sub.id ASC
                    """,
                    conversation_id, msg_id,
                )
            if prev_pair:
                prev_context = " ".join(r["content"][:80] for r in prev_pair if r["content"])
                search_question = f"{prev_context} {query}"
                logger.info("멀티턴 검색 보강: '%s' → '%s'", query, search_question)

            # 두 임베딩을 병렬로 생성 (RAG 검색용 + 캐시 정규화용)
            query_vec, cache_vec = await asyncio.gather(
                embedding_service.embed(search_question),
                embedding_service.embed(sem_cache.normalize_query(search_question)),
            )

            # ── Semantic Cache 조회 ──
            cached = await sem_cache.get_cached(namespace, cache_vec)
            if cached:
                await update_assistant_message(msg_id, cached["answer"], "completed")
                yield {
                    "type": "meta", "conversation_id": conversation_id, "message_id": msg_id,
                    "mapped_term": cached.get("mapped_term"),
                    "results": cached.get("results", []),
                }
                yield {"type": "token", "data": cached["answer"]}
                await create_query_log(namespace, query, cached["answer"], bool(cached.get("results")), cached.get("mapped_term"), msg_id)
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
            results, fewshots = await asyncio.gather(
                retrieval.search_knowledge(namespace, query_vec, enriched_query, w_vector, w_keyword, top_k, category),
                retrieval.fetch_fewshots(namespace, query_vec),
            )

            fs_section = retrieval.build_fewshot_section(fewshots)
            doc_context = retrieval.build_context(results)
            llm_context = f"{fs_section}\n\n{doc_context}" if fs_section else doc_context
            has_results = len(results) > 0

            async with get_conn() as conn:
                await conn.execute(
                    "UPDATE ops_message SET mapped_term = $1, results = $2::jsonb WHERE id = $3",
                    mapped_term, results_to_json(results), msg_id,
                )

            yield {
                "type": "meta", "conversation_id": conversation_id, "message_id": msg_id,
                "mapped_term": mapped_term, "results": results_to_payload(results),
            }

            yield {"type": "status", "step": "llm", "message": "AI 답변 생성 중..."}

            new_inhouse_conv_id: Optional[str] = None

            def _capture_inhouse_conv_id(cid: str) -> None:
                nonlocal new_inhouse_conv_id
                new_inhouse_conv_id = cid

            chat_prompt = await resolve_system_prompt()

            try:
                async for token in get_llm_provider().generate_stream(
                    llm_context, query, history, api_key=api_key,
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
            await update_assistant_message(msg_id, final_answer, msg_status)
            if new_inhouse_conv_id and new_inhouse_conv_id != inhouse_conv_id:
                await update_inhouse_conv_id(conversation_id, new_inhouse_conv_id)
            await create_query_log(namespace, query, final_answer, has_results, mapped_term, msg_id)

            # ── Semantic Cache 저장 (LLM 정상 응답 시만, 결과 유무 무관) ──
            if final_answer != LLM_UNAVAILABLE_MSG:
                await sem_cache.set_cached(namespace, cache_vec, {
                    "answer": final_answer,
                    "mapped_term": mapped_term,
                    "results": results_to_payload(results),
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
