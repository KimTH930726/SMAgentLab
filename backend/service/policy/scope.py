"""채팅의 정책서 범위 — 질문한 파트에서 정책서(정책·참고표·개요·용어)를 어느 파트까지 찾을지 (2026-10-20, policy-v0-design.md §6).

결정(사용자 10/20): 지식은 지금처럼 선택한 파트만, 정책서만 넓힌다 — 정책서는 모든 업무가 참조하는 공통 기준이고,
범위를 전체로 넓혀도 정확도가 그대로였다(v0 실험: 사람 26문항 1위 85% = 파트 범위와 같음). 지식은 컨플루언스 92%가 검증된 경로라 그대로.
  - 정책 파트를 골랐으면 → 그 파트만
  - 업무 파트를 골랐으면 → "같은 업무"로 연결된 정책 파트(ops_link) / 연결이 없으면 정책 파트 전부
  - 업무 파트에 옛 방식(엑셀) 정책이 살아 있으면 그 파트도(보관된 건 검색 대상이 아니라 자동으로 빠짐)
정책 데이터가 있는 파트만 돌려준다(빈 파트로 쿼리를 늘리지 않게).
테스트용 정책 파트(kind='policy_test', E2E 임시 파트)는 그 파트를 직접 고를 때만 — "정책 파트 전부"에 넣으면 테스트 중 실제 채팅·측정에
가짜 정책이 섞인다(리뷰 10/20 M1).
캐시하지 않는다 — 작은 쿼리 하나(질문당 ~1ms)라 이득이 작고, 파트를 지웠다 다시 만들면 옛 id를 가리켰다(리뷰 10/20 L6).
"""
from __future__ import annotations

POLICY_KINDS = ("policy", "policy_test")

_SQL = """
WITH me AS (SELECT id, kind FROM ops_namespace WHERE name = $1),
linked AS (
    SELECT n.id FROM ops_link l, me, ops_namespace n
    WHERE l.relation = 'same_work' AND l.status = 'active' AND l.from_type = 'part' AND l.to_type = 'part'
      AND n.kind = 'policy'
      AND ((l.from_key = me.id::text AND n.id::text = l.to_key) OR (l.to_key = me.id::text AND n.id::text = l.from_key))
),
cand AS (
    SELECT me.id, 0 AS ord FROM me                                     -- 고른 파트 자신(정책 파트이거나 옛 방식 정책이 있는 업무 파트)
    UNION ALL
    SELECT id, 1 FROM linked WHERE (SELECT kind FROM me) NOT IN ('policy', 'policy_test')
    UNION ALL
    SELECT n.id, 2 FROM ops_namespace n
    WHERE n.kind = 'policy' AND (SELECT kind FROM me) NOT IN ('policy', 'policy_test') AND NOT EXISTS (SELECT 1 FROM linked)
)
SELECT c.id FROM cand c
WHERE EXISTS (SELECT 1 FROM policy_item i WHERE i.namespace_id = c.id AND i.status NOT IN ('deprecated', 'rejected'))
GROUP BY c.id ORDER BY min(c.ord), c.id
"""


async def policy_namespace_ids(conn, namespace: str) -> list[int]:
    return [r["id"] for r in await conn.fetch(_SQL, namespace)]
