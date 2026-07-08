"""지식 베이스 및 용어집 CRUD — 네임스페이스 소유 파트 기반 권한."""
import csv
import io
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from shared.embedding import embedding_service

from core.dependencies import get_current_user, check_namespace_ownership
from agents.knowledge_rag.knowledge.schemas import (
    GlossaryCreate, GlossaryOut, GlossaryUpdate,
    KnowledgeCreate, KnowledgeOut, KnowledgeUpdate,
    BulkCreateRequest, IngestionJobOut,
)
from agents.knowledge_rag.knowledge import service

import logging

logger = logging.getLogger(__name__)

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


class BulkDeleteRequest(BaseModel):
    ids: list[int]


class VectorSearchRequest(BaseModel):
    namespace: str
    query: str
    top_k: int = 30


@router.post("/bulk-delete", status_code=200)
async def bulk_remove_knowledge(body: BulkDeleteRequest, user: dict = Depends(get_current_user)):
    deleted = await service.bulk_delete_knowledge(body.ids)
    return {"deleted": deleted}


@router.post("/search")
async def vector_search_knowledge(body: VectorSearchRequest, user: dict = Depends(get_current_user)):
    query_vec = await embedding_service.embed(body.query)
    results = await service.vector_search_knowledge(body.namespace, query_vec, body.top_k)
    return results


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
    try:
        return await service.create_glossary(
            body.namespace, body.term, body.description,
            created_by_part=user["part"], created_by_user_id=user["id"],
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


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


@router.post("/glossary/bulk-delete", status_code=200)
async def bulk_remove_glossary(body: BulkDeleteRequest, user: dict = Depends(get_current_user)):
    deleted = await service.bulk_delete_glossary(body.ids)
    return {"deleted": deleted}


@router.post("/glossary/search")
async def vector_search_glossary(body: VectorSearchRequest, user: dict = Depends(get_current_user)):
    query_vec = await embedding_service.embed(body.query)
    results = await service.vector_search_glossary(body.namespace, query_vec, body.top_k)
    return results


# ─── 벌크 등록 / 인제스천 ────────────────────────────────────────────────────

@router.post("/bulk", status_code=201)
async def bulk_create(body: BulkCreateRequest, user: dict = Depends(get_current_user)):
    """JSON 배열로 지식 벌크 등록."""
    await check_namespace_ownership(body.namespace, user)
    result = await service.bulk_create_knowledge(
        namespace=body.namespace,
        items=[item.model_dump() for item in body.items],
        source_file=body.source_file,
        source_type=body.source_type,
        created_by_part=user["part"],
        created_by_user_id=user["id"],
    )
    return result


@router.post("/import/csv", status_code=201)
async def import_csv(
    file: UploadFile = File(...),
    namespace: str = Form(...),
    column_mapping: str = Form(...),
    category: Optional[str] = Form(default=None),
    user: dict = Depends(get_current_user),
):
    """CSV 파일 업로드 → 파싱 → 벌크 등록.

    column_mapping: JSON 문자열 {"content": "csv_col_name", "category": "csv_col_name", ...}
    """
    await check_namespace_ownership(namespace, user)

    # CSV 파싱 — UTF-8-BOM → UTF-8 → EUC-KR(CP949) 순으로 디코딩 시도
    try:
        raw = await file.read()
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp949"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            raise HTTPException(
                status_code=400,
                detail="CSV 인코딩을 인식할 수 없습니다. UTF-8 또는 EUC-KR(한글 Windows)로 저장된 파일만 지원합니다.",
            )
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV 파싱 실패: {e}")

    if not rows:
        raise HTTPException(status_code=400, detail="CSV에 데이터가 없습니다.")

    # 컬럼 매핑
    try:
        mapping = json.loads(column_mapping)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="column_mapping이 유효한 JSON이 아닙니다.")

    content_col = mapping.get("content")
    if not content_col:
        raise HTTPException(status_code=400, detail="content 컬럼 매핑이 필요합니다.")

    # 매핑 적용
    items = []
    for row in rows:
        content = row.get(content_col, "").strip()
        if not content:
            continue
        item = {"content": content}
        if mapping.get("category") and row.get(mapping["category"]):
            item["category"] = row[mapping["category"]].strip()
        elif category:
            item["category"] = category
        if mapping.get("container_name") and row.get(mapping["container_name"]):
            item["container_name"] = row[mapping["container_name"]].strip()
        if mapping.get("target_tables") and row.get(mapping["target_tables"]):
            item["target_tables"] = [t.strip() for t in row[mapping["target_tables"]].split(",") if t.strip()]
        if mapping.get("query_template") and row.get(mapping["query_template"]):
            item["query_template"] = row[mapping["query_template"]].strip()
        items.append(item)

    if not items:
        raise HTTPException(status_code=400, detail="유효한 데이터가 없습니다.")

    result = await service.bulk_create_knowledge(
        namespace=namespace,
        items=items,
        source_file=file.filename,
        source_type="csv_import",
        created_by_part=user["part"],
        created_by_user_id=user["id"],
    )
    return result


