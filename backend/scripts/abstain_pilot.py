"""ABSTAIN 판정 임계값 오프라인 파일럿 (2026-09-22, 거버넌스 감사 후속).

목적: ANSWER/CLARIFY/ABSTAIN 판정 계층을 실사용에 노출하기 전에, 후보 ABSTAIN 규칙이
"실제로 답이 있는 질문"(골든셋 89문항, 전부 실 DB의 실제 policy_item에서 역추적된 정답
존재)을 얼마나 잘못 걷어내는지(false abstain) 먼저 측정한다. 이 스크립트는 프로덕션
코드를 전혀 건드리지 않고 읽기 전용으로 실행되며, 결과는 임계값 선택의 근거 자료일 뿐
자동으로 반영되지 않는다 — 반드시 사람이 결과를 보고 임계값을 정한 뒤 실제 판정 계층
코드에 반영해야 한다.

한계(중요): 이 골든셋엔 "진짜 답이 없는 질문"(정당한 ABSTAIN 대상) 사례가 없다
(docs/tech/... 감사에서 확인됨) — 그래서 이 파일럿은 "false abstain 비율"만 측정 가능,
"true abstain 정확도"는 측정 불가. 후자를 보려면 적대적 질문 세트를 별도로 만들어야 한다
(P2 항목, 지금 범위 아님).

실행: docker compose exec backend python scripts/abstain_pilot.py
"""
from __future__ import annotations

import asyncio
import sys
from collections import defaultdict

sys.path.insert(0, "/app")

from core.database import init_pool, get_conn, resolve_namespace_id
from shared.embedding import embedding_service
from service.policy.track2 import _load_golden_set, _search_b, _GOLDEN_SET_PATH, _FILE_TO_NAMESPACE

# 후보 ABSTAIN 규칙: "narrative 최고 점수가 이 값 미만이고 param 히트도 없으면 ABSTAIN".
# build_policy_context()가 이미 쓰는 신뢰도 라벨 경계값(0.4=보통, 0.6=높음)을 그대로
# 후보로 재사용 — 완전히 새로운 숫자를 만들지 않는다.
CANDIDATE_THRESHOLDS = [0.0, 0.4, 0.6]


async def main() -> None:
    await init_pool()
    embedding_service.load()

    async with get_conn() as conn:
        real_ns_ids: dict[str, int] = {}
        for ns in {ns for _, ns in _FILE_TO_NAMESPACE}:
            resolved = await resolve_namespace_id(conn, ns)
            if resolved is not None:
                real_ns_ids[ns] = resolved

    golden = await _load_golden_set(_GOLDEN_SET_PATH, real_ns_ids)
    print(f"골든셋 로드: {len(golden)}건 (전부 실제 정답 존재)\n")

    false_abstain_count: dict[float, int] = defaultdict(int)
    false_abstain_examples: dict[float, list[str]] = defaultdict(list)

    for entry in golden:
        result = await _search_b(entry["namespace_name"], entry["query"], top_k=5)
        has_param = bool(result.params)
        max_narrative_score = max((n.score for n in result.narratives), default=0.0)

        for th in CANDIDATE_THRESHOLDS:
            would_abstain = not has_param and max_narrative_score < th
            if would_abstain:
                false_abstain_count[th] += 1
                if len(false_abstain_examples[th]) < 5:
                    false_abstain_examples[th].append(
                        f"  · [{entry['type']}] \"{entry['query']}\" (최고 narrative 점수: {max_narrative_score:.3f}, param 히트: {has_param})"
                    )

    print("=== 임계값별 false-abstain 비율 (실제 답 있는 질문을 잘못 거부한 비율) ===")
    for th in CANDIDATE_THRESHOLDS:
        n = false_abstain_count[th]
        pct = n / len(golden) * 100
        print(f"\n임계값 {th}: {n}/{len(golden)}건 ({pct:.1f}%) false abstain")
        for ex in false_abstain_examples[th]:
            print(ex)


if __name__ == "__main__":
    asyncio.run(main())
