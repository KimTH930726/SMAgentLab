"""데이터 보관 정책 — 30일 자동 정리 (2026-07-30 확정).

ops_email_analysis: 폴링 재조회 윈도우(email_lookback_days, 기본 7일)보다 넉넉히
긴 30일만 있으면 중복 방지(source_message_id UNIQUE) 목적엔 충분하다 — 그보다
오래된 메일은 Graph API가 date_from~date_to 범위 밖이라 다시 안 가져오므로, dedup
키로서의 가치가 이미 없어진 상태다. 장기 감사·컴플라이언스 보관이 필요해지면
그건 별도 비즈니스 결정으로 재검토한다(이번 결정은 딱 "동작에 필요한 최소" 기준).

ops_email_poll_cycle: 순수 운영 모니터링 로그라 마찬가지로 30일이면 충분.

호출 주체(scheduler.py)가 하루 1회로 실행 빈도를 제한한다 — 이 모듈 자체는
"몇 번을 불러도 안전한" 멱등 정리 함수만 제공한다.
"""
import logging
from datetime import timedelta

from core.database import get_conn

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30


def _parse_delete_count(result: str) -> int:
    """asyncpg execute()는 "DELETE <n>" 형태의 커맨드 태그 문자열을 반환한다."""
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError):
        return 0


async def cleanup_old_records() -> dict:
    """RETENTION_DAYS보다 오래된 분석 기록/사이클 로그를 삭제한다. 여러 번 호출해도 안전."""
    cutoff = timedelta(days=RETENTION_DAYS)
    async with get_conn() as conn:
        analysis_result = await conn.execute(
            "DELETE FROM ops_email_analysis WHERE created_at < NOW() - $1::interval", cutoff,
        )
        cycle_result = await conn.execute(
            "DELETE FROM ops_email_poll_cycle WHERE started_at < NOW() - $1::interval", cutoff,
        )
    deleted_analysis = _parse_delete_count(analysis_result)
    deleted_cycles = _parse_delete_count(cycle_result)
    if deleted_analysis or deleted_cycles:
        logger.info(
            "[VOC 보관정책] %d일 초과 데이터 정리 완료 — 분석기록 %d건, 사이클로그 %d건 삭제",
            RETENTION_DAYS, deleted_analysis, deleted_cycles,
        )
    return {"deleted_analysis": deleted_analysis, "deleted_cycles": deleted_cycles}
