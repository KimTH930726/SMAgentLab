"""Few-shot CRUD — 네임스페이스 소유 파트 기반 권한."""
from fastapi import APIRouter, Depends, HTTPException

from core.database import get_conn, resolve_namespace_id
from core.dependencies import get_current_user, check_namespace_ownership
from shared.embedding import embedding_service
from agents.knowledge_rag.knowledge import retrieval
from agents.knowledge_rag.fewshot.schemas import (
    FewshotCreate, FewshotOut, FewshotUpdate, FewshotStatusUpdate,
    FewshotSearchRequest, FewshotSearchResponse, FewshotResult,
)

router = APIRouter(prefix="/api/fewshots", tags=["fewshots"])


@router.get("", response_model=list[FewshotOut])
async def list_fewshots(namespace: str, status: str = None, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        if status:
            rows = await conn.fetch(
                """
                SELECT f.id, n.name AS namespace, f.question, f.answer, f.knowledge_id,
                       f.created_by_part, f.created_by_user_id,
                       u.username AS created_by_username,
                       f.created_at::text,
                       COALESCE(f.status, 'active') AS status
                FROM rag_fewshot f
                JOIN ops_namespace n ON f.namespace_id = n.id
                LEFT JOIN ops_user u ON f.created_by_user_id = u.id
                WHERE f.namespace_id = $1
                  AND f.status = $2
                ORDER BY f.created_at DESC
                """,
                ns_id, status,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT f.id, n.name AS namespace, f.question, f.answer, f.knowledge_id,
                       f.created_by_part, f.created_by_user_id,
                       u.username AS created_by_username,
                       f.created_at::text,
                       COALESCE(f.status, 'active') AS status
                FROM rag_fewshot f
                JOIN ops_namespace n ON f.namespace_id = n.id
                LEFT JOIN ops_user u ON f.created_by_user_id = u.id
                WHERE f.namespace_id = $1
                ORDER BY f.created_at DESC
                """,
                ns_id,
            )
    return [dict(r) for r in rows]


@router.post("/search", response_model=FewshotSearchResponse)
async def search_fewshots(body: FewshotSearchRequest, user: dict = Depends(get_current_user)):
    query_vec = await embedding_service.embed(body.question)
    fewshots = await retrieval.fetch_fewshots(body.namespace, query_vec, limit=2)
    prompt_section = retrieval.build_fewshot_section(fewshots)
    return FewshotSearchResponse(
        question=body.question,
        namespace=body.namespace,
        fewshots=[
            FewshotResult(question=fs["question"], answer=fs["answer"], similarity=fs["similarity"])
            for fs in fewshots
        ],
        prompt_section=prompt_section or "(검색된 few-shot 없음 — 유사도 0.6 미만)",
    )


@router.post("", response_model=FewshotOut, status_code=201)
async def create_fewshot(body: FewshotCreate, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(body.namespace, user)
    embedding = await embedding_service.embed(body.question)
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, body.namespace)
        if ns_id is None:
            raise HTTPException(status_code=404, detail="네임스페이스를 찾을 수 없습니다.")
        row = await conn.fetchrow(
            """
            INSERT INTO rag_fewshot (namespace_id, question, answer, knowledge_id, embedding,
                                     created_by_part, created_by_user_id)
            VALUES ($1, $2, $3, $4, $5::vector, $6, $7)
            RETURNING id, $8::text AS namespace, question, answer, knowledge_id,
                      created_by_part, created_by_user_id, created_at::text,
                      COALESCE(status, 'active') AS status
            """,
            ns_id, body.question, body.answer, body.knowledge_id,
            str(embedding), user["part"], user["id"], body.namespace,
        )
    return dict(row)


@router.put("/{fewshot_id}", response_model=FewshotOut)
async def update_fewshot(fewshot_id: int, body: FewshotUpdate, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT f.*, n.name AS ns_name FROM rag_fewshot f JOIN ops_namespace n ON f.namespace_id = n.id WHERE f.id = $1",
            fewshot_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Few-shot not found")

        await check_namespace_ownership(existing["ns_name"], user)

        new_question = body.question if body.question is not None else existing["question"]
        new_answer = body.answer if body.answer is not None else existing["answer"]

        if body.question is not None and body.question != existing["question"]:
            embedding = await embedding_service.embed(new_question)
            row = await conn.fetchrow(
                """
                UPDATE rag_fewshot SET question = $2, answer = $3, embedding = $4::vector,
                       created_by_part = $5, created_by_user_id = $6
                WHERE id = $1
                RETURNING id, namespace_id, question, answer, knowledge_id,
                          created_by_part, created_by_user_id, created_at::text
                """,
                fewshot_id, new_question, new_answer, str(embedding),
                user["part"], user["id"],
            )
        else:
            row = await conn.fetchrow(
                """
                UPDATE rag_fewshot SET question = $2, answer = $3,
                       created_by_part = $4, created_by_user_id = $5
                WHERE id = $1
                RETURNING id, namespace_id, question, answer, knowledge_id,
                          created_by_part, created_by_user_id, created_at::text
                """,
                fewshot_id, new_question, new_answer,
                user["part"], user["id"],
            )
        result = dict(row)
        result["namespace"] = existing["ns_name"]
    return result


@router.delete("/{fewshot_id}", status_code=204)
async def delete_fewshot(fewshot_id: int, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT f.namespace_id, n.name AS ns_name FROM rag_fewshot f JOIN ops_namespace n ON f.namespace_id = n.id WHERE f.id = $1",
            fewshot_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Few-shot not found")
        await check_namespace_ownership(existing["ns_name"], user)
        result = await conn.execute("DELETE FROM rag_fewshot WHERE id = $1", fewshot_id)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Few-shot not found")


@router.patch("/{fewshot_id}/status", status_code=200)
async def update_fewshot_status(fewshot_id: int, body: FewshotStatusUpdate, user: dict = Depends(get_current_user)):
    if body.status not in ("active", "candidate", "rejected"):
        raise HTTPException(status_code=400, detail="유효하지 않은 상태값입니다.")
    async with get_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT f.namespace_id, n.name AS ns_name FROM rag_fewshot f JOIN ops_namespace n ON f.namespace_id = n.id WHERE f.id = $1",
            fewshot_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Few-shot not found")
        await check_namespace_ownership(existing["ns_name"], user)
        await conn.execute("UPDATE rag_fewshot SET status = $1 WHERE id = $2", body.status, fewshot_id)
    return {"status": body.status}
