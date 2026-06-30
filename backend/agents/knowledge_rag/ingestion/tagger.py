"""LLM 기반 자동 메타데이터 태깅 + 용어 추출."""
import logging
from typing import Optional

from agents.knowledge_rag.ingestion.utils import parse_json_array

_parse_json_array = parse_json_array  # 테스트 호환성 유지

logger = logging.getLogger(__name__)

_TAGGER_SYSTEM = """You are a metadata extraction expert for an IT operations knowledge base.
Analyze the given text chunks and extract metadata in JSON format.
Always respond with valid JSON only."""

_TAGGER_PROMPT = """아래 텍스트 청크들의 메타데이터를 추출해주세요.

[텍스트 청크 목록]
{chunks_text}

[사용 가능한 카테고리]
{categories}

각 청크에 대해 다음을 추출하세요:
1. category: 위 카테고리 중 하나 (해당 없으면 null)
2. container_name: 시스템명/컨테이너명 (텍스트에 언급된 경우만, 없으면 null)
3. priority_score: 0.0~1.0 (업무 중요도 — 장애 대응/핵심 프로세스는 높게)

응답 형식 (JSON 배열만 반환):
[{{"idx": 0, "category": "카테고리", "container_name": "시스템명", "priority_score": 0.7}}, ...]"""

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


async def auto_tag_chunks(
    chunks: list[dict],
    categories: list[str],
    llm,
    *,
    user_credentials: Optional[dict] = None,
) -> list[dict]:
    """LLM으로 청크들의 카테고리, 컨테이너명, 중요도를 자동 태깅.

    Args:
        chunks: [{"idx": int, "text": str}, ...]
        categories: 사용 가능한 카테고리 목록
        llm: LLM provider instance
    Returns:
        [{"idx": 0, "category": str|None, "container_name": str|None, "priority_score": float}, ...]
    """
    if not chunks:
        return []

    # 청크 텍스트 요약 (토큰 절약 — 각 청크 처음 300자)
    chunks_text = "\n\n".join(
        f"[청크 {c['idx']}]\n{c['text'][:600]}{'...' if len(c['text']) > 600 else ''}"
        for c in chunks
    )
    categories_text = ", ".join(categories) if categories else "(미정의 — null 반환)"

    prompt = _TAGGER_PROMPT.format(chunks_text=chunks_text, categories=categories_text)

    try:
        raw = await llm.generate_once(
            prompt=prompt,
            system=_TAGGER_SYSTEM,
            max_tokens=2000,
            user_credentials=user_credentials,
        )
        result = parse_json_array(raw)
        logger.info("자동 태깅 완료: %d개 청크", len(result))
        return result
    except Exception as e:
        logger.warning("자동 태깅 실패 (폴백: 빈 태그): %s", e)
        return [{"idx": c["idx"], "category": None, "container_name": None, "priority_score": 0.5} for c in chunks]


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


