"""정책 항목(policy_item) 브라우저 — item 단위로 목록을 보여주고, 그 밑에 실제로 어떤 param
(RDB 정확조회)/chunk(벡터 검색)가 달려있는지 함께 반환한다.

docs/policy-doc-pipeline-plan.md §4-2에 검색을 전용 엔드포인트로 둔 이유가 있는데, 이 화면은
그 검색과는 다른 목적이다 — 검색은 "질의에 맞는 결과 찾기"이고, 이건 "지금 뭐가 어떻게 저장돼
있는지 사람이 훑어보기"다. 그래서 쿼리 없이도(빈 q) 전체 목록이 나와야 하고, 결과가 item→
param/chunk 3층 구조를 그대로 보여줘야 한다 — search.py처럼 평평한 히트 리스트가 아니다.

페이징은 클라이언트에서 한다(GlossaryTable.tsx와 동일 패턴) — 네임스페이스당 최대
수백 건 규모라 서버 페이징 없이도 충분하고, 기존 관리자 화면들과 일관성을 맞춘다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.database import get_conn, resolve_namespace_id

_LIST_LIMIT = 1000  # 네임스페이스당 이 이상 쌓이면 서버 페이징으로 전환 검토(§7)


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
    status: str
    parse_status: str
    system_key: Optional[str]
    params: list[ParamOut] = field(default_factory=list)
    narratives: list[ChunkOut] = field(default_factory=list)


async def list_policy_items(
    namespace: str, category: Optional[str] = None, q: Optional[str] = None,
) -> list[PolicyItemOut]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")

        clauses = ["namespace_id = $1", "status != 'deprecated'"]
        args: list = [ns_id]
        if category:
            args.append(category)
            clauses.append(f"${len(args)} = ANY(category_path)")
        if q:
            args.append(f"%{q}%")
            clauses.append(f"policy_name ILIKE ${len(args)}")

        item_rows = await conn.fetch(
            f"""
            SELECT id, logical_id, version, policy_name, category_path, status, parse_status, system_key
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
            status=r["status"], parse_status=r["parse_status"], system_key=r["system_key"],
            params=params_by_item.get(r["id"], []), narratives=chunks_by_item.get(r["id"], []),
        )
        for r in item_rows
    ]
