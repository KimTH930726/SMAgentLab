"""정책서 임포트/검색 API."""
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from core.dependencies import get_current_user, check_namespace_ownership
from service.policy import service, search as search_service, unresolved_report, browse
from service.policy.schemas import ImportSummaryOut, PolicySearchOut, UnresolvedSummaryOut, PolicyItemOut

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


@router.get("/unresolved-summary", response_model=UnresolvedSummaryOut)
async def get_unresolved_summary(
    namespace: str = Query(...),
    system_key: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
):
    """unresolved/partial로 분류된 정책 항목을 system_key별로 집계.

    LLM 분해가 서술/파라미터 어디에도 못 넣은 내용이 팀별로 몇 건, 어떤 사유로 쌓였는지
    보여준다 — 팀 표준화 요청의 근거 자료(§2-3), 그리고 분해 프롬프트 개선 여지를 사람이
    발견하는 유일한 경로(2026-09-04 데모 중 발견된 헤더/데이터유실 버그가 이 갭을 드러냈다).
    """
    await check_namespace_ownership(namespace, user)
    try:
        summary = await unresolved_report.get_unresolved_summary(namespace, system_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return UnresolvedSummaryOut(**asdict(summary))


@router.get("/items", response_model=list[PolicyItemOut])
async def list_policy_items(
    namespace: str = Query(...),
    category: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
):
    """정책 항목을 item 단위로 목록 조회 — 각 item에 실제로 달린 param(RDB)/narrative(벡터)
    자식까지 함께 반환한다. `/search`와 달리 쿼리 없이도 전체 목록을 볼 수 있고, 결과가
    검색 히트가 아니라 3층 구조(item→param/chunk) 그대로다 — "지금 뭐가 어떻게 저장돼
    있는지" 사람이 훑어보는 용도(§4-2 참고: 검색과 목적이 달라 별도 함수로 분리).
    """
    await check_namespace_ownership(namespace, user)
    try:
        items = await browse.list_policy_items(namespace, category, q)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return [PolicyItemOut(**asdict(i)) for i in items]