from pydantic import BaseModel as _BM


class _TextSplitBody(_BM):
    namespace: str
    raw_text: str
    strategy: str = "auto"
    category: Optional[str] = None


@router.post("/import/text-split", status_code=201)
async def import_text_split(body: _TextSplitBody, user: dict = Depends(get_current_user)):
    """대량 텍스트 → 자동 분할 → 벌크 등록."""
    await check_namespace_ownership(body.namespace, user)

    chunks = service.split_text_to_chunks(body.raw_text, body.strategy)
    if not chunks:
        raise HTTPException(status_code=400, detail="분할된 청크가 없습니다.")

    items = [{"content": c, "category": body.category} for c in chunks]
    result = await service.bulk_create_knowledge(
        namespace=body.namespace,
        items=items,
        source_file="텍스트 직접입력",
        source_type="paste_split",
        created_by_part=user["part"],
        created_by_user_id=user["id"],
    )
    return {**result, "chunks": len(chunks)}


class _TextSplitPreviewBody(_BM):
    raw_text: str
    strategy: str = "auto"


@router.post("/import/text-split/preview")
async def preview_text_split(body: _TextSplitPreviewBody, user: dict = Depends(get_current_user)):
    """텍스트 분할 미리보기 — LLM Analyzer로 전략 자동 결정."""
    strategy = body.strategy
    detected_strategy = strategy

    try:
        from agents.knowledge_rag.ingestion.analyzer import analyze_document
        from service.llm.factory import get_llm_provider
        from core.security import get_user_llm_credentials
        llm = get_llm_provider()
        analysis = await analyze_document(body.raw_text, llm, user_credentials=get_user_llm_credentials(user))
        detected_strategy = analysis.get("chunk_strategy", "auto")
        strategy = detected_strategy
    except Exception as e:
        logger.warning("텍스트 분할 전략 자동 감지 실패 (auto 사용): %s", e)

    chunks = service.split_text_to_chunks(body.raw_text, strategy)
    return {"chunks": chunks, "count": len(chunks), "detected_strategy": detected_strategy}


# ─── 파일 업로드 + 자동 청킹 (Tier 2) ────────────────────────────────────────

