"""지식 베이스, 용어집 CRUD 서비스."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service
from agents.knowledge_rag.knowledge.retrieval import find_similar_active_knowledge, get_thresholds

logger = logging.getLogger(__name__)

_KNOWLEDGE_COLS = """k.id, n.name AS namespace, k.container_name, k.target_tables,
    k.content, k.query_template, k.base_weight, k.category, k.status,
    k.source_file, k.source_chunk_idx, k.source_type,
    k.created_by_part, k.created_by_user_id, u.username AS created_by_username,
    k.created_at::text, k.updated_at::text"""

_GLOSSARY_COLS = """g.id, n.name AS namespace, g.term, g.description,
    g.created_by_part, g.created_by_user_id, u.username AS created_by_username"""


def _require_category(category: Optional[str]) -> str:
    """지식 항목의 업무구분(category)은 필수값 — 비어있으면 등록 거부."""
    if not category or not category.strip():
        raise ValueError("업무구분(category)은 필수입니다.")
    return category.strip()


# ─── rag_knowledge ────────────────────────────────────────────────────────────

async def create_knowledge(
    namespace: str,
    content: str,
    container_name: Optional[str] = None,
    target_tables: Optional[list[str]] = None,
    query_template: Optional[str] = None,
    base_weight: float = 1.0,
    category: Optional[str] = None,
    *,
    created_by_part: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> dict:
    category = _require_category(category)
    embedding = await embedding_service.embed(content)

    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"Namespace '{namespace}' not found")

    matches = await find_similar_active_knowledge(ns_id, embedding)
    is_duplicate = bool(matches) and matches[0]["similarity"] >= get_thresholds()["duplicate_min_similarity"]
    status = "pending_review" if is_duplicate else "active"

    async with get_conn() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO rag_knowledge
                (namespace_id, container_name, target_tables, content,
                 query_template, embedding, base_weight, category,
                 created_by_part, created_by_user_id, status)
            VALUES ($1, $2, $3, $4, $5, $6::vector, $7, $8, $9, $10, $11)
            RETURNING id, namespace_id, container_name, target_tables,
                      content, query_template, base_weight, category, status,
                      created_by_part, created_by_user_id,
                      created_at::text, updated_at::text
            """,
            ns_id, container_name, target_tables, content,
            query_template, str(embedding), base_weight, category,
            created_by_part, created_by_user_id, status,
        )
        if is_duplicate:
            await conn.executemany(
                "INSERT INTO rag_knowledge_duplicate_match (new_knowledge_id, matched_knowledge_id, similarity) "
                "VALUES ($1, $2, $3)",
                [(row["id"], m["id"], m["similarity"]) for m in matches],
            )

    result = dict(row)
    result["namespace"] = namespace
    result["pending_review"] = is_duplicate
    result["duplicate_matches"] = matches if is_duplicate else []
    return result


async def update_knowledge(
    knowledge_id: int,
    content: Optional[str] = None,
    container_name: Optional[str] = None,
    target_tables: Optional[list[str]] = None,
    query_template: Optional[str] = None,
    base_weight: Optional[float] = None,
    category: Optional[str] = None,
    *,
    updated_by_part: Optional[str] = None,
    updated_by_user_id: Optional[int] = None,
) -> Optional[dict]:
    async with get_conn() as conn:
        current = await conn.fetchrow(
            "SELECT k.*, n.name AS ns_name FROM rag_knowledge k JOIN ops_namespace n ON k.namespace_id = n.id WHERE k.id = $1",
            knowledge_id,
        )
        if not current:
            return None

        new_content = content if content is not None else current["content"]
        new_container = container_name if container_name is not None else current["container_name"]
        new_tables = target_tables if target_tables is not None else current["target_tables"]
        new_template = query_template if query_template is not None else current["query_template"]
        new_weight = base_weight if base_weight is not None else current["base_weight"]
        # category=None은 "변경 없음". 업무구분은 필수값이라 빈 문자열로 초기화하는 것은 허용하지 않음.
        new_category = _require_category(category) if category is not None else current.get("category")

        new_embedding = str(await embedding_service.embed(new_content)) if content else str(current["embedding"])

        row = await conn.fetchrow(
            """
            UPDATE rag_knowledge
            SET container_name=$1, target_tables=$2, content=$3,
                query_template=$4, embedding=$5::vector, base_weight=$6,
                category=$8,
                updated_at=NOW()
            WHERE id = $7
            RETURNING id, namespace_id, container_name, target_tables,
                      content, query_template, base_weight, category,
                      created_by_part, created_by_user_id,
                      created_at::text, updated_at::text
            """,
            new_container, new_tables, new_content,
            new_template, new_embedding, new_weight, knowledge_id,
            new_category,
        )
        if not row:
            return None
        result = dict(row)
        result["namespace"] = current["ns_name"]
    return result


