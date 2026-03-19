"""Stage 6: 쿼리 실행 — 원격 대상 DB에 SELECT 실행."""
import logging

from agents.text2sql.pipeline.safety import BlockedQueryError, validate_sql_safety
from agents.text2sql.admin import service

logger = logging.getLogger(__name__)

MAX_EXECUTION_RETRIES = 2


async def run(context: dict, namespace_id: int, stage_cfg: dict) -> dict:
    """원격 DB에 SQL을 실행하고 결과를 반환.

    Returns:
        {"columns": [...], "rows": [...], "row_count": int, "truncated": bool}
        또는 {"execute_error": str}
    """
    sql = context.get("sql", "")

    # Safety 재검증 (defense in depth)
    try:
        validate_sql_safety(sql)
    except BlockedQueryError as e:
        return {"execute_error": str(e)}

    cfg = context.get("_target_db_cfg") or await service.get_target_db_config(namespace_id)
    if not cfg:
        return {"execute_error": "대상 DB 연결 정보가 없습니다."}

    db = service.build_target_db(cfg)
    try:
        result = await db.execute_query(sql, timeout_sec=30, max_rows=1000)
        return result
    except Exception as e:
        logger.warning("쿼리 실행 실패: %s", e)
        return {"execute_error": str(e)}