@router.post("/import/file", status_code=201)
async def import_file(
    file: UploadFile = File(...),
    namespace: str = Form(...),
    chunk_strategy: str = Form(default="auto"),
    category: Optional[str] = Form(default=None),
    auto_analyze: bool = Form(default=False),
    auto_tag: bool = Form(default=False),
    auto_glossary: bool = Form(default=False),
    auto_fewshot: bool = Form(default=False),
    user: dict = Depends(get_current_user),
):
    """파일 업로드 → 파싱 → 청킹 → 벌크 등록.

    지원 포맷: .txt, .md, .pdf, .xlsx, .xlsm, .csv
    chunk_strategy: auto, section, paragraph, fixed
    auto_analyze: True이면 LLM Analyzer Agent로 전략/메타데이터 자동 결정
    auto_tag: True이면 LLM으로 카테고리/컨테이너명 자동 태깅
    auto_glossary: True이면 LLM으로 용어 자동 추출
    auto_fewshot: True이면 LLM으로 Q&A 자동 생성 → fewshot candidate
    """
    await check_namespace_ownership(namespace, user)

    # 파일 파싱
    from agents.knowledge_rag.ingestion.adapters import parse_file
    from agents.knowledge_rag.ingestion.chunker import chunk_document

    try:
        raw = await file.read()
        doc = parse_file(raw, file.filename or "unknown")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"파일 파싱 실패: {e}")

    # Analyzer Agent (선택적) — 청킹 전에 문서 분석
    analyzer_result = None
    if auto_analyze:
        try:
            from agents.knowledge_rag.ingestion.analyzer import analyze_document
            from service.llm.factory import get_llm_provider
            from core.security import get_user_llm_credentials

            llm = get_llm_provider()
            analyzer_result = await analyze_document(doc.raw_text, llm, user_credentials=get_user_llm_credentials(user))

            # 분석 결과로 전략 오버라이드
            chunk_strategy = analyzer_result.get("chunk_strategy", chunk_strategy)
            if not category and analyzer_result.get("suggested_categories"):
                category = analyzer_result["suggested_categories"][0]
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Analyzer 실패 (기존 전략 사용): %s", e)

    # 청킹
    chunks = chunk_document(doc, strategy=chunk_strategy)
    if not chunks:
        raise HTTPException(status_code=400, detail="분할된 청크가 없습니다.")

    # items 구성
    base_weight = 1.0
    if analyzer_result and analyzer_result.get("priority_score") is not None:
        base_weight = 0.5 + float(analyzer_result["priority_score"]) * 1.5
    items = [{"content": c.text, "category": category, "base_weight": base_weight} for c in chunks]

    # LLM 자동 태깅 (선택적)
    if auto_tag:
        try:
            from agents.knowledge_rag.ingestion.tagger import auto_tag_chunks
            from service.llm.factory import get_llm_provider
            from core.security import get_user_llm_credentials

            categories = []
            try:
                from agents.knowledge_rag.knowledge.schemas import BulkKnowledgeItem
                from core.database import get_conn, resolve_namespace_id
                async with get_conn() as conn:
                    ns_id = await resolve_namespace_id(conn, namespace)
                    cat_rows = await conn.fetch(
                        "SELECT name FROM rag_knowledge_category WHERE namespace_id = $1", ns_id
                    )
                categories = [r["name"] for r in cat_rows]
            except Exception:
                pass

            llm = get_llm_provider()
            tag_input = [{"idx": i, "text": c.text} for i, c in enumerate(chunks)]
            tags = await auto_tag_chunks(tag_input, categories, llm, user_credentials=get_user_llm_credentials(user))

            # 태그 적용
            tag_map = {t["idx"]: t for t in tags}
            for i, item in enumerate(items):
                tag = tag_map.get(i, {})
                if tag.get("category"):
                    item["category"] = tag["category"]
                if tag.get("container_name"):
                    item["container_name"] = tag["container_name"]
                if tag.get("priority_score") is not None:
                    item["base_weight"] = 0.5 + float(tag["priority_score"]) * 1.5
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("자동 태깅 실패 (무시하고 계속): %s", e)

    # 벌크 등록
    result = await service.bulk_create_knowledge(
        namespace=namespace,
        items=items,
        source_file=file.filename,
        source_type="file_upload",
        created_by_part=user["part"],
        created_by_user_id=user["id"],
    )

    # 용어 자동 추출 (선택적)
    glossary_count = 0
    if auto_glossary:
        try:
            from agents.knowledge_rag.ingestion.tagger import extract_glossary_terms
            from service.llm.factory import get_llm_provider
            from core.security import get_user_llm_credentials
            from core.database import get_conn, resolve_namespace_id

            async with get_conn() as conn:
                ns_id = await resolve_namespace_id(conn, namespace)
                existing = await conn.fetch(
                    "SELECT term FROM rag_glossary WHERE namespace_id = $1", ns_id
                )
            existing_terms = [r["term"] for r in existing]

            llm = get_llm_provider()
            terms = await extract_glossary_terms(
                doc.raw_text, existing_terms, llm, user_credentials=get_user_llm_credentials(user),
            )

            for term_data in terms:
                try:
                    await service.create_glossary(
                        namespace, term_data["term"], term_data.get("description", ""),
                        created_by_part=user["part"], created_by_user_id=user["id"],
                    )
                    glossary_count += 1
                except Exception:
                    pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("용어 추출 실패 (무시하고 계속): %s", e)

    # 자동 Q&A 생성 (선택적)
    fewshot_count = 0
    if auto_fewshot:
        try:
            from agents.knowledge_rag.ingestion.qa_gen import bulk_generate_qa
            from service.llm.factory import get_llm_provider
            from core.security import get_user_llm_credentials
            from core.database import get_conn, resolve_namespace_id

            llm = get_llm_provider()
            # 상위 5개 청크에서만 Q&A 생성 (비용 절약)
            qa_input = [{"idx": i, "content": c.text} for i, c in enumerate(chunks[:5])]
            qa_pairs = await bulk_generate_qa(qa_input, llm, user_credentials=get_user_llm_credentials(user))

            async with get_conn() as conn:
                ns_id = await resolve_namespace_id(conn, namespace)
                for qa in qa_pairs:
                    emb = await embedding_service.embed(qa["question"])
                    await conn.execute("""
                        INSERT INTO rag_fewshot (namespace_id, question, answer, status, embedding)
                        VALUES ($1, $2, $3, 'candidate', $4::vector)
                    """, ns_id, qa["question"], qa["answer"], str(emb))
                    fewshot_count += 1
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Q&A 자동 생성 실패 (무시하고 계속): %s", e)

    # job 업데이트 (용어 수 + fewshot 수)
    if result.get("job_id") and (glossary_count > 0 or fewshot_count > 0):
        try:
            from core.database import get_conn
            async with get_conn() as conn:
                await conn.execute(
                    "UPDATE rag_ingestion_job SET auto_glossary = $1, auto_fewshot = $2, analyzer_result = $3 WHERE id = $4",
                    glossary_count, fewshot_count,
                    json.dumps(analyzer_result, ensure_ascii=False) if analyzer_result else None,
                    result["job_id"],
                )
        except Exception:
            pass

    return {
        **result,
        "chunks": len(chunks),
        "auto_glossary": glossary_count,
        "auto_fewshot": fewshot_count,
        "analyzer": analyzer_result,
        "source_name": doc.source_name,
        "page_count": doc.metadata.get("page_count"),
    }


