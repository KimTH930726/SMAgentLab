"""LLM 기반 용어 추출(업무구분 자동 태깅 auto_tag_chunks는 10/22 업무구분 폐지로 삭제)."""
import logging
from typing import Optional

from shared.json_utils import parse_json_array

_parse_json_array = parse_json_array  # 테스트 호환성 유지

logger = logging.getLogger(__name__)

_GLOSSARY_SYSTEM = """You are a domain terminology expert for IT operations.
Extract key business terms from the text. Return valid JSON only."""

_GLOSSARY_PROMPT = """아래 텍스트에서 도메인 전문 용어를 추출해주세요.
일반적인 단어가 아닌, 이 조직/시스템에서 특별한 의미를 가진 용어만 추출하세요.

[텍스트]
{text}

[이미 등록된 용어 (중복 제외)]
{existing_terms}

응답 형식 (JSON 배열만 반환):
[{{"term": "용어", "description": "설명 (20자 내외)"}}, ...]"""


async def extract_glossary_terms(
    text: str,
    existing_terms: list[str],
    llm,
    *,
    user_credentials: Optional[dict] = None,
) -> list[dict]:
    """LLM으로 텍스트에서 도메인 용어 추출.

    Returns:
        [{"term": str, "description": str}, ...]
    """
    if not text.strip():
        return []

    # 텍스트 앞부분만 (토큰 절약)
    sample = text[:3000]
    existing_text = ", ".join(existing_terms[:50]) if existing_terms else "(없음)"

    prompt = _GLOSSARY_PROMPT.format(text=sample, existing_terms=existing_text)

    try:
        raw = await llm.generate_once(
            prompt=prompt,
            system=_GLOSSARY_SYSTEM,
            max_tokens=1000,
            user_credentials=user_credentials,
        )
        terms = parse_json_array(raw)
        # 기존 용어와 중복 제거
        existing_lower = {t.lower() for t in existing_terms}
        filtered = [t for t in terms if t.get("term", "").lower() not in existing_lower]
        logger.info("용어 추출: %d개 (중복 제외 후 %d개)", len(terms), len(filtered))
        return filtered
    except Exception as e:
        logger.warning("용어 추출 실패: %s", e)
        return []


