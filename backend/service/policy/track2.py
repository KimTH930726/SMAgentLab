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
from dataclasses import asdict, dataclass, field
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
    a_precision: float
    b_precision: float
    b_hit_rdb_only: float
    b_hit_vector_only: float
    b_hit_both: float
    a_top1_accuracy: float = 0.0
    b_top1_param_accuracy: float = 0.0
    b_top1_narrative_accuracy: float = 0.0


@dataclass
class Track2Result:
    total_n: int
    a_hit_rate: float
    b_hit_rate: float
    a_precision: float
    b_precision: float
    b_hit_rdb_only: float
    b_hit_vector_only: float
    b_hit_both: float
    by_type: list[Track2TypeResult] = field(default_factory=list)
    golden_set_file: str = ""
    top_k: int = 10
    duration_seconds: float = 0.0
    a_top1_accuracy: float = 0.0
    b_top1_param_accuracy: float = 0.0
    b_top1_narrative_accuracy: float = 0.0


@dataclass
class _QueryScore:
    """골든셋 한 문항의 채점 결과. B그룹은 RDB(policy_param)/벡터(policy_chunk) 중 어느
    경로로 정답을 찾았는지까지 남긴다 — b_hit(둘 중 하나라도 맞으면 성공)만 보면 "하이브리드가
    이겼다"는 알아도 "RDB랑 벡터 중 실제로 뭐가 일하고 있는지"는 안 보여서(2026-09-09 대화
    중 지적) 추가.

    top1_*(2026-09-15, 실험실 게이트 작업2): "근거카드 1건 노출" 소비 패턴에 대응하는 지표.
    A는 search_knowledge()가 단일 랭킹 리스트라 0번째가 정답인지로 그대로 정의된다. B는
    RDB/벡터 두 채널로 나뉘어 있어 "진짜 하나의 순위"가 원래 없다(아키텍처 자체의 특징,
    v2.71부터 계속 확인돼온 것) — 억지로 하나로 합치지 않고 **채널별로 따로** 1위 정확도를
    본다. reranker_enabled=True일 때는 채널별 재정렬 결과의 1위를 그대로 쓰므로 자연히
    반영된다(search.py 참고)."""
    a_hit: bool
    b_hit: bool
    a_precision: float
    b_precision: float
    b_via_rdb: bool
    b_via_vector: bool
    a_top1: bool = False
    b_top1_param: bool = False
    b_top1_narrative: bool = False


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
        # precision은 "top-K(A는 top_k, B는 param+narrative 합쳐 최대 2*top_k) 중 실제
        # 골든셋 정답 item과 겹치는 고유 item 비율" — hit@K는 "정답이 있냐 없냐"만 보고
        # 후보군에 잡음이 얼마나 섞였는지는 안 보므로, 같은 hit@K를 내는 두 전략이라도
        # 컨텍스트 품질(잡음 비율)이 다를 수 있다는 걸 보완하기 위해 추가(2026-09-08).
        # A/B가 후보 개수 자체가 다를 수 있어(B는 param+narrative 합산) 분모는 고정 K가
        # 아니라 실제 반환된 고유 item 개수를 쓴다 — 두 전략의 "후보 풀 크기"가 다르다는
        # 것 자체도 실측해서 같이 보고한다(순수 정답 비율만으로 비교하면 이 차이가 가려짐).
        per_type: dict[str, list[_QueryScore]] = {}
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
            a_precision = (len(gold_ids & a_item_ids) / len(a_item_ids)) if a_item_ids else 0.0
            a_top1 = bool(a_hits and id_map.get(a_hits[0].id) in gold_ids)

            b_result = await search_service.search_policy(namespace_name, query, top_k=top_k)
            b_param_ids = {p.item_id for p in b_result.params}
            b_narrative_ids = {n.item_id for n in b_result.narratives}
            b_item_ids = b_param_ids | b_narrative_ids
            b_hit = bool(gold_ids & b_item_ids)
            b_precision = (len(gold_ids & b_item_ids) / len(b_item_ids)) if b_item_ids else 0.0
            b_via_rdb = bool(gold_ids & b_param_ids)
            b_via_vector = bool(gold_ids & b_narrative_ids)
            b_top1_param = bool(b_result.params and b_result.params[0].item_id in gold_ids)
            b_top1_narrative = bool(b_result.narratives and b_result.narratives[0].item_id in gold_ids)

            per_type.setdefault(qtype, []).append(
                _QueryScore(
                    a_hit, b_hit, a_precision, b_precision, b_via_rdb, b_via_vector,
                    a_top1, b_top1_param, b_top1_narrative,
                )
            )
    finally:
        async with get_conn() as conn:
            await conn.execute("DELETE FROM rag_knowledge WHERE namespace_id = $1", ns_id_a)
            await conn.execute("DELETE FROM ops_namespace WHERE id = $1", ns_id_a)

    def _rate(rows: list[_QueryScore], pick) -> float:
        return sum(1 for r in rows if pick(r)) / len(rows) if rows else 0.0

    def _avg(rows: list[_QueryScore], pick) -> float:
        return sum(pick(r) for r in rows) / len(rows) if rows else 0.0

    # b_hit_rdb_only + b_hit_vector_only + b_hit_both == b_hit_rate — RDB만/벡터만/둘 다에서
    # 정답을 찾은 비율로 쪼개서, "하이브리드가 이겼다"가 아니라 "그중 RDB와 벡터가 각각
    # 얼마나 기여했는지"를 보여준다(2026-09-09).
    by_type = [
        Track2TypeResult(
            type=t, n=len(rows),
            a_hit_rate=_rate(rows, lambda r: r.a_hit),
            b_hit_rate=_rate(rows, lambda r: r.b_hit),
            a_precision=_avg(rows, lambda r: r.a_precision),
            b_precision=_avg(rows, lambda r: r.b_precision),
            b_hit_rdb_only=_rate(rows, lambda r: r.b_via_rdb and not r.b_via_vector),
            b_hit_vector_only=_rate(rows, lambda r: r.b_via_vector and not r.b_via_rdb),
            b_hit_both=_rate(rows, lambda r: r.b_via_rdb and r.b_via_vector),
            a_top1_accuracy=_rate(rows, lambda r: r.a_top1),
            b_top1_param_accuracy=_rate(rows, lambda r: r.b_top1_param),
            b_top1_narrative_accuracy=_rate(rows, lambda r: r.b_top1_narrative),
        )
        for t, rows in per_type.items()
    ]
    all_rows = [r for rows in per_type.values() for r in rows]
    total_n = len(all_rows)
    return Track2Result(
        total_n=total_n,
        a_hit_rate=_rate(all_rows, lambda r: r.a_hit),
        b_hit_rate=_rate(all_rows, lambda r: r.b_hit),
        a_precision=_avg(all_rows, lambda r: r.a_precision),
        b_precision=_avg(all_rows, lambda r: r.b_precision),
        b_hit_rdb_only=_rate(all_rows, lambda r: r.b_via_rdb and not r.b_via_vector),
        b_hit_vector_only=_rate(all_rows, lambda r: r.b_via_vector and not r.b_via_rdb),
        b_hit_both=_rate(all_rows, lambda r: r.b_via_rdb and r.b_via_vector),
        by_type=by_type,
        golden_set_file=_GOLDEN_SET_PATH.name,
        top_k=top_k,
        duration_seconds=round(time.monotonic() - t0, 1),
        a_top1_accuracy=_rate(all_rows, lambda r: r.a_top1),
        b_top1_param_accuracy=_rate(all_rows, lambda r: r.b_top1_param),
        b_top1_narrative_accuracy=_rate(all_rows, lambda r: r.b_top1_narrative),
    )


