"""정책 파라미터/서술 수정 — `review.py`(상태 전이 전용)/`browse.py`(읽기 전용)와 분리.

`policy_param`/`policy_chunk`는 지금까지 임포트 시점과 미분류 편입 시점의 INSERT만 있었지
UPDATE/DELETE는 프로젝트 전체에 한 번도 없었다(2026-09-23 리서치 확인) — 이 모듈이 그
첫 수정 기능이다. "반려하면 그냥 데이터를 버리는데?"라는 지적(2026-09-23)으로 추가 —
반려는 폐기가 아니라 "고쳐서 다시 검토받으라"는 뜻이어야 한다는 게 배경. 그래서 수정은
반려(rejected) 상태 항목에만 허용하고, 저장하면 자동으로 검토대기(pending_review)로
되돌려 재승인 기회를 준다(API 레벨에서도 강제 — 프론트가 반려 항목에만 편집 UI를 띄우는
것과 별개로, 직접 호출로 검토 절차를 우회하지 못하게 막는다).
"""
from __future__ import annotations

from typing import Optional

from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service


async def _resubmit_for_review(conn, item_id: int) -> None:
    await conn.execute(
        "UPDATE policy_item SET status = 'pending_review', updated_at = NOW() WHERE id = $1",
        item_id,
    )


async def update_param(
    namespace: str, param_id: int,
    name: str, condition: Optional[str], value: Optional[str], unit: Optional[str],
) -> str:
    """반려된 항목의 파라미터를 수정하고 검토대기로 되돌림. 반환값: 새 상태(항상 'pending_review')."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")

        row = await conn.fetchrow(
            """SELECT p.policy_item_id, i.status FROM policy_param p
               JOIN policy_item i ON i.id = p.policy_item_id
               WHERE p.id = $1 AND i.namespace_id = $2""",
            param_id, ns_id,
        )
        if row is None:
            raise ValueError(f"파라미터를 찾을 수 없습니다: param_id={param_id}")
        if row["status"] != "rejected":
            raise ValueError(f"반려(rejected) 상태의 항목만 수정할 수 있습니다(현재: {row['status']})")

        if not name.strip():
            raise ValueError("파라미터 항목명은 비워둘 수 없습니다.")
        # unit 컬럼만 VARCHAR(50) 제약이 있다(promote_segment_to_param과 동일 처리)
        unit_value = unit.strip()[:50] if unit and unit.strip() else None

        await conn.execute(
            "UPDATE policy_param SET name = $1, condition = $2, value = $3, unit = $4 WHERE id = $5",
            name.strip(), (condition or None), (value or None), unit_value, param_id,
        )
        await _resubmit_for_review(conn, row["policy_item_id"])
    return "pending_review"


async def update_narrative(namespace: str, chunk_id: int, chunk_text: str) -> str:
    """반려된 항목의 서술을 수정하고 검토대기로 되돌림. 텍스트가 곧 검색 대상이라 반드시
    재임베딩한다 — 안 하면 검색 결과가 옛 텍스트 기준으로 남는 조용한 버그가 된다.
    반환값: 새 상태(항상 'pending_review')."""
    if not chunk_text.strip():
        raise ValueError("서술 내용은 비워둘 수 없습니다.")

    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")

        row = await conn.fetchrow(
            """SELECT c.policy_item_id, i.status FROM policy_chunk c
               JOIN policy_item i ON i.id = c.policy_item_id
               WHERE c.id = $1 AND i.namespace_id = $2""",
            chunk_id, ns_id,
        )
        if row is None:
            raise ValueError(f"서술을 찾을 수 없습니다: chunk_id={chunk_id}")
        if row["status"] != "rejected":
            raise ValueError(f"반려(rejected) 상태의 항목만 수정할 수 있습니다(현재: {row['status']})")

        embedding = await embedding_service.embed(chunk_text.strip())
        await conn.execute(
            "UPDATE policy_chunk SET chunk_text = $1, embedding = $2::vector WHERE id = $3",
            chunk_text.strip(), str(embedding), chunk_id,
        )
        await _resubmit_for_review(conn, row["policy_item_id"])
    return "pending_review"
