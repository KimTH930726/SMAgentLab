"""정책서 임포트/검색 API."""
import logging
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from core.dependencies import get_current_user, get_current_admin, check_namespace_ownership
from service.policy import service, search as search_service, unresolved_report, browse, track2, pipeline_stats
from service.policy.schemas import (
    ImportSummaryOut, PolicySearchOut, UnresolvedSummaryOut, PolicyItemOut, Track2ResultOut,
    Track2RunHistoryOut, PipelineStatsOut, PromoteSegmentRequest, PromoteSegmentOut,
)

logger = logging.getLogger(__name__)

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


@router.post("/unresolved/{item_id}/promote", response_model=PromoteSegmentOut)
async def promote_unresolved_segment(
    item_id: int,
    body: PromoteSegmentRequest,
    user: dict = Depends(get_current_user),
):
    """unresolved segment 1건을 서술(policy_chunk)로 수동 편입 — "조회만 있고 액션이 없다"는
    지적(2026-09-16)으로 신규. 정밀 재분류(param 필드 추출)가 아니라 최소한 검색은 되게
    만드는 원클릭 액션이다(unresolved_report.promote_segment_to_narrative 참고)."""
    await check_namespace_ownership(body.namespace, user)
    try:
        remaining = await unresolved_report.promote_segment_to_narrative(
            body.namespace, item_id, body.segment_index,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PromoteSegmentOut(remaining_segments=remaining)


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


@router.get("/track2/axes")
async def get_track2_axes(user: dict = Depends(get_current_user)):
    """비교 가능한 데이터 축 목록(2026-09-16, 엔진 파라미터화) — 지금은 정책서 하나뿐이지만
    새 축(CMDB 등)이 track2.AXIS_REGISTRY에 등록되면 여기 자동으로 같이 늘어난다. 화면의
    축 선택 드롭다운이 이 목록을 그대로 쓴다."""
    return [{"key": k, "label": track2.AXIS_LABELS.get(k, k)} for k in track2.AXIS_REGISTRY]


@router.post("/track2/run", response_model=Track2ResultOut)
async def run_track2(
    top_k: int = Query(default=10, ge=1, le=50),
    axis: str = Query(default="policy"),
    user: dict = Depends(get_current_admin),
):
    """Track 2 저장소 전략 비교(§4)를 즉시 실행 — A(rag_knowledge 지식-only, 임시 격리
    네임스페이스) vs B(지금 하이브리드 스키마) 를 골든셋으로 비교해 유형별 hit@K를 반환한다.

    전체 policy_item 규모만큼 임베딩을 다시 계산해야 해서 몇 분 걸린다 — admin 전용(무거운
    실험 실행이라 일반 사용자가 실수로 반복 실행하지 않도록). 실행 중 만드는 임시 데이터는
    끝나면 자동 삭제되어 프로덕션에 흔적을 남기지 않는다.

    `axis`는 track2.AXIS_REGISTRY에서 골든셋 경로+전략 조합을 찾는다(2026-09-16 엔진
    파라미터화) — 지금은 "policy" 하나뿐, 새 축이 등록되면 값만 늘어난다.
    """
    if axis not in track2.AXIS_REGISTRY:
        raise HTTPException(status_code=400, detail=f"알 수 없는 축: {axis} (가능: {list(track2.AXIS_REGISTRY)})")
    golden_set_path, strategies = track2.AXIS_REGISTRY[axis]
    try:
        result = await track2.run_comparison(golden_set_path=golden_set_path, strategies=strategies, top_k=top_k)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 실행마다 자동 저장(2026-09-15, 실험실 게이트 작업3) — "이력이 쌓이는 것 자체가
    # 산출물"이라 별도 저장 버튼 없이 매 실행이 곧 스냅샷이 되게 한다. 저장 실패로 조회
    # 결과 자체를 못 돌려주는 건 과하므로 best-effort(로그만 남기고 응답은 그대로 반환).
    try:
        await track2.save_run(result, triggered_by=user.get("id"))
    except Exception as e:
        logger.warning("[Track2] 실행 이력 저장 실패(응답은 정상 반환): %s", e)
    return Track2ResultOut(**asdict(result))


@router.get("/track2/history", response_model=list[Track2RunHistoryOut])
async def get_track2_history(
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """Track2 실행 이력 — 모니터링 뷰의 추이 차트용(실험실 게이트 작업3)."""
    rows = await track2.list_run_history(limit=limit)
    return [Track2RunHistoryOut(**r) for r in rows]


@router.get("/pipeline-stats", response_model=PipelineStatsOut)
async def get_pipeline_stats(
    namespace: str = Query(...),
    user: dict = Depends(get_current_user),
):
    """기준정보 축적 현황 — 모니터링 뷰(실험실 게이트 작업3)의 "①기준정보 축적" 섹션."""
    await check_namespace_ownership(namespace, user)
    stats = await pipeline_stats.get_pipeline_stats(namespace)
    return PipelineStatsOut(**stats)
