"""사용자 피드백(10/30) — 답 내용 오류가 아닌 **시스템 오류 신고 · 기능 제안**을 받는 채널 + 관리자 분류(트리아지).

업계: Jira Service Management(Report a bug / Suggest a feature 요청 유형 → 분류 → 백로그) · Canny(제안 → 선정 → 완료, 보낸 사람에게 상태).
답이 틀렸다는 건 여기로 받지 않는다 — 채팅 [정정 요청](/api/feedback, 정정 원장)이 따로 있어 같은 일이 두 곳으로 갈라지지 않게.
자동 감지(질의 기록 system_error: 게이트웨이 거부·LLM 연결 실패·처리 오류)는 건수만 날짜·종류별로 — 질문 원문은 내보내지 않는다.
11/02: 첨부 이미지(화면 캡처 붙여넣기 — 관리자가 무슨 오류인지 보고 판단) · 오류 창에서 바로 [오류 신고](오류 문맥 자동) · 진행 중 상태
(선정 → 진행 중 → 완료 — 폐쇄망이라 개발은 로컬 CLI에서, 진행 중일 때 화면이 개발 요청서를 만든다).
안 하는 것(다시 볼 조건): 화면 자동 캡처(붙여넣기로 충분, 민감 화면이 그대로 찍힘) · 투표·공개 로드맵(사용자 수백 명 넘어 의견이 많아지면) · Jira 연동(팀이 개발 작업을 Jira로 관리하기로 하면) ·
담당자 배정·SLA(관리자 둘 이상) · 비슷한 의견 AI 묶기(한 주 수십 건 넘으면) · 개선 후보 목록과 합치기(오픈 뒤 실제 사례 보고).
"""
from __future__ import annotations

import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core.database import get_conn
from core.dependencies import get_current_admin, get_current_user

router = APIRouter(prefix="/api/user-feedback", tags=["user-feedback"])

KINDS = ("bug", "idea")
STATUSES = ("new", "planned", "in_progress", "done", "duplicate", "declined")
MAX_FILES = 3
MAX_FILE_BYTES = 5 * 1024 * 1024


