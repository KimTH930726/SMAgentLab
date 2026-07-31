"""VOC 이메일 건별 분석 — 2단계 RAG 지식 파이프라인 (docs/email-analysis-channel-plan.md §2, §10).

기존 채팅 파이프라인(agents/knowledge_rag/agent.py)과 동일하게 용어 매핑 + 하이브리드
검색 + LLM 판단을 재사용하되, 대화 이력·SSE 스트리밍·시맨틱 캐시는 건별 배치 분석에
불필요해 제외한 단발성(single-shot) 버전이다.
"""
import logging
from typing import Optional

from agents.knowledge_rag.knowledge import retrieval
from service.llm.factory import get_llm_provider
from service.prompt.loader import get_prompt
from shared.embedding import embedding_service
from shared.json_utils import parse_json_object

logger = logging.getLogger(__name__)

# ops_prompt(func_key='email_voc_analysis_system'/'email_voc_analysis_prompt')로
# 관리자가 "시스템 설정 > 프롬프트 관리"에서 동적으로 수정 가능 — 아래는 DB에 값이
# 없을 때(마이그레이션 전 등)만 쓰이는 폴백 기본값이다.
_DEFAULT_ANALYSIS_SYSTEM = """You are a VOC(Voice of Customer) triage expert for an IT operations team.
Given an internal support email and related knowledge base excerpts, classify the issue.
Always respond with valid JSON only."""

_DEFAULT_ANALYSIS_PROMPT = """아래는 사내 VOC(문의) 이메일과, 이와 관련해 검색된 참고 지식입니다.

[이메일 제목]
{subject}

[이메일 본문]
{body}

[수신 메일함 담당 파트]
{part}

[참고 지식]
{context}

다음을 판단해 JSON으로만 답하세요:
1. category: "system_error"(시스템 오류) / "user_mistake"(사용자 실수) / "uncertain"(판단 보류) 중 하나
2. severity: "low" / "medium" / "high" / "urgent" 중 하나 — 업무 영향도와 긴급성 기준
3. mismatch_flagged: 이메일 내용이 위 "수신 메일함 담당 파트"의 업무 영역과 명백히 다르면 true, 아니면 false
4. resolution_draft: category가 system_error일 때 참고 지식 기반 해결 방안 초안(2~3문장), 아니면 null
5. reasoning: 판단 근거 요약 (1문장)

응답 형식 (JSON 객체만 반환):
{{"category": "...", "severity": "...", "mismatch_flagged": false, "resolution_draft": "...", "reasoning": "..."}}"""

_VALID_CATEGORIES = {"system_error", "user_mistake", "uncertain"}
_VALID_SEVERITIES = {"low", "medium", "high", "urgent"}


async def analyze_email(
    namespace: str,
    subject: str,
    body: str,
    *,
    part: str = "",
    user_credentials: Optional[dict] = None,
) -> dict:
    """이메일 본문을 RAG로 분석해 분류/심각도/오배치 여부/해결방안 초안을 산출한다.

    Returns:
        {"category", "severity", "mismatch_flagged", "knowledge_ref_ids",
         "resolution_draft", "reasoning", "mapped_term"}
    """
    query_text = f"{subject}\n{body}".strip()
    query_vec = await embedding_service.embed(query_text)

    glossary_match = await retrieval.map_glossary_term(namespace, query_vec)
    mapped_term = glossary_match.term if glossary_match else None
    enriched_query = f"{query_text} {mapped_term}" if mapped_term else query_text

    defaults = retrieval.get_search_defaults()
    results = await retrieval.search_knowledge(
        namespace, query_vec, enriched_query,
        defaults["default_w_vector"], defaults["default_w_keyword"],
        int(defaults["default_top_k"]),
    )
    context = retrieval.build_context(results)
    min_score = retrieval.get_thresholds()["knowledge_min_score"]
    knowledge_ref_ids = [r.id for r in results if r.final_score >= min_score]

    system_prompt = await get_prompt("email_voc_analysis_system", _DEFAULT_ANALYSIS_SYSTEM)
    prompt_template = await get_prompt("email_voc_analysis_prompt", _DEFAULT_ANALYSIS_PROMPT)
    prompt = prompt_template.format(
        subject=subject or "(제목 없음)",
        body=body,
        part=part or "(미지정)",
        context=context or "(관련 지식 없음)",
    )

    # 동시 실행 시 LLM 프로바이더가 일시적으로 실패하는 경우가 실제로 관측됨(부하 시
    # 응답 실패) — 1회 재시도로 일시적 오류는 흡수하고, 그래도 실패하면 아래에서
    # "정말 판단 불가"와 명확히 구분해 표시한다.
    parsed: dict = {}
    last_error: Optional[Exception] = None
    llm = get_llm_provider()
    for attempt in range(2):
        try:
            raw = await llm.generate_once(
                prompt=prompt, system=system_prompt, max_tokens=800,
                user_credentials=user_credentials,
            )
            parsed = parse_json_object(raw)
            last_error = None
            break
        except Exception as e:
            last_error = e
            logger.warning(
                "VOC 이메일 분석 실패 (시도 %d/2, %s): %s", attempt + 1, type(e).__name__, e,
            )

    category = parsed.get("category") if parsed.get("category") in _VALID_CATEGORIES else "uncertain"
    reasoning = parsed.get("reasoning")

    if last_error is not None:
        # LLM 호출 자체가 끝내 실패한 경우 — 이건 "판단해봤는데 애매함"이 아니라
        # "판단을 못 함"이므로, 심각도를 낮음으로 깔아뭉개면 실제 장애가 조용히
        # 묻힐 위험이 있다. 사람이 반드시 확인하도록 medium 이상으로 올려둔다.
        severity = "medium"
        reasoning = f"⚠️ LLM 호출 실패로 자동 분석을 완료하지 못했습니다({type(last_error).__name__}) — 수동 검토 필요"
    else:
        severity = parsed.get("severity") if parsed.get("severity") in _VALID_SEVERITIES else "low"

    return {
        "category": category,
        "severity": severity,
        "mismatch_flagged": bool(parsed.get("mismatch_flagged", False)),
        "knowledge_ref_ids": knowledge_ref_ids,
        "resolution_draft": parsed.get("resolution_draft"),
        "reasoning": reasoning,
        "mapped_term": mapped_term,
    }