async def delete_knowledge(knowledge_id: int) -> bool:
    async with get_conn() as conn:
        result = await conn.execute(
            "DELETE FROM rag_knowledge WHERE id = $1", knowledge_id
        )
    return result == "DELETE 1"


async def bulk_delete_knowledge(ids: list[int]) -> int:
    if not ids:
        return 0
    async with get_conn() as conn:
        result = await conn.execute(
            "DELETE FROM rag_knowledge WHERE id = ANY($1::int[])", ids
        )
    return int(result.split()[-1])


async def get_knowledge_namespaces(ids: list[int]) -> list[str]:
    """주어진 지식 id들이 걸쳐 있는 네임스페이스 이름 목록 (권한 확인용)."""
    if not ids:
        return []
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT n.name FROM rag_knowledge k
            JOIN ops_namespace n ON k.namespace_id = n.id
            WHERE k.id = ANY($1::int[])
            """,
            ids,
        )
    return [r["name"] for r in rows]


async def bulk_update_knowledge(
    ids: list[int], *, category: Optional[str] = None, source_type: Optional[str] = None,
) -> int:
    """선택한 지식 항목들의 업무구분/소스유형을 일괄 변경. None인 필드는 유지."""
    if not ids:
        return 0
    if category is None and source_type is None:
        raise ValueError("변경할 필드(업무구분 또는 소스유형)를 지정해야 합니다.")
    if category is not None:
        category = _require_category(category)
    async with get_conn() as conn:
        result = await conn.execute(
            """
            UPDATE rag_knowledge
            SET category = COALESCE($2, category),
                source_type = COALESCE($3, source_type),
                updated_at = NOW()
            WHERE id = ANY($1::int[])
            """,
            ids, category, source_type,
        )
    return int(result.split()[-1])


async def vector_search_knowledge(namespace: str, query_vec: list[float], top_k: int = 30) -> list[dict]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            f"""
            SELECT {_KNOWLEDGE_COLS},
                   1 - (k.embedding <=> $2::vector) AS similarity
            FROM rag_knowledge k
            JOIN ops_namespace n ON k.namespace_id = n.id
            LEFT JOIN ops_user u ON k.created_by_user_id = u.id
            WHERE k.namespace_id = $1 AND k.embedding IS NOT NULL
              AND (k.status IS NULL OR k.status = 'active')
            ORDER BY k.embedding <=> $2::vector
            LIMIT $3
            """,
            ns_id, str(query_vec), top_k,
        )
    return [dict(r) for r in rows]


async def list_knowledge(namespace: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    """지식 목록 조회. status 미지정 시 기본으로 'active'만 반환 —
    승인 대기(pending_review)/반려(rejected) 항목은 명시적으로 status를 넘겨야 보임
    (메인 목록에 검토 대상이 섞여 헷갈리지 않도록)."""
    status_filter = status or "active"
    async with get_conn() as conn:
        if namespace:
            ns_id = await resolve_namespace_id(conn, namespace)
            if ns_id is None:
                return []
            rows = await conn.fetch(
                f"""
                SELECT {_KNOWLEDGE_COLS}
                FROM rag_knowledge k
                JOIN ops_namespace n ON k.namespace_id = n.id
                LEFT JOIN ops_user u ON k.created_by_user_id = u.id
                WHERE k.namespace_id = $1 AND k.status = $2
                ORDER BY k.created_at DESC
                """,
                ns_id, status_filter,
            )
        else:
            rows = await conn.fetch(
                f"""
                SELECT {_KNOWLEDGE_COLS}
                FROM rag_knowledge k
                JOIN ops_namespace n ON k.namespace_id = n.id
                LEFT JOIN ops_user u ON k.created_by_user_id = u.id
                WHERE k.status = $1
                ORDER BY n.name, k.created_at DESC
                """,
                status_filter,
            )
    return [dict(r) for r in rows]


async def get_knowledge_part(knowledge_id: int) -> Optional[str]:
    """리소스의 created_by_part 반환 (레거시 호환용)."""
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT created_by_part FROM rag_knowledge WHERE id = $1", knowledge_id
        )


async def get_knowledge_namespace(knowledge_id: int) -> Optional[str]:
    """리소스의 namespace name 반환 (네임스페이스 소유 파트 기반 권한 체크용)."""
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT n.name FROM rag_knowledge k JOIN ops_namespace n ON k.namespace_id = n.id WHERE k.id = $1",
            knowledge_id,
        )


# ─── 중복 승인 대기 리뷰 ─────────────────────────────────────────────────────

async def get_duplicate_matches(knowledge_id: int) -> list[dict]:
    """pending_review 지식이 어떤 기존 활성 지식(들)과 얼마나 유사했는지 원문과 함께 반환."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT m.matched_knowledge_id AS id, k.content, m.similarity
            FROM rag_knowledge_duplicate_match m
            JOIN rag_knowledge k ON m.matched_knowledge_id = k.id
            WHERE m.new_knowledge_id = $1
            ORDER BY m.similarity DESC
            """,
            knowledge_id,
        )
    return [dict(r) for r in rows]


async def resolve_duplicate(
    knowledge_id: int, action: str, target_id: Optional[int] = None,
    content: Optional[str] = None,
) -> dict:
    """승인 대기 지식에 대한 리뷰어 판단 처리.

    - approve: 새 지식을 그대로 활성화 (중복 아님으로 판단).
    - reject: 새 지식을 반려 상태로 마감 (하드 삭제 안 함 — 감사 기록 보존).
    - merge: 매칭된 기존 지식(target_id, 미지정 시 유사도 1위)의 내용을 교체(재임베딩)
      — 지식 현행화. content가 주어지면(리뷰어가 병합 화면에서 직접 다듬은 최종 내용)
      그걸 쓰고, 없으면 새 지식의 원본 내용을 그대로 쓴다. 새 지식 자신은 반려로 마감.
    """
    if action not in ("approve", "reject", "merge"):
        raise ValueError(f"알 수 없는 action: {action}")

    async with get_conn() as conn:
        pending = await conn.fetchrow(
            "SELECT id, content, status FROM rag_knowledge WHERE id = $1", knowledge_id
        )
    if not pending:
        raise ValueError("지식을 찾을 수 없습니다.")

    if action == "approve":
        async with get_conn() as conn:
            await conn.execute("UPDATE rag_knowledge SET status = 'active' WHERE id = $1", knowledge_id)
        return {"id": knowledge_id, "status": "active"}

    if action == "reject":
        async with get_conn() as conn:
            await conn.execute("UPDATE rag_knowledge SET status = 'rejected' WHERE id = $1", knowledge_id)
        return {"id": knowledge_id, "status": "rejected"}

    # merge
    if target_id is None:
        matches = await get_duplicate_matches(knowledge_id)
        if not matches:
            raise ValueError("병합할 기존 지식을 찾을 수 없습니다 (매칭 기록 없음).")
        target_id = matches[0]["id"]

    merge_content = content.strip() if content and content.strip() else pending["content"]
    embedding = await embedding_service.embed(merge_content)
    async with get_conn() as conn:
        target = await conn.fetchrow(
            "UPDATE rag_knowledge SET content = $1, embedding = $2::vector, updated_at = NOW() "
            "WHERE id = $3 RETURNING id, content",
            merge_content, str(embedding), target_id,
        )
        if not target:
            raise ValueError(f"병합 대상 지식을 찾을 수 없습니다 (id={target_id}).")
        await conn.execute("UPDATE rag_knowledge SET status = 'rejected' WHERE id = $1", knowledge_id)
    return {"id": knowledge_id, "status": "rejected", "merged_into": target_id}


# ─── rag_glossary ─────────────────────────────────────────────────────────────

async def create_glossary(
    namespace: str, term: str, description: str,
    *, created_by_part: Optional[str] = None, created_by_user_id: Optional[int] = None,
) -> dict:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"Namespace '{namespace}' not found")
        dup = await conn.fetchval(
            "SELECT id FROM rag_glossary WHERE namespace_id = $1 AND LOWER(term) = LOWER($2)",
            ns_id, term,
        )
        if dup is not None:
            raise ValueError(f"이미 등록된 용어입니다: {term}")

    embedding = await embedding_service.embed(description)
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO rag_glossary (namespace_id, term, description, embedding, created_by_part, created_by_user_id)
            VALUES ($1, $2, $3, $4::vector, $5, $6)
            RETURNING id, namespace_id, term, description, created_by_part, created_by_user_id
            """,
            ns_id, term, description, str(embedding), created_by_part, created_by_user_id,
        )
        result = dict(row)
        result["namespace"] = namespace
    return result