async def save_run(result: Track2Result, triggered_by: Optional[int] = None) -> int:
    """Track2 실행 결과를 policy_track2_run에 스냅샷으로 저장 — 실험실 게이트 작업3
    (모니터링 뷰)의 재료. by_type은 요약 없이 JSONB 통째로 넣어 나중에 지표가 늘어나도
    (MRR·nDCG 등) 컬럼을 매번 안 늘려도 되게 한다."""
    async with get_conn() as conn:
        run_id = await conn.fetchval(
            """
            INSERT INTO policy_track2_run
                (top_k, total_n, a_hit_rate, b_hit_rate, a_precision, b_precision,
                 b_hit_rdb_only, b_hit_vector_only, b_hit_both,
                 a_top1_accuracy, b_top1_param_accuracy, b_top1_narrative_accuracy,
                 by_type, golden_set_file, duration_seconds, triggered_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
            RETURNING id
            """,
            result.top_k, result.total_n, result.a_hit_rate, result.b_hit_rate,
            result.a_precision, result.b_precision,
            result.b_hit_rdb_only, result.b_hit_vector_only, result.b_hit_both,
            result.a_top1_accuracy, result.b_top1_param_accuracy, result.b_top1_narrative_accuracy,
            json.dumps([asdict(t) for t in result.by_type], ensure_ascii=False),
            result.golden_set_file, result.duration_seconds, triggered_by,
        )
    return run_id


async def list_run_history(limit: int = 50) -> list[dict]:
    """최근 Track2 실행 이력 — 모니터링 뷰의 추이 차트용. run_at 내림차순(최신 먼저)."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, run_at, top_k, total_n, a_hit_rate, b_hit_rate, a_precision, b_precision,
                   b_hit_rdb_only, b_hit_vector_only, b_hit_both,
                   a_top1_accuracy, b_top1_param_accuracy, b_top1_narrative_accuracy,
                   by_type, golden_set_file, duration_seconds, triggered_by
            FROM policy_track2_run
            ORDER BY run_at DESC
            LIMIT $1
            """,
            limit,
        )
    results = []
    for r in rows:
        d = dict(r)
        d["by_type"] = json.loads(d["by_type"]) if isinstance(d["by_type"], str) else d["by_type"]
        results.append(d)
    return results
