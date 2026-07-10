"""Analyzer Agent — LLM 기반 문서 분석 + 청킹 전략 자동 결정."""
import logging
from typing import Optional

from shared.json_utils import parse_json_object

logger = logging.getLogger(__name__)

_ANALYZER_SYSTEM = """You are a document analysis expert for an enterprise knowledge base.
Analyze the document structure, type, and domain. Return valid JSON only."""

_ANALYZER_PROMPT = """아래 문서의 처음과 끝 부분을 분석하여 메타데이터를 추출해주세요.

[문서 앞부분 (최대 3000자)]
{head}

[문서 끝부분 (최대 500자)]
{tail}

다음 JSON 형식으로 응답하세요:
{{
  "doc_type": "operation_manual | troubleshooting_guide | api_doc | tabular_data | meeting_notes | faq | mixed",
  "domain": "도메인/서브도메인 (예: IT운영/쿠폰시스템)",
  "structure": "hierarchical_sections | flat_paragraphs | table_rows | qa_pairs | mixed",
  "has_tables": true/false,
  "has_code_blocks": true/false,
  "suggested_categories": ["카테고리1", "카테고리2"],
  "key_terms": [
    {{"term": "용어", "description": "간단 설명"}}
  ],
  "priority_score": 0.0~1.0,
  "chunk_strategy": "section | paragraph | fixed | auto",
  "estimated_chunks": 예상 청크 수
}}

규칙:
- chunk_strategy는 반드시 section, paragraph, fixed, auto 중 하나
- hierarchical_sections 구조면 section, flat_paragraphs면 paragraph 추천
- priority_score: 장애 대응/핵심 프로세스는 0.8+, 일반 참고자료는 0.3~0.5
- key_terms: 이 조직/시스템에서 특별한 의미를 가진 용어만 (일반 단어 제외)
- suggested_categories: 1~3개"""

# doc_type → chunk_strategy 기본 매핑 (LLM 응답이 부적절할 때 fallback)
_STRATEGY_MAP = {
    "operation_manual": "section",
    "troubleshooting_guide": "section",
    "api_doc": "section",
    "tabular_data": "paragraph",
    "meeting_notes": "paragraph",
    "faq": "paragraph",
    "mixed": "auto",
}


async def analyze_document(
    raw_text: str,
    llm,
    *,
    user_credentials: Optional[dict] = None,
) -> dict:
    """LLM으로 문서를 분석하여 최적 청킹 전략 + 메타데이터 반환.

    Returns:
        {
            "doc_type": str,
            "domain": str,
            "structure": str,
            "has_tables": bool,
            "has_code_blocks": bool,
            "suggested_categories": list[str],
            "key_terms": list[dict],
            "priority_score": float,
            "chunk_strategy": str,
            "estimated_chunks": int,
        }
    """
    if not raw_text.strip():
        return _default_result()

    head = raw_text[:3000]
    tail = raw_text[-500:] if len(raw_text) > 3500 else ""

    prompt = _ANALYZER_PROMPT.format(head=head, tail=tail)

    try:
        raw_response = await llm.generate_once(
            prompt=prompt,
            system=_ANALYZER_SYSTEM,
            max_tokens=1000,
            user_credentials=user_credentials,
        )
        result = parse_json_object(raw_response)
        result = _validate_and_normalize(result)
        logger.info("Analyzer 결과: doc_type=%s, strategy=%s, estimated=%d",
                     result["doc_type"], result["chunk_strategy"], result["estimated_chunks"])
        return result
    except Exception as e:
        logger.warning("Analyzer Agent 실패 (fallback 사용): %s", e)
        return _default_result()


def _default_result() -> dict:
    return {
        "doc_type": "mixed",
        "domain": "",
        "structure": "mixed",
        "has_tables": False,
        "has_code_blocks": False,
        "suggested_categories": [],
        "key_terms": [],
        "priority_score": 0.5,
        "chunk_strategy": "auto",
        "estimated_chunks": 0,
    }


def _validate_and_normalize(result: dict) -> dict:
    """LLM 응답을 정규화 + 유효하지 않은 값 보정."""
    valid_strategies = {"section", "paragraph", "fixed", "auto"}
    strategy = result.get("chunk_strategy", "auto")
    if strategy not in valid_strategies:
        # doc_type 기반 fallback
        doc_type = result.get("doc_type", "mixed")
        strategy = _STRATEGY_MAP.get(doc_type, "auto")
        result["chunk_strategy"] = strategy

    # priority_score 범위 보정
    score = result.get("priority_score", 0.5)
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.5
    result["priority_score"] = max(0.0, min(1.0, score))

    # estimated_chunks 보정
    try:
        result["estimated_chunks"] = max(0, int(result.get("estimated_chunks", 0)))
    except (TypeError, ValueError):
        result["estimated_chunks"] = 0

    # 필수 필드 기본값
    result.setdefault("doc_type", "mixed")
    result.setdefault("domain", "")
    result.setdefault("structure", "mixed")
    result.setdefault("has_tables", False)
    result.setdefault("has_code_blocks", False)
    result.setdefault("suggested_categories", [])
    result.setdefault("key_terms", [])

    return result


_parse_json_object = parse_json_object  # 테스트 호환성 유지 (공개 import용)
