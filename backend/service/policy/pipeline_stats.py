"""정책 지식 파이프라인 축적 현황 — 실험실 게이트 작업3(2026-09-15) 모니터링 뷰 재료.

"기준정보 축적 → 지식화 레이어 → 평가체계 축적 → retrieval 평가 현황"을 한 화면에서 보기
위한 집계. 평가체계/retrieval 쪽은 이미 있는 track2.list_run_history()를 그대로 재사용하고,
이 모듈은 "기준정보 축적"(policy_item/param/chunk + ref_db_column/ref_common_code 건수)만
새로 집계한다 — 지금까지 이 숫자들을 한 번에 보여주는 API가 없었다.

policy_item/ref_db_column/ref_common_code 전부 namespace_id로 격리되는 테이블이라, 다른
policy 엔드포인트(/items, /search)와 동일하게 namespace로 스코프한다 — 전체 테넌트 합산을
아무 로그인 사용자에게나 보여주면 안 된다.
"""
from __future__ import annotations

from core.database import get_conn, resolve_namespace_id


async def get_pipeline_stats(namespace: str) -> dict:
    """기준정보 축적 현황. counts는 현재 스냅샷, trend는 policy_item 생성일 기준 최근
    30일 일별 건수(정책서는 지금까지 일괄 임포트 방식이라 특정일에 몰려 찍히는 게 정상 —
    "매일 조금씩 늘어나는 그래프"를 기대하는 지표가 아니라 "언제 뭘 얼마나 넣었는지"
    이력을 보는 용도)."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return {
                "policy_item": 0, "policy_param": 0, "policy_chunk": 0,
                "ref_db_column": 0, "ref_common_code": 0, "trend": [],
            }
        item = await conn.fetchval("SELECT count(*) FROM policy_item WHERE namespace_id = $1", ns_id)
        param = await conn.fetchval(
            """
            SELECT count(*) FROM policy_param p
            JOIN policy_item i ON i.id = p.policy_item_id
            WHERE i.namespace_id = $1
            """,
            ns_id,
        )
        chunk = await conn.fetchval(
            """
            SELECT count(*) FROM policy_chunk c
            JOIN policy_item i ON i.id = c.policy_item_id
            WHERE i.namespace_id = $1
            """,
            ns_id,
        )
        ref_col = await conn.fetchval("SELECT count(*) FROM ref_db_column WHERE namespace_id = $1", ns_id)
        ref_code = await conn.fetchval("SELECT count(*) FROM ref_common_code WHERE namespace_id = $1", ns_id)
        trend_rows = await conn.fetch(
            """
            SELECT created_at::date AS day, count(*) AS n
            FROM policy_item
            WHERE namespace_id = $1 AND created_at >= NOW() - INTERVAL '30 days'
            GROUP BY 1 ORDER BY 1
            """,
            ns_id,
        )
    return {
        "policy_item": item,
        "policy_param": param,
        "policy_chunk": chunk,
        "ref_db_column": ref_col,
        "ref_common_code": ref_code,
        "trend": [{"day": r["day"].isoformat(), "count": r["n"]} for r in trend_rows],
    }
