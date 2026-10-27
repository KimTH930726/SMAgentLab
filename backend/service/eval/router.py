"""/api/eval — 평가 게이트 판정 기록(품질 › 평가 게이트 화면). 읽기는 로그인 사용자(옛 /policy/answer-eval/history와 같음),
기준선 지정은 관리자. 판정 실행은 scripts/eval_gate.py(문항당 LLM 호출·채점이라 화면에서 안 돌림)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

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


@router.get("/gate-runs/{run_id}/layers")
async def get_layer_questions(run_id: int, admin: dict = Depends(get_current_admin)):
    """층별 검색 놓침·답 틀림 문항 번호 — [검색]·[답] 층 줄을 누르면 그 문항들(관리자)."""
    from service.eval.files import ResultFileError
    try:
        r = await service.layer_questions(run_id)
    except ResultFileError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if r is None:
        raise HTTPException(status_code=404, detail="판정 기록이 없습니다.")
    return r


@router.get("/gate-runs/{run_id}/questions/{qid}")
async def get_question_detail(run_id: int, qid: str, admin: dict = Depends(get_current_admin)):
    """문항 상세(관리자) — 질문·답·채점 이유는 결과 파일에서 읽기만(DB 저장 없음). 정책 내용이 담겨 관리자만."""
    from service.eval.files import ResultFileError
    try:
        r = await service.question_detail(run_id, qid)
    except ResultFileError as e:
        raise HTTPException(status_code=404, detail=str(e))
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


class ReviewBody(BaseModel):
    status: str
    reason: str = ""


class AdoptBody(BaseModel):
    gate_run_id: int


class QuestionDecision(BaseModel):
    status: str   # added(시험지에 넣기) / dismissed(빼기) / proposed(되돌리기)


class HypothesisBody(BaseModel):
    cause: str
    name: str
    status: str   # confirmed(맞아요) / rejected(다시 묶기) / open(되돌리기)


@router.get("/improvements/latest")
async def get_latest_improvement(run_id: Optional[int] = None, user: dict = Depends(get_current_user)):
    """최근 진단(원인별 qid·가설) + 개선 후보 — run_id를 주면 그 진단(틀린 문항 리포트). 만들기는 scripts/eval_improve.py --save."""
    return await service.latest_improvement(run_id)


@router.post("/candidates/{cand_id}/review")
async def review_candidate(cand_id: int, body: ReviewBody, admin: dict = Depends(get_current_admin)):
    try:
        r = await service.review_candidate(cand_id, status=body.status, reason=body.reason, user_id=admin["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not r:
        raise HTTPException(status_code=404, detail="후보가 없습니다.")
    return r


@router.post("/candidates/{cand_id}/adopt")
async def adopt_candidate(cand_id: int, body: AdoptBody, admin: dict = Depends(get_current_admin)):
    try:
        r = await service.adopt_candidate(cand_id, gate_run_id=body.gate_run_id, user_id=admin["id"])
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not r:
        raise HTTPException(status_code=404, detail="후보가 없습니다.")
    return r


@router.get("/techniques")
async def list_techniques(user: dict = Depends(get_current_user)):
    """답 기법 대장(읽기) — 기준값은 지금 서버의 운영값."""
    from service.eval.techniques import as_rows
    rows = as_rows()
    # 지금 진단에서 이 기법을 가리키는 개선 후보 — "효과가 있었나·지금 무엇이 걸려 있나"(사용자 10/26)
    latest = await service.latest_improvement()
    by_tech: dict[str, list[dict]] = {}
    for c in (latest or {}).get("candidates", []):
        by_tech.setdefault(c["technique"], []).append({"id": c["id"], "title": c["title"], "status": c["status"],
                                                       "recommend": c["recommend"], "n_target": c["n_target"]})
    return [{**r, "candidates": by_tech.get(r["key"], [])} for r in rows]


@router.get("/decisions")
async def get_decisions_report(days: int = Query(default=90, ge=1, le=365), admin: dict = Depends(get_current_admin)):
    """AI 판단 원장 집계(품질 › AI 판단, 관리자) — AI가 정한 것을 사람이 얼마나 바꿨나·틀렸다고 했나."""
    return await service.decisions_report(days)


@router.post("/improvements/{improvement_id}/hypotheses")
async def mark_hypothesis(improvement_id: int, body: HypothesisBody, admin: dict = Depends(get_current_admin)):
    """AI 묶음 확인 — 맞아요(이름 고정) / 다시 묶기(다음 진단이 새로 묶음) / 되돌리기."""
    try:
        r = await service.mark_hypothesis(improvement_id, cause=body.cause, name=body.name, status=body.status, user_id=admin["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not r:
        raise HTTPException(status_code=404, detail="묶음이 없습니다.")
    return r


class VerdictBody(BaseModel):
    verdict: str = Field(pattern="^(wrong|context_changed|unknown)$")
    reason: str = Field(default="", max_length=200)   # 원장이 200자까지 남긴다(ledger.set_verdict) — 말없이 자르지 않게


@router.post("/decisions/{decision_id}/verdict")
async def set_decision_verdict(decision_id: int, body: VerdictBody, user: dict = Depends(get_current_user)):
    """사람이 AI 값을 바꾼 판단에 "AI가 틀렸나요?" 답 — 틀림 / 상황이 바뀜 / 모르겠음. 바꾼 사람만(ledger.set_verdict — 관리자가 아니어도 자기가 바꾼 건 답함)."""
    from service.decision import ledger
    from core.database import get_conn
    async with get_conn() as conn:
        ok = await ledger.set_verdict(conn, decision_id, verdict=body.verdict, reason=body.reason.strip(), user_id=user["id"])
    if not ok:
        raise HTTPException(status_code=403, detail="이 판단을 바꾼 사람만 답할 수 있어요(또는 없는 판단).")
    return {"id": decision_id, "verdict": body.verdict}


@router.get("/question-candidates")
async def list_question_candidates(status: Optional[str] = Query(default=None), admin: dict = Depends(get_current_admin)):
    """실사용 문항 후보(관리자) — 정정 승인된 채팅 질문. 질문은 사용자가 쓴 글 그대로라 관리자만."""
    from service.eval import questions
    return await questions.list_candidates(status)


@router.post("/question-candidates/{cand_id}")
async def decide_question_candidate(cand_id: int, body: QuestionDecision, admin: dict = Depends(get_current_admin)):
    from service.eval import questions
    try:
        r = await questions.decide(cand_id, status=body.status, user_id=admin["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not r:
        raise HTTPException(status_code=404, detail="후보가 없습니다.")
    return r


@router.get("/ingest-checks/latest")
async def get_latest_ingest_check(user: dict = Depends(get_current_user)):
    """최근 적재 검수(설계 완료 기준 검사 + 쓰임 검사) — 평가 게이트 [적재 검수]. 만들기는 scripts/check_ingest.py --save."""
    return await service.latest_ingest_check()


@router.get("/report")
async def get_report(admin: dict = Depends(get_current_admin)):
    """틀린 문항 리포트(관리자) — 지금 점수 · 틀린 문항(질문·왜·저장 구조·적용해 볼 기법) · 기법별 실제 효과(잠근 시험지).
    질문 글은 결과 파일에서 읽기만(정책 내용이 담겨 관리자만)."""
    from service.eval.files import ResultFileError
    from service.eval.report import build_report
    try:
        return await build_report()
    except ResultFileError as e:   # 같은 이름 결과 파일이 여러 곳 등 — 500 대신 이유(리뷰 L1)
        raise HTTPException(status_code=409, detail=str(e))