@router.post("/import/file/preview")
async def preview_file_upload(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """파일 업로드 미리보기 — LLM Analyzer로 전략 자동 결정 후 청킹 결과 반환."""
    from agents.knowledge_rag.ingestion.adapters import parse_file
    from agents.knowledge_rag.ingestion.chunker import chunk_document

    try:
        raw = await file.read()
        doc = parse_file(raw, file.filename or "unknown")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"파일 파싱 실패: {e}")

    strategy = "auto"
    detected_strategy = "auto"
    try:
        from agents.knowledge_rag.ingestion.analyzer import analyze_document
        from service.llm.factory import get_llm_provider
        from core.security import get_user_llm_credentials
        llm = get_llm_provider()
        analysis = await analyze_document(doc.raw_text, llm, user_credentials=get_user_llm_credentials(user))
        detected_strategy = analysis.get("chunk_strategy", "auto")
        strategy = detected_strategy
    except Exception:
        pass

    chunks = chunk_document(doc, strategy=strategy)

    return {
        "source_name": doc.source_name,
        "source_type": doc.source_type,
        "page_count": doc.metadata.get("page_count"),
        "total_chars": len(doc.raw_text),
        "sections": len(doc.sections),
        "tables": len(doc.tables),
        "chunks": [{"idx": c.idx, "text": c.text, "title": c.section_title} for c in chunks],
        "chunk_count": len(chunks),
        "detected_strategy": detected_strategy,
    }


# ─── URL / Confluence 인제스천 ────────────────────────────────────────────────

class _UrlImportBody(_BM):
    namespace: str
    url: str
    confluence_token: Optional[str] = None
    chunk_strategy: str = "auto"
    category: Optional[str] = None
    auto_tag: bool = False
    auto_glossary: bool = False


