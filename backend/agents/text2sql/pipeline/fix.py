"""Stage 5: 자동 수정 — AST 수정(무료) + LLM 재생성 (최대 2회)."""
import logging
import re

from agents.text2sql.pipeline.safety import BlockedQueryError, validate_sql_safety
from service.prompt.loader import get_prompt

logger = logging.getLogger(__name__)

MAX_RETRIES = 2

_DEFAULT_SYSTEM = "You are an expert SQL debugger. Fix the SQL based on the errors provided."
_DEFAULT_PROMPT = """다음 SQL에 오류가 있습니다. 수정하여 올바른 SQL만 반환하세요.

[원본 SQL]
{{sql}}

[오류 목록]
{{errors}}

[스키마 참고]
{{schema}}

수정된 SQL만 ```sql ... ``` 형식으로 반환하세요."""


def _extract_sql(text: str) -> str:
    m = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        inner = m.group(1).strip()
        # 주석만 있는 경우 거부
        lines = [l.strip() for l in inner.splitlines() if l.strip() and not l.strip().startswith("--")]
        if not lines:
            logger.warning("코드블록 안에 주석만 존재 — 빈 문자열 반환")
            return ""
        # SELECT 또는 WITH로 시작하지 않으면 거부
        if not re.match(r"(?i)^(SELECT|WITH)\b", lines[0]):
            logger.warning("코드블록 SQL이 SELECT/WITH로 시작하지 않음 — 거부: %s", lines[0][:80])
            return ""
        return inner
    # fallback: SELECT 또는 WITH 시작 부분 추출
    m2 = re.search(r"((?:SELECT|WITH)\s+.+)", text, re.DOTALL | re.IGNORECASE)
    if m2:
        candidate = m2.group(1).strip()
        # 산문(prose)처럼 보이면 거부
        if len(candidate) > 2000 and candidate.count("\n") > 50:
            logger.warning("fallback 추출 결과가 산문으로 판단 — 거부")
            return ""
        return candidate
    return ""


def _format_schema(schema_results: list[dict]) -> str:
    if not schema_results:
        return ""
    tables: dict[str, list] = {}
    for r in schema_results:
        tname = r["table_name"]
        if tname not in tables:
            tables[tname] = []
        tables[tname].append(f"  {r['name']} ({r['data_type']})")
    return "\n".join(
        f"Table: {t}\n" + "\n".join(cols) for t, cols in tables.items()
    )


async def run(context: dict, llm, stage_cfg: dict) -> dict:
    """Returns: {"sql": str, "fixed": bool}"""
    sql = context.get("sql", "")
    errors = context.get("validation_errors", [])
    rag = context.get("rag", {})
    schema_text = _format_schema(rag.get("schema", []))

    if not errors:
        return {"sql": sql, "fixed": False}

    system = await get_prompt("sql2_fix_system", _DEFAULT_SYSTEM)
    prompt_tmpl = await get_prompt("sql2_fix", _DEFAULT_PROMPT)

    original_sql = sql  # 원본 보존 — LLM이 쓰레기 반환해도 원본 유지
    fixed_sql = sql
    for attempt in range(1, MAX_RETRIES + 1):
        errors_text = "\n".join(f"- {e}" for e in errors)
        prompt = (
            prompt_tmpl
            .replace("{{sql}}", fixed_sql)
            .replace("{{errors}}", errors_text)
            .replace("{{schema}}", schema_text)
        )
        try:
            raw = await llm.generate_once(prompt=prompt, system=system, max_tokens=1000, api_key=context.get("api_key"))
            candidate = _extract_sql(raw)
            # 빈 SQL이면 원본으로 재시도 (쓰레기로 연쇄 오염 방지)
            if not candidate.strip():
                logger.warning("fix 결과가 빈 SQL — 원본으로 재시도")
                fixed_sql = original_sql
                continue
            validate_sql_safety(candidate)
            fixed_sql = candidate
            logger.info("fix 스테이지: %d번째 시도 성공", attempt)
            return {"sql": fixed_sql, "fixed": True}
        except BlockedQueryError as e:
            logger.warning("fix 결과가 safety 위반: %s", e)
            errors = [str(e)]
            fixed_sql = original_sql  # safety 위반 시에도 원본으로 복귀
        except Exception as e:
            logger.warning("fix 스테이지 %d번째 시도 실패: %s", attempt, e)

    return {"sql": original_sql, "fixed": False}  # 최종 실패 시 원본 반환