async def list_glossary(namespace: Optional[str] = None) -> list[dict]:
    async with get_conn() as conn:
        if namespace:
            ns_id = await resolve_namespace_id(conn, namespace)
            if ns_id is None:
                return []
            rows = await conn.fetch(
                f"""
                SELECT {_GLOSSARY_COLS}
                FROM rag_glossary g
                JOIN ops_namespace n ON g.namespace_id = n.id
                LEFT JOIN ops_user u ON g.created_by_user_id = u.id
                WHERE g.namespace_id = $1
                ORDER BY g.id DESC
                """,
                ns_id,
            )
        else:
            rows = await conn.fetch(
                f"""
                SELECT {_GLOSSARY_COLS}
                FROM rag_glossary g
                JOIN ops_namespace n ON g.namespace_id = n.id
                LEFT JOIN ops_user u ON g.created_by_user_id = u.id
                ORDER BY g.id DESC
                """
            )
    return [dict(r) for r in rows]


async def update_glossary(
    glossary_id: int, term: str, description: str,
    *, updated_by_part: Optional[str] = None, updated_by_user_id: Optional[int] = None,
) -> Optional[dict]:
    embedding = await embedding_service.embed(description)
    async with get_conn() as conn:
        # namespace name 조회 (응답용)
        ns_name = await conn.fetchval(
            "SELECT n.name FROM rag_glossary g JOIN ops_namespace n ON g.namespace_id = n.id WHERE g.id = $1",
            glossary_id,
        )
        row = await conn.fetchrow(
            """
            UPDATE rag_glossary
            SET term = $1, description = $2, embedding = $3::vector
            WHERE id = $4
            RETURNING id, namespace_id, term, description, created_by_part, created_by_user_id
            """,
            term, description, str(embedding), glossary_id,
        )
        if not row:
            return None
        result = dict(row)
        result["namespace"] = ns_name
    return result


