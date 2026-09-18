"""참조데이터(공통코드/DB스키마) 검색 API — 조회 전용.

적재(ingest_common_codes/ingest_db_columns)는 아직 관리자 업로드 UI가 없다 — 지금까지는
1회성 마이그레이션 스크립트(scripts/migrate_id20_to_refdata.py)로만 채워졌다. 이 라우터는
평가 게이트 즉석 질의(UnifiedAdhocSearch)가 chat과 동일한 네 번째 RRF 축을 보여주기 위한
조회 엔드포인트만 제공한다(2026-09-18)."""
from fastapi import APIRouter, Depends, Query

from core.dependencies import get_current_user, check_namespace_ownership
from service.refdata import service
from service.refdata.schemas import RefDataSearchOut

router = APIRouter(prefix="/api/refdata", tags=["refdata"])


@router.get("/search", response_model=RefDataSearchOut)
async def search_refdata(
    namespace: str = Query(...),
    q: str = Query(..., min_length=1),
    top_k: int = Query(default=10, ge=1, le=50),
    user: dict = Depends(get_current_user),
):
    """공통코드/DB스키마 두 갈래를 함께 검색해 반환 — agent.py의 채택 축과 동일 구조."""
    await check_namespace_ownership(namespace, user)
    common_codes = await service.search_common_codes(namespace, q, top_k)
    db_columns = await service.search_db_columns(namespace, q, top_k)
    return RefDataSearchOut(common_codes=common_codes, db_columns=db_columns)
