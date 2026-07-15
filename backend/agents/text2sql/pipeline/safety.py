"""SQL 안전 검증 — SELECT 전용 하드코딩 차단 (우회 불가)."""
import re

import sqlparse

_BLOCKED_KEYWORDS = [
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE",
    "TRUNCATE", "GRANT", "REVOKE", "EXEC", "EXECUTE", "MERGE",
]
# 표준 DML/DDL 키워드는 아니지만 SELECT 문 안에서도 호출 가능한 파일읽기·외부접속·
# 세션제어 함수 — 키워드 블록리스트만으로는 못 잡는 구멍(pg_read_file, dblink 등으로
# 실제 사고 사례가 있는 함수들). \w*로 dblink_exec/dblink_connect 같은 계열까지 포괄.
_BLOCKED_FUNCTIONS = [
    r"PG_READ_FILE", r"PG_READ_BINARY_FILE", r"PG_LS_DIR", r"PG_STAT_FILE",
    r"LO_EXPORT", r"LO_IMPORT",
    r"DBLINK\w*",
    r"PG_TERMINATE_BACKEND", r"PG_CANCEL_BACKEND", r"PG_RELOAD_CONF",
    r"SET_CONFIG",
    r"LOAD_FILE",
    r"UTL_FILE\w*", r"UTL_HTTP\w*", r"UTL_TCP\w*", r"UTL_SMTP\w*",
    r"DBMS_LOB", r"DBMS_SCHEDULER", r"DBMS_JAVA",
    r"XP_CMDSHELL", r"OPENROWSET", r"OPENDATASOURCE", r"OPENQUERY",
]
_BLOCKED_PATTERN = re.compile(
    r"\b(" + "|".join(_BLOCKED_KEYWORDS + _BLOCKED_FUNCTIONS) + r")\b",
    re.IGNORECASE,
)
_MULTI_STMT_PATTERN = re.compile(
    r";\s*(" + "|".join(_BLOCKED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
# 단일 키워드로는 안 잡히는 다단어 위험 구문 — sqlparse가 이런 문장을 종종 UNKNOWN
# 타입으로 분류해 SELECT 전용 검사를 그냥 통과시키는 경우가 있어 별도로 막는다
_MULTI_WORD_PATTERN = re.compile(
    r"\bINTO\s+(OUTFILE|DUMPFILE)\b|\bATTACH\s+DATABASE\b|\bCOPY\b.*\bPROGRAM\b",
    re.IGNORECASE | re.DOTALL,
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

    # 4) 다단어 위험 구문 (INTO OUTFILE/DUMPFILE, ATTACH DATABASE, COPY...PROGRAM)
    if _MULTI_WORD_PATTERN.search(sql):
        raise BlockedQueryError("차단된 구문 포함 (파일 쓰기/DB 연결 관련)")