async def delete_glossary(glossary_id: int) -> bool:
    async with get_conn() as conn:
        result = await conn.execute(
            "DELETE FROM rag_glossary WHERE id = $1", glossary_id
        )
    return result == "DELETE 1"


async def bulk_delete_glossary(ids: list[int]) -> int:
    if not ids:
        return 0
    async with get_conn() as conn:
        result = await conn.execute(
            "DELETE FROM rag_glossary WHERE id = ANY($1::int[])", ids
        )
    return int(result.split()[-1])


async def vector_search_glossary(namespace: str, query_vec: list[float], top_k: int = 30) -> list[dict]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            f"""
            SELECT {_GLOSSARY_COLS},
                   1 - (g.embedding <=> $2::vector) AS similarity
            FROM rag_glossary g
            JOIN ops_namespace n ON g.namespace_id = n.id
            LEFT JOIN ops_user u ON g.created_by_user_id = u.id
            WHERE g.namespace_id = $1 AND g.embedding IS NOT NULL
            ORDER BY g.embedding <=> $2::vector
            LIMIT $3
            """,
            ns_id, str(query_vec), top_k,
        )
    return [dict(r) for r in rows]


async def get_glossary_part(glossary_id: int) -> Optional[str]:
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT created_by_part FROM rag_glossary WHERE id = $1", glossary_id
        )


