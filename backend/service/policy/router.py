"""정책서 임포트/검색 API."""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from core.dependencies import get_current_user, check_namespace_ownership
from service.policy import service, search as search_service
from service.policy.schemas import ImportSummaryOut, PolicySearchOut

router = APIRouter(prefix="/api/policy", tags=["policy"])


@router.post("/import", response_model=ImportSummaryOut)
async def import_policy_excel(
    file: UploadFile = File(...),
    namespace: str = Form(...),
    system_key: str = Form(default=""),
    user: dict = Depends(get_current_user),
):
    """정책서 엑셀 업로드 → 파싱 → LLM 분해 → RDB 적재(pending_review).

    시트는 위치가 아니라 헤더 내용으로 용어집/정책 시트를 자동 판별한다. 재업로드 시 내용이
    바뀐 row만 새 버전으로 처리하고(변경 없으면 LLM 재호출 없이 스킵), 이전 버전은 삭제하지
    않고 deprecated로 보존한다(docs/policy-doc-pipeline-plan.md §2-1).
    """
    await check_namespace_ownership(namespace, user)
    try:
        raw = await file.read()
        result = await service.import_excel(namespace, system_key, file.filename or "unknown.xlsx", raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"정책서 임포트 실패: {e}")
    return ImportSummaryOut(
        source_file=result.source_file,
        sheets=[s.__dict__ for s in result.sheets],
    )


@router.get("/search", response_model=PolicySearchOut)
async def search_policy(
    namespace: str = Query(...),
    q: str = Query(..., min_length=1),
    category: Optional[str] = Query(default=None),
    top_k: int = Query(default=10, ge=1, le=50),
    user: dict = Depends(get_current_user),
):
    """정책 데이터 검색 — 파라미터(RDB 정확 조회)와 서술(벡터 검색) 두 갈래를 함께 반환한다.

    v1엔 검토/승인 화면이 없어(§2-4) status='pending_review'인 데이터도 검색 대상에
    포함한다 — 응답의 `status` 필드로 미검토 여부를 구분할 수 있다.
    """
    await check_namespace_ownership(namespace, user)
    try:
        result = await search_service.search_policy(namespace, q, category, top_k)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PolicySearchOut(
        params=[p.__dict__ for p in result.params],
        narratives=[n.__dict__ for n in result.narratives],
    )
