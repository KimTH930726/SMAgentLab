"""/api/eval — 평가 게이트 판정 기록(품질 › 평가 게이트 화면). 읽기는 로그인 사용자(옛 /policy/answer-eval/history와 같음),
기준선 지정은 관리자. 판정 실행은 scripts/eval_gate.py(문항당 LLM 호출·채점이라 화면에서 안 돌림)."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.dependencies import get_current_admin, get_current_user, log_cross_part_namespace_read
from service.eval import service

router = APIRouter(prefix="/api/eval", tags=["eval"])


AXIS_PATTERN = "^(" + "|".join(service.AXES) + ")$"


@router.get("/gate-runs")
async def list_gate_runs(limit: int = Query(default=30, ge=1, le=200), axis: Optional[str] = Query(default=None, pattern=AXIS_PATTERN),
                         user: dict = Depends(get_current_user)):
    """판정 이력(최신순) — 헤드라인 숫자만. 나빠진 qid 등 문항 목록은 상세. axis = 정책서(policy)·지식(knowledge), 없으면 전부."""
    return await service.list_runs(limit, axis)


@router.get("/gate-runs/baseline")
async def get_gate_baseline(axis: str = Query(default="policy", pattern=AXIS_PATTERN), user: dict = Depends(get_current_user)):
    """그 축의 현재 기준선(없으면 null) — /{run_id}보다 먼저(경로 순서)."""
    return await service.get_baseline(axis)


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
async def get_latest_improvement(run_id: Optional[int] = None, axis: str = Query(default="policy", pattern=AXIS_PATTERN),
                                 user: dict = Depends(get_current_user)):
    """최근 진단(원인별 qid·가설) + 개선 후보 — run_id를 주면 그 진단(틀린 문항 리포트). 만들기는 scripts/eval_improve.py --save."""
    return await service.latest_improvement(run_id, axis)


@router.get("/improvements/board")
async def get_candidate_board(axis: str = Query(default="policy", pattern=AXIS_PATTERN), user: dict = Depends(get_current_user)):
    """평가 게이트 › 개선 후보(한 목록 + 상태 필터, 10/30) — 최근 진단 후보 + 그 전 진단에서 종결된 반영·실패·기각. 줄마다 화면 단계(phase)."""
    return await service.candidate_board(axis)


@router.post("/candidates/{cand_id}/review")
async def review_candidate(cand_id: int, body: ReviewBody, admin: dict = Depends(get_current_admin)):
    try:
        r = await service.review_candidate(cand_id, status=body.status, reason=body.reason, user_id=admin["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not r:
        raise HTTPException(status_code=404, detail="후보가 없습니다.")
    if body.status == "approved":   # [실험해 보기] = 실제로 잰다(10/30) — 자동 실험이 안 되는 후보는 이유만 돌려준다(결정은 저장됨)
        try:
            r = {**r, "experiment": await service.start_experiment(cand_id, user_id=admin["id"])}
        except ValueError as e:
            r = {**r, "experiment": None, "experiment_note": str(e)}
    return r


@router.post("/candidates/{cand_id}/experiment")
async def start_experiment(cand_id: int, admin: dict = Depends(get_current_admin)):
    """[다시 돌리기] — 실패했거나 다시 재고 싶을 때. 진행 중인 작업이 있으면 그것을 돌려준다(하나만)."""
    try:
        return await service.start_experiment(cand_id, user_id=admin["id"])
    except LookupError:
        raise HTTPException(status_code=404, detail="후보가 없습니다.")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


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
    by_tech: dict[str, list[dict]] = {}
    latest = [await service.latest_improvement(axis=ax) for ax in ("policy", "knowledge")]   # 축마다 가장 최근 진단(10/29 업무지식도 루프)
    for c in [c for x in latest for c in (x or {}).get("candidates", [])]:
        by_tech.setdefault(c["technique"], []).append({"id": c["id"], "title": c["title"], "status": c["status"],
                                                       "recommend": c["recommend"], "n_target": c["n_target"]})
    # 근거 자동(10/30): 코드에 적은 근거 + 개선 후보의 반영·실패(날짜·측정 번호) — 반영할 때마다 손으로 근거 줄을 적지 않게
    from core.database import get_conn
    from service.eval.history import from_decisions
    async with get_conn() as conn:
        dec = [dict(r) for r in await conn.fetch(
            """SELECT id, key, technique, title, status, reviewed_at, review_reason, measured_gate_run_ids, adopted_gate_run_id
               FROM eval_candidate WHERE status IN ('adopted', 'failed') AND reviewed_at IS NOT NULL""")]
    auto: dict[str, list[dict]] = {}
    for d in from_decisions(dec, {}, {}):
        auto.setdefault(d["technique"], []).append({"date": d["date"], "note": f"{d['result']} — {d['what']}", "gate_run_id": d["gate_run_id"],
                                                    "auto": True})
    return [{**r, "evidence": sorted(r["evidence"] + auto.get(r["key"], []), key=lambda e: e["date"]),
             "candidates": by_tech.get(r["key"], [])} for r in rows]


@router.get("/decisions")
async def get_decisions_report(days: int = Query(default=90, ge=1, le=365), admin: dict = Depends(get_current_admin)):
    """AI 판단 원장 집계(품질 › AI 판단, 관리자) — AI가 정한 것을 사람이 얼마나 바꿨나·틀렸다고 했나."""
    return await service.decisions_report(days)


@router.get("/history")
async def get_history(limit: int = Query(default=200, ge=1, le=500), user: dict = Depends(get_current_user)):
    """평가 게이트 › 적용 이력 — 기법 대장 근거 + 개선 후보 결정을 날짜순(무엇을 언제 어떤 측정으로 바꿨나 · 효과 · 결과). 원문 없음."""
    from service.eval.history import history
    return await history(limit)


@router.get("/overrides")
async def get_overrides(days: int = Query(default=30, ge=1, le=365), admin: dict = Depends(get_current_admin)):
    """운영 번복 요약(관리자, AI 감사 로그 위 요약) — 종류별 판단·번복·번복률·이유·패턴, 형식적 승인 의심, 위치 판정 리플레이. 원문 없음."""
    from core.database import get_conn
    from service.eval import override as override_mod
    from service.eval.improve import LATEST_KNOWLEDGE_RUN_SQL
    async with get_conn() as conn:
        rep = await override_mod.report(conn, days)
    async with get_conn() as conn:   # 번복으로 만든 개선 후보(가장 최근 진단) — 감사 로그 "번복으로 만든 후보 n →"
        cands = [dict(r) for r in await conn.fetch(
            "SELECT c.id, c.title, c.status, c.qids FROM eval_candidate c WHERE c.cause = 'override_location' "
            "AND c.run_id = " + LATEST_KNOWLEDGE_RUN_SQL + " ORDER BY c.id")]   # 업무지식 축 진단에서 만든다(10/29)
    for c in cands:
        c["qids"] = json.loads(c["qids"]) if isinstance(c["qids"], str) else (c["qids"] or [])
    rep["candidates"] = cands
    for k in rep["kinds"]:
        k.pop("override_ids", None)
    return rep


@router.get("/decisions/log")
async def get_decision_log(days: int = Query(default=30, ge=1, le=365), kind: Optional[str] = Query(default=None, max_length=40),
                           changed_only: bool = False, limit: int = Query(default=50, ge=1, le=200), before: Optional[int] = None,
                           admin: dict = Depends(get_current_admin)):
    """AI 감사 로그(관리자) — 원장 시간순 한 줄씩(최신 먼저, before = 다음 페이지). 원문 없음."""
    from service.eval.decision_log import decision_log
    return await decision_log(days=days, kind=kind, changed_only=changed_only, limit=limit, before_id=before)


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
async def get_report(axis: str = Query(default="policy", pattern=AXIS_PATTERN), admin: dict = Depends(get_current_admin)):
    """틀린 문항 리포트(관리자) — 지금 점수 · 틀린 문항(질문·왜·저장 구조·적용해 볼 기법) · 기법별 실제 효과(잠근 시험지).
    질문 글은 결과 파일에서 읽기만(정책 내용이 담겨 관리자만)."""
    from service.eval.files import ResultFileError
    from service.eval.report import build_report
    try:
        return await build_report(axis)
    except ResultFileError as e:   # 같은 이름 결과 파일이 여러 곳 등 — 500 대신 이유(리뷰 L1)
        raise HTTPException(status_code=409, detail=str(e))



class PreviewRequest(BaseModel):
    namespace: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=1000)


class PreviewAddRequest(PreviewRequest):
    logical_id: int


@router.post("/preview")
async def post_preview(body: PreviewRequest, user: dict = Depends(get_current_user)):
    """AI가 본 근거(정정 요청 신고 카드·지식 상세) — 채팅과 같은 검색(build_chat_context)을 돌려 판정·문맥 후보를 보여 준다. LLM 0, 답 없음.
    권한 = 채팅과 같다(로그인 사용자, 다른 파트 컬렉션을 보면 감사 기록) — 채팅으로 볼 수 있는 근거를 보여 줄 뿐이라(10/28 신고 처리 담당이 관리자가 아님).
    시험지에 넣기는 관리자만(아래)."""
    from service.eval.preview import preview
    await log_cross_part_namespace_read(body.namespace, user)
    return await preview(body.namespace, body.question.strip())


@router.post("/preview/questions")
async def post_preview_question(body: PreviewAddRequest, admin: dict = Depends(get_current_admin)):
    """[시험지에 추가] — 정답 = 고른 정책. 실사용 층에 바로(다음 측정부터). 같은 질문이 이미 있으면 그 줄을 돌려준다."""
    from service.eval.preview import add_question
    try:
        return await add_question(namespace=body.namespace, question=body.question, logical_id=body.logical_id, user_id=admin["id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
