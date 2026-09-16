"""unresolved 팀별 집계 리포트 — docs/policy-doc-pipeline-plan.md §2-3.

"완전 자동화는 목표가 아니다"는 설계 원칙의 실행부. LLM 분해가 못 잡은 내용은
`policy_item.unresolved_segments`(JSONB)에 쌓이기만 하고 지금까지 아무도 정기적으로 보지
않았다 — 2026-09-04 발표 데모 중 실제 데이터 유실 버그를 "시스템이 알려줘서"가 아니라
"우연히 발표 준비하다가" 발견한 게 이 갭을 드러냈다. 이 모듈은 그 갭을 메운다.

reason 자동 클러스터링은 하지 않는다 — LLM이 매번 자유 텍스트로 사유를 쓰기 때문에 정확
문자열 매칭으로 그룹핑해봐야 의미가 없다(예: "상태 전이 규칙, 구조화 방법 미정"과 "상태
전이(A→B) 패턴이라 파라미터/서술 어디에도 안 맞음"은 같은 원인이지만 문자열이 다르다).
지금은 사람이 목록을 눈으로 훑어보는 용도로 충분하다 — 실제로 임베딩 기반 클러스터링 같은
게 필요할 만큼 건수가 쌓이는지부터 실측하고 판단한다(LLM Comparator 때와 같은 원칙, YAGNI).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service


@dataclass
class UnresolvedSegmentOut:
    text: str
    reason: Optional[str]


@dataclass
class UnresolvedItemOut:
    item_id: int
    logical_id: int
    policy_name: str
    category_path: list[str]
    segments: list[UnresolvedSegmentOut]


@dataclass
class SystemUnresolvedGroup:
    system_key: str
    item_count: int
    segment_count: int
    items: list[UnresolvedItemOut] = field(default_factory=list)


@dataclass
class UnresolvedSummary:
    total_items: int
    total_segments: int
    by_system: list[SystemUnresolvedGroup]


async def get_unresolved_summary(namespace: str, system_key: Optional[str] = None) -> UnresolvedSummary:
    """parse_status가 unresolved/partial인 policy_item을 system_key별로 묶어 반환.
    표준화 요청(§2-3) 근거 자료 — "카드 시스템에서 N건이 이런 사유로 자동 분류 실패" 식으로
    바로 쓸 수 있는 형태."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")

        system_clause = "AND system_key = $2" if system_key else ""
        args = [ns_id] + ([system_key] if system_key else [])
        rows = await conn.fetch(
            f"""
            SELECT id, logical_id, system_key, policy_name, category_path, unresolved_segments
            FROM policy_item
            WHERE namespace_id = $1 AND status != 'deprecated'
              AND parse_status IN ('unresolved', 'partial')
              AND unresolved_segments IS NOT NULL
              {system_clause}
            ORDER BY system_key, id
            """,
            *args,
        )

    groups: dict[str, SystemUnresolvedGroup] = {}
    total_items = 0
    total_segments = 0
    for r in rows:
        segs_raw = r["unresolved_segments"]
        if not segs_raw:
            continue
        segs = json.loads(segs_raw) if isinstance(segs_raw, str) else segs_raw
        segments = [UnresolvedSegmentOut(text=s.get("text", ""), reason=s.get("reason")) for s in segs]
        if not segments:
            continue

        key = r["system_key"] or "(미지정)"
        group = groups.setdefault(key, SystemUnresolvedGroup(system_key=key, item_count=0, segment_count=0))
        group.items.append(UnresolvedItemOut(
            item_id=r["id"], logical_id=r["logical_id"], policy_name=r["policy_name"],
            category_path=list(r["category_path"] or []), segments=segments,
        ))
        group.item_count += 1
        group.segment_count += len(segments)
        total_items += 1
        total_segments += len(segments)

    return UnresolvedSummary(
        total_items=total_items, total_segments=total_segments,
        by_system=sorted(groups.values(), key=lambda g: -g.segment_count),
    )


async def promote_segment_to_narrative(namespace: str, item_id: int, segment_index: int) -> int:
    """unresolved segment 1건을 서술(policy_chunk)로 수동 편입 — §6 "검토 UI" 중 실제로
    쌓인 문제(조회만 있고 액션이 없음, 2026-09-16 사용자 지적)를 해소하는 첫 액션. 원문을
    그대로 벡터 색인해 최소한 검색은 되게 만든다(service.py의 narrative 처리·벡터 폴백과
    동일 패턴) — param처럼 구조화된 필드 추출은 하지 않는다(폼 없이 원클릭으로 되는 선에서
    "완전히 방치"를 벗어나는 게 목적, 정밀 재분류는 여전히 사람이 원문을 보고 값을 추출해야
    하는 별개 작업).

    반환값: 편입 후 남은 unresolved segment 개수.
    """
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")

        row = await conn.fetchrow(
            "SELECT unresolved_segments FROM policy_item WHERE id = $1 AND namespace_id = $2",
            item_id, ns_id,
        )
        if row is None:
            raise ValueError(f"정책 항목을 찾을 수 없습니다: item_id={item_id}")

        segs_raw = row["unresolved_segments"]
        segments = (json.loads(segs_raw) if isinstance(segs_raw, str) else segs_raw) or []
        if not (0 <= segment_index < len(segments)):
            raise ValueError(f"segment_index 범위를 벗어났습니다: {segment_index} (전체 {len(segments)}개)")

        segment_text = (segments[segment_index] or {}).get("text", "").strip()
        if not segment_text:
            raise ValueError("빈 segment는 편입할 수 없습니다.")

        next_chunk_idx = await conn.fetchval(
            "SELECT COALESCE(MAX(chunk_idx) + 1, 0) FROM policy_chunk WHERE policy_item_id = $1", item_id,
        )
        embedding = await embedding_service.embed(segment_text)
        await conn.execute(
            "INSERT INTO policy_chunk (policy_item_id, chunk_text, embedding, chunk_idx) VALUES ($1, $2, $3::vector, $4)",
            item_id, segment_text, str(embedding), next_chunk_idx,
        )

        remaining = segments[:segment_index] + segments[segment_index + 1:]
        new_parse_status = "parsed" if not remaining else "partial"
        await conn.execute(
            """UPDATE policy_item SET unresolved_segments = $1::jsonb, parse_status = $2, updated_at = NOW()
               WHERE id = $3""",
            json.dumps(remaining, ensure_ascii=False), new_parse_status, item_id,
        )
    return len(remaining)
