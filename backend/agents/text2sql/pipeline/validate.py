"""Stage 4: SQL 검증 — safety + sqlglot AST 검증."""
import logging
import re

from agents.text2sql.pipeline.safety import BlockedQueryError, validate_sql_safety

logger = logging.getLogger(__name__)

try:
    import sqlglot
    import sqlglot.errors
    _SQLGLOT_AVAILABLE = True
except ImportError:
    _SQLGLOT_AVAILABLE = False


_DIALECT_MAP = {
    "postgresql": "postgres",
    "mysql": "mysql",
    "oracle": "oracle",
    "sqlite": "sqlite",
}


def _ast_validate(sql: str, db_type: str) -> list[str]:
    if not _SQLGLOT_AVAILABLE:
        return []
    errors = []
    dialect = _DIALECT_MAP.get(db_type.lower(), "")
    try:
        stmts = sqlglot.parse(sql, dialect=dialect, error_level=sqlglot.errors.ErrorLevel.RAISE)
        if not stmts:
            errors.append("SQL 파싱 실패: 빈 결과")
    except sqlglot.errors.ParseError as e:
        errors.append(f"SQL 문법 오류: {e}")
    except Exception as e:
        errors.append(f"AST 검증 오류: {e}")
    return errors


def _schema_validate(sql: str, schema_results: list[dict]) -> list[str]:
    """스키마에 없는 테이블/컬럼 참조 검사 (간이 regex 기반)."""
    if not schema_results:
        return []
    known_tables = {r["table_name"].lower() for r in schema_results}
    sql_lower = sql.lower()
    errors = []
    # FROM / JOIN 뒤 테이블명 추출
    table_refs = re.findall(r"(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql_lower)
    for tref in table_refs:
        if tref not in known_tables and tref not in ("select", "where", "on"):
            errors.append(f"스키마에 없는 테이블 참조: {tref}")
    return errors


async def run(context: dict, db_type: str, stage_cfg: dict) -> dict:
    """Returns: {"valid": bool, "errors": list[str]}"""
    sql = context.get("sql", "")
    errors: list[str] = []

    # 1) Safety 검증 (하드코딩, 우회 불가)
    try:
        validate_sql_safety(sql)
    except BlockedQueryError as e:
        return {"valid": False, "errors": [str(e)]}

    # 2) AST 검증
    errors.extend(_ast_validate(sql, db_type))

    # 3) 스키마 참조 검증
    rag = context.get("rag", {})
    errors.extend(_schema_validate(sql, rag.get("schema", [])))

    return {"valid": len(errors) == 0, "errors": errors}