@router.post("/import/url", status_code=201)
async def import_from_url(body: _UrlImportBody, user: dict = Depends(get_current_user)):
    """URL(일반 웹 or Confluence) → 텍스트 추출 → 청킹 → 벌크 등록."""
    await check_namespace_ownership(body.namespace, user)

    from agents.knowledge_rag.ingestion.web_crawler import fetch_url
    from agents.knowledge_rag.ingestion.chunker import chunk_document
    from core.security import get_user_confluence_pat

    confluence_token = body.confluence_token or get_user_confluence_pat(user)
    try:
        doc = await fetch_url(body.url, confluence_token=confluence_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"URL 수집 실패: {e}")

    chunks = chunk_document(doc, strategy=body.chunk_strategy)
    if not chunks:
        raise HTTPException(status_code=400, detail="수집된 콘텐츠가 없습니다.")

    items = [{"content": c.text, "category": body.category} for c in chunks]

    # LLM 자동 태깅 (선택적)
    if body.auto_tag:
        try:
            from agents.knowledge_rag.ingestion.tagger import auto_tag_chunks
            from service.llm.factory import get_llm_provider
            from core.security import get_user_llm_credentials

            llm = get_llm_provider()
            tag_input = [{"idx": i, "text": c.text} for i, c in enumerate(chunks)]
            tags = await auto_tag_chunks(tag_input, [], llm, user_credentials=get_user_llm_credentials(user))
            tag_map = {t["idx"]: t for t in tags}
            for i, item in enumerate(items):
                tag = tag_map.get(i, {})
                if tag.get("category"):
                    item["category"] = tag["category"]
                if tag.get("container_name"):
                    item["container_name"] = tag["container_name"]
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("자동 태깅 실패 (무시하고 계속): %s", e)

    result = await service.bulk_create_knowledge(
        namespace=body.namespace,
        items=items,
        source_file=body.url,
        source_type=doc.source_type,
        created_by_part=user["part"],
        created_by_user_id=user["id"],
    )

    # 용어 자동 추출 (선택적)
    glossary_count = 0
    if body.auto_glossary:
        try:
            from agents.knowledge_rag.ingestion.tagger import extract_glossary_terms
            from service.llm.factory import get_llm_provider
            from core.security import get_user_llm_credentials
            from core.database import get_conn, resolve_namespace_id

            async with get_conn() as conn:
                ns_id = await resolve_namespace_id(conn, body.namespace)
                existing = await conn.fetch("SELECT term FROM rag_glossary WHERE namespace_id = $1", ns_id)
            existing_terms = [r["term"] for r in existing]

            llm = get_llm_provider()
            terms = await extract_glossary_terms(
                doc.raw_text, existing_terms, llm, user_credentials=get_user_llm_credentials(user),
            )
            for term_data in terms:
                try:
                    await service.create_glossary(
                        body.namespace, term_data["term"], term_data.get("description", ""),
                        created_by_part=user["part"], created_by_user_id=user["id"],
                    )
                    glossary_count += 1
                except Exception:
                    pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("용어 추출 실패 (무시하고 계속): %s", e)

    return {
        **result,
        "chunks": len(chunks),
        "auto_glossary": glossary_count,
        "source_name": doc.source_name,
        "source_type": doc.source_type,
        "url": body.url,
    }


# ─── Teams 인제스천 ──────────────────────────────────────────────────────────

# 메시지는 raw dict로 받음 — "from" 키가 파이썬 예약어라 Pydantic 모델로 감싸지 않음
class _TeamsThread(_BM):
    title: str = ""
    messages: list[dict]


class _TeamsImportBody(_BM):
    namespace: str
    chat_id: Optional[str] = None
    chat_label: Optional[str] = None
    docs: list[_TeamsThread]
    chunk_strategy: str = "auto"
    category: Optional[str] = None


@router.post("/import/teams", status_code=201)
async def import_from_teams(body: _TeamsImportBody, user: dict = Depends(get_current_user)):
    """Teams 대화 스레드 → 청킹 → 벌크 등록.

    프론트에서 선택한 메시지들을 스레드 단위로 받아, 각 스레드를 하나의
    ParsedDocument로 변환 후 기존 청킹/인제스천 파이프라인에 태운다.
    """
    await check_namespace_ownership(body.namespace, user)

    from agents.knowledge_rag.ingestion.adapters import ParsedDocument
    from agents.knowledge_rag.ingestion.chunker import chunk_document
    from agents.knowledge_rag.ingestion.teams_crawler import thread_to_content

    if not body.docs:
        raise HTTPException(status_code=400, detail="저장할 대화가 없습니다.")

    all_items: list[dict] = []
    thread_titles: list[str] = []

    for i, thread in enumerate(body.docs):
        messages = thread.messages or []
        if not messages:
            continue

        content = thread_to_content(messages)
        if not content.strip():
            continue

        title = (thread.title or "").strip() or f"Teams 대화 #{i + 1} ({len(messages)}건)"
        thread_titles.append(title)

        # 스레드 텍스트를 ParsedDocument로 감싸 청킹 파이프라인 공유
        doc = ParsedDocument(
            source_type="teams",
            source_name=title,
            raw_text=content,
            metadata={
                "chat_id": body.chat_id,
                "chat_label": body.chat_label,
                "message_count": len(messages),
                "participants": sorted({m.get("from", "") for m in messages if m.get("from")}),
            },
        )
        chunks = chunk_document(doc, strategy=body.chunk_strategy)
        if not chunks:
            continue
        for c in chunks:
            all_items.append({
                "content": c.text,
                "category": body.category,
            })

    if not all_items:
        raise HTTPException(status_code=400, detail="분할된 청크가 없습니다.")

    source_file = body.chat_label or body.chat_id or "teams"
    result = await service.bulk_create_knowledge(
        namespace=body.namespace,
        items=all_items,
        source_file=source_file,
        source_type="teams",
        created_by_part=user["part"],
        created_by_user_id=user["id"],
    )
    return {
        **result,
        "threads": len(thread_titles),
        "chunks": len(all_items),
        "source_name": source_file,
        "source_type": "teams",
    }


