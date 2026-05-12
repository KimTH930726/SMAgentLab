"""자동 Q&A 생성 — 지식 청크에서 예상 질문-답변 쌍을 LLM으로 생성."""
import logging
from typing import Optional

from agents.knowledge_rag.ingestion.utils import parse_json_array

logger = logging.getLogger(__name__)

_QA_SYSTEM = """You are a Q&A pair generator for an IT operations knowledge base.
Generate realistic question-answer pairs that users would actually ask.
Questions must be in Korean. Return valid JSON only."""

_QA_PROMPT = """아래 지식 내용을 읽고, 이 내용으로 답변할 수 있는 질문-답변 쌍을 생성해주세요.

[지식 내용]
{content}

규칙:
- 실제 IT 운영 담당자가 물어볼 법한 자연스러운 질문
- 답변은 제공된 내용을 기반으로 정확하게 (없는 내용 추가 금지)
- 질문은 한국어, 1~2문장
- 답변은 한국어, 2~5문장
- 최소 1개, 최대 3개 생성

응답 형식 (JSON 배열만 반환):
[{{"question": "질문 내용", "answer": "답변 내용"}}, ...]"""


async def generate_qa_pairs(
    content: str,
    llm,
    *,
    user_credentials: Optional[dict] = None,
    max_pairs: int = 3,
) -> list[dict]:
    """지식 청크에서 Q&A 쌍 생성.

    Returns:
        [{"question": str, "answer": str}, ...]
    """
    if not content.strip() or len(content.strip()) < 50:
        return []

    # 토큰 절약: 청크 앞부분 2000자만
    sample = content[:2000]
    prompt = _QA_PROMPT.format(content=sample)

    try:
        raw = await llm.generate_once(
            prompt=prompt,
            system=_QA_SYSTEM,
            max_tokens=1500,
            user_credentials=user_credentials,
        )
        pairs = parse_json_array(raw)

        # 유효성 검증 + max_pairs 제한
        valid = []
        for p in pairs:
            q = p.get("question", "").strip()
            a = p.get("answer", "").strip()
            if q and a and len(q) >= 5 and len(a) >= 10:
                valid.append({"question": q, "answer": a})
            if len(valid) >= max_pairs:
                break

        logger.info("Q&A 생성: %d쌍 (유효 %d / 원본 %d)", len(valid), len(valid), len(pairs))
        return valid
    except Exception as e:
        logger.warning("Q&A 생성 실패: %s", e)
        return []


async def bulk_generate_qa(
    chunks: list[dict],
    llm,
    *,
    user_credentials: Optional[dict] = None,
    max_pairs_per_chunk: int = 2,
) -> list[dict]:
    """여러 청크에 대해 Q&A 일괄 생성.

    Args:
        chunks: [{"idx": int, "content": str}, ...]
    Returns:
        [{"chunk_idx": int, "question": str, "answer": str}, ...]
    """
    all_pairs: list[dict] = []
    for chunk in chunks:
        pairs = await generate_qa_pairs(
            chunk["content"], llm,
            user_credentials=user_credentials,
            max_pairs=max_pairs_per_chunk,
        )
        for p in pairs:
            all_pairs.append({
                "chunk_idx": chunk["idx"],
                "question": p["question"],
                "answer": p["answer"],
            })

    logger.info("벌크 Q&A 생성 완료: %d개 청크 → %d쌍", len(chunks), len(all_pairs))
    return all_pairs


