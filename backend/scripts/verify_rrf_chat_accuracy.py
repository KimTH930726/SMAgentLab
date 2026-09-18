"""agent.py의 RRF 컨텍스트 병합(OLD: doc_context+policy_context 이어붙이기 vs NEW:
_build_rrf_context) 적용 전/후 정확도를 수치로 비교한다(2026-09-18, 재검증).

1차 검증(scripts/compare_rrf_context.py)은 "답변이 같은가/다른가"만 봐서 정성적이었다
("사실관계는 동일해 보인다"는 눈으로 읽은 판단). 이번엔 실측 근거를 남긴다:

1. 질문은 발명하지 않고 실제 ops_query_log(딜리버스 DB, status='resolved')에서 가져온다
   — 실사용 질문 분포를 반영. 여기엔 정책 축 질문이 없어서(실측: 12건 전부 일반지식/DB
   질의), 앞서 정책 데이터가 실제로 걸리는 걸 확인한 교차축 질문 3건을 추가해 일반지식
   전용/정책 포함 두 그룹을 모두 커버한다.
2. "정확도"는 채점 기준이 없어(골든셋 없음, 이 세션에서 이미 확인된 제약) 사람이
   맞다고 채점할 수 없다. 대신 검증 가능한 대리 지표를 쓴다: 병합된 목록에서 RRF
   순위 1위 항목(OLD/NEW 둘 다 같은 항목 집합을 담고 있고 순서만 다름 — 그래서 "가장
   근거가 되어야 할 항목"은 순서와 무관하게 동일하게 정의 가능)의 본문에서 숫자·코드성
   토큰(예: "20개", "DS14", "15,000원")을 뽑아 "핵심 사실"로 삼고, 그 사실이 OLD/NEW
   각 답변에 그대로 남아있는지 센다. 정책 답변은 원문 숫자를 거의 그대로 옮겨 적는다는
   게 이미 이 프로젝트에서 실측 확인된 전제라(policy/search.py의 select_cited_hit
   docstring), 순서 재배열이 사실을 누락시켰는지 보는 데 유효한 대리 지표다.

실행 (컨테이너 안):
    docker compose exec backend python scripts/verify_rrf_chat_accuracy.py
"""
import asyncio
import re
import sys

sys.path.insert(0, "/app")

from core.database import init_pool, close_pool, get_conn
from shared.embedding import embedding_service
from service.llm.factory import get_llm_provider
from service.llm.base import resolve_system_prompt
from agents.knowledge_rag.knowledge import retrieval
from service.policy import search as policy_search
from agents.knowledge_rag.agent import _build_rrf_context

NAMESPACE = "딜리버스 DB"

# 정책 축이 실제로 걸리는 것까지 함께 검증하기 위한 추가 질문(1차 검증에서 이미
# 확인된 교차축 질문 재사용) — ops_query_log엔 정책 질문이 없었음(실측)
EXTRA_CROSS_AXIS_QUESTIONS = [
    "쿠폰 재발급 언제 돼?",
    "배차 지연되면 어떻게 돼?",
    "최소 주문 금액이 얼마야?",
]

_FACT_RE = re.compile(
    r"[A-Za-z]{1,4}[0-9]{2,4}"  # DS14, X0019류 코드
    r"|[0-9]{1,3}(?:,[0-9]{3})*(?:원|개|회|분|초|%|건|일|월|년|명|시간)"  # 단위 붙은 숫자
)


def _is_hex_fragment(token: str) -> bool:
    """UUID/해시 조각 오탐 제외 — "aaa818"/"bd312"처럼 순수 16진 문자로만 된 짧은
    토큰은 실제 업무 코드가 아니라 Jira 이슈 키(uuid) 등에서 잘려나온 잡음이다
    (id=12851 "변경 이력" 문서에서 실측으로 확인됨). DS14/X0019처럼 실제 코드는
    16진수가 아닌 문자(S, X 등)를 포함해 이 필터를 통과한다."""
    return bool(re.fullmatch(r"[0-9a-fA-F]+", token)) and len(token) <= 8