@router.post("/import/url/preview")
async def preview_url(body: _UrlImportBody, user: dict = Depends(get_current_user)):
    """URL 수집 미리보기 — LLM Analyzer로 전략 자동 결정 후 청킹 결과 반환."""
    from agents.knowledge_rag.ingestion.web_crawler import fetch_url
    from agents.knowledge_rag.ingestion.chunker import chunk_document
    from core.security import get_user_confluence_pat

    confluence_token = body.confluence_token or get_user_confluence_pat(user)
    try:
        doc = await fetch_url(body.url, confluence_token=confluence_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"URL 수집 실패: {e}")

    strategy = "auto"
    detected_strategy = "auto"
    try:
        from agents.knowledge_rag.ingestion.analyzer import analyze_document
        from service.llm.factory import get_llm_provider
        from core.security import get_user_llm_credentials
        llm = get_llm_provider()
        analysis = await analyze_document(doc.raw_text, llm, user_credentials=get_user_llm_credentials(user))
        detected_strategy = analysis.get("chunk_strategy", "auto")
        strategy = detected_strategy
    except Exception:
        pass

    chunks = chunk_document(doc, strategy=strategy)
    return {
        "source_name": doc.source_name,
        "source_type": doc.source_type,
        "total_chars": len(doc.raw_text),
        "sections": len(doc.sections),
        "chunks": [{"idx": c.idx, "text": c.text, "title": c.section_title} for c in chunks],
        "chunk_count": len(chunks),
        "detected_strategy": detected_strategy,
        "url": body.url,
    }


# ─── Confluence 트리 + 일괄 인제스천 ─────────────────────────────────────────


class _UrlTreeBody(BaseModel):
    url: str
    confluence_token: Optional[str] = None
    max_depth: int = 3
    max_pages: int = 100


