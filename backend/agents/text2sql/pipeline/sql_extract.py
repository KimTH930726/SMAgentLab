"""파이프라인 여러 스테이지(generate/fix)에서 공유하는 SQL 코드블록 추출 정규식."""
import re

SQL_CODEBLOCK_RE = re.compile(r"```sql\s*(.*?)```", re.DOTALL | re.IGNORECASE)