def extract_facts(text: str, limit: int = 8) -> list[str]:
    seen: list[str] = []
    for m in _FACT_RE.findall(text):
        if m in seen or _is_hex_fragment(m):
            continue
        seen.append(m)
        if len(seen) >= limit:
            break
    return seen


async def get_real_questions(conn, namespace: str, limit: int = 15) -> list[str]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT q.question
        FROM ops_query_log q
        JOIN ops_namespace n ON q.namespace_id = n.id
        WHERE n.name = $1 AND q.status = 'resolved'
        ORDER BY q.question
        LIMIT $2
        """,
        namespace, limit,
    )
    return [r["question"] for r in rows]


async def main() -> None:
    embedding_service.load()
    await init_pool()
    try:
        async with get_conn() as conn:
            real_questions = await get_real_questions(conn, NAMESPACE)
        questions = real_questions + EXTRA_CROSS_AXIS_QUESTIONS

        llm = get_llm_provider()
        chat_prompt = await resolve_system_prompt()

        old_hits_total = 0
        new_hits_total = 0
        facts_total = 0
        rows_report = []

        for question in questions:
            query_vec = await embedding_service.embed(question)
            results = await retrieval.search_knowledge(NAMESPACE, query_vec, question, 0.7, 0.3, 5)
            policy_available = await policy_search.has_policy_data(NAMESPACE)
            policy_result = policy_search.PolicySearchResult()
            if policy_available:
                policy_result = await policy_search.search_policy(NAMESPACE, question, top_k=5, query_vec=query_vec)

            old_doc_context = retrieval.build_context(results)
            policy_context = policy_search.build_policy_context(policy_result)
            old_context = f"{old_doc_context}\n\n{policy_context}" if policy_context else old_doc_context
            new_context = _build_rrf_context(results, policy_result)

            if not old_context.strip():
                continue  # 컨텍스트 자체가 없으면(지식 공백) 비교 대상 아님

            # RRF 순위 1위 항목 본문에서 핵심 사실 추출 — OLD/NEW 둘 다 같은 항목 집합이라
            # "1위가 뭐여야 하는지"는 RRF 기준으로 하나로 정의됨
            th = retrieval.get_thresholds()
            relevant = [r for r in results if r.final_score >= th["knowledge_min_score"]]
            top_source_text = relevant[0].content if relevant else ""
            if policy_result.params:
                top_source_text += " " + policy_result.params[0].raw_body
            if policy_result.narratives:
                top_source_text += " " + policy_result.narratives[0].chunk_text
            facts = extract_facts(top_source_text)
            if not facts:
                continue  # 대조할 숫자/코드성 사실이 없는 질문은 이 지표로 못 봄

            old_answer = await llm.generate_once(
                prompt=question, system=f"{chat_prompt}\n\n[참고 문서]\n{old_context}", max_tokens=400,
            )
            new_answer = await llm.generate_once(
                prompt=question, system=f"{chat_prompt}\n\n[참고 문서]\n{new_context}", max_tokens=400,
            )

            old_hit = sum(1 for f in facts if f in old_answer)
            new_hit = sum(1 for f in facts if f in new_answer)
            old_hits_total += old_hit
            new_hits_total += new_hit
            facts_total += len(facts)

            rows_report.append((question, facts, old_hit, new_hit))
            print(f"Q: {question}")
            print(f"  핵심 사실({len(facts)}): {facts}")
            print(f"  OLD 포함: {old_hit}/{len(facts)}   NEW 포함: {new_hit}/{len(facts)}")
            if old_hit != new_hit:
                print(f"  ⚠ 차이 발생 — OLD답변: {old_answer[:150]!r}")
                print(f"             NEW답변: {new_answer[:150]!r}")
            print()

        print("=" * 60)
        print(f"검증 질문 수: {len(rows_report)} (사실 대조 불가 질문 제외)")
        if facts_total:
            print(f"OLD 핵심사실 포함률: {old_hits_total}/{facts_total} ({100*old_hits_total/facts_total:.1f}%)")
            print(f"NEW 핵심사실 포함률: {new_hits_total}/{facts_total} ({100*new_hits_total/facts_total:.1f}%)")
        else:
            print("대조 가능한 핵심 사실이 없었음")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
