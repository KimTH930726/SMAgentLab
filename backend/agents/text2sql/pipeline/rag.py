"""Stage 2: RAG 검색 — pgvector에서 스키마/용어/예제 병렬 검색."""
import asyncio
import logging

from agents.text2sql.admin import service
from shared.embedding import embedding_service

logger = logging.getLogger(__name__)


async def run(context: dict, namespace_id: int, stage_cfg: dict) -> dict:
    """임베딩 1회 생성 후 스키마/용어/예제를 asyncio.gather로 병렬 검색.

    Returns: {"rag": {"schema": [...], "synonyms": [...], "fewshots": [...]}}
    """
    question = context["question"]
    vec = await embedding_service.embed(question)

    schema_results, synonym_results, fewshot_results = await asyncio.gather(
        service.search_schema(namespace_id, question, top_k=20, vec=vec),
        service.search_synonyms(namespace_id, question, top_k=5, vec=vec),
        service.search_fewshots(namespace_id, question, top_k=3, vec=vec),
    )

    logger.debug(
        "RAG 병렬 검색 완료: schema=%d, synonyms=%d, fewshots=%d",
        len(schema_results), len(synonym_results), len(fewshot_results),
    )
    return {
        "rag": {
            "schema": schema_results,
            "synonyms": synonym_results,
            "fewshots": fewshot_results,
        }
    }
