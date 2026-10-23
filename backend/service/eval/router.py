"""/api/eval — 평가 게이트 판정 기록(품질 › 평가 게이트 화면). 읽기는 로그인 사용자(옛 /policy/answer-eval/history와 같음),
기준선 지정은 관리자. 판정 실행은 scripts/eval_gate.py(문항당 LLM 호출·채점이라 화면에서 안 돌림)."""
from fastapi import APIRouter, Depends, HTTPException, Query

from core.dependencies import get_current_admin, get_current_user
from service.eval import service

router = APIRouter(prefix="/api/eval", tags=["eval"])


@router.get("/gate-runs")
async def list_gate_runs(limit: int = Query(default=30, ge=1, le=200), user: dict = Depends(get_current_user)):
    """판정 이력(최신순) — 헤드라인 숫자만. 나빠진 qid 등 문항 목록은 상세."""
    return await service.list_runs(limit)


@router.get("/gate-runs/baseline")
async def get_gate_baseline(user: dict = Depends(get_current_user)):
    """현재 기준선(없으면 null) — /{run_id}보다 먼저(경로 순서)."""
    return await service.get_baseline()


@router.get("/gate-runs/{run_id}")
async def get_gate_run(run_id: int, user: dict = Depends(get_current_user)):
    r = await service.get_run(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="판정 기록이 없습니다.")
    return r


@router.post("/gate-runs/{run_id}/baseline")
async def set_gate_baseline(run_id: int, admin: dict = Depends(get_current_admin)):
    try:
        r = await service.set_baseline(run_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not r:
        raise HTTPException(status_code=404, detail="판정 기록이 없습니다.")
    return r
