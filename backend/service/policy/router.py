"""정책서 임포트/검색 API."""
import logging
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from core.dependencies import get_current_user, get_current_admin, check_namespace_ownership
from service.policy import service, search as search_service, unresolved_report, browse, track2, pipeline_stats, review, edit, decompose, auto_review, json_format
from service.policy.schemas import AutoReviewRevertRequest, AutoReviewRunRequest
from service.policy.schemas import (
    ImportSummaryOut, PolicySearchOut, UnresolvedSummaryOut, PolicyItemOut, Track2ResultOut,
    Track2RunHistoryOut, PipelineStatsOut, PromoteSegmentRequest, PromoteSegmentOut,
    PromoteParamRequest, ItemActionRequest, UpdateParamRequest, UpdateNarrativeRequest,
    ItemStatusOut, SuggestParamRequest, SuggestParamOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/policy", tags=["policy"])


@router.post("/import", response_model=ImportSummaryOut)
async def import_policy_excel(
    file: UploadFile = File(...),
    namespace: str = Form(...),
    system_key: str = Form(default=""),
    reprocess_all: bool = Form(default=False),
    user: dict = Depends(get_current_user),
):
    """정책서 엑셀 업로드 → 파싱 → LLM 분해 → RDB 적재(pending_review).

    reprocess_all=true면 내용·파이프라인 버전이 같은 행도 전부 다시 분해한다(재정제 후 전체 재적재용).
    그렇지 않아도 파서·분해 프롬프트가 바뀐 뒤 올린 파일은 바뀐 행만이 아니라 전체가 재분해된다
    (응답의 pipeline_reprocessed).

    시트는 위치가 아니라 헤더 내용으로 용어집/정책 시트를 자동 판별한다. 재업로드 시 내용이
    바뀐 row만 새 버전으로 처리하고(변경 없으면 LLM 재호출 없이 스킵), 이전 버전은 삭제하지
    않고 deprecated로 보존한다(docs/policy-doc-pipeline-plan.md §2-1).
    """
    await check_namespace_ownership(namespace, user)
    if reprocess_all and user.get("role") != "admin":
        # 전 항목을 다시 AI로 분해해 새 버전으로 넣는다(수십 분 + 검토 큐 재충전) — 화면처럼 서버도 관리자만(/code-review)
        raise HTTPException(status_code=403, detail="'전체 다시 분해'는 관리자만 할 수 있습니다. 체크를 끄고 다시 올려 주세요.")
    try:
        raw = await file.read()
        result = await service.import_excel(
            namespace, system_key, file.filename or "unknown.xlsx", raw, force_reprocess=reprocess_all,
        )
    except json_format.PolicyJsonError as e:
        # 사용자가 바로 고칠 수 있게 "어디 / 무엇이 문제 / 어떻게 고치나" 목록 그대로(화면이 표로 보여 줌)
        raise HTTPException(status_code=400, detail=e.detail())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # 시트 단위 트랜잭션이라 실패한 시트는 통째로 반영되지 않는다(앞서 성공한 시트는 반영됨 —
        # 같은 파일을 다시 올리면 그 시트들은 내용 변경 없음으로 스킵되고 실패한 시트만 재처리)
        raise HTTPException(
            status_code=400,
            detail=f"정책서 임포트 실패: {e} — 실패한 시트는 반영되지 않았습니다. 같은 파일을 다시 올리면 이어서 처리됩니다.",
        )
    # 재임포트는 바뀐 행을 새 버전(pending_review)으로 다시 쌓는다 — 규칙이 켜져 있으면 그 파트에 바로 자동 통과를
    # 적용해 큐가 다시 차지 않게 한다(실패해도 임포트는 성공, auto_review.run_after_import 참고)
    auto = await auto_review.run_after_import(namespace)
    return ImportSummaryOut(
        source_file=result.source_file,
        sheets=[s.__dict__ for s in result.sheets],
        missing_marked=result.missing_marked,
        warnings=result.warnings,
        auto_review=auto,
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

    검토대기(pending_review)도 검색 대상에 포함한다(WBS 2-1 결정 — 반려·폐기만 제외). 그래서 위험도 기반
    자동 통과(auto_review.py)로 검토대기↔승인이 바뀌어도 검색 결과는 같다. 응답의 `status`로 미검토 여부 구분.
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


@router.post("/unresolved/{item_id}/promote-param", response_model=PromoteSegmentOut)
async def promote_unresolved_segment_to_param(
    item_id: int,
    body: PromoteParamRequest,
    user: dict = Depends(get_current_user),
):
    """unresolved segment 1건을 파라미터(policy_param)로 수동 편입 — "서술로 편입밖에
    없으면 반쪽짜리 아니냐"는 지적(2026-09-23)으로 신규. name/condition/value/unit은
    사람이 폼으로 직접 입력한 값 그대로 저장한다(unresolved_report.promote_segment_to_param
    참고, v1은 LLM 프리필 없음)."""
    await check_namespace_ownership(body.namespace, user)
    try:
        remaining = await unresolved_report.promote_segment_to_param(
            body.namespace, item_id, body.segment_index,
            body.name, body.condition, body.value, body.unit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PromoteSegmentOut(remaining_segments=remaining)


@router.post("/unresolved/{item_id}/suggest-param", response_model=SuggestParamOut)
async def suggest_unresolved_segment_param(
    item_id: int,
    body: SuggestParamRequest,
    user: dict = Depends(get_current_user),
):
    """미분류 segment의 파라미터 필드를 LLM이 1차 추측 — "값 넣을 사람이 없겠다"는
    지적(2026-09-23)으로 신규. 폼을 열자마자 프론트가 자동 호출해 프리필하는 용도라,
    추측이 안 되거나 실패해도 에러를 내지 않고 빈 필드로 200을 반환한다(decompose.
    suggest_param_fields 참고 — 사람이 그냥 수동으로 채우면 되는 보조 기능일 뿐)."""
    await check_namespace_ownership(body.namespace, user)
    try:
        text, reason = await unresolved_report.get_unresolved_segment(
            body.namespace, item_id, body.segment_index,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    suggestion = await decompose.suggest_param_fields(text, reason)
    return SuggestParamOut(**(suggestion or {}))


@router.patch("/params/{param_id}", response_model=ItemStatusOut)
async def update_policy_param(
    param_id: int,
    body: UpdateParamRequest,
    user: dict = Depends(get_current_user),
):
    """반려된 항목의 파라미터 수정 — "반려하면 그냥 데이터를 버리는데?"라는 지적(2026-09-23)
    으로 신규. 저장하면 검토대기로 되돌아가 재승인 기회를 얻는다(edit.py 참고)."""
    await check_namespace_ownership(body.namespace, user)
    try:
        new_status = await edit.update_param(
            body.namespace, param_id, body.name, body.condition, body.value, body.unit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ItemStatusOut(status=new_status)


@router.patch("/narratives/{chunk_id}", response_model=ItemStatusOut)
async def update_policy_narrative(
    chunk_id: int,
    body: UpdateNarrativeRequest,
    user: dict = Depends(get_current_user),
):
    """반려된 항목의 서술 수정 — 위 update_policy_param과 동일 취지. 텍스트가 바뀌므로
    재임베딩 후 검토대기로 되돌린다(edit.py 참고)."""
    await check_namespace_ownership(body.namespace, user)
    try:
        new_status = await edit.update_narrative(body.namespace, chunk_id, body.chunk_text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ItemStatusOut(status=new_status)


@router.post("/items/{item_id}/approve")
async def approve_policy_item(
    item_id: int,
    body: ItemActionRequest,
    user: dict = Depends(get_current_user),
):
    """검토 대기 정책 항목을 승인 — status: pending_review → active(review.py 참고)."""
    await check_namespace_ownership(body.namespace, user)
    try:
        await review.approve_item(body.namespace, item_id, user["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "active"}


@router.post("/items/{item_id}/reject")
async def reject_policy_item(
    item_id: int,
    body: ItemActionRequest,
    user: dict = Depends(get_current_user),
):
    """검토 대기 정책 항목을 반려 — status: pending_review → rejected(review.py 참고)."""
    await check_namespace_ownership(body.namespace, user)
    try:
        result = await review.reject_item(body.namespace, item_id, user["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 자동 통과 표본이 반려되면 그 규칙이 멈춘다 — 화면이 바로 알리도록 같이 돌려준다
    return {"status": "rejected", **result}


# ── 위험도 기반 자동 통과(2026-10-01, auto_review.py) ──────────────────────────

@router.get("/review-summary")
async def policy_review_summary(namespace: Optional[str] = Query(default=None), user: dict = Depends(get_current_user)):
    """사람이 실제로 봐야 할 큐 크기(높음·중간·표본) + 자동 통과 누계 + 규칙 상태."""
    if namespace:
        await check_namespace_ownership(namespace, user)
    elif user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="전체 요약은 관리자만 볼 수 있습니다.")
    return await auto_review.summary(namespace)


@router.post("/auto-review/run")
async def run_policy_auto_review(body: AutoReviewRunRequest, admin: dict = Depends(get_current_admin)):
    """낮음 등급 자동 통과 실행(dry_run=true면 건수 미리보기만). 표본은 사람 큐에 남긴다."""
    return await auto_review.run(body.namespace, actor_id=admin["id"], dry_run=body.dry_run)


@router.post("/auto-review/rules/{rule_key}/resume")
async def resume_policy_auto_rule(rule_key: str, admin: dict = Depends(get_current_admin)):
    """표본 반려로 멈춘 규칙을 확인 후 재개."""
    try:
        return await auto_review.resume_rule(rule_key, admin["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/auto-review/revert")
async def revert_policy_auto_review(body: AutoReviewRevertRequest, admin: dict = Depends(get_current_admin)):
    """한 번의 실행 또는 한 규칙으로 자동 통과된 항목을 검토대기로 일괄 되돌림."""
    try:
        return {"reverted": await auto_review.revert_bulk(run_id=body.run_id, rule_key=body.rule_key,
                                                          actor_id=admin["id"], namespace=body.namespace)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/items/{item_id}/revert-auto")
async def revert_auto_policy_item(item_id: int, body: ItemActionRequest, user: dict = Depends(get_current_user)):
    """자동 통과된 항목 하나를 검토대기로 되돌림(승인 권한과 동일)."""
    await check_namespace_ownership(body.namespace, user)
    try:
        await auto_review.revert_item(body.namespace, item_id, user["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "pending_review"}


@router.get("/items", response_model=list[PolicyItemOut])
async def list_policy_items(
    namespace: str = Query(...),
    category: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    sort: Optional[str] = Query(default=None, pattern="^risk$"),
    user: dict = Depends(get_current_user),
):
    """정책 항목을 item 단위로 목록 조회 — 각 item에 실제로 달린 param(RDB)/narrative(벡터)
    자식까지 함께 반환한다. `/search`와 달리 쿼리 없이도 전체 목록을 볼 수 있고, 결과가
    검색 히트가 아니라 3층 구조(item→param/chunk) 그대로다 — "지금 뭐가 어떻게 저장돼
    있는지" 사람이 훑어보는 용도(§4-2 참고: 검색과 목적이 달라 별도 함수로 분리).
    """
    await check_namespace_ownership(namespace, user)
    try:
        items = await browse.list_policy_items(namespace, category, q, status, sort)
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
