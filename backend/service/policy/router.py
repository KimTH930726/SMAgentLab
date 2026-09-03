"""정책서 임포트 API — POST /api/policy/import."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from core.dependencies import get_current_user, check_namespace_ownership
from service.policy import service
from service.policy.schemas import ImportSummaryOut

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