async def get_glossary_namespace(glossary_id: int) -> Optional[str]:
    """용어집의 namespace name 반환 (네임스페이스 소유 파트 기반 권한 체크용)."""
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT n.name FROM rag_glossary g JOIN ops_namespace n ON g.namespace_id = n.id WHERE g.id = $1",
            glossary_id,
        )


# ─── 벌크 등록 (Ingestion) ──────────────────────────────────────────────────

_INGEST_BATCH_SIZE = 50
_EMBEDDING_MODEL_NAME = "paraphrase-multilingual-mpnet-base-v2"

# asyncio.create_task()로 만든 태스크는 강한 참조가 없으면 GC 대상이 될 수 있음
# (asyncio 공식 문서 권고) — 완료될 때까지 참조를 유지한다.
_background_tasks: set[asyncio.Task] = set()


async def bulk_create_knowledge(
    namespace: str,
    items: list[dict],
    *,
    source_file: Optional[str] = None,
    source_type: str = "manual",
    created_by_part: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
    background: bool = True,
) -> dict:
    """여러 지식을 배치 단위로 등록 — 기본적으로 백그라운드에서 실행.

    작업(rag_ingestion_job) 행을 먼저 만들고 job_id를 즉시 반환한다.
    실제 임베딩/INSERT는 백그라운드 태스크가 _INGEST_BATCH_SIZE개씩 나눠 처리하며,
    배치마다 created_chunks를 갱신(진행률)하고 cancel_requested 플래그를 확인한다.

    Returns:
        {"created": 0, "job_id": int, "status": "processing"} (background=True, 기본값)
        {"created": int, "job_id": int, "status": "completed"|"failed"|"cancelled"} (background=False)
    """
    for item in items:
        item["category"] = _require_category(item.get("category"))

    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"Namespace '{namespace}' not found")

    async with get_conn() as conn:
        job_id = await conn.fetchval("""
            INSERT INTO rag_ingestion_job
                (namespace_id, source_file, source_type, status, total_chunks,
                 embedding_model, created_by_user_id)
            VALUES ($1, $2, $3, 'processing', $4, $5, $6) RETURNING id
        """, ns_id, source_file, source_type, len(items),
            _EMBEDDING_MODEL_NAME, created_by_user_id)

    coro = _run_bulk_ingestion(
        job_id, ns_id, items,
        source_file=source_file, source_type=source_type,
        created_by_part=created_by_part, created_by_user_id=created_by_user_id,
    )
    if background:
        task = asyncio.create_task(coro)
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return {"created": 0, "job_id": job_id, "status": "processing"}
    return await coro


