"""Stage 1: 질문 분석 — intent / difficulty / entities 추출."""
import json
import logging
import re

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM = "You are a query parser for a Text-to-SQL system. Always respond with valid JSON."

_DEFAULT_PROMPT = """다음 사용자 질문을 분석하여 JSON으로 반환하세요.

질문: {{question}}

반환 형식:
{
  "intent": "simple_select|aggregation|join|subquery|window_function|cte",
  "difficulty": "simple|moderate|complex",
  "entities": ["언급된 테이블/컬럼명 후보"],
  "conditions": [{"type": "date|filter", "column": "컬럼명", "value": "값"}],
  "aggregation": "집계 표현식 (없으면 null)",
  "keywords": ["핵심 키워드"]
}"""


def _extract_json(text: str) -> dict:
    # 마크다운 코드블록 제거
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # { ... } 구간 추출
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {"intent": "simple_select", "difficulty": "simple", "entities": [], "keywords": []}


async def run(context: dict, llm, stage_cfg: dict) -> dict:
    """Returns: {"parsed": dict}"""
    question = context["question"]
    system = stage_cfg.get("system_prompt") or _DEFAULT_SYSTEM
    prompt_tmpl = stage_cfg.get("prompt") or _DEFAULT_PROMPT
    prompt = prompt_tmpl.replace("{{question}}", question)
    try:
        raw = await llm.generate_once(prompt=prompt, system=system, max_tokens=512, api_key=context.get("api_key"))
        parsed = _extract_json(raw)
    except Exception as e:
        logger.warning("parse 스테이지 실패: %s", e)
        parsed = {"intent": "simple_select", "difficulty": "simple", "entities": [], "keywords": []}
    return {"parsed": parsed}
