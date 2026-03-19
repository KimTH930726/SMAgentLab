"""지식 베이스 및 용어집 CRUD — 네임스페이스 소유 파트 기반 권한."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.dependencies import get_current_user, check_namespace_ownership
from agents.knowledge_rag.knowledge.schemas import (
    GlossaryCreate, GlossaryOut, GlossaryUpdate,
    KnowledgeCreate, KnowledgeOut, KnowledgeUpdate,
)
from agents.knowledge_rag.knowledge import service

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


# ─── ops_knowledge ─────────────────────────────────────────────────────────────

@router.get("", response_model=list[KnowledgeOut])
async def get_knowledge_list(
    namespace: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
):
    return await service.list_knowledge(namespace)


@router.post("", response_model=KnowledgeOut, status_code=201)
async def add_knowledge(body: KnowledgeCreate, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(body.namespace, user)
    row = await service.create_knowledge(
        namespace=body.namespace,
        content=body.content,
        container_name=body.container_name,
        target_tables=body.target_tables,
        query_template=body.query_template,
        base_weight=body.base_weight,
        category=body.category,
        created_by_part=user["part"],
        created_by_user_id=user["id"],
    )
    return row


@router.put("/{knowledge_id}", response_model=KnowledgeOut)
async def modify_knowledge(knowledge_id: int, body: KnowledgeUpdate, user: dict = Depends(get_current_user)):
    ns = await service.get_knowledge_namespace(knowledge_id)
    if ns is None:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    await check_namespace_ownership(ns, user)

    row = await service.update_knowledge(
        knowledge_id=knowledge_id,
        content=body.content,
        container_name=body.container_name,
        target_tables=body.target_tables,
        query_template=body.query_template,
        base_weight=body.base_weight,
        category=body.category,
        updated_by_part=user["part"],
        updated_by_user_id=user["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return row


@router.delete("/{knowledge_id}", status_code=204)
async def remove_knowledge(knowledge_id: int, user: dict = Depends(get_current_user)):
    ns = await service.get_knowledge_namespace(knowledge_id)
    if ns is None:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    await check_namespace_ownership(ns, user)

    deleted = await service.delete_knowledge(knowledge_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge not found")


# ─── ops_glossary ──────────────────────────────────────────────────────────────

@router.get("/glossary", response_model=list[GlossaryOut])
async def get_glossary_list(
    namespace: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
):
    return await service.list_glossary(namespace)


@router.post("/glossary", response_model=GlossaryOut, status_code=201)
async def add_glossary(body: GlossaryCreate, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(body.namespace, user)
    return await service.create_glossary(
        body.namespace, body.term, body.description,
        created_by_part=user["part"], created_by_user_id=user["id"],
    )


@router.put("/glossary/{glossary_id}", response_model=GlossaryOut)
async def modify_glossary(glossary_id: int, body: GlossaryUpdate, user: dict = Depends(get_current_user)):
    ns = await service.get_glossary_namespace(glossary_id)
    if ns is None:
        raise HTTPException(status_code=404, detail="Glossary term not found")
    await check_namespace_ownership(ns, user)

    row = await service.update_glossary(
        glossary_id, body.term, body.description,
        updated_by_part=user["part"], updated_by_user_id=user["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Glossary term not found")
    return row


@router.delete("/glossary/{glossary_id}", status_code=204)
async def remove_glossary(glossary_id: int, user: dict = Depends(get_current_user)):
    ns = await service.get_glossary_namespace(glossary_id)
    if ns is None:
        raise HTTPException(status_code=404, detail="Glossary term not found")
    await check_namespace_ownership(ns, user)

    deleted = await service.delete_glossary(glossary_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Glossary term not found")
