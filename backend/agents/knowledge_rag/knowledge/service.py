"""지식 베이스, 용어집 CRUD 서비스."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import numpy as np

from core.config import settings
from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service
from agents.knowledge_rag.knowledge.retrieval import find_similar_active_knowledge, get_thresholds, is_keyword_only_category

logger = logging.getLogger(__name__)

_KNOWLEDGE_COLS = """k.id, n.name AS namespace,
    k.content, k.base_weight, k.category, k.status,
    k.source_file, k.source_chunk_idx, k.source_type,
    k.created_by_part, k.created_by_user_id, u.username AS created_by_username,
    k.created_at::text, k.updated_at::text"""

_GLOSSARY_COLS = """g.id, n.name AS namespace, g.term, g.description,
    g.created_by_part, g.created_by_user_id, u.username AS created_by_username"""


def _require_category(category: Optional[str]) -> str:
    """지식 항목의 업무구분(category)은 필수값 — 비어있으면 등록 거부."""
    if not category or not category.strip():
        raise ValueError("업무구분(category)은 필수입니다.")
    category = category.strip()
    if is_keyword_only_category(category):
        # 재발 방지 로그(2026-09-18) — 이 카테고리는 rag_knowledge 안에서 벡터축과
        # final_score로 경쟁하다 top_k LIMIT 단계에서부터 후보 풀에 못 들어가는 구조적
        # 문제가 실측 확인됨("DS14가 뭐야?" 사고, id=20을 ref_common_code로 이전해
        # 해결). 이 카테고리로 신규 등록되는 지식은 같은 문제를 조용히 재현할 수 있어
        # 등록 자체를 막지는 않되(기존 저장 경로를 깨뜨리지 않음), 운영 중 재발을
        # 알아챌 수 있게 로그만 남긴다.
        logger.warning(
            "지식이 키워드 전용 카테고리(%s)로 등록됨 — rag_knowledge 안에서는 "
            "top_k 후보 선별 단계에서 밀려날 수 있음. 구조화 코드/스키마 데이터는 "
            "ref_common_code/ref_db_column(service/refdata) 등록을 권장.",
            category,
        )
    return category


# ─── rag_knowledge ────────────────────────────────────────────────────────────

async def create_knowledge(
    namespace: str,
    content: str,
    category: Optional[str] = None,
    *,
    created_by_part: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> dict:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"Namespace '{namespace}' not found")

    # 카테고리 자동 관리(2026-09-24) — 사람이 안 골라도 LLM 추천 → 실패 시 "미분류"로
    # 항상 유효한 값을 갖는다(service/admin/service.py 참고). ns_id 해석을 위로 옮긴 이유.
    from service.admin.service import resolve_or_create_category
    category = await resolve_or_create_category(ns_id, category, content)
    if is_keyword_only_category(category):
        logger.warning(
            "지식이 키워드 전용 카테고리(%s)로 등록됨 — rag_knowledge 안에서는 "
            "top_k 후보 선별 단계에서 밀려날 수 있음. 구조화 코드/스키마 데이터는 "
            "ref_common_code/ref_db_column(service/refdata) 등록을 권장.",
            category,
        )
    embedding = await embedding_service.embed(content)

    async with get_conn() as conn:
        async with conn.transaction():
            # 네임스페이스 단위 advisory lock — 거의 동일한 내용을 담은 단건 등록
            # 2개가 동시에 들어오면, 유사도 검사 시점엔 서로가 아직 INSERT 전이라
            # 둘 다 "중복 아님"으로 판단해 나란히 active로 들어가버리는 TOCTOU를
            # 막는다. 트랜잭션이 끝나면(커밋/롤백) 자동 해제.
            await conn.execute("SELECT pg_advisory_xact_lock($1)", ns_id)

            matches = await find_similar_active_knowledge(ns_id, embedding)
            is_duplicate = bool(matches) and matches[0]["similarity"] >= get_thresholds()["duplicate_min_similarity"]
            status = "pending_review" if is_duplicate else "active"

            row = await conn.fetchrow(
                f"""
                INSERT INTO rag_knowledge
                    (namespace_id, content, embedding, base_weight, category,
                     created_by_part, created_by_user_id, status, embedding_model)
                VALUES ($1, $2, $3::vector, $4, $5, $6, $7, $8, $9)
                RETURNING id, namespace_id, content, base_weight, category, status,
                          created_by_part, created_by_user_id,
                          created_at::text, updated_at::text
                """,
                ns_id, content, str(embedding), 1.0, category,
                created_by_part, created_by_user_id, status, _EMBEDDING_MODEL_NAME,
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
        new_weight = current["base_weight"]  # 가중치 입력 제거(2026-10-02) — 값은 그대로 둔다
        # category=None은 "변경 없음". 업무구분은 필수값이라 빈 문자열로 초기화하는 것은 허용하지 않음.
        new_category = _require_category(category) if category is not None else current.get("category")

        new_embedding = str(await embedding_service.embed(new_content)) if content else str(current["embedding"])

        row = await conn.fetchrow(
            """
            UPDATE rag_knowledge
            SET content=$1, embedding=$2::vector, base_weight=$3,
                category=$5,
                updated_at=NOW()
            WHERE id = $4
            RETURNING id, namespace_id,
                      content, base_weight, category,
                      created_by_part, created_by_user_id,
                      created_at::text, updated_at::text
            """,
            new_content,
            new_embedding, new_weight, knowledge_id,
            new_category,
        )
        if not row:
            return None
        result = dict(row)
        result["namespace"] = current["ns_name"]
    return result


async def delete_knowledge(knowledge_id: int) -> bool:
    """소프트 삭제 — status='deleted'로만 바꾼다(하드 DELETE 아님).

    검색/목록(search_knowledge, list_knowledge 기본값)은 이미 status='active'만 보므로
    삭제된 행은 즉시 안 보이게 되지만, 실수 삭제 시 복구 가능하다(knowledge-lifecycle-design.md
    §4 Phase 1 — 하드 삭제 위험 대응)."""
    async with get_conn() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "UPDATE rag_knowledge SET status = 'deleted', updated_at = NOW() WHERE id = $1 AND status != 'deleted'",
                knowledge_id,
            )
            # 이 지식으로 메운 지식 공백은 다시 열린다(반려와 같은 처리) — 안 그러면 "공백 메움"으로 계속 세진다
            await conn.execute(
                "UPDATE ops_query_log SET resolved_knowledge_id = NULL, resolved_at = NULL WHERE resolved_knowledge_id = $1",
                knowledge_id,
            )
    return result == "UPDATE 1"


async def bulk_delete_knowledge(ids: list[int]) -> int:
    """소프트 삭제(일괄) — delete_knowledge와 동일하게 status만 변경."""
    if not ids:
        return 0
    async with get_conn() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "UPDATE rag_knowledge SET status = 'deleted', updated_at = NOW() WHERE id = ANY($1::int[]) AND status != 'deleted'",
                ids,
            )
            await conn.execute(
                "UPDATE ops_query_log SET resolved_knowledge_id = NULL, resolved_at = NULL "
                "WHERE resolved_knowledge_id = ANY($1::int[])", ids,
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

# 중복 의심 매칭은 검토 대기 화면·병합 대상 선택에서만 읽는다 — 검토가 끝나면(승인·반려·병합) 지운다(2026-10-06).
# 안 지우면 끝난 건의 매칭이 계속 쌓였다(55건 중 49건).
_DELETE_DUP_MATCHES = "DELETE FROM rag_knowledge_duplicate_match WHERE new_knowledge_id = $1"


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
    content: Optional[str] = None, *, reviewer_username: Optional[str] = None,
) -> dict:
    """승인 대기 지식에 대한 리뷰어 판단 처리.

    - approve: 새 지식을 그대로 활성화 (중복 아님으로 판단).
    - reject: 새 지식을 반려 상태로 마감 (하드 삭제 안 함 — 감사 기록 보존).
    - merge: 매칭된 기존 지식(target_id, 미지정 시 유사도 1위)의 내용을 교체(재임베딩)
      — 지식 현행화. content가 주어지면(리뷰어가 병합 화면에서 직접 다듬은 최종 내용)
      그걸 쓰고, 없으면 새 지식의 원본 내용을 그대로 쓴다. 새 지식 자신은 반려로 마감.

    `reviewer_username`을 `owner`/`reviewed_at`에 기록한다(2026-09-22 추가) — 이전엔 이
    두 컬럼이 스키마에만 있고 어디서도 안 쓰여 "누가 언제 이 판단을 내렸는지" 감사 추적이
    불가능했다(RAG 거버넌스 감사에서 발견). 승인/반려 권한 자체를 분리하는 건 아니고
    (같은 네임스페이스 쓰기 권한자면 누구나 여전히 처리 가능), 기록만 남긴다.
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
            async with conn.transaction():
                await conn.execute(
                    "UPDATE rag_knowledge SET status = 'active', reviewed_at = NOW(), owner = $2 WHERE id = $1",
                    knowledge_id, reviewer_username,
                )
                await conn.execute(_DELETE_DUP_MATCHES, knowledge_id)
        return {"id": knowledge_id, "status": "active"}

    if action == "reject":
        async with get_conn() as conn:
            await conn.execute(
                "UPDATE rag_knowledge SET status = 'rejected', reviewed_at = NOW(), owner = $2 WHERE id = $1",
                knowledge_id, reviewer_username,
            )
            # 이 지식으로 "해결됨" 처리된 질의가 있었다면 연결을 끊는다 — 반려된 내용이
            # 통계 화면에 계속 "해결된 답변"으로 남아있는 걸 막기 위함
            await conn.execute(
                "UPDATE ops_query_log SET resolved_knowledge_id = NULL, resolved_at = NULL WHERE resolved_knowledge_id = $1",
                knowledge_id,
            )
            await conn.execute(_DELETE_DUP_MATCHES, knowledge_id)
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
        async with conn.transaction():
            # 덮어쓰기 전에 기존 content/embedding을 이력 테이블에 먼저 보존한다 —
            # 이전엔 병합이 대상 행을 그 자리에서 덮어써 원문이 어디에도 안 남았다
            # (knowledge-lifecycle-design.md §2.2 "실질적 데이터 소실 위험", 우선순위 1위).
            before = await conn.fetchrow(
                "SELECT content, embedding FROM rag_knowledge WHERE id = $1", target_id
            )
            if not before:
                raise ValueError(f"병합 대상 지식을 찾을 수 없습니다 (id={target_id}).")
            await conn.execute(
                "INSERT INTO rag_knowledge_history (knowledge_id, content, embedding, replaced_by_knowledge_id) "
                "VALUES ($1, $2, $3::vector, $4)",
                target_id, before["content"], before["embedding"], knowledge_id,
            )
            target = await conn.fetchrow(
                "UPDATE rag_knowledge SET content = $1, embedding = $2::vector, updated_at = NOW() "
                "WHERE id = $3 RETURNING id, content",
                merge_content, str(embedding), target_id,
            )
            await conn.execute(
                "UPDATE rag_knowledge SET status = 'rejected', reviewed_at = NOW(), owner = $2 WHERE id = $1",
                knowledge_id, reviewer_username,
            )
            await conn.execute(_DELETE_DUP_MATCHES, knowledge_id)
        # 반려되는 pending 지식이 이미 어떤 질의를 "해결"한 상태였다면, 실제 내용이 옮겨간
        # target으로 연결을 옮겨줘야 통계 화면이 계속 유효한 내용을 보여준다
        await conn.execute(
            "UPDATE ops_query_log SET resolved_knowledge_id = $2 WHERE resolved_knowledge_id = $1",
            knowledge_id, target_id,
        )
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
# 실제 임베딩 계산에 쓰이는 모델(core/config.py)과 반드시 같은 값이어야 한다 — v2.72에서
# mpnet→KURE-v1로 실제 임베딩 모델을 교체했을 때 이 상수는 하드코딩된 옛 이름 그대로
# 남아있어서, 그 이후 등록된 지식 18건이 실제로는 KURE-v1(1024차원)로 임베딩됐으면서도
# metadata엔 옛 모델명으로 잘못 기록되는 조용한 데이터 오염이 있었다(2026-09-22 발견,
# `vector_dims(embedding)`으로 실제 차원 대조해 확인). 하드코딩 대신 설정값을 그대로
# 참조해 앞으로는 이 둘이 어긋날 수 없게 한다.
_EMBEDDING_MODEL_NAME = settings.embedding_model

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
    after_activation: Optional[Callable[[], Awaitable[int]]] = None,
) -> dict:
    """여러 지식을 배치 단위로 등록 — 기본적으로 백그라운드에서 실행.

    after_activation: job이 성공적으로 일괄 전환된 "뒤에만" 실행할 후속 작업(자동 용어 추출).
    반환값(등록된 용어 수)은 rag_ingestion_job.auto_glossary에 기록된다. 예전엔 라우터가 job
    시작 직후 바로 용어를 추출해, job이 실패·취소돼도 용어만 남았다(2026-09-28).

    작업(rag_ingestion_job) 행을 먼저 만들고 job_id를 즉시 반환한다.
    실제 임베딩/INSERT는 백그라운드 태스크가 _INGEST_BATCH_SIZE개씩 나눠 처리하며,
    배치마다 created_chunks를 갱신(진행률)하고 cancel_requested 플래그를 확인한다.

    Returns:
        {"created": 0, "job_id": int, "status": "processing"} (background=True, 기본값)
        {"created": int, "job_id": int, "status": "completed"|"failed"|"cancelled"} (background=False)
    """
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"Namespace '{namespace}' not found")

    # 카테고리 자동 관리(2026-09-24) — 컨플루언스 벌크는 이 시점에 이미 페이지별로
    # 카테고리가 정해진 채로 들어오지만(빈 값 없음, resolve_or_create_category가 그대로
    # 반환), 파일 업로드/텍스트 분할처럼 프론트가 못 채웠거나 안 채운 경우도
    # 여기서 항상 유효한 값을 갖도록 안전망을 건다.
    from service.admin.service import resolve_or_create_category
    for item in items:
        item["category"] = await resolve_or_create_category(ns_id, item.get("category"), item["content"])
        if is_keyword_only_category(item["category"]):
            logger.warning(
                "지식이 키워드 전용 카테고리(%s)로 등록됨 — rag_knowledge 안에서는 "
                "top_k 후보 선별 단계에서 밀려날 수 있음. 구조화 코드/스키마 데이터는 "
                "ref_common_code/ref_db_column(service/refdata) 등록을 권장.",
                item["category"],
            )

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
        namespace_name=namespace,
        after_activation=after_activation,
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
    namespace_name: Optional[str] = None,
    after_activation: Optional[Callable[[], Awaitable[int]]] = None,
    source_file: Optional[str],
    source_type: str,
    created_by_part: Optional[str],
    created_by_user_id: Optional[int],
) -> dict:
    """배치 단위 임베딩+INSERT. 배치마다 진행률 갱신 + 취소 요청 확인.

    2단계 활성화(2026-09-28, WBS 1-2): 배치 행은 'staging'(비중복)/'staging_review'
    (중복 의심)로 넣어 job이 끝날 때까지 검색·검토 큐 어디에도 안 보이게 하고, 마지막에
    한 트랜잭션으로 'active'/'pending_review'로 일괄 전환한다. 예전엔 50건 배치마다
    바로 active로 커밋돼 job 도중(그리고 실패 시엔 영구히) 반쪽짜리 문서가 챗 답변에
    섞였다. 취소·실패 시엔 스테이징 행을 지워 흔적을 남기지 않는다.

    각 청크는 삽입 전 같은 네임스페이스의 활성 지식과 유사도를 비교해, 임계값
    이상이면 'pending_review' 상태로 등록해 검색에서 숨기고 승인 대기 큐로 보낸다
    (중복 등록 방지). 배치 내 청크 수가 많을 수 있어(대량 업로드) 유사도 조회는
    asyncio.gather로 병렬 실행한다.
    """
    created = 0
    pending_total = 0
    dup_threshold = get_thresholds()["duplicate_min_similarity"]
    # 컨플루언스 재등록 교체(2026-09-28) — 이 등록에 실린 페이지의 기존 active 행은 옛 버전이다.
    # 중복 검사에선 비교 대상에서 빼고(안 그러면 안 바뀐 청크가 승인 대기로 빠진 뒤 옛 버전 폐기로
    # 내용이 사라짐), 일괄 전환 트랜잭션에서 deprecated로 내린다(실패·취소면 옛 버전 그대로 유지).
    replacing_pages = sorted({str(it["confluence_page_id"]) for it in items if it.get("confluence_page_id")})
    try:
        for start in range(0, len(items), _INGEST_BATCH_SIZE):
            batch = items[start:start + _INGEST_BATCH_SIZE]
            texts = [it["content"] for it in batch]
            embeddings = await embedding_service.embed_batch(texts)

            match_results = await asyncio.gather(
                *(find_similar_active_knowledge(
                    ns_id, emb, staging_job_id=job_id, replacing_confluence_pages=replacing_pages or None,
                ) for emb in embeddings)
            )
            db_is_duplicate = [
                bool(m) and m[0]["similarity"] >= dup_threshold for m in match_results
            ]

            # 배치 내 상호 중복 검사 — 같은 배치 안의 항목끼리는 아직 INSERT 전이라
            # find_similar_active_knowledge(DB 조회)가 서로를 못 본다(둘 다 'active'로
            # 조회되지 않으므로). 정규화된 임베딩이라 내적=코사인 유사도. DB 중복이
            # 아닌(=active로 남을 예정인) 더 앞쪽 항목을 기준으로만 비교해서, 먼저
            # 등장한 항목이 대표로 남고 이후 유사 항목들이 그걸 가리키게 한다.
            local_match: dict[int, tuple[int, float]] = {}  # offset -> (matched offset, similarity)
            if len(batch) > 1:
                emb_matrix = np.array(embeddings, dtype=np.float32)
                sims = emb_matrix @ emb_matrix.T
                for i in range(len(batch)):
                    if db_is_duplicate[i]:
                        continue
                    best_j, best_sim = None, 0.0
                    for j in range(i):
                        if db_is_duplicate[j] or j in local_match:
                            continue  # j 자신도 결국 pending으로 빠질 항목이면 기준으로 안 삼음
                        sim = float(sims[i, j])
                        if sim > best_sim:
                            best_j, best_sim = j, sim
                    if best_j is not None and best_sim >= dup_threshold:
                        local_match[i] = (best_j, best_sim)

            rows = []
            pending_chunk_indices: dict[int, list[dict]] = {}  # source_chunk_idx → DB matches
            local_pending: dict[int, tuple[int, float]] = {}   # chunk_idx → (matched chunk_idx, similarity), 같은 배치 내 매칭
            for offset, (item, emb, matches) in enumerate(zip(batch, embeddings, match_results)):
                # PDF/XLSX 추출 텍스트에 null byte(\x00)가 포함되면 PostgreSQL이 거부함 → 제거
                content = item["content"].replace("\x00", "")
                chunk_idx = start + offset
                is_duplicate = db_is_duplicate[offset] or offset in local_match
                if db_is_duplicate[offset]:
                    pending_chunk_indices[chunk_idx] = matches
                elif offset in local_match:
                    matched_offset, sim = local_match[offset]
                    local_pending[chunk_idx] = (start + matched_offset, sim)
                rows.append((
                    ns_id,
                    content,
                    str(emb),
                    1.0,  # 가중치 입력 제거(2026-10-02)
                    item.get("category"),
                    source_file,
                    chunk_idx,
                    source_type,
                    created_by_part,
                    created_by_user_id,
                    job_id,
                    "staging_review" if is_duplicate else "staging",
                    _EMBEDDING_MODEL_NAME,
                    item.get("confluence_page_id"),
                    item.get("confluence_version"),
                    item.get("heading_path") or None,
                ))

            async with get_conn() as conn:
                await conn.executemany("""
                    INSERT INTO rag_knowledge
                        (namespace_id, content,
                         embedding, base_weight, category,
                         source_file, source_chunk_idx, source_type,
                         created_by_part, created_by_user_id, ingestion_job_id, status,
                         embedding_model, confluence_page_id, confluence_version, heading_path)
                    VALUES ($1, $2, $3::vector, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                """, rows)
                created += len(rows)
                pending_total += len(pending_chunk_indices) + len(local_pending)

                if pending_chunk_indices or local_pending:
                    # executemany는 RETURNING을 안 주므로, 방금 넣은 행을 source_chunk_idx로
                    # 역매칭 — local_pending의 매칭 대상(같은 배치 내 다른 청크)의 id도
                    # 같은 방식으로 필요하므로 함께 조회한다
                    needed_idxs = set(pending_chunk_indices.keys()) | set(local_pending.keys()) | {
                        matched_idx for matched_idx, _ in local_pending.values()
                    }
                    new_rows = await conn.fetch(
                        "SELECT id, source_chunk_idx FROM rag_knowledge "
                        "WHERE ingestion_job_id = $1 AND source_chunk_idx = ANY($2::int[])",
                        job_id, list(needed_idxs),
                    )
                    id_by_chunk_idx = {r["source_chunk_idx"]: r["id"] for r in new_rows}
                    match_rows = [
                        (id_by_chunk_idx[chunk_idx], m["id"], m["similarity"])
                        for chunk_idx, matches in pending_chunk_indices.items()
                        for m in matches
                    ]
                    for chunk_idx, (matched_idx, sim) in local_pending.items():
                        if chunk_idx in id_by_chunk_idx and matched_idx in id_by_chunk_idx:
                            match_rows.append((id_by_chunk_idx[chunk_idx], id_by_chunk_idx[matched_idx], sim))
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
                await _discard_staged_job(job_id, "cancelled")
                return {"created": 0, "job_id": job_id, "status": "cancelled"}
    except Exception as e:
        logger.exception("인제스천 작업 실패 (job_id=%s)", job_id)
        # 스테이징 행은 한 번도 노출된 적 없으니 지워도 안전 — 예전엔 실패한 job의 앞 배치가
        # active로 남아 반쪽짜리 문서가 계속 검색됐다. 진행 정도는 error_message에만 남긴다.
        try:
            await _discard_staged_job(
                job_id, "failed", error_message=f"{str(e)[:1900]} (진행 {created}/{len(items)}건 폐기)",
            )
        except Exception:
            # DB 자체가 죽은 경우 — 스테이징 행은 원래 안 보이고, 재시작 시
            # _cleanup_orphaned_ingestion_jobs가 processing job과 함께 정리한다.
            logger.exception("실패한 인제스천 작업 정리 실패 (job_id=%s)", job_id)
        return {"created": 0, "job_id": job_id, "status": "failed"}

    # 후속 작업(자동 용어 추출)은 실제로 active가 된 행이 있을 때만 — 전부 승인 대기로 빠졌으면
    # 검토에서 반려될 수 있는 내용이라 용어를 미리 만들지 않는다(/code-review 지적, 2026-09-28).
    run_follow_up = after_activation is not None and created > pending_total

    # 일괄 전환 — job 완료 표시와 행 전환을 한 트랜잭션으로 묶는다. cancel_requested 조건은
    # 마지막 배치 확인 뒤에 들어온 취소 요청(레이스)을 여기서 잡기 위함: 0행이면 전환 대신 폐기.
    # 후속 작업이 있으면 job은 그게 끝날 때까지 'processing'으로 둔다 — 화면이 processing 동안만
    # 폴링해서, 먼저 completed로 바꾸면 뒤늦게 기록되는 용어 수가 새로고침 전엔 안 보였다
    # (/code-review 지적). 행 전환 자체는 이 트랜잭션에서 끝나므로 검색 반영은 지연되지 않는다.
    try:
        async with get_conn() as conn:
            async with conn.transaction():
                activated = await conn.fetchval("""
                    UPDATE rag_ingestion_job
                    SET status = CASE WHEN $4 THEN 'processing' ELSE 'completed' END,
                        created_chunks = $1, pending_chunks = $2,
                        completed_at = CASE WHEN $4 THEN NULL ELSE NOW() END
                    WHERE id = $3 AND cancel_requested = FALSE
                    RETURNING id
                """, created, pending_total, job_id, run_follow_up)
                if activated:
                    if replacing_pages:
                        # 새 행은 아직 staging이라 status='active' 조건에 안 걸린다 — 옛 버전만 내림
                        await conn.execute("""
                            UPDATE rag_knowledge SET status = 'deprecated'
                            WHERE namespace_id = $1 AND confluence_page_id = ANY($2::text[]) AND status = 'active'
                        """, ns_id, replacing_pages)
                    await conn.execute("""
                        UPDATE rag_knowledge
                        SET status = CASE status WHEN 'staging' THEN 'active' ELSE 'pending_review' END
                        WHERE ingestion_job_id = $1 AND status IN ('staging', 'staging_review')
                    """, job_id)
        if not activated:
            await _discard_staged_job(job_id, "cancelled")
            return {"created": 0, "job_id": job_id, "status": "cancelled"}
    except Exception as e:
        # 전환 트랜잭션은 롤백됐으니 행은 전부 staging 그대로 — 여기서 안 잡으면 job이
        # 'processing'으로 영원히 남고(UI 무한 진행중) 재시작 때 에러 기록 없이 폐기된다.
        logger.exception("인제스천 일괄 전환 실패 (job_id=%s)", job_id)
        try:
            await _discard_staged_job(job_id, "failed", error_message=f"일괄 전환 실패: {str(e)[:1900]}")
        except Exception:
            logger.exception("일괄 전환 실패 후 정리도 실패 (job_id=%s) — 재시작 시 정리됨", job_id)
        return {"created": 0, "job_id": job_id, "status": "failed"}

    # 시맨틱 캐시엔 job 도중 계산된 답("지식 없음" 포함)이 TTL 동안 남아 있어, 방금 활성화한
    # 지식이 반영 안 된 답이 계속 서빙될 수 있다 — 네임스페이스 캐시를 비운다(best-effort).
    if namespace_name:
        try:
            from shared.cache import invalidate_namespace
            await invalidate_namespace(namespace_name)
        except Exception:
            logger.warning("수집 완료 후 시맨틱 캐시 무효화 실패 (job_id=%s)", job_id, exc_info=True)

    result = {"created": created, "job_id": job_id, "status": "completed", "pending": pending_total}
    if run_follow_up:
        # 지식은 이미 커밋·활성화됐으니 여기 실패는 job 결과를 바꾸지 않는다(로그만). 어떤 경우든
        # job은 마지막에 completed로 닫는다(안 닫으면 화면이 무한 진행중).
        glossary_count = 0
        try:
            glossary_count = await after_activation()
            result["auto_glossary"] = glossary_count
        except Exception:
            logger.warning("수집 완료 후 자동 용어 추출 실패 (job_id=%s)", job_id, exc_info=True)
        finally:
            async with get_conn() as conn:
                await conn.execute(
                    "UPDATE rag_ingestion_job SET status = 'completed', auto_glossary = $1, completed_at = NOW() "
                    "WHERE id = $2",
                    glossary_count, job_id,
                )
    return result


async def _discard_staged_job(job_id: int, status: str, *, error_message: Optional[str] = None) -> None:
    """job의 스테이징 행을 지우고 job을 종료 상태로 표시(취소·실패 공통).

    스테이징 상태로 한정해 지운다 — 이미 전환된(검토자가 다룰 수 있는) 행은 건드리지 않는다.
    """
    async with get_conn() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM rag_knowledge WHERE ingestion_job_id = $1 AND status IN ('staging', 'staging_review')",
                job_id,
            )
            await conn.execute("""
                UPDATE rag_ingestion_job
                SET status = $2, created_chunks = 0, pending_chunks = 0, completed_at = NOW(),
                    error_message = COALESCE($3, error_message)
                WHERE id = $1
            """, job_id, status, error_message)


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

    실제 중단·롤백은 백그라운드 태스크가 다음 배치 경계에서 수행한다. 일괄 전환이 끝난 뒤
    후속 작업(자동 용어 추출) 동안에도 job은 'processing'이라 여기서 요청은 받지만, 행은 이미
    반영된 뒤라 취소되지 않고 그대로 completed로 끝난다(몇 초 창).
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
                   j.total_chunks, j.created_chunks, j.pending_chunks, j.auto_glossary,
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
