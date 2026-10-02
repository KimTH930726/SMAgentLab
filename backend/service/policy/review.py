"""정책 항목 승인/반려 — 상태 전이 전용.

`browse.py`(읽기 전용, item→param/chunk 조회)와 분리한다. 지금까지 `policy_item`은
임포트 시 무조건 `status='pending_review'`로 쌓이기만 하고 그걸 바꾸는 코드가 프로젝트
전체에 없었다(재업로드로 대체된 이전 버전을 `deprecated`로 바꾸는 것뿐, `service.py`)
— 사용자 지적(2026-09-23)으로 실제 승인/반려 액션을 추가한다.

반려는 재업로드로 대체된 것과 다른 원인이라 `deprecated`를 재사용하지 않고 별도
`rejected` 상태를 쓴다 — 나중에 "왜 검색에 안 나오지"를 추적할 때 "새 버전이 와서"와
"사람이 틀렸다고 판단해서"가 같은 값으로 뭉쳐 있으면 원인을 못 가른다.

`policy_item.reviewed_at`/`reviewed_by`는 테이블 생성 시점부터 있었지만(`main.py`) 지금까지
어디서도 채워진 적 없는 죽은 컬럼이었다 — 바로 이 용도로 만들어둔 것이라 여기서 처음 채운다.
"""
from __future__ import annotations

from core.database import get_conn, resolve_namespace_id
from service.policy import auto_review, risk


async def _transition(namespace: str, item_id: int, reviewer_id: int, new_status: str) -> dict:
    """상태 전이 + 결정 이력(2026-10-01). 상태 확인과 변경 사이에 다른 결정(자동 통과 포함)이 끼지 않도록
    한 트랜잭션에서 행을 잠근다. 사람 결정은 review_source='human'으로 자동 통과와 구분하고, 결정 시점의
    위험도·근거를 policy_review_log에 남긴다(자동 승인 기준 조정용)."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")

        async with conn.transaction():
            row = await conn.fetchrow(
                f"""SELECT p.id, p.logical_id, p.namespace_id, p.status, p.parse_status, p.review_sample, p.review_rule,
                           {risk.RISK_FEATURES_SQL}
                    FROM policy_item p WHERE p.id = $1 AND p.namespace_id = $2 FOR UPDATE OF p""",
                item_id, ns_id,
            )
            if row is None:
                raise ValueError(f"정책 항목을 찾을 수 없습니다: item_id={item_id}")
            if row["status"] != "pending_review":
                raise ValueError(f"검토 대기 상태가 아닌 항목은 처리할 수 없습니다(현재: {row['status']})")

            await conn.execute(
                """UPDATE policy_item SET status = $1, reviewed_at = NOW(), reviewed_by = $2, review_source = 'human',
                   updated_at = NOW() WHERE id = $3""",
                new_status, reviewer_id, item_id,
            )
            rk = risk.classify_row(row)
            row = dict(row)
            snap = {"id": row["id"], "logical_id": row["logical_id"], "namespace_id": row["namespace_id"],
                    "rule_key": row["review_rule"] if row["review_sample"] else rk.rule_key,
                    "risk_level": rk.level, "risk_reasons": rk.reasons}
            action = "approved" if new_status == "active" else "rejected"
            await auto_review.log(conn, [snap], action, actor_id=reviewer_id,
                                  note="자동 통과 표본 확인" if row["review_sample"] else None)
            paused = False
            if action == "rejected":
                paused = await auto_review.on_human_reject(conn, row, rk, reviewer_id)
    return {"rule_paused": paused}


async def approve_item(namespace: str, item_id: int, reviewer_id: int) -> dict:
    """검토 대기(pending_review) 항목을 승인 — status='active'."""
    return await _transition(namespace, item_id, reviewer_id, "active")


async def reject_item(namespace: str, item_id: int, reviewer_id: int) -> dict:
    """검토 대기(pending_review) 항목을 반려 — status='rejected'. 자동 통과 표본이었다면 그 규칙을 멈춘다."""
    return await _transition(namespace, item_id, reviewer_id, "rejected")
