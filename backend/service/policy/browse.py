"""정책 항목(policy_item) 브라우저 — item 단위로 목록을 보여주고, 그 밑에 실제로 어떤 param
(RDB 정확조회)/chunk(벡터 검색)가 달려있는지 함께 반환한다.

docs/policy-doc-pipeline-plan.md §4-2에 검색을 전용 엔드포인트로 둔 이유가 있는데, 이 화면은
그 검색과는 다른 목적이다 — 검색은 "질의에 맞는 결과 찾기"이고, 이건 "지금 뭐가 어떻게 저장돼
있는지 사람이 훑어보기"다. 그래서 쿼리 없이도(빈 q) 전체 목록이 나와야 하고, 결과가 item→
param/chunk 3층 구조를 그대로 보여줘야 한다 — search.py처럼 평평한 히트 리스트가 아니다.

q가 있을 때는(2026-09-04 개선) 단순 ILIKE가 아니라 실제 `search.search_policy()`(param
RDB tsquery + narrative 벡터)를 그대로 재사용해 "이 item이 어떻게 걸렸는지"(matched_via)까지
알려준다 — 이전엔 policy_name ILIKE 부분매칭뿐이라 본문/파라미터 내용은 전혀 못 찾았다.

페이징은 클라이언트에서 한다(GlossaryTable.tsx와 동일 패턴) — 네임스페이스당 최대
수백 건 규모라 서버 페이징 없이도 충분하고, 기존 관리자 화면들과 일관성을 맞춘다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from service.policy import search as search_service

_LIST_LIMIT = 1000  # 네임스페이스당 이 이상 쌓이면 서버 페이징으로 전환 검토(§7)
_SEARCH_MATCH_LIMIT = 30  # q 검색 시 "이 item들이 매칭됐다"를 판별하기 위한 후보 상한
# narrative(벡터)는 코사인 유사도 기반이라 임계치 없이 top-K를 그대로 쓰면 사실상 전부
# "매칭"으로 잡힌다(실측: 딜리버스 71개 item 중 top_k=100으로 67개=94%가 걸림 — 필터로서
# 의미가 없음, 2026-09-04). search_policy()의 /search 엔드포인트는 채팅 컨텍스트용이라
# 후보를 넉넉히 주는 게 맞지만, 이 브라우저는 "사람이 훑어볼 필터"라 목적이 달라 여기서만
# 별도로 최소 유사도를 적용한다(search.py는 안 건드림 — 이미 검증된 채팅 검색 경로).
_NARRATIVE_MATCH_MIN_SCORE = 0.4


@dataclass
class ParamOut:
    id: int
    name: str
    condition: Optional[str]
    value: Optional[str]
    unit: Optional[str]


@dataclass
class ChunkOut:
    id: int
    chunk_text: str
    chunk_idx: int


@dataclass
class PolicyItemOut:
    item_id: int
    logical_id: int
    version: int
    policy_name: str
    category_path: list[str]
    raw_body: str
    status: str
    parse_status: str
    system_key: Optional[str]
    params: list[ParamOut] = field(default_factory=list)
    narratives: list[ChunkOut] = field(default_factory=list)
    matched_via: list[str] = field(default_factory=list)  # q 검색 시에만 채움: "param"/"narrative"


async def list_policy_items(
    namespace: str, category: Optional[str] = None, q: Optional[str] = None,
) -> list[PolicyItemOut]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")

    # q가 있으면 policy_name ILIKE가 아니라 실제 검색(RDB tsquery + 벡터)로 매칭되는 item을
    # 먼저 찾는다 — 어떤 경로로 걸렸는지(matched_via)도 여기서 함께 얻는다.
    matched_via: dict[int, set[str]] = {}
    if q:
        search_result = await search_service.search_policy(namespace, q, category, top_k=_SEARCH_MATCH_LIMIT)
        for p in search_result.params:
            matched_via.setdefault(p.item_id, set()).add("param")
        for n in search_result.narratives:
            if n.score >= _NARRATIVE_MATCH_MIN_SCORE:
                matched_via.setdefault(n.item_id, set()).add("narrative")
        if not matched_via:
            return []

    async with get_conn() as conn:
        clauses = ["namespace_id = $1", "status != 'deprecated'"]
        args: list = [ns_id]
        if category:
            args.append(category)
            clauses.append(f"${len(args)} = ANY(category_path)")
        if q:
            args.append(list(matched_via.keys()))
            clauses.append(f"id = ANY(${len(args)})")

        item_rows = await conn.fetch(
            f"""
            SELECT id, logical_id, version, policy_name, category_path, raw_body, status, parse_status, system_key
            FROM policy_item
            WHERE {' AND '.join(clauses)}
            ORDER BY id DESC
            LIMIT {_LIST_LIMIT}
            """,
            *args,
        )
        if not item_rows:
            return []

        item_ids = [r["id"] for r in item_rows]
        param_rows = await conn.fetch(
            "SELECT id, policy_item_id, name, condition, value, unit FROM policy_param WHERE policy_item_id = ANY($1)",
            item_ids,
        )
        chunk_rows = await conn.fetch(
            "SELECT id, policy_item_id, chunk_text, chunk_idx FROM policy_chunk WHERE policy_item_id = ANY($1) ORDER BY chunk_idx",
            item_ids,
        )

    params_by_item: dict[int, list[ParamOut]] = {}
    for r in param_rows:
        params_by_item.setdefault(r["policy_item_id"], []).append(
            ParamOut(id=r["id"], name=r["name"], condition=r["condition"], value=r["value"], unit=r["unit"])
        )
    chunks_by_item: dict[int, list[ChunkOut]] = {}
    for r in chunk_rows:
        chunks_by_item.setdefault(r["policy_item_id"], []).append(
            ChunkOut(id=r["id"], chunk_text=r["chunk_text"], chunk_idx=r["chunk_idx"])
        )

    return [
        PolicyItemOut(
            item_id=r["id"], logical_id=r["logical_id"], version=r["version"],
            policy_name=r["policy_name"], category_path=list(r["category_path"] or []),
            raw_body=r["raw_body"], status=r["status"], parse_status=r["parse_status"], system_key=r["system_key"],
            params=params_by_item.get(r["id"], []), narratives=chunks_by_item.get(r["id"], []),
            matched_via=sorted(matched_via.get(r["id"], set())),
        )
        for r in item_rows
    ]