async def _run_bulk_ingestion(
    job_id: int,
    ns_id: int,
    items: list[dict],
    *,
    source_file: Optional[str],
    source_type: str,
    created_by_part: Optional[str],
    created_by_user_id: Optional[int],
) -> dict:
    """배치 단위 임베딩+INSERT. 배치마다 진행률 갱신 + 취소 요청 확인.

    각 청크는 삽입 전 같은 네임스페이스의 활성 지식과 유사도를 비교해, 임계값
    이상이면 'pending_review' 상태로 등록해 검색에서 숨기고 승인 대기 큐로 보낸다
    (중복 등록 방지). 배치 내 청크 수가 많을 수 있어(대량 업로드) 유사도 조회는
    asyncio.gather로 병렬 실행한다.
    """
    created = 0
    pending_total = 0
    dup_threshold = get_thresholds()["duplicate_min_similarity"]
    try:
        for start in range(0, len(items), _INGEST_BATCH_SIZE):
            batch = items[start:start + _INGEST_BATCH_SIZE]
            texts = [it["content"] for it in batch]
            embeddings = await embedding_service.embed_batch(texts)

            match_results = await asyncio.gather(
                *(find_similar_active_knowledge(ns_id, emb) for emb in embeddings)
            )

            rows = []
            pending_chunk_indices: dict[int, list[dict]] = {}  # source_chunk_idx → matches
            for offset, (item, emb, matches) in enumerate(zip(batch, embeddings, match_results)):
                # PDF/XLSX 추출 텍스트에 null byte(\x00)가 포함되면 PostgreSQL이 거부함 → 제거
                content = item["content"].replace("\x00", "")
                chunk_idx = start + offset
                is_duplicate = bool(matches) and matches[0]["similarity"] >= dup_threshold
                if is_duplicate:
                    pending_chunk_indices[chunk_idx] = matches
                rows.append((
                    ns_id,
                    item.get("container_name"),
                    item.get("target_tables"),
                    content,
                    item.get("query_template"),
                    str(emb),
                    item.get("base_weight", 1.0),
                    item.get("category"),
                    source_file,
                    chunk_idx,
                    source_type,
                    created_by_part,
                    created_by_user_id,
                    job_id,
                    "pending_review" if is_duplicate else "active",
                ))

            async with get_conn() as conn:
                await conn.executemany("""
                    INSERT INTO rag_knowledge
                        (namespace_id, container_name, target_tables, content,
                         query_template, embedding, base_weight, category,
                         source_file, source_chunk_idx, source_type,
                         created_by_part, created_by_user_id, ingestion_job_id, status)
                    VALUES ($1, $2, $3, $4, $5, $6::vector, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                """, rows)
                created += len(rows)
                pending_total += len(pending_chunk_indices)

                if pending_chunk_indices:
                    # executemany는 RETURNING을 안 주므로, 방금 넣은 행을 source_chunk_idx로 역매칭
                    new_rows = await conn.fetch(
                        "SELECT id, source_chunk_idx FROM rag_knowledge "
                        "WHERE ingestion_job_id = $1 AND source_chunk_idx = ANY($2::int[])",
                        job_id, list(pending_chunk_indices.keys()),
                    )
                    match_rows = [
                        (r["id"], m["id"], m["similarity"])
                        for r in new_rows
                        for m in pending_chunk_indices[r["source_chunk_idx"]]
                    ]
                    if match_rows:
                        await conn.executemany(
                            "INSERT INTO rag_knowledge_duplicate_match "
                            "(new_knowledge_id, matched_knowledge_id, similarity) VALUES ($1, $2, $3)",
                            match_rows,
                        )

                cancel_requested = await conn.fetchval("""
                    UPDATE rag_ingestion_job SET created_chunks = $1, pending_chunks = $2
                    WHERE id = $3 RETURNING cancel_requested
                """, created, pending_total, job_id)

            if cancel_requested:
                async with get_conn() as conn:
                    await conn.execute("DELETE FROM rag_knowledge WHERE ingestion_job_id = $1", job_id)
                    await conn.execute("""
                        UPDATE rag_ingestion_job
                        SET status = 'cancelled', created_chunks = 0, pending_chunks = 0, completed_at = NOW()
                        WHERE id = $1
                    """, job_id)
                return {"created": 0, "job_id": job_id, "status": "cancelled"}
    except Exception as e:
        logger.exception("인제스천 작업 실패 (job_id=%s)", job_id)
        async with get_conn() as conn:
            await conn.execute("""
                UPDATE rag_ingestion_job
                SET status = 'failed', created_chunks = $1, pending_chunks = $2, error_message = $3, completed_at = NOW()
                WHERE id = $4
            """, created, pending_total, str(e)[:2000], job_id)
        return {"created": created, "job_id": job_id, "status": "failed"}

    async with get_conn() as conn:
        await conn.execute("""
            UPDATE rag_ingestion_job
            SET status = 'completed', created_chunks = $1, pending_chunks = $2, completed_at = NOW()
            WHERE id = $3
        """, created, pending_total, job_id)

    return {"created": created, "job_id": job_id, "status": "completed", "pending": pending_total}


async def get_ingestion_job(job_id: int) -> Optional[dict]:
    """인제스천 작업 단건 상태 조회 (진행률 폴링용)."""
    async with get_conn() as conn:
        row = await conn.fetchrow("""
            SELECT id, namespace_id, source_file, source_type, status,
                   total_chunks, created_chunks, pending_chunks, cancel_requested,
                   error_message, created_at::text, completed_at::text
            FROM rag_ingestion_job WHERE id = $1
        """, job_id)
    return dict(row) if row else None


