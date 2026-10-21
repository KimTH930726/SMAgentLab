"""채팅의 정책 범위 — 질문한 업무 영역에서 정책서(정책·분류 목록·용어)를 어느 영역까지 찾을지 (2026-10-20, policy-v0-design.md §6).

지식 문서는 지금처럼 고른 업무 영역만, 정책서는 넓힌다 — 정책은 모든 업무가 참조하는 공통 기준이고, 범위를 넓혀도 정확도가
그대로였다(v0 실험: 사람 26문항 1위 85% = 영역 하나와 같음). 지식은 컨플루언스 92%가 검증된 경로라 측정 전엔 그대로.
  - 고른 영역 자신(정책이 있으면)
  - + "같은 업무"로 연결된 영역(ops_link) / 연결이 없으면 정책이 있는 영역 전부
"정책 영역 / 업무 영역" 구분은 없앴다(2026-10-20 — 지식은 업무 영역 하나에 정책·지식·용어가 함께 붙는다).
테스트용 영역(kind='policy_test', E2E 임시)은 그 영역을 직접 고를 때만 — "전부"에 넣으면 테스트 중 실제 채팅·측정에 가짜 정책이
섞인다(리뷰 10/20 M1).
정책이 있는 영역만 돌려준다. 캐시하지 않는다(작은 쿼리 하나, 영역을 지웠다 다시 만들면 옛 id를 가리켰다 — 리뷰 L6).
"""
from __future__ import annotations

from service.policy.visibility import searchable_sql

# 정책이 "있다" = 검색에 보이는 정책(10/21 — v0 검토 대기만 있는 영역은 범위에 안 넣는다)
_SQL = f"""
WITH me AS (SELECT id, kind FROM ops_namespace WHERE name = $1),
linked AS (
    SELECT n.id FROM ops_link l, me, ops_namespace n
    WHERE l.relation = 'same_work' AND l.status = 'active' AND l.from_type = 'domain' AND l.to_type = 'domain'
      AND n.kind <> 'policy_test'
      AND ((l.from_key = me.id::text AND n.id::text = l.to_key) OR (l.to_key = me.id::text AND n.id::text = l.from_key))
),
cand AS (
    SELECT me.id, 0 AS ord FROM me
    UNION ALL
    SELECT id, 1 FROM linked WHERE (SELECT kind FROM me) <> 'policy_test'
    UNION ALL
    SELECT n.id, 2 FROM ops_namespace n
    WHERE n.kind <> 'policy_test' AND (SELECT kind FROM me) <> 'policy_test' AND NOT EXISTS (SELECT 1 FROM linked)
)
SELECT c.id FROM cand c
WHERE EXISTS (SELECT 1 FROM policy_item i WHERE i.namespace_id = c.id AND {searchable_sql('i')})
GROUP BY c.id ORDER BY min(c.ord), c.id
"""


async def policy_namespace_ids(conn, namespace: str) -> list[int]:
    return [r["id"] for r in await conn.fetch(_SQL, namespace)]
