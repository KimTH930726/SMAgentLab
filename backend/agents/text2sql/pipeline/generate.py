"""Stage 3: SQL 생성 — CoT + DB 방언별 규칙."""
import logging
import re

from service.prompt.loader import get_prompt

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM = "You are an expert SQL generator. Think step-by-step, then return the SQL."

_DIALECT_RULES = {
    "postgresql": "PostgreSQL 규칙: ILIKE(대소문자 무시), DATE_TRUNC, ::type 캐스팅, STRING_AGG, FILTER 사용.",
    "mysql": "MySQL 규칙: 백틱으로 예약어 이스케이프, DATE_FORMAT, IFNULL, GROUP_CONCAT 사용.",
    "oracle": "Oracle 규칙: NVL, TO_DATE, FETCH FIRST N ROWS ONLY (LIMIT 금지), LISTAGG 사용.",
    "sqlite": "SQLite 규칙: strftime, IFNULL, LIKE는 대소문자 무시.",
}

_COT_INSTRUCTIONS = {
    "simple": "관련 테이블을 식별하고 SQL을 작성하세요.",
    "moderate": "1) 관련 테이블 파악 → 2) JOIN 조건 결정 → 3) SQL 작성",
    "complex": "1) 모든 관련 테이블 → 2) JOIN 전략 → 3) 서브쿼리/CTE 필요 여부 → 4) 윈도우 함수 → 5) 완성 SQL",
}

_DEFAULT_PROMPT = """다음 정보를 바탕으로 {{db_type}} SQL 쿼리를 작성하세요.

[질문]
{{question}}

[스키마]
{{schema}}

[테이블 관계]
{{relations}}

[유사 용어]
{{synonyms}}

[SQL 예제]
{{fewshots}}

[이전 대화]
{{history}}

난이도: {{difficulty}}
{{cot_instruction}}

DB 방언 규칙:
{{dialect_rules}}

<reasoning>
(단계별 사고 과정)
</reasoning>

```sql
-- 최종 SQL
```"""


def _format_schema(schema_results: list[dict]) -> str:
    tables: dict[str, list] = {}
    for r in schema_results:
        tname = r["table_name"]
        if tname not in tables:
            tables[tname] = []
        pk_mark = " (PK)" if r.get("is_pk") else ""
        fk_mark = f" (FK -> {r['fk_reference']})" if r.get("fk_reference") else ""
        desc = f": {r['description']}" if r.get("description") else ""
        tables[tname].append(f"  - {r['name']} {r['data_type']}{pk_mark}{fk_mark}{desc}")
    lines = []
    for tname, cols in tables.items():
        lines.append(f"Table: {tname}")
        lines.extend(cols)
    return "\n".join(lines)


def _format_relations(relations: list[dict]) -> str:
    if not relations:
        return "(없음)"
    return "\n".join(
        f"JOIN {r['to_table']} ON {r['to_table']}.{r['to_col']} = {r['from_table']}.{r['from_col']}"
        + (f" -- {r['description']}" if r.get("description") else "")
        for r in relations
    )


def _format_synonyms(synonyms: list[dict]) -> str:
    if not synonyms:
        return "(없음)"
    return "\n".join(f"- '{s['term']}' → {s['target']}" for s in synonyms)


def _format_fewshots(fewshots: list[dict]) -> str:
    if not fewshots:
        return "(없음)"
    parts = []
    for f in fewshots:
        parts.append(f"Q: {f['question']}\nSQL: {f['sql']}")
    return "\n\n".join(parts)


def _extract_sql_and_reasoning(text: str) -> tuple[str, str]:
    reasoning = ""
    m = re.search(r"<reasoning>(.*?)</reasoning>", text, re.DOTALL)
    if m:
        reasoning = m.group(1).strip()

    sql = ""
    m2 = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m2:
        sql = m2.group(1).strip()
    else:
        # 코드블록 없이 SELECT로 시작하는 경우
        m3 = re.search(r"(SELECT\s+.+)", text, re.DOTALL | re.IGNORECASE)
        if m3:
            sql = m3.group(1).strip()
    return sql, reasoning


async def run(context: dict, llm, relations: list[dict], db_type: str, stage_cfg: dict) -> dict:
    """Returns: {"sql": str, "reasoning": str}"""
    rag = context.get("rag", {})
    parsed = context.get("parsed", {})
    difficulty = parsed.get("difficulty", "simple")

    schema_text = _format_schema(rag.get("schema", []))
    relations_text = _format_relations(relations)
    synonyms_text = _format_synonyms(rag.get("synonyms", []))
    fewshots_text = _format_fewshots(rag.get("fewshots", []))
    dialect = _DIALECT_RULES.get(db_type.lower(), "")
    cot = _COT_INSTRUCTIONS.get(difficulty, _COT_INSTRUCTIONS["simple"])

    system = await get_prompt("sql2_generate_system", _DEFAULT_SYSTEM)
    prompt_tmpl = await get_prompt("sql2_generate", _DEFAULT_PROMPT)
    prompt = (
        prompt_tmpl
        .replace("{{question}}", context["question"])
        .replace("{{schema}}", schema_text)
        .replace("{{relations}}", relations_text)
        .replace("{{synonyms}}", synonyms_text)
        .replace("{{fewshots}}", fewshots_text)
        .replace("{{history}}", context.get("history", ""))
        .replace("{{difficulty}}", difficulty)
        .replace("{{cot_instruction}}", cot)
        .replace("{{dialect_rules}}", dialect)
        .replace("{{db_type}}", db_type)
        .replace("{{enriched_schema}}", "")
    )

    try:
        raw = await llm.generate_once(prompt=prompt, system=system, max_tokens=1500, api_key=context.get("api_key"))
        sql, reasoning = _extract_sql_and_reasoning(raw)
    except Exception as e:
        logger.error("generate 스테이지 실패: %s", e)
        return {"sql": "", "reasoning": ""}

    return {"sql": sql, "reasoning": reasoning}
