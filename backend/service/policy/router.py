"""정책서 API — 정책서 화면 조회(v0, 2026-10-21) · 즉석 검색 · 실험실(Track2·답변 정확도·축적 현황).

v0 재구축 2단계(10/21)에서 옛 엑셀 경로 화면의 API(올리기·붙여넣기·미분류·파라미터/서술 줄 안 편집·위험도 검토 큐·항목 승인/반려)를 뺐다.
정책 내용 변경은 모두 정정 원장(/api/corrections — 정책 화면의 직접 수정·AI 수정 제안)으로 들어가 정정 요청에서 승인할 때만 새 버전이 된다.
"""
import logging
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.dependencies import get_current_user, get_current_admin, check_knowledge_read
from service.policy import answer_eval
from service.policy import catalog, search as search_service, track2, pipeline_stats
from service.policy.schemas import PolicySearchOut, Track2ResultOut, Track2RunHistoryOut, PipelineStatsOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/policy", tags=["policy"])


# ── 정책서 화면(v0) — 읽기 전용. 권한: 조회는 로그인 사용자 누구나(check_knowledge_read) ─────────────

@router.get("/policies")
async def list_policies(namespace: str = Query(...), q: Optional[str] = Query(default=None, max_length=200),
                        user: dict = Depends(get_current_user)):
    """지금 쓰이는 정책 1건 = 1줄(문서 순서) + 검토 중 정정 번호."""
    await check_knowledge_read(namespace, user)
    return await catalog.list_policies(namespace, q)


@router.get("/policies/{logical_id}")
async def get_policy(logical_id: int, namespace: str = Query(...), user: dict = Depends(get_current_user)):
    """정책 상세 — 원문·비고·파라미터·용어·버전 이력·검토 중 정정."""
    await check_knowledge_read(namespace, user)
    out = await catalog.get_policy(namespace, logical_id)
    if out is None:
        raise HTTPException(status_code=404, detail="이 업무 영역에 지금 쓰이는 그 정책이 없어요 — 새로고침하세요.")
    return out


@router.get("/ref-tables")
async def list_ref_tables(namespace: str = Query(...), user: dict = Depends(get_current_user)):
    await check_knowledge_read(namespace, user)
    return await catalog.list_ref_tables(namespace)


@router.get("/ref-tables/{table_id}")
async def get_ref_table(table_id: int, namespace: str = Query(...), user: dict = Depends(get_current_user)):
    """참고표 원표 — 열 + 행({열 이름: 값})."""
    await check_knowledge_read(namespace, user)
    out = await catalog.get_ref_table(namespace, table_id)
    if out is None:
        raise HTTPException(status_code=404, detail="참고표를 찾을 수 없어요.")
    return out


@router.get("/overviews")
async def list_overviews(namespace: str = Query(...), user: dict = Depends(get_current_user)):
    """문서 개요(서문)."""
    await check_knowledge_read(namespace, user)
    return await catalog.list_overviews(namespace)


@router.get("/search", response_model=PolicySearchOut)
async def search_policy(
    namespace: str = Query(...),
    q: str = Query(..., min_length=1),
    category: Optional[str] = Query(default=None),
    top_k: int = Query(default=10, ge=1, le=50),
    user: dict = Depends(get_current_user),
):
    """정책 데이터 검색 — 파라미터(RDB 정확 조회)와 서술(벡터 검색) 두 갈래를 함께 반환한다.

    대상은 채팅 검색과 같은 기준(visibility.searchable_sql — v0는 승인된 것만, 옛 엑셀 경로는 검토 대기 포함).
    응답의 `status`로 미검토 여부 구분.
    """
    await check_knowledge_read(namespace, user)
    try:
        result = await search_service.search_policy(namespace, q, category, top_k)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PolicySearchOut(
        params=[p.__dict__ for p in result.params],
        narratives=[n.__dict__ for n in result.narratives],
    )


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


@router.get("/answer-eval/history")
async def get_answer_eval_history(limit: int = Query(default=30, ge=1, le=200), user: dict = Depends(get_current_user)):
    """최종 답변 정확도 실행 이력(집계만) — 평가 게이트 "답변 정확도" 탭. 실행은 scripts/eval_answers.py(문항당 사내 LLM 호출이라 화면에서 안 돌림)."""
    return await answer_eval.list_history(limit)


@router.get("/pipeline-stats", response_model=PipelineStatsOut)
async def get_pipeline_stats(
    namespace: str = Query(...),
    user: dict = Depends(get_current_user),
):
    """기준정보 축적 현황 — 모니터링 뷰(실험실 게이트 작업3)의 "①기준정보 축적" 섹션."""
    await check_knowledge_read(namespace, user)
    stats = await pipeline_stats.get_pipeline_stats(namespace)
    return PipelineStatsOut(**stats)