@router.post("/import/url/tree")
async def preview_confluence_tree(body: _UrlTreeBody, user: dict = Depends(get_current_user)):
    """입력 URL을 root로 자손 페이지 트리 메타데이터 반환 (본문 fetch 안 함, 빠름)."""
    from agents.knowledge_rag.ingestion.web_crawler import fetch_confluence_tree, _is_confluence
    from core.security import get_user_confluence_pat

    if not _is_confluence(body.url):
        raise HTTPException(status_code=400, detail="Confluence URL이 아닙니다. 일반 웹 URL은 단건 등록만 지원합니다.")

    token = body.confluence_token or get_user_confluence_pat(user)
    if not token:
        raise HTTPException(status_code=400, detail="Confluence PAT가 필요합니다. 프로필에서 등록하거나 요청에 포함해주세요.")

    try:
        tree = await fetch_confluence_tree(
            body.url, token,
            max_depth=body.max_depth, max_pages=body.max_pages,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Confluence 트리 조회 실패: {e}")

    return tree


class _ConfluencePageRef(BaseModel):
    page_id: str
    title: Optional[str] = None
    url: Optional[str] = None


class _BulkPagesBody(BaseModel):
    namespace: str
    base_url: str                       # Confluence base URL (예: https://confl.sinc.co.kr)
    pages: list[_ConfluencePageRef]     # 사용자가 트리에서 체크한 페이지들
    confluence_token: Optional[str] = None
    chunk_strategy: str = "auto"
    category: Optional[str] = None
    auto_tag: bool = False
    auto_glossary: bool = False


@router.post("/import/url/bulk-pages/preview")
async def preview_confluence_bulk(body: _BulkPagesBody, user: dict = Depends(get_current_user)):
    """선택된 페이지들을 fetch + 청킹만 수행 (DB 등록 X). 청크 리뷰용."""
    await check_namespace_ownership(body.namespace, user)
    if not body.pages:
        raise HTTPException(status_code=400, detail="선택된 페이지가 없습니다.")
    if len(body.pages) > 200:
        raise HTTPException(status_code=400, detail="한 번에 최대 200개 페이지까지 미리보기 가능합니다.")

    from agents.knowledge_rag.ingestion.web_crawler import fetch_confluence_by_id
    from agents.knowledge_rag.ingestion.chunker import chunk_document
    from core.security import get_user_confluence_pat

    token = body.confluence_token or get_user_confluence_pat(user)
    if not token:
        raise HTTPException(status_code=400, detail="Confluence PAT가 필요합니다.")

    import asyncio

    async def _fetch_and_chunk(p):
        try:
            doc = await fetch_confluence_by_id(body.base_url, p.page_id, token)
            chunks = chunk_document(doc, strategy=body.chunk_strategy)
            return {"page_id": p.page_id, "title": doc.source_name, "chunks": chunks, "error": None}
        except Exception as e:
            return {"page_id": p.page_id, "title": p.title or p.page_id, "chunks": [], "error": str(e)}

    fetched = await asyncio.gather(*(_fetch_and_chunk(p) for p in body.pages))

    chunks_out: list[dict] = []
    pages_meta: list[dict] = []
    failed: list[dict] = []
    idx = 0
    for f in fetched:
        if f["error"]:
            failed.append({"page_id": f["page_id"], "title": f["title"], "error": f["error"]})
            continue
        page_start = idx
        for c in f["chunks"]:
            chunks_out.append({
                "idx": idx,
                "page_id": f["page_id"],
                "page_title": f["title"],
                "text": c.text,
                "title": c.section_title,
            })
            idx += 1
        pages_meta.append({
            "page_id": f["page_id"],
            "title": f["title"],
            "chunk_start": page_start,
            "chunk_count": idx - page_start,
        })

    return {
        "chunks": chunks_out,
        "chunk_count": len(chunks_out),
        "pages": pages_meta,
        "failed_pages": failed,
    }


@router.post("/import/url/bulk-pages", status_code=201)
async def import_confluence_bulk(body: _BulkPagesBody, user: dict = Depends(get_current_user)):
    """선택된 Confluence 페이지들을 일괄 인제스천. 각 페이지의 청크를 모두 합쳐 단일 ingestion_job."""
    await check_namespace_ownership(body.namespace, user)
    if not body.pages:
        raise HTTPException(status_code=400, detail="선택된 페이지가 없습니다.")
    if len(body.pages) > 200:
        raise HTTPException(status_code=400, detail="한 번에 최대 200개 페이지까지 등록할 수 있습니다.")

    from agents.knowledge_rag.ingestion.web_crawler import fetch_confluence_by_id
    from agents.knowledge_rag.ingestion.chunker import chunk_document
    from core.security import get_user_confluence_pat

    token = body.confluence_token or get_user_confluence_pat(user)
    if not token:
        raise HTTPException(status_code=400, detail="Confluence PAT가 필요합니다.")

    import asyncio

    # 각 페이지를 병렬 fetch + 청킹 (실패는 개별 처리)
    async def _fetch_and_chunk(page_ref: _ConfluencePageRef):
        try:
            doc = await fetch_confluence_by_id(body.base_url, page_ref.page_id, token)
            chunks = chunk_document(doc, strategy=body.chunk_strategy)
            return {
                "page_id": page_ref.page_id,
                "doc": doc,
                "chunks": chunks,
                "error": None,
            }
        except Exception as e:
            logger.warning("페이지 %s fetch 실패: %s", page_ref.page_id, e)
            return {"page_id": page_ref.page_id, "doc": None, "chunks": [], "error": str(e)}

    fetched = await asyncio.gather(*(_fetch_and_chunk(p) for p in body.pages))

    # 청크 → items 변환 + per-page 메타데이터 보존
    items: list[dict] = []
    failed_pages: list[dict] = []
    page_summaries: list[dict] = []
    for f in fetched:
        if f["error"]:
            failed_pages.append({"page_id": f["page_id"], "error": f["error"]})
            continue
        doc = f["doc"]
        chunks = f["chunks"]
        page_summaries.append({
            "page_id": f["page_id"],
            "title": doc.source_name,
            "chunks": len(chunks),
            "chars": len(doc.raw_text),
        })
        for c in chunks:
            items.append({
                "content": c.text,
                "category": body.category,
                "container_name": doc.source_name,
            })

    if not items:
        raise HTTPException(
            status_code=400,
            detail=f"수집된 청크가 없습니다. failed_pages={failed_pages}",
        )

    # LLM 자동 태깅 (선택)
    if body.auto_tag and items:
        try:
            from agents.knowledge_rag.ingestion.tagger import auto_tag_chunks
            from service.llm.factory import get_llm_provider
            from core.security import get_user_llm_credentials

            llm = get_llm_provider()
            tag_input = [{"idx": i, "text": it["content"]} for i, it in enumerate(items)]
            tags = await auto_tag_chunks(tag_input, [], llm, user_credentials=get_user_llm_credentials(user))
            tag_map = {t["idx"]: t for t in tags}
            for i, item in enumerate(items):
                tag = tag_map.get(i, {})
                if tag.get("category"):
                    item["category"] = tag["category"]
        except Exception as e:
            logger.warning("자동 태깅 실패: %s", e)

    # 벌크 등록 — source_file은 root URL 또는 페이지 수 표기
    source_file = f"Confluence bulk ({len(page_summaries)} pages)"
    result = await service.bulk_create_knowledge(
        namespace=body.namespace,
        items=items,
        source_file=source_file,
        source_type="confluence_bulk",
        created_by_part=user["part"],
        created_by_user_id=user["id"],
    )

    # 용어 자동 추출 (선택, 전체 합쳐서)
    glossary_count = 0
    if body.auto_glossary:
        try:
            from agents.knowledge_rag.ingestion.tagger import extract_glossary_terms
            from service.llm.factory import get_llm_provider
            from core.security import get_user_llm_credentials
            from core.database import get_conn, resolve_namespace_id

            async with get_conn() as conn:
                ns_id = await resolve_namespace_id(conn, body.namespace)
                existing = await conn.fetch("SELECT term FROM rag_glossary WHERE namespace_id = $1", ns_id)
            existing_terms = [r["term"] for r in existing]

            llm = get_llm_provider()
            combined_text = "\n\n".join(f["doc"].raw_text for f in fetched if f["doc"])
            terms = await extract_glossary_terms(
                combined_text[:20000], existing_terms, llm,
                user_credentials=get_user_llm_credentials(user),
            )
            for term_data in terms:
                try:
                    await service.create_glossary(
                        body.namespace, term_data["term"], term_data.get("description", ""),
                        created_by_part=user["part"], created_by_user_id=user["id"],
                    )
                    glossary_count += 1
                except Exception:
                    pass
        except Exception as e:
            logger.warning("용어 추출 실패: %s", e)

    return {
        **result,
        "pages_succeeded": len(page_summaries),
        "pages_failed": len(failed_pages),
        "failed_pages": failed_pages,
        "page_summaries": page_summaries,
        "auto_glossary": glossary_count,
        "chunks": len(items),
        "source_name": source_file,
        "source_type": "confluence_bulk",
    }


# ─── 인제스천 작업 이력 ──────────────────────────────────────────────────────

@router.get("/ingestion-jobs", response_model=list[IngestionJobOut])
async def get_ingestion_jobs(
    namespace: str = Query(...),
    user: dict = Depends(get_current_user),
):
    return await service.list_ingestion_jobs(namespace)


@router.get("/ingestion-jobs/{job_id}")
async def get_ingestion_job_status(job_id: int, user: dict = Depends(get_current_user)):
    """인제스천 작업 진행률 조회 (폴링용)."""
    job = await service.get_ingestion_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return job


@router.post("/ingestion-jobs/{job_id}/cancel")
async def cancel_ingestion_job_endpoint(job_id: int, user: dict = Depends(get_current_user)):
    """진행 중인 인제스천 작업 중지 요청 — 다음 배치 경계에서 중단 + 이미 등록된 데이터 롤백."""
    job = await service.cancel_ingestion_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="진행 중인 작업이 아니거나 존재하지 않습니다.")
    return job
