"""chat 실제 답변 생성에 RRF 병합을 적용하면 뭐가 달라지는지 실측 비교(2026-09-18).

현재 agent.py는 search_knowledge 결과(build_context)와 search_policy 결과
(build_policy_context)를 그냥 텍스트로 이어붙인다(RRF 없음). 게이트(EvaluationGate)의
즉석질의엔 이미 RRF를 적용했는데, "그럼 chat도 똑같이 가야 하는 거 아니냐"는 질문에
답하기 위해 — agent.py를 먼저 바꾸지 않고, 같은 질문에 대해 OLD(현재 이어붙이기)
컨텍스트와 NEW(RRF 재정렬) 컨텍스트를 각각 만들어 LLM에 실제로 태워서 답변을 나란히
비교한다. 실 서비스 트래픽에는 영향 없음(읽기 전용 조회 + 별도 LLM 호출만).

실행 (컨테이너 안):
    docker compose exec backend python scripts/compare_rrf_context.py
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from core.database import init_pool, close_pool
from shared.embedding import embedding_service
from service.llm.factory import get_llm_provider
from service.llm.base import resolve_system_prompt
from agents.knowledge_rag.knowledge import retrieval
from service.policy import search as policy_search

NAMESPACE = "딜리버스 DB"
TEST_QUESTIONS = [
    "쿠폰 재발급 언제 돼?",
    "배차 지연되면 어떻게 돼?",
    "최소 주문 금액이 얼마야?",
]
RRF_K = 60


def rrf(rank: int) -> float:
    return 1 / (RRF_K + rank + 1)


def build_rrf_context(results, policy_result) -> str:
    """OLD(build_context+build_policy_context 이어붙이기)와 비교하기 위한 RRF 재정렬 버전.
    개별 문서/파라미터/서술 각각을 하나의 아이템으로 놓고 RRF 순위로 재배열해 이어붙인다."""
    th = retrieval.get_thresholds()
    general_items = [r for r in results if r.final_score >= th["knowledge_min_score"]]

    items = []  # (rrf_score, text)
    for i, r in enumerate(general_items):
        items.append((rrf(i), f"[일반지식] {r.content}"))
    for i, p in enumerate(policy_result.params):
        items.append((rrf(i), f"[정책-파라미터] {p.policy_name} {p.param_name}: {p.value or ''}{p.unit or ''} ({p.condition or '조건없음'})"))
    for i, n in enumerate(policy_result.narratives):
        items.append((rrf(i), f"[정책-서술] {n.policy_name}: {n.chunk_text}"))

    items.sort(key=lambda x: x[0], reverse=True)
    return "\n\n".join(text for _, text in items)


async def main() -> None:
    embedding_service.load()
    await init_pool()
    try:
        llm = get_llm_provider()
        chat_prompt = await resolve_system_prompt()

        for question in TEST_QUESTIONS:
            print(f"\n{'=' * 70}\nQ: {question}\n{'=' * 70}")

            query_vec = await embedding_service.embed(question)
            results = await retrieval.search_knowledge(NAMESPACE, query_vec, question, 0.7, 0.3, 5)
            policy_available = await policy_search.has_policy_data(NAMESPACE)
            policy_result = policy_search.PolicySearchResult()
            if policy_available:
                policy_result = await policy_search.search_policy(NAMESPACE, question, top_k=5, query_vec=query_vec)

            old_doc_context = retrieval.build_context(results)
            policy_context = policy_search.build_policy_context(policy_result)
            old_context = f"{old_doc_context}\n\n{policy_context}" if policy_context else old_doc_context

            new_context = build_rrf_context(results, policy_result)

            print(f"\n--- OLD 컨텍스트 순서 (앞부분) ---\n{old_context[:400]}")
            print(f"\n--- NEW(RRF) 컨텍스트 순서 (앞부분) ---\n{new_context[:400]}")

            old_answer = await llm.generate_once(
                prompt=question, system=f"{chat_prompt}\n\n[참고 문서]\n{old_context}", max_tokens=500,
            )
            new_answer = await llm.generate_once(
                prompt=question, system=f"{chat_prompt}\n\n[참고 문서]\n{new_context}", max_tokens=500,
            )

            print(f"\n[OLD 답변]\n{old_answer}")
            print(f"\n[NEW(RRF) 답변]\n{new_answer}")
            print(f"\n[동일 여부] {'같음' if old_answer.strip() == new_answer.strip() else '다름'}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
