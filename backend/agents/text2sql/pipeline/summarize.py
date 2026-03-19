"""Stage 7: 결과 요약 — LLM 결과 요약 + 차트 추천 (기본 disabled)."""
import json
import logging
import re

from service.prompt.loader import get_prompt

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM = "You are a data analyst. Respond ONLY with valid JSON."
_DEFAULT_PROMPT = """다음 SQL 실행 결과를 분석하여 JSON으로 반환하세요.

질문: {{question}}
SQL: {{sql}}
결과 (최대 20행): {{result_preview}}
컬럼: {{columns}}

{
  "summary": "한국어 1~2문장 요약",
  "chart": null 또는 {"type": "bar|line|pie|scatter|area", "x": "컬럼명", "y": "컬럼명", "title": "차트 제목"}
}"""

_SUPPORTED_CHART_TYPES = {"bar", "line", "pie", "scatter", "area"}


def _extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {"summary": text[:200], "chart": None}


def _validate_chart(chart: dict | None, columns: list[str]) -> dict | None:
    if not chart or not isinstance(chart, dict):
        return None
    if chart.get("type") not in _SUPPORTED_CHART_TYPES:
        return None
    if chart.get("x") and chart["x"] not in columns:
        return None
    if chart.get("y") and chart["y"] not in columns:
        return None
    return chart


async def run(context: dict, llm, stage_cfg: dict) -> dict:
    """Returns: {"summary": str, "chart": dict | None}"""
    rows = context.get("rows", [])
    columns = context.get("columns", [])

    if not rows:
        return {"summary": "조회 결과가 없습니다.", "chart": None}

    # 최대 20행만 LLM에 전달
    preview = rows[:20]
    system = await get_prompt("sql2_summarize_system", _DEFAULT_SYSTEM)
    prompt_tmpl = await get_prompt("sql2_summarize", _DEFAULT_PROMPT)
    prompt = (
        prompt_tmpl
        .replace("{{question}}", context["question"])
        .replace("{{sql}}", context.get("sql", ""))
        .replace("{{result_preview}}", json.dumps(preview, ensure_ascii=False, default=str))
        .replace("{{columns}}", ", ".join(columns))
    )

    try:
        raw = await llm.generate_once(prompt=prompt, system=system, max_tokens=512, api_key=context.get("api_key"))
        parsed = _extract_json(raw)
        summary = parsed.get("summary", "")
        chart = _validate_chart(parsed.get("chart"), columns)
    except Exception as e:
        logger.warning("summarize 스테이지 실패: %s", e)
        summary = f"결과 {len(rows)}건 조회됨."
        chart = None

    return {"summary": summary, "chart": chart}
