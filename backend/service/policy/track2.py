"""Track 2 — 저장소 전략 비교 실행을 API로 승격(2026-09-04).

docs/policy-doc-pipeline-plan.md §4 실험을 매번 일회성 스크립트로 짜지 않고, 관리자 화면에서
버튼 하나로 재실행할 수 있게 한다. 로직은 최초 실측 때 쓴 스크립트와 동일 — A그룹(rag_knowledge
지식-only, 격리된 임시 네임스페이스에 전체 policy_item을 원문 그대로 얹음)과 B그룹(지금
하이브리드 스키마, search.search_policy() 그대로 재사용)에 골든셋을 동일하게 질의해 item-id
기반 hit@K로 채점한다. A그룹 색인은 실행마다 새로 만들고 끝나면 삭제한다 — 결과에 남는 건
`rag_knowledge`가 아니라 이 함수의 반환값뿐, 프로덕션 데이터에 흔적을 남기지 않는다.

실행에 몇 분 걸린다(전체 policy_item 수만큼 임베딩 1회씩) — 실시간 기능이 아니라 "가끔 재측정"
용도라 동기 호출로 충분하다고 판단(YAGNI, 별도 잡 큐 없음).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service
from agents.knowledge_rag.knowledge.retrieval import search_knowledge
from service.policy import search as search_service

_GOLDEN_SET_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "golden_set" / "online_delivus_v1.jsonl"
_TRACK_A_NAMESPACE = "TrackA_정책비교_테스트 DB"
_TRACK_A_CATEGORY = "정책서-TrackA-테스트"
# 골든셋 source.file이 이 중 어느 팀 파일 이름을 포함하는지로 실제 네임스페이스를 판별한다
# (§4-1 스펙상 source.file은 재구성 워크북 이름). 새 시스템 정책서가 골든셋에 추가되면
# 여기 목록도 같이 늘려야 매칭된다 — 하드코딩이지만 지금 골든셋 소스가 이 2개뿐이라 YAGNI.
_FILE_TO_NAMESPACE = [("온라인스토어", "온라인스토어 DB"), ("딜리버스", "딜리버스 DB")]


@dataclass
class Track2TypeResult:
    type: str
    n: int
    a_hit_rate: float
    b_hit_rate: float


@dataclass
class Track2Result:
    total_n: int
    a_hit_rate: float
    b_hit_rate: float
    by_type: list[Track2TypeResult] = field(default_factory=list)
    golden_set_file: str = ""
    top_k: int = 10
    duration_seconds: float = 0.0


def _namespace_for_file(file_: str) -> Optional[str]:
    for needle, ns in _FILE_TO_NAMESPACE:
        if needle in file_:
            return ns
    return None


async def _resolve_item_by_source(conn, file_: str, sheet: str, row: int) -> Optional[dict]:
    r = await conn.fetchrow(
        "SELECT id, namespace_id FROM policy_item WHERE source_file=$1 AND source_sheet=$2 AND source_row=$3 AND status != 'deprecated'",
        file_, sheet, row,
    )
    return dict(r) if r else None


async def _resolve_items_by_category(conn, ns_id: int, category: str) -> set[int]:
    rows = await conn.fetch(
        "SELECT id FROM policy_item WHERE namespace_id=$1 AND status != 'deprecated' AND $2 = ANY(category_path)",
        ns_id, category,
    )
    return {r["id"] for r in rows}


async def _resolve_items_by_condition(conn, ns_id: int, condition: str) -> set[int]:
    rows = await conn.fetch(
        """SELECT DISTINCT p.policy_item_id AS id FROM policy_param p
           JOIN policy_item i ON i.id = p.policy_item_id
           WHERE i.namespace_id=$1 AND i.status != 'deprecated' AND p.condition = $2""",
        ns_id, condition,
    )
    return {r["id"] for r in rows}


async def _setup_track_a(conn, ns_id_a: int) -> dict[int, int]:
    """전체 policy_item을 원문 그대로 rag_knowledge 테스트 네임스페이스에 얹는다.
    반환값: {rag_knowledge.id: policy_item.id} — 검색 결과를 다시 item으로 역매핑하기 위함."""
    rows = await conn.fetch(
        "SELECT id, policy_name, category_path, raw_body FROM policy_item WHERE status != 'deprecated'"
    )
    id_map: dict[int, int] = {}
    for r in rows:
        content = f"[{r['policy_name']}] ({' / '.join(r['category_path'] or [])})\n{r['raw_body']}"
        embedding = await embedding_service.embed(content)
        kid = await conn.fetchval(
            """INSERT INTO rag_knowledge (namespace_id, content, embedding, base_weight, category, status)
               VALUES ($1, $2, $3::vector, 1.0, $4, 'active') RETURNING id""",
            ns_id_a, content, str(embedding), _TRACK_A_CATEGORY,
        )
        id_map[kid] = r["id"]
    return id_map


async def run_comparison(top_k: int = 10) -> Track2Result:
    if not _GOLDEN_SET_PATH.exists():
        raise ValueError(
            f"골든셋 파일이 없습니다: {_GOLDEN_SET_PATH}. "
            "backend/tests/fixtures/golden_set/online_delivus_v1.jsonl 준비 후 재시도하세요."
        )
    with open(_GOLDEN_SET_PATH, encoding="utf-8") as f:
        golden = [json.loads(line) for line in f if line.strip()]

    t0 = time.monotonic()

    async with get_conn() as conn:
        ns_id_a = await resolve_namespace_id(conn, _TRACK_A_NAMESPACE)
        if ns_id_a is None:
            ns_id_a = await conn.fetchval(
                "INSERT INTO ops_namespace (name, description) VALUES ($1, $2) RETURNING id",
                _TRACK_A_NAMESPACE, "Track 2 A그룹(지식-only) 임시 테스트 네임스페이스 — 실행 후 삭제",
            )
        id_map = await _setup_track_a(conn, ns_id_a)

        real_ns_ids: dict[str, int] = {}
        for ns in {ns for _, ns in _FILE_TO_NAMESPACE}:
            resolved = await resolve_namespace_id(conn, ns)
            if resolved is not None:
                real_ns_ids[ns] = resolved

    try:
        per_type: dict[str, list[tuple[bool, bool]]] = {}
        for entry in golden:
            qtype, query, src = entry["type"], entry["query"], entry["source"]
            namespace_name = _namespace_for_file(src.get("file", ""))
            if namespace_name is None or namespace_name not in real_ns_ids:
                continue
            real_ns_id = real_ns_ids[namespace_name]

            async with get_conn() as conn:
                if qtype in ("param", "narrative"):
                    item = await _resolve_item_by_source(conn, src["file"], src["sheet"], src["row"])
                    gold_ids = {item["id"]} if item else set()
                elif qtype == "navigation":
                    gold_ids = await _resolve_items_by_category(conn, real_ns_id, src["category"])
                else:
                    gold_ids = await _resolve_items_by_condition(conn, real_ns_id, src["condition"])
            if not gold_ids:
                continue

            query_vec = await embedding_service.embed(query)
            a_hits = await search_knowledge(_TRACK_A_NAMESPACE, query_vec, query, top_k=top_k)
            a_item_ids = {id_map.get(h.id) for h in a_hits}
            a_hit = bool(gold_ids & a_item_ids)

            b_result = await search_service.search_policy(namespace_name, query, top_k=top_k)
            b_item_ids = {p.item_id for p in b_result.params} | {n.item_id for n in b_result.narratives}
            b_hit = bool(gold_ids & b_item_ids)

            per_type.setdefault(qtype, []).append((a_hit, b_hit))
    finally:
        async with get_conn() as conn:
            await conn.execute("DELETE FROM rag_knowledge WHERE namespace_id = $1", ns_id_a)
            await conn.execute("DELETE FROM ops_namespace WHERE id = $1", ns_id_a)

    by_type = [
        Track2TypeResult(
            type=t, n=len(hits),
            a_hit_rate=sum(a for a, _ in hits) / len(hits),
            b_hit_rate=sum(b for _, b in hits) / len(hits),
        )
        for t, hits in per_type.items()
    ]
    all_hits = [h for hits in per_type.values() for h in hits]
    total_n = len(all_hits)
    return Track2Result(
        total_n=total_n,
        a_hit_rate=(sum(a for a, _ in all_hits) / total_n) if total_n else 0.0,
        b_hit_rate=(sum(b for _, b in all_hits) / total_n) if total_n else 0.0,
        by_type=by_type,
        golden_set_file=_GOLDEN_SET_PATH.name,
        top_k=top_k,
        duration_seconds=round(time.monotonic() - t0, 1),
    )