async def get_ingestion_job_namespace(job_id: int) -> Optional[str]:
    """작업 소유 네임스페이스 이름 조회 (권한 확인용)."""
    async with get_conn() as conn:
        return await conn.fetchval("""
            SELECT n.name FROM rag_ingestion_job j
            JOIN ops_namespace n ON j.namespace_id = n.id
            WHERE j.id = $1
        """, job_id)


async def cancel_ingestion_job(job_id: int) -> Optional[dict]:
    """진행 중인 인제스천 작업에 취소 요청 플래그를 설정.

    실제 중단·롤백은 백그라운드 태스크가 다음 배치 경계에서 수행한다.
    """
    async with get_conn() as conn:
        row = await conn.fetchrow("""
            UPDATE rag_ingestion_job SET cancel_requested = TRUE
            WHERE id = $1 AND status = 'processing'
            RETURNING id, status
        """, job_id)
    return dict(row) if row else None


async def list_ingestion_jobs(namespace: str) -> list[dict]:
    """인제스천 작업 이력 조회."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch("""
            SELECT j.id, j.namespace_id, j.source_file, j.source_type, j.status,
                   j.total_chunks, j.created_chunks, j.pending_chunks, j.auto_glossary, j.auto_fewshot,
                   j.chunk_strategy, j.error_message,
                   j.created_by_user_id, u.username AS created_by_username,
                   j.created_at::text, j.completed_at::text
            FROM rag_ingestion_job j
            LEFT JOIN ops_user u ON j.created_by_user_id = u.id
            WHERE j.namespace_id = $1
            ORDER BY j.created_at DESC
            LIMIT 50
        """, ns_id)
    return [dict(r) for r in rows]


def split_text_to_chunks(
    text: str,
    strategy: str = "auto",
) -> list[str]:
    """텍스트를 청크로 분할.

    strategy:
      - paragraph/section/fixed: LLM Analyzer(ingestion/analyzer.py)가 반환하는 전략
        이름 체계 — ingestion/chunker.py의 분할 로직을 그대로 재사용한다. (예전엔 이
        값들이 아래의 heading/blank_line/separator 체계와 안 맞아 매칭되는 분기가 없어
        전부 fallback으로 빠져 텍스트 전체가 청크 1개로 묶이는 버그가 있었음)
      - auto: ## 헤더 → 빈 줄 → --- 순서로 시도
      - heading: ## 헤더 기준
      - blank_line: 빈 줄 (\\n\\n) 기준
      - separator: --- 기준
      - none: 분할 안함
    """
    import re

    if strategy == "none" or not text.strip():
        return [text.strip()] if text.strip() else []

    if strategy in ("paragraph", "section", "fixed"):
        from agents.knowledge_rag.ingestion.chunker import (
            _chunk_by_paragraphs, _chunk_fixed_size,
            MAX_CHUNK_CHARS, MIN_CHUNK_CHARS, OVERLAP_CHARS,
        )
        if strategy == "fixed":
            chunks = _chunk_fixed_size(text, MAX_CHUNK_CHARS, OVERLAP_CHARS)
        else:
            # "section"은 구조화된 헤더 메타데이터(ParsedDocument.sections)가 있어야
            # 하는데 붙여넣은 순수 텍스트에는 없으므로 단락 분할로 대체
            chunks = _chunk_by_paragraphs(text, MAX_CHUNK_CHARS, MIN_CHUNK_CHARS)
        return [c.text for c in chunks]

    if strategy == "heading" or strategy == "auto":
        # ## 헤더 기준 분할
        parts = re.split(r'\n(?=#{1,3}\s)', text)
        chunks = [p.strip() for p in parts if p.strip()]
        if len(chunks) > 1 or strategy == "heading":
            return chunks

    if strategy == "separator" or strategy == "auto":
        # --- 구분선 기준
        parts = re.split(r'\n---+\n', text)
        chunks = [p.strip() for p in parts if p.strip()]
        if len(chunks) > 1 or strategy == "separator":
            return chunks

    if strategy == "blank_line" or strategy == "auto":
        # 빈 줄 기준
        parts = re.split(r'\n\s*\n', text)
        chunks = [p.strip() for p in parts if p.strip()]
        if len(chunks) > 1:
            return chunks

    # fallback: 전체를 하나의 청크로
    return [text.strip()]
