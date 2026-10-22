"""지식 베이스 및 용어집 CRUD — 네임스페이스 소유 파트 기반 권한."""
import asyncio
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

async def _require_resource_namespace(namespace: Optional[str], user: dict, not_found_detail: str) -> str:
    """단건 리소스의 namespace를 찾아 소유권을 검증. namespace가 없으면(리소스 미존재) 404."""
    if namespace is None:
        raise HTTPException(status_code=404, detail=not_found_detail)
    await check_namespace_ownership(namespace, user)
    return namespace


@router.get("", response_model=list[KnowledgeOut])
async def get_knowledge_list(
    namespace: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
):
    # 영역 없이 부르면 전 영역 지식이 한 번에 나갔다 — 관리자만 허용(막 쓰기 테스트 10/20). 영역별 열람은 그대로(다른 파트는 읽기 전용 설계)
    if not namespace and user["role"] != "admin":
        raise HTTPException(status_code=400, detail="업무 영역을 골라 주세요.")
    return await service.list_knowledge(namespace, status)


# 업무구분 자동 추천(_auto_category)은 10/22 업무구분 폐지로 삭제


@router.post("/source-documents/{doc_id}/rechunk")
async def rechunk_source_document(doc_id: int, force: bool = Query(default=False), user: dict = Depends(get_current_user)):
    """원문 원장의 원문을 지금 코드로 다시 분할(2026-10-08) — 파이프라인 결함 뒤 재처리. 파트 수정 권한 필요.
    결과가 같으면 변화 없음, 사람이 고친 청크가 있으면 force 없이는 멈춘다(blocked)."""
    await _require_resource_namespace(await service.get_source_document_namespace(doc_id), user, "Source document not found")
    try:
        return await service.rechunk_source_document(doc_id, force=force, user=user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{knowledge_id}/structure")
async def get_knowledge_structure(knowledge_id: int, user: dict = Depends(get_current_user)):
    """지식의 문서 안 위치 — 분할 방식별(섹션 구조/문서 순서/단건) 상위 경로·앞뒤 청크·검색 때 함께 붙는 이웃(2026-10-07)."""
    # 이웃 미리보기가 나가므로 단건 읽기(duplicate-matches)와 같은 파트 권한(리뷰 2026-10-07)
    await _require_resource_namespace(await service.get_knowledge_namespace(knowledge_id), user, "Knowledge not found")
    out = await service.get_knowledge_structure(knowledge_id)
    if out is None:
        raise HTTPException(status_code=404, detail="지식을 찾을 수 없습니다.")
    return out


@router.post("", response_model=KnowledgeOut, status_code=201)
async def add_knowledge(body: KnowledgeCreate, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(body.namespace, user)
    row = await service.create_knowledge(
        namespace=body.namespace,
        content=body.content,
        created_by_user_id=user["id"],
    )
    # 용어 자동 추출 복원(2026-09-24) — 미리보기+검토 2단계 흐름으로 전환되며 끊겼던
    # _run_auto_glossary()를 모든 등록 경로 공통으로 되살림. 사람이 체크박스를 켜야
    # 했던 예전 opt-in 방식 대신, 이 세션 내내 적용한 "사람 개입 최소화" 원칙대로
    # 항상 실행(실패해도 등록 자체는 막지 않음 — 함수 내부에 이미 try/except 있음).
    # 백그라운드로(2026-10-06) — 요청 안에서 LLM 응답을 기다리느라 지식 1건 등록이 89초 걸렸다(사내 LLM 지연 시
    # 실측, E2E 30초 초과). 등록 결과는 바로 돌려주고 용어는 잠시 뒤 추가된다(벌크 등록도 이미 비동기).
    _spawn(_run_auto_glossary(body.namespace, body.content, user, max_chars=20000))
    return row


@router.put("/{knowledge_id}", response_model=KnowledgeOut)
async def modify_knowledge(knowledge_id: int, body: KnowledgeUpdate, user: dict = Depends(get_current_user)):
    ns = await service.get_knowledge_namespace(knowledge_id)
    await _require_resource_namespace(ns, user, "Knowledge not found")

    row = await service.update_knowledge(
        knowledge_id=knowledge_id,
        content=body.content,
        updated_by_user_id=user["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge not found")
    return row


@router.delete("/{knowledge_id}", status_code=204)
async def remove_knowledge(knowledge_id: int, user: dict = Depends(get_current_user)):
    ns = await service.get_knowledge_namespace(knowledge_id)
    await _require_resource_namespace(ns, user, "Knowledge not found")

    deleted = await service.delete_knowledge(knowledge_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge not found")


@router.get("/{knowledge_id}/duplicate-matches")
async def get_duplicate_matches(knowledge_id: int, user: dict = Depends(get_current_user)):
    ns = await service.get_knowledge_namespace(knowledge_id)
    await _require_resource_namespace(ns, user, "Knowledge not found")
    return await service.get_duplicate_matches(knowledge_id)


class ResolveDuplicateRequest(BaseModel):
    action: str  # 'approve' | 'reject' | 'merge'
    target_id: Optional[int] = None
    content: Optional[str] = None  # merge 전용 — 리뷰어가 직접 다듬은 최종 병합 내용


@router.post("/{knowledge_id}/resolve")
async def resolve_duplicate(knowledge_id: int, body: ResolveDuplicateRequest, user: dict = Depends(get_current_user)):
    ns = await service.get_knowledge_namespace(knowledge_id)
    await _require_resource_namespace(ns, user, "Knowledge not found")
    try:
        return await service.resolve_duplicate(
            knowledge_id, body.action, body.target_id, body.content,
            reviewer_username=user["username"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class FlagForReviewRequest(BaseModel):
    namespace: str
    reason: str = "search_noise"
    query: Optional[str] = None  # 어떤 질의 결과에서 이상했는지 — 담당자가 맥락을 보도록


@router.post("/{knowledge_id}/flag-for-review", status_code=201)
async def flag_knowledge_for_review(knowledge_id: int, body: FlagForReviewRequest, user: dict = Depends(get_current_user)):
    """평가 게이트 "이상해요" — 개선 원장(정정 검토 탭)에 검색 노이즈 1건으로 기록(2026-10-02, 옛 리뷰 신호 대체)."""
    from service.improvement import service as improvement  # 지연 import — 지식 모듈이 개선 원장에 묶이지 않게
    ns = await service.get_knowledge_namespace(knowledge_id)
    await _require_resource_namespace(ns, user, "Knowledge not found")
    try:
        await improvement.record_search_noise(ns, knowledge_id, user.get("id"), (body.query or "").strip() or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "flagged"}


class BulkDeleteRequest(BaseModel):
    ids: list[int]


class BulkUpdateRequest(BaseModel):
    ids: list[int]
    source_type: Optional[str] = None   # 업무구분 일괄 변경은 10/22 폐지


class VectorSearchRequest(BaseModel):
    namespace: str
    query: str
    top_k: int = 30


@router.post("/bulk-delete", status_code=200)
async def bulk_remove_knowledge(body: BulkDeleteRequest, user: dict = Depends(get_current_user)):
    # 권한 검사(2026-10-02) — 예전엔 없어서 조회 전용·다른 파트 사용자도 아무 지식이나 지울 수 있었다.
    # 단건 삭제·bulk-update와 같은 기준: 대상 지식이 속한 파트마다 소유권 확인
    for ns in await service.get_knowledge_namespaces(body.ids):
        await check_namespace_ownership(ns, user)
    deleted = await service.bulk_delete_knowledge(body.ids)
    return {"deleted": deleted}


@router.post("/bulk-update", status_code=200)
async def bulk_update_knowledge_endpoint(body: BulkUpdateRequest, user: dict = Depends(get_current_user)):
    if not body.ids:
        raise HTTPException(status_code=400, detail="선택된 항목이 없습니다.")
    if body.source_type is None:
        raise HTTPException(status_code=400, detail="변경할 필드를 하나 이상 선택하세요.")
    for ns in await service.get_knowledge_namespaces(body.ids):
        await check_namespace_ownership(ns, user)
    updated = await service.bulk_update_knowledge(body.ids, source_type=body.source_type)
    return {"updated": updated}


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
            created_by_user_id=user["id"],
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/glossary/{glossary_id}", response_model=GlossaryOut)
async def modify_glossary(glossary_id: int, body: GlossaryUpdate, user: dict = Depends(get_current_user)):
    ns = await service.get_glossary_namespace(glossary_id)
    await _require_resource_namespace(ns, user, "Glossary term not found")

    row = await service.update_glossary(
        glossary_id, body.term, body.description,
        updated_by_user_id=user["id"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Glossary term not found")
    return row


@router.delete("/glossary/{glossary_id}", status_code=204)
async def remove_glossary(glossary_id: int, user: dict = Depends(get_current_user)):
    ns = await service.get_glossary_namespace(glossary_id)
    await _require_resource_namespace(ns, user, "Glossary term not found")

    deleted = await service.delete_glossary(glossary_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Glossary term not found")


@router.delete("/glossary/synonyms/{synonym_id}", status_code=204)
async def remove_glossary_synonym(synonym_id: int, user: dict = Depends(get_current_user)):
    """동의어 지우기(v2.128) — 동의어는 LLM이 자동으로 붙이고 사람은 틀린 것만 지운다. 지운 표현은 다시 붙지 않는다(막힘 표시)."""
    ns = await service.get_synonym_namespace(synonym_id)
    await _require_resource_namespace(ns, user, "Synonym not found")
    if not await service.block_glossary_synonym(synonym_id):
        raise HTTPException(status_code=404, detail="Synonym not found")


@router.post("/glossary/bulk-delete", status_code=200)
async def bulk_remove_glossary(body: BulkDeleteRequest, user: dict = Depends(get_current_user)):
    # 권한 검사(2026-10-06, v2.128) — 예전엔 없어서 조회 전용·다른 파트 사용자도 아무 용어나 지울 수 있었다.
    # 지식 bulk-delete(2026-10-02)·용어 단건 삭제와 같은 기준: 대상 용어가 속한 파트마다 소유권 확인(하나라도 안 되면 전체 거부)
    for ns in await service.get_glossary_namespaces(body.ids):
        await check_namespace_ownership(ns, user)
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
    # 용어 자동 추출 복원(2026-09-24) — 파일/URL 단발성 등록에만 있던 _run_auto_glossary()가
    # 미리보기+검토 2단계 흐름(파일/텍스트/컨플루언스 전부 이 엔드포인트로 확정)으로
    # 넘어오며 끊겨있던 것을 모든 벌크 등록 경로 공통으로 되살림 — 체크박스 없이 항상 실행.
    # job 성공 후에만 실행되고, 개수는 job 행(auto_glossary)에 기록된다(화면은 job 행을 읽음).
    combined_text = "\n".join(item.content for item in body.items)
    try:
        return await service.bulk_create_knowledge(
            namespace=body.namespace,
            items=[item.model_dump() for item in body.items],
            source_file=body.source_file,
            source_type=body.source_type,
            created_by_user_id=user["id"],
            after_activation=_glossary_after_activation(body.namespace, combined_text, user, max_chars=20000),
            confirm_replace=body.confirm_replace,
        )
    except service.ReplaceConfirmRequired as e:
        # 화면은 등록 전에 /bulk/replace-check로 먼저 묻는다 — 여기는 확인 없이 들어온 요청을 막는 두 번째 벽
        raise HTTPException(status_code=409, detail=str(e))


class _ReplaceCheckBody(BaseModel):
    namespace: str
    source_document_ids: list[int] = []
    confluence_page_ids: list[str] = []


@router.post("/bulk/replace-check")
async def bulk_replace_check(body: _ReplaceCheckBody, user: dict = Depends(get_current_user)):
    """등록 전 확인(A안 2026-10-12) — 이 등록이 교체할 같은 원본의 기존 조각에 사람이 손댄 흔적(고친·뺀 조각, 일부만 등록). needs_confirm이면 화면이 사유를 보여주고 확인받은 뒤 confirm_replace=true로 등록한다."""
    await check_namespace_ownership(body.namespace, user)
    from core.database import get_conn, resolve_namespace_id
    from agents.knowledge_rag.ingestion.source_ledger import replace_check
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, body.namespace)
        if ns_id is None:
            raise HTTPException(status_code=404, detail="파트를 찾을 수 없습니다.")
        return await replace_check(conn, ns_id, body.source_document_ids, body.confluence_page_ids)


@router.post("/import/csv", status_code=201)
async def import_csv(
    file: UploadFile = File(...),
    namespace: str = Form(...),
    column_mapping: str = Form(...),
    user: dict = Depends(get_current_user),
):
    """CSV 파일 업로드 → 파싱 → 벌크 등록.

    column_mapping: JSON 문자열 {"content": "csv_col_name"} (업무구분 열 매핑은 10/22 폐지)
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
        items.append({"content": content})

    if not items:
        raise HTTPException(status_code=400, detail="유효한 데이터가 없습니다.")

    result = await service.bulk_create_knowledge(
        namespace=namespace,
        items=items,
        source_file=file.filename,
        source_type="csv_import",
        created_by_user_id=user["id"],
    )
    return result


from pydantic import BaseModel as _BM


class _TextSplitBody(_BM):
    namespace: str
    raw_text: str
    strategy: str = "auto"


@router.post("/import/text-split", status_code=201)
async def import_text_split(body: _TextSplitBody, user: dict = Depends(get_current_user)):
    """대량 텍스트 → 자동 분할 → 벌크 등록."""
    await check_namespace_ownership(body.namespace, user)

    split = service.split_text_to_items(body.raw_text, body.strategy)
    if not split:
        raise HTTPException(status_code=400, detail="분할된 청크가 없습니다.")
    chunks = [x["content"] for x in split]

    items = [{"content": x["content"], "heading_path": x["heading_path"] or None} for x in split]
    result = await service.bulk_create_knowledge(
        namespace=body.namespace,
        items=items,
        source_file="텍스트 직접입력",
        source_type="paste_split",
        created_by_user_id=user["id"],
    )
    return {**result, "chunks": len(chunks)}


class _TextSplitPreviewBody(_BM):
    raw_text: str
    strategy: str = "auto"
    namespace: Optional[str] = None   # 원문 원장 초안 저장용(2026-10-08) — 없으면 원장 없이


async def _ledger_ns_id(namespace: Optional[str], user: dict) -> Optional[int]:
    """원문 원장 초안을 저장할 파트(2026-10-08). 파트를 안 보낸 옛 호출은 원장 없이 진행(호환)."""
    if not namespace:
        return None
    from core.database import get_conn, resolve_namespace_id
    try:
        await check_namespace_ownership(namespace, user)
    except HTTPException:
        return None   # 조회 전용 사용자도 미리보기는 그대로(원장만 건너뜀 — 어차피 등록 권한이 없다, 리뷰 2026-10-08)
    async with get_conn() as conn:
        return await resolve_namespace_id(conn, namespace)


async def _save_ledger_draft(ns_id: Optional[int], doc, strategy: Optional[str], user: dict,
                             source_ref: Optional[str] = None) -> Optional[int]:
    if ns_id is None:
        return None
    from core.database import get_conn
    from agents.knowledge_rag.ingestion.source_ledger import save_draft_for_parsed
    try:
        async with get_conn() as conn:
            return await save_draft_for_parsed(conn, ns_id, doc, chunk_strategy=strategy, user_id=user.get("id"),
                                               source_ref=source_ref)
    except Exception:
        # 원장 저장 실패가 미리보기를 막지 않게 — 원장 없이 등록되면 "다시 분할"만 안 될 뿐(로그로 추적)
        logger.warning("원문 원장 초안 저장 실패 — 원장 없이 진행", exc_info=True)
        return None


@router.post("/import/text-split/preview")
async def preview_text_split(body: _TextSplitPreviewBody, user: dict = Depends(get_current_user)):
    """텍스트 분할 미리보기 — strategy가 auto일 때만 LLM Analyzer로 전략 자동 결정.

    사용자가 미리보기 화면에서 전략을 수동으로 재선택한 경우(strategy != "auto")는
    이미 원하는 전략이 정해진 것이므로 LLM 재호출 없이 바로 그 전략으로 재청킹한다.
    """
    strategy = body.strategy
    detected_strategy = strategy

    # 제목이 실제로 2개 이상 인식되면 LLM 추측보다 문서 구조가 우선(2026-10-07 — LLM이 "단락"을 고르면 제목 구조가 무시됐다)
    if strategy == "auto":
        from agents.knowledge_rag.ingestion.adapters import extract_heading_sections
        if sum(1 for x in extract_heading_sections(body.raw_text) if x["title"]) >= 2:
            strategy = detected_strategy = "section"

    if strategy == "auto":
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

    split = service.split_text_to_items(body.raw_text, strategy)
    from agents.knowledge_rag.ingestion.adapters import ParsedDocument
    doc_id = await _save_ledger_draft(await _ledger_ns_id(body.namespace, user),
                                      ParsedDocument(source_type="paste", source_name="텍스트 직접입력", raw_text=body.raw_text),
                                      strategy, user, source_ref=None)
    return {"chunks": [x["content"] for x in split], "heading_paths": [x["heading_path"] for x in split],
            "count": len(split), "detected_strategy": detected_strategy, "source_document_id": doc_id}


# ─── 파일 업로드 + 자동 청킹 (Tier 2) ────────────────────────────────────────

# 업무구분 자동 태깅(_run_auto_tag)은 10/22 업무구분 폐지로 삭제


def _glossary_after_activation(namespace: str, raw_text: str, user: dict, *, max_chars: Optional[int] = None):
    """벌크 등록 job이 성공적으로 전환된 뒤에만 용어를 추출하도록 넘기는 후속 작업
    (bulk_create_knowledge의 after_activation). 예전엔 job 시작 직후 바로 추출해, job이
    실패·취소돼 지식은 한 건도 안 남았는데 용어만 남는 경우가 있었다(2026-09-28)."""
    async def _run() -> int:
        return await _run_auto_glossary(namespace, raw_text, user, max_chars=max_chars)
    return _run


# 단건 등록의 용어 추출(백그라운드) 동시 실행 상한 — 연달아 등록하면 느린 사내 LLM에 호출이 몰리지 않게(/code-review)
_GLOSSARY_CONCURRENCY = asyncio.Semaphore(2)


def _spawn(coro) -> None:
    """응답을 막지 않는 보조 작업. 태스크 참조는 지식 서비스의 기존 집합에 둔다(GC로 중간에 사라지지 않게 — 같은 패턴을
    두 군데 두지 않으려고). 실패는 작업 안에서 로그로 남긴다(_run_auto_glossary가 try/except + warning). 재시작 때 진행 중이던
    추출은 사라진다 — 용어는 보조 정보라 감수(다음 등록·벌크 때 다시 뽑힘)."""
    async def _limited():
        async with _GLOSSARY_CONCURRENCY:
            return await coro
    task = asyncio.create_task(_limited())
    service._background_tasks.add(task)
    task.add_done_callback(service._background_tasks.discard)


async def _run_auto_glossary(namespace: str, raw_text: str, user: dict, *, max_chars: Optional[int] = None) -> int:
    """raw_text에서 LLM으로 용어를 추출해 rag_glossary에 등록. 등록된 용어 수 반환."""
    count = 0
    try:
        from agents.knowledge_rag.ingestion.tagger import extract_glossary_terms
        from service.llm.factory import get_llm_provider
        from core.security import get_user_llm_credentials
        from core.database import get_conn, resolve_namespace_id

        async with get_conn() as conn:
            ns_id = await resolve_namespace_id(conn, namespace)
            existing = await conn.fetch("SELECT term FROM rag_glossary WHERE namespace_id = $1", ns_id)
        existing_terms = [r["term"] for r in existing]

        llm = get_llm_provider()
        text = raw_text[:max_chars] if max_chars else raw_text
        terms = await extract_glossary_terms(text, existing_terms, llm, user_credentials=get_user_llm_credentials(user))
        for term_data in terms:
            try:
                await service.create_glossary(
                    namespace, term_data["term"], term_data.get("description", ""),
                    created_by_user_id=user["id"],
                )
                count += 1
            except Exception:
                # 이미 있는 용어(중복) 등 — 한 용어 실패가 나머지를 막지 않게 넘기되 흔적은 남긴다(2026-10-07 감사)
                logger.warning("자동 용어 등록 건너뜀: %s", term_data.get("term"), exc_info=True)
    except Exception as e:
        logger.warning("용어 추출 실패 (무시하고 계속): %s", e)
    return count


@router.post("/import/file", status_code=201)
async def import_file(
    file: UploadFile = File(...),
    namespace: str = Form(...),
    chunk_strategy: str = Form(default="auto"),
    auto_analyze: bool = Form(default=False),
    auto_glossary: bool = Form(default=False),
    user: dict = Depends(get_current_user),
):
    """파일 업로드 → 파싱 → 청킹 → 벌크 등록.

    지원 포맷: .txt, .md, .pdf, .xlsx, .xlsm, .csv
    chunk_strategy: auto, section, paragraph, fixed
    auto_analyze: True이면 LLM Analyzer Agent로 전략/메타데이터 자동 결정
    auto_glossary: True이면 LLM으로 용어 자동 추출
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
        except Exception as e:
            logger.warning("Analyzer 실패 (기존 전략 사용): %s", e)

    # 청킹
    chunks = chunk_document(doc, strategy=chunk_strategy)
    if not chunks:
        raise HTTPException(status_code=400, detail="분할된 청크가 없습니다.")

    # items 구성
    items = [{"content": c.text} for c in chunks]

    # 벌크 등록
    result = await service.bulk_create_knowledge(
        namespace=namespace,
        items=items,
        source_file=file.filename,
        source_type="file_upload",
        created_by_user_id=user["id"],
        # 용어 추출은 job 성공 후에만(개수는 job 행에 기록) — 예전엔 여기서 바로 실행했다
        after_activation=_glossary_after_activation(namespace, doc.raw_text, user) if auto_glossary else None,
    )

    # 분석 결과 기록 — 예전엔 용어 수 갱신과 한 UPDATE로 묶여 있어 추출된 용어가 0건이면
    # analyzer_result도 같이 버려졌다(조건이 glossary_count > 0). 이제 독립적으로 남긴다.
    if result.get("job_id") and analyzer_result:
        try:
            from core.database import get_conn
            async with get_conn() as conn:
                await conn.execute(
                    "UPDATE rag_ingestion_job SET analyzer_result = $1 WHERE id = $2",
                    json.dumps(analyzer_result, ensure_ascii=False), result["job_id"],
                )
        except Exception:
            logger.warning("분석 결과 기록 실패 (job_id=%s)", result.get("job_id"), exc_info=True)

    return {
        **result,
        "chunks": len(chunks),
        "analyzer": analyzer_result,
        "source_name": doc.source_name,
        "page_count": doc.metadata.get("page_count"),
    }


@router.post("/import/file/preview")
async def preview_file_upload(
    file: UploadFile = File(...),
    strategy: Optional[str] = Form(None),
    namespace: Optional[str] = Form(None),
    user: dict = Depends(get_current_user),
):
    """파일 업로드 미리보기 — strategy 미지정(auto)일 때만 LLM Analyzer로 전략 자동
    결정 후 청킹. 사용자가 미리보기에서 전략을 수동 재선택한 경우(strategy 지정)는
    LLM 재호출 없이 바로 그 전략으로 재청킹한다."""
    from agents.knowledge_rag.ingestion.adapters import parse_file
    from agents.knowledge_rag.ingestion.chunker import chunk_document

    try:
        raw = await file.read()
        doc = parse_file(raw, file.filename or "unknown")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"파일 파싱 실패: {e}")

    if strategy and strategy != "auto":
        detected_strategy = strategy
    else:
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
        except Exception as e:
            logger.warning("문서 분석 실패 — 분할 방식 auto로: %s", e)

    chunks = chunk_document(doc, strategy=strategy)
    doc_id = await _save_ledger_draft(await _ledger_ns_id(namespace, user), doc, strategy, user,
                                      source_ref=file.filename or None)

    return {
        "source_document_id": doc_id,
        "source_name": doc.source_name,
        "source_type": doc.source_type,
        "page_count": doc.metadata.get("page_count"),
        "total_chars": len(doc.raw_text),
        "sections": len(doc.sections),
        "tables": len(doc.tables),
        "chunks": [{"idx": c.idx, "text": c.text, "title": c.section_title, "heading_path": list(c.heading_path or [])} for c in chunks],
        "chunk_count": len(chunks),
        "detected_strategy": detected_strategy,
    }


# ─── URL / Confluence 인제스천 ────────────────────────────────────────────────

class _UrlImportBody(_BM):
    namespace: str
    url: str
    confluence_token: Optional[str] = None
    chunk_strategy: str = "auto"
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

    items = [{"content": c.text} for c in chunks]

    result = await service.bulk_create_knowledge(
        namespace=body.namespace,
        items=items,
        source_file=body.url,
        source_type=doc.source_type,
        created_by_user_id=user["id"],
        after_activation=_glossary_after_activation(body.namespace, doc.raw_text, user) if body.auto_glossary else None,
    )

    return {
        **result,
        "chunks": len(chunks),
        "source_name": doc.source_name,
        "source_type": doc.source_type,
        "url": body.url,
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
    except Exception as e:
        logger.warning("문서 분석 실패 — 분할 방식 auto로: %s", e)

    chunks = chunk_document(doc, strategy=strategy)
    doc_id = await _save_ledger_draft(await _ledger_ns_id(body.namespace, user), doc, strategy, user)
    return {
        "source_document_id": doc_id,
        "source_name": doc.source_name,
        "source_type": doc.source_type,
        "total_chars": len(doc.raw_text),
        "sections": len(doc.sections),
        # 컨플루언스 단일 페이지도 일괄 등록·원장 다시 분할과 같은 상위 경로(페이지 제목 포함, 리뷰 2026-10-08)
        "chunks": [{"idx": c.idx, "text": c.text, "title": c.section_title,
                    "heading_path": (_enrich_heading_path(doc, c.heading_path, doc.metadata.get("title") or doc.source_name)
                                     if doc.source_type == "confluence" else list(c.heading_path or []))} for c in chunks],
        "chunk_count": len(chunks),
        "detected_strategy": detected_strategy,
        "url": body.url,
        # 컨플루언스 단일 페이지면 원본 식별자 — 확정 시 청크마다 실어 보내 재등록 교체에 쓴다
        "confluence_page_id": doc.metadata.get("page_id") if doc.source_type == "confluence" else None,
        "confluence_version": doc.metadata.get("version") if doc.source_type == "confluence" else None,
        "structure": _structure_summary(doc),
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
    auto_glossary: bool = False


# 컨플루언스 "상위 페이지 제목 → 업무구분 자동 생성"(_resolve_confluence_page_category)은 10/22 업무구분 폐지로 삭제 —
# 하위 업무는 등록자가 하위 업무 영역을 골라 넣는다(category-removal-design §3)


def _structure_summary(doc) -> dict:
    """페이지 구조 품질(2026-10-01) — 헤딩(h1~h4)으로 나뉜 섹션 수. 섹션 경계와 부모 섹션 확장(v2.116)은
    전부 원본 헤딩에 의존하므로(정적 파싱, LLM 미사용), 헤딩 없이 굵은 글씨·번호로만 구분한 페이지는
    등록 시점에 드러내 작성자가 고치거나 감안하게 한다. 섹션이 2개 이상이어야 구조화된 것으로 본다."""
    n = sum(1 for s in (doc.sections or []) if (s.get("title") or "").strip())
    return {"heading_sections": n, "structured": n >= 2}


def _enrich_heading_path(doc, in_page_heading_path: Optional[list[str]], page_title: Optional[str] = None) -> list[str]:
    """페이지 조상(직계 상위 페이지 제목)을 청크의 페이지 내 헤딩 조상 앞에 붙인다
    (2026-09-22, `docs/tech/knowledge-category-automation.md` §8). `heading_path`(v2.98)는
    지금까지 페이지 **안**의 h1~h4 조상만 담았는데, 페이지 자체에 구분되는 제목이 없는
    문서(표만 있는 페이지 등)는 여전히 빈 값이었다. 직계 상위 페이지 제목은 이미 카테고리
    자동화에서 실측된 신호("외부서비스" 아래 배달의민족/쿠팡이츠처럼 실제로 구분됨)라 그대로
    재사용 — 새 API 호출이나 저장 없이 기존 heading_path 배열 맨 앞에 얹기만 한다."""
    parent_title = doc.metadata.get("parent_title")
    base = list(in_page_heading_path or [])
    # 페이지 자기 제목도(2026-10-07 감사) — 없으면 같은 상위 아래 형제 페이지(배민/쿠팡이츠처럼 구조가 같은 페이지)를 문맥에서
    # 구분할 수 없고, 부모 섹션 확장(path[:-1])이 형제 페이지 청크까지 끌어왔다
    if page_title:
        # [페이지 제목, 페이지 안 조상] — 상위 페이지 제목까지 앞에 두면 최상위 섹션의 path[:-1]이 상위 페이지가 돼 부모 섹션 확장이
        # 형제 페이지로 번졌다(리뷰 2026-10-07). 상위 페이지(채널 구분)는 넣지 않는다
        return base if base[:1] == [page_title] else [page_title] + base
    if parent_title and (not base or base[0] != parent_title):
        return [parent_title] + base
    return base


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
    from core.database import resolve_namespace_id
    from core.security import get_user_confluence_pat

    token = body.confluence_token or get_user_confluence_pat(user)
    if not token:
        raise HTTPException(status_code=400, detail="Confluence PAT가 필요합니다.")

    import asyncio

    async def _fetch_and_chunk(p):
        try:
            doc = await fetch_confluence_by_id(body.base_url, p.page_id, token)
            chunks = chunk_document(doc, strategy=body.chunk_strategy)
            return {"page_id": p.page_id, "title": doc.source_name, "doc": doc, "chunks": chunks, "error": None}
        except Exception as e:
            return {"page_id": p.page_id, "title": p.title or p.page_id, "doc": None, "chunks": [], "error": str(e)}

    fetched = await asyncio.gather(*(_fetch_and_chunk(p) for p in body.pages))

    from core.database import get_conn
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, body.namespace)

    chunks_out: list[dict] = []
    pages_meta: list[dict] = []
    failed: list[dict] = []
    idx = 0
    for f in fetched:
        if f["error"]:
            failed.append({"page_id": f["page_id"], "title": f["title"], "error": f["error"]})
            continue
        page_start = idx
        page_doc_id = await _save_ledger_draft(ns_id, f["doc"], body.chunk_strategy, user, source_ref=str(f["page_id"]))
        for c in f["chunks"]:
            chunks_out.append({
                "source_document_id": page_doc_id,
                "idx": idx,
                "page_id": f["page_id"],
                "page_title": f["title"],
                "text": c.text,
                "title": c.section_title,
                "heading_path": _enrich_heading_path(f["doc"], c.heading_path, f["title"]),
                "confluence_page_id": f["page_id"],
                "confluence_version": f["doc"].metadata.get("version"),
            })
            idx += 1
        pages_meta.append({
            "page_id": f["page_id"],
            "title": f["title"],
            "chunk_start": page_start,
            "chunk_count": idx - page_start,
            **_structure_summary(f["doc"]),
        })

    return {
        "chunks": chunks_out,
        "chunk_count": len(chunks_out),
        "pages": pages_meta,
        "failed_pages": failed,
    }


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
    ns = await service.get_ingestion_job_namespace(job_id)
    if ns is None:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    await check_namespace_ownership(ns, user)

    job = await service.cancel_ingestion_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="진행 중인 작업이 아니거나 존재하지 않습니다.")
    return job