def image_mime(head: bytes) -> Optional[str]:
    """파일 앞 바이트로 이미지 종류(PNG·JPEG·GIF·WEBP) — 확장자·브라우저가 준 종류는 믿지 않는다(이미지가 아닌 파일을 이미지로 내려주지 않게)."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def clean_context(raw: Optional[str]) -> Optional[dict]:
    """오류 창이 붙인 문맥 — 정한 칸만, 길이 제한(임의 JSON을 그대로 저장하지 않음). 잘못된 값이면 버린다(신고 자체는 받음)."""
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    out: dict = {}
    if isinstance(d.get("message"), str) and d["message"].strip():
        out["message"] = d["message"].strip()[:500]
    if isinstance(d.get("status"), int) and not isinstance(d["status"], bool) and 0 <= d["status"] < 1000:
        out["status"] = d["status"]
    if isinstance(d.get("path"), str) and d["path"].strip():
        out["path"] = d["path"].strip()[:200]
    if d.get("source") in ("dialog", "chat"):
        out["source"] = d["source"]
    return out or None


class TriageIn(BaseModel):
    status: Literal["new", "planned", "in_progress", "done", "duplicate", "declined"]
    note: str = Field(default="", max_length=500)
    duplicate_of: Optional[int] = None


@router.post("", status_code=201)
async def submit(request: Request, kind: Optional[Literal["bug", "idea"]] = Form(default=None), body: str = Form(default="", max_length=2000),
                 page: Optional[str] = Form(default=None, max_length=300), conversation_id: Optional[int] = Form(default=None),
                 context: Optional[str] = Form(default=None), files: list[UploadFile] = File(default=[]),
                 user: dict = Depends(get_current_user)):
    """[피드백 보내기] — 로그인한 누구나(조회 전용 포함). multipart: 글 + 이미지 최대 3장(장당 5MB, PNG·JPEG·GIF·WEBP).
    대화 번호는 본인 대화일 때만 남긴다(남의 대화가 붙지 않게). 오류 창에서 온 신고(오류 문구 있음)는 글이 비어도 받는다."""
    if kind is None:   # 옛 화면(JSON 전송)이 배포 직후 잠깐 남아 있을 때 — 영어 "Field required" 대신(리뷰 L7)
        json_body = "json" in (request.headers.get("content-type") or "")
        raise HTTPException(status_code=400, detail="화면을 새로고침한 뒤 다시 보내 주세요." if json_body else "유형(오류 신고·기능 제안)을 골라 주세요.")
    text = body.strip()
    # 오류 문맥은 화면이 500자로 잘라 보낸다 — 터무니없이 큰 값(2만 자 넘음)은 문맥만 버리고 신고는 받는다(clean_context가 칸별 길이도 자름, 리뷰 M2)
    ctx = clean_context(context if context and len(context) <= 20000 else None)
    if len(text) < 2 and not (ctx and ctx.get("message")):   # 길이 검사는 공백을 뺀 글로(리뷰 L3)
        raise HTTPException(status_code=400, detail="내용을 두 글자 이상 적어 주세요.")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"이미지는 {MAX_FILES}장까지 보낼 수 있어요.")
    images: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail=f"이미지 한 장은 5MB까지예요({f.filename or '이미지'}).")
        mime = image_mime(data[:16])
        if mime is None:
            raise HTTPException(status_code=400, detail=f"이미지 파일(PNG·JPEG·GIF·WEBP)만 보낼 수 있어요({f.filename or '파일'}).")
        images.append((mime, data))
    async with get_conn() as conn:
        async with conn.transaction():   # 글과 이미지를 한 번에 — 이미지만 빠진 신고가 남지 않게
            conv = None
            if conversation_id is not None:
                conv = await conn.fetchval("SELECT id FROM ops_conversation WHERE id = $1 AND user_id = $2", conversation_id, user["id"])
            fid = await conn.fetchval(
                """INSERT INTO ops_user_feedback (kind, body, page, conversation_id, reporter_user_id, context)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb) RETURNING id""",
                kind, text, page, conv, user["id"], json.dumps(ctx, ensure_ascii=False) if ctx else None)
            for mime, data in images:
                await conn.execute("INSERT INTO ops_user_feedback_file (feedback_id, mime, size, data) VALUES ($1, $2, $3, $4)",
                                   fid, mime, len(data), data)
    return {"id": fid, "status": "new", "files": len(images)}


_FILES_SQL = "COALESCE((SELECT json_agg(x.id ORDER BY x.id) FROM ops_user_feedback_file x WHERE x.feedback_id = f.id), '[]'::json) AS files"


def _row(r) -> dict:
    d = dict(r)
    for k in ("files", "context"):   # asyncpg는 json·jsonb를 문자열로 준다
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return d


@router.get("/{fid}/files/{file_id}")
async def get_file(fid: int, file_id: int, user: dict = Depends(get_current_user)):
    """첨부 이미지 — 보낸 사람과 관리자만(남의 것은 있는지도 알리지 않게 404). 종류는 저장할 때 앞 바이트로 정한 것(nosniff)."""
    async with get_conn() as conn:
        r = await conn.fetchrow(
            """SELECT x.mime, x.data, f.reporter_user_id FROM ops_user_feedback_file x JOIN ops_user_feedback f ON f.id = x.feedback_id
               WHERE x.id = $1 AND x.feedback_id = $2""", file_id, fid)
    if r is None or (user.get("role") != "admin" and r["reporter_user_id"] != user["id"]):
        raise HTTPException(status_code=404, detail="이미지가 없습니다.")
    return Response(content=bytes(r["data"]), media_type=r["mime"],
                    headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})


@router.get("/mine")
async def mine(user: dict = Depends(get_current_user)):
    """내가 보낸 피드백과 처리 상태(최근 50) — 보낸 사람이 결과를 본다(Canny). 볼 때 '확인함'으로."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""SELECT f.id, f.kind, f.body, f.status, f.admin_note, f.created_at, f.decided_at, {_FILES_SQL},
                      (f.decided_at IS NOT NULL AND (f.reporter_seen_at IS NULL OR f.reporter_seen_at < f.decided_at)) AS unseen
               FROM ops_user_feedback f WHERE f.reporter_user_id = $1 ORDER BY f.id DESC LIMIT 50""", user["id"])
        await conn.execute("UPDATE ops_user_feedback SET reporter_seen_at = NOW() WHERE reporter_user_id = $1 AND decided_at IS NOT NULL",
                           user["id"])
    return [_row(r) for r in rows]


@router.get("/mine/unseen")
async def mine_unseen(user: dict = Depends(get_current_user)):
    """처리 결과가 바뀌었는데 아직 안 본 내 피드백 수 — 버튼 점 표시."""
    async with get_conn() as conn:
        n = await conn.fetchval("SELECT count(*) FROM ops_user_feedback WHERE reporter_user_id = $1 AND decided_at IS NOT NULL "
                                "AND (reporter_seen_at IS NULL OR reporter_seen_at < decided_at)", user["id"])
    return {"count": n}


@router.get("")
async def list_all(status: Optional[str] = Query(default=None, pattern="^(new|planned|in_progress|done|duplicate|declined)$"),
                   admin: dict = Depends(get_current_admin)):
    """관리자 목록 — 상태별 건수 + 목록(최근 200). 보낸 사람 이름·파트는 처리에 필요해 보인다(관리자만)."""
    async with get_conn() as conn:
        counts = {r["status"]: r["n"] for r in await conn.fetch("SELECT status, count(*) n FROM ops_user_feedback GROUP BY status")}
        rows = await conn.fetch(
            f"""SELECT f.id, f.kind, f.body, f.page, f.conversation_id, f.status, f.admin_note, f.duplicate_of, f.created_at, f.decided_at,
                      f.context, {_FILES_SQL}, u.username reporter, p.name reporter_part, d.username decided_by
               FROM ops_user_feedback f LEFT JOIN ops_user u ON u.id = f.reporter_user_id LEFT JOIN ops_part p ON p.id = u.part_id
               LEFT JOIN ops_user d ON d.id = f.decided_by_user_id
               WHERE $1::text IS NULL OR f.status = $1 ORDER BY f.id DESC LIMIT 200""", status)
    return {"counts": {s: counts.get(s, 0) for s in STATUSES}, "items": [_row(r) for r in rows]}


@router.patch("/{fid}")
async def triage(fid: int, body: TriageIn, admin: dict = Depends(get_current_admin)):
    """분류 — 선정·진행 중·완료·중복(원래 건 번호)·기각(사유 필수). 신규로 되돌리면 결정 흔적을 비운다(보낸 사람에게도 다시 '접수')."""
    if body.status == "declined" and not body.note.strip():
        raise HTTPException(status_code=400, detail="기각 사유를 적어 주세요 — 보낸 사람이 봅니다.")
    if body.status == "duplicate" and (body.duplicate_of is None or body.duplicate_of == fid):
        raise HTTPException(status_code=400, detail="어느 피드백과 같은지 번호를 골라 주세요.")
    async with get_conn() as conn:
        if body.status == "duplicate" and not await conn.fetchval("SELECT 1 FROM ops_user_feedback WHERE id = $1", body.duplicate_of):
            raise HTTPException(status_code=400, detail="원래 피드백 번호가 없습니다.")
        undo = body.status == "new"
        r = await conn.fetchrow(
            """UPDATE ops_user_feedback SET status = $2, admin_note = NULLIF($3, ''), duplicate_of = $4,
                      decided_by_user_id = $5, decided_at = CASE WHEN $6 THEN NULL ELSE NOW() END
               WHERE id = $1 RETURNING id, status""",
            fid, body.status, "" if undo else body.note.strip(), body.duplicate_of if body.status == "duplicate" else None,
            None if undo else admin["id"], undo)
    if r is None:
        raise HTTPException(status_code=404, detail="피드백이 없습니다.")
    return dict(r)


def failure_kind(answer: Optional[str]) -> str:
    """장애 종류 — 질의 기록 system_error의 답 문구로(원문은 내보내지 않는다)."""
    from service.chat.helpers import LLM_EMPTY_MSG, LLM_UNAVAILABLE_MSG, is_gateway_refusal
    if answer and is_gateway_refusal(answer):
        return "게이트웨이 거부"
    if answer in (LLM_UNAVAILABLE_MSG, LLM_EMPTY_MSG) or not answer:
        return "LLM 연결 실패·빈 응답"
    return "처리 오류"


@router.get("/auto-signals")
async def auto_signals(days: int = Query(default=14, ge=1, le=90), admin: dict = Depends(get_current_admin)):
    """자동 감지 — 최근 n일 채팅 장애(질의 기록 system_error)를 날짜·종류별 건수로. 질문·답 원문 없음."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            """SELECT (created_at AT TIME ZONE 'Asia/Seoul')::date::text d, answer FROM ops_query_log
               WHERE status = 'system_error' AND created_at > NOW() - make_interval(days => $1)""", days)
        total_q = await conn.fetchval("SELECT count(*) FROM ops_query_log WHERE created_at > NOW() - make_interval(days => $1)", days)
    by: dict[tuple[str, str], int] = {}
    for r in rows:
        k = (r["d"], failure_kind(r["answer"]))
        by[k] = by.get(k, 0) + 1
    kinds: dict[str, int] = {}
    for (_, k), n in by.items():
        kinds[k] = kinds.get(k, 0) + n
    return {"days": days, "total_questions": total_q, "failures": len(rows), "by_kind": kinds,
            "by_day": [{"date": d, "kind": k, "n": n} for (d, k), n in sorted(by.items(), reverse=True)]}
