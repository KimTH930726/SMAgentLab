"""SQL 안전 검증 — SELECT 전용 하드코딩 차단 (우회 불가)."""
import re

import sqlparse

_BLOCKED_KEYWORDS = [
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE",
    "TRUNCATE", "GRANT", "REVOKE", "EXEC", "EXECUTE", "MERGE",
]
_BLOCKED_PATTERN = re.compile(
    r"\b(" + "|".join(_BLOCKED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_MULTI_STMT_PATTERN = re.compile(
    r";\s*(" + "|".join(_BLOCKED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


class BlockedQueryError(ValueError):
    """허용되지 않는 SQL 쿼리."""


def validate_sql_safety(sql: str) -> None:
    """SQL이 SELECT 전용인지 검증. 위반 시 BlockedQueryError 발생."""
    if not sql or not sql.strip():
        raise BlockedQueryError("SQL이 비어 있습니다.")

    # 0) 주석 제거 후 실제 SQL이 있는지 확인
    stripped = sqlparse.format(sql, strip_comments=True).strip()
    if not stripped:
        raise BlockedQueryError("빈 SQL입니다.")

    # 1) sqlparse로 statement type 확인
    statements = sqlparse.parse(sql.strip())
    for stmt in statements:
        stype = stmt.get_type()
        if stype and stype not in ("SELECT", "UNKNOWN", None):
            raise BlockedQueryError(f"허용되지 않는 쿼리 타입: {stype}")

    # 2) 차단 키워드 검색
    if _BLOCKED_PATTERN.search(sql):
        match = _BLOCKED_PATTERN.search(sql)
        raise BlockedQueryError(f"차단된 키워드 포함: {match.group()}")

    # 3) 세미콜론 뒤 위험 패턴
    if _MULTI_STMT_PATTERN.search(sql):
        raise BlockedQueryError("다중 구문에 위험 키워드 포함")
