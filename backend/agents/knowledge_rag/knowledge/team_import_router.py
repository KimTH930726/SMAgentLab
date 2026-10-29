"""팀 적재(컨플 수집기) API — 수집 대상 → 분류 미리보기 → 분류 확정·적재 → 롤백. 로직: ingestion/team_import.py,
설계: confluence-curation-layer-design §12. 배치는 같은 팀(관리자는 전부)이 보고, 만든 사람(또는 관리자)만 고친다.
분류해 넣는 곳은 담당 규칙 그대로 — 화면은 넣을 수 있는 곳만 고르게 하고(writable), 확정 때 다시 검사한다."""
import asyncio
import json
from typing import Literal, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.database import get_conn
from core.dependencies import check_namespace_ownership, edit_denied_reason, get_current_user
from agents.knowledge_rag.ingestion import team_import as ti

router = APIRouter(prefix="/api/knowledge/team-import", tags=["knowledge"])

_tasks: set[asyncio.Task] = set()   # 백그라운드 태스크 강한 참조(GC 방지 — service._background_tasks와 같은 이유)


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


class _StartBody(BaseModel):
    url: str
    team_id: Optional[int] = None       # 관리자(파트 없음)만 — 다른 사람은 자기 파트의 팀
    default_namespace_id: Optional[int] = None   # 공간 기본 위치(업무 컬렉션) — 공간 전체가 한 업무일 때
    confluence_token: Optional[str] = None


async def _my_team(conn, user: dict) -> Optional[int]:
    return await conn.fetchval("SELECT team_id FROM ops_part WHERE id = $1", user.get("part_id")) if user.get("part_id") else None


async def _user_team(conn, user: dict, team_id: Optional[int]) -> int:
    if user["role"] == "admin":
        if team_id is None:
            raise HTTPException(status_code=400, detail="관리자는 적재할 팀을 골라 주세요.")
        if not await conn.fetchval("SELECT 1 FROM ops_team WHERE id = $1", team_id):
            raise HTTPException(status_code=404, detail="팀을 찾을 수 없습니다.")
        return team_id
    t = await _my_team(conn, user)
    if t is None:
        raise HTTPException(status_code=400, detail="소속 팀이 없어 팀 적재를 할 수 없습니다.")
    return t


@router.post("", status_code=201)
async def start(body: _StartBody, user: dict = Depends(get_current_user)):
    """분류 미리보기 시작(백그라운드). 아무것도 적재하지 않는다."""
    if user["role"] == "viewer":
        raise HTTPException(status_code=403, detail="조회 전용 계정은 지식을 등록할 수 없습니다.")
    from agents.knowledge_rag.ingestion.web_crawler import _is_confluence, _parse_confluence_url
    from core.security import get_user_confluence_pat
    if not _is_confluence(body.url):
        raise HTTPException(status_code=400, detail="컨플루언스 주소가 아닙니다.")
    try:
        base_url, page_id, space_key, _title = _parse_confluence_url(body.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not page_id and not space_key:
        raise HTTPException(status_code=400, detail="페이지 주소(pageId=…) 또는 공간 주소(/display/공간키)를 넣어 주세요.")
    token = body.confluence_token or get_user_confluence_pat(user)
    if not token:
        raise HTTPException(status_code=400, detail="Confluence PAT가 필요합니다 — 프로필에서 등록해 주세요.")
    async with get_conn() as conn:
        team_id = await _user_team(conn, user, body.team_id)
        if body.default_namespace_id is not None:
            await _allowed(conn, {"team_id": team_id}, user, body.default_namespace_id, domain_only=True)
        try:   # 팀마다 진행 중 배치 하나 — 부분 유니크 인덱스가 동시 시작도 막는다
            bid = await conn.fetchval("INSERT INTO rag_import_batch (team_id, root_url, created_by_user_id, default_namespace_id) "
                                      "VALUES ($1, $2, $3, $4) RETURNING id", team_id, body.url.strip(), user["id"], body.default_namespace_id)
        except asyncpg.UniqueViolationError:
            busy = await conn.fetchval("SELECT id FROM rag_import_batch WHERE team_id = $1 AND status IN ('previewing', 'loading')", team_id)
            raise HTTPException(status_code=409, detail=f"이 팀의 적재 배치 #{busy}가 진행 중입니다 — 끝난 뒤 다시.")
    _spawn(ti.run_preview(bid, user, token, base_url))
    return {"id": bid}


@router.get("")
async def list_batches(user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        team = None if user["role"] == "admin" else (await _my_team(conn, user) or -1)
        rows = await conn.fetch(
            """SELECT b.id, b.team_id, t.name team_name, b.root_url, b.root_title, b.status, b.total_pages, b.done_pages, b.created_at::text
               FROM rag_import_batch b LEFT JOIN ops_team t ON t.id = b.team_id
               WHERE $1::int IS NULL OR b.team_id = $1 ORDER BY b.id DESC LIMIT 50""", team)
    return [dict(r) for r in rows]


async def _batch_for(conn, batch_id: int, user: dict, *, write: bool = False) -> dict:
    b = await conn.fetchrow("SELECT * FROM rag_import_batch WHERE id = $1", batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="적재 배치를 찾을 수 없습니다.")
    if user["role"] != "admin":
        if await _my_team(conn, user) != b["team_id"]:
            raise HTTPException(status_code=404, detail="적재 배치를 찾을 수 없습니다.")
        if write and (user["role"] == "viewer" or b["created_by_user_id"] != user["id"]):
            raise HTTPException(status_code=403, detail="이 배치를 만든 사람만 고칠 수 있습니다.")
    return dict(b)


async def _allowed(conn, b: dict, user: dict, namespace_id: Optional[int], *, domain_only: bool = False) -> None:
    """분류해 넣을 곳 검사 — 이 팀 범위이고 이 사람이 넣을 수 있는 곳(담당 규칙). 확정 때 다시 본다. domain_only = 업무 컬렉션만(공간 기본 위치)."""
    ctx = await ti.team_context(conn, b["team_id"])
    if namespace_id not in ti.allowed_ids(ctx, b["team_id"]) or (
            domain_only and namespace_id not in {c.id for c in ctx["cands"] if c.kind == "domain"}):
        raise HTTPException(status_code=400, detail="이 팀이 넣을 수 있는 컬렉션이 아닙니다.")
    if domain_only and namespace_id in ctx["unowned"]:   # 담당은 AI도 기본값도 정할 수 없는 정보 — 관리자여도(리뷰 10/29 L2)
        raise HTTPException(status_code=400, detail="담당 파트가 없는 업무는 기본 위치로 둘 수 없어요 — 조직·권한에서 담당을 먼저 정해 주세요.")
    reason = edit_denied_reason(ctx["owners"].get(namespace_id, set()), user)
    if reason:
        raise HTTPException(status_code=403, detail=reason)


_PAGE_COLS = ("id, page_id, title, bundle, depth, status, prev_status, reason, chunk_count, ai_namespace_id, proposed_namespace_id, "
              "assigned_by, sample_ns, "
              "confident, signals, final_namespace_id, sample_state, job_id")


# "/{batch_id}"보다 먼저 — 순서가 바뀌면 "collections"가 배치 id로 해석돼 422
@router.get("/collections")
async def list_collections(team_id: Optional[int] = None, user: dict = Depends(get_current_user)):
    """등록 화면의 "이 공간 기본 위치" 고르기용 — 배치를 만들기 전에. 관리자는 팀을 넘긴다."""
    async with get_conn() as conn:
        return await _collections(conn, await _user_team(conn, user, team_id), user)


@router.get("/{batch_id}")
async def get_batch(batch_id: int, user: dict = Depends(get_current_user)):
    """배치 + 페이지 + 고를 수 있는 컬렉션(이 팀 범위, 넣을 수 있나) + 확정을 막는 것."""
    async with get_conn() as conn:
        b = await _batch_for(conn, batch_id, user)
        pages = [dict(r) for r in await conn.fetch(f"SELECT {_PAGE_COLS} FROM rag_import_page WHERE batch_id = $1 ORDER BY id", batch_id)]
        collections = await _collections(conn, b["team_id"], user)
    blockers = ti.blockers(pages, b["sampling_required"])
    counts, wrong, _ = ti._sample_view(pages)
    for p in pages:
        s = p["signals"]
        s = json.loads(s) if isinstance(s, str) else (s or {})
        # 화면에 필요한 것만 — 이웃 컬렉션별 수, AI 이유 한 문장, 실패·제한 사유
        nb: dict[int, int] = {}
        for ns, _sim in s.get("neighbors") or []:
            nb[ns] = nb.get(ns, 0) + 1
        p["signals"] = {"neighbors": nb, "reason": s.get("reason"), "fallback": s.get("fallback"), "error": s.get("error"),
                        "restricted": s.get("restricted"), "doc_type": s.get("doc_type")}
    return {"batch": {k: (str(v) if k.endswith("_at") and v is not None else v) for k, v in b.items()},
            "pages": pages, "collections": collections, "blockers": blockers,
            "sample_targets": ti.sample_targets(counts, wrong),
            "sample_size": ti.SAMPLE_SIZE, "reasons": ti.REASONS}


async def _collections(conn, team_id: int, user: dict) -> list[dict]:
    """이 팀 배치가 넣을 수 있는 곳 — 업무(팀 범위) + 그 팀 조직, 담당 파트 이름 · 이 사람이 넣을 수 있나(writable)."""
    ctx = await ti.team_context(conn, team_id)
    part_names = {r["id"]: r["name"] for r in await conn.fetch("SELECT id, name FROM ops_part WHERE id = ANY($1::int[])",
                                                                sorted({p for s in ctx["owners"].values() for p in s}))}
    allowed = ti.allowed_ids(ctx, team_id)
    return [{"id": c.id, "label": c.label, "kind": c.kind, "unowned": c.id in ctx["unowned"],
             "writable": edit_denied_reason(ctx["owners"].get(c.id, set()), user) is None,
             "parts": [part_names.get(x) for x in sorted(ctx["owners"].get(c.id, set()) & ctx["team_parts"])]}
            for c in ctx["cands"] if c.id in allowed]


class _DefaultBody(BaseModel):
    namespace_id: Optional[int] = None   # 없음 = 기본 위치 해제(AI가 페이지마다)


@router.post("/{batch_id}/default")
async def set_default(batch_id: int, body: _DefaultBody, user: dict = Depends(get_current_user)):
    """공간 기본 위치 정하기·바꾸기(미리보기 중) — 사람이 손대지 않은 페이지를 저장된 AI 판정으로 다시 분류(LLM 안 부름)."""
    async with get_conn() as conn:
        b = await _batch_for(conn, batch_id, user, write=True)
        async with conn.transaction():
            await _lock_preview(conn, batch_id)
            if body.namespace_id is not None:
                await _allowed(conn, b, user, body.namespace_id, domain_only=True)
            return await ti.apply_default(conn, batch_id, user, body.namespace_id)


class _PagesBody(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=ti.MAX_PAGES)
    action: Literal["classify", "hold", "exclude", "include"]
    namespace_id: Optional[int] = None


# 고치기는 배치가 미리보기일 때만 — 확정(→ 적재 중)과 겹쳐도 바뀐 값이 확정 검사를 건너뛰지 않게 SQL에서 다시 본다(리뷰 10/29)
_IN_PREVIEW = "EXISTS (SELECT 1 FROM rag_import_batch WHERE id = $1 AND status = 'preview')"


# 포함할 때 "손대지 않은 공간 기본값 페이지" — 기본값으로 정해진 그대로(최종 = 정해질 때 곳)이고 표본 판정이 없다
_U = ("(p.prev_status = 'auto' AND p.assigned_by = 'space_default' AND p.sample_state IS NULL "
      "AND p.final_namespace_id IS NOT DISTINCT FROM p.sample_ns)")


async def _lock_preview(conn, batch_id: int) -> None:
    """트랜잭션 맨 앞 — 배치 행 공유 잠금. 확정(status UPDATE)은 이 트랜잭션이 끝날 때까지 기다리고, 이미 확정됐으면 409(재리뷰 10/29)."""
    if not await conn.fetchval("SELECT 1 FROM rag_import_batch WHERE id = $1 AND status = 'preview' FOR SHARE", batch_id):
        raise HTTPException(status_code=409, detail="분류 미리보기 상태에서만 고칠 수 있습니다.")


@router.post("/{batch_id}/pages")
async def update_pages(batch_id: int, body: _PagesBody, user: dict = Depends(get_current_user)):
    """분류(검토 필요·자동 분류 모두 — 분류 변경) · 보류 · 제외 · 포함으로 변경. 미리보기 상태에서만."""
    async with get_conn() as conn:
        b = await _batch_for(conn, batch_id, user, write=True)
        if b["status"] != "preview":
            raise HTTPException(status_code=409, detail="분류 미리보기 상태에서만 고칠 수 있습니다.")
        async with conn.transaction():
            await _lock_preview(conn, batch_id)
            if body.action == "classify":
                await _allowed(conn, b, user, body.namespace_id)
                r = await conn.execute(f"UPDATE rag_import_page SET final_namespace_id = $3, decided_by_user_id = $4 "
                                       f"WHERE batch_id = $1 AND id = ANY($2::bigint[]) AND status = ANY($5::text[]) AND {_IN_PREVIEW}",
                                       batch_id, body.ids, body.namespace_id, user["id"], list(ti.DECIDABLE))
            elif body.action in ("hold", "exclude"):
                # 아직 안 본 표본이 빠지면 표본에서 내리고 그 컬렉션에서 다시 뽑는다(표본을 빼서 검토를 건너뛰지 못하게)
                r = await conn.execute(f"UPDATE rag_import_page SET prev_status = status, status = $3, decided_by_user_id = $4, "
                                       f"sample_state = CASE WHEN sample_state = 'pending' THEN NULL ELSE sample_state END "
                                       f"WHERE batch_id = $1 AND id = ANY($2::bigint[]) AND status = ANY($5::text[]) AND {_IN_PREVIEW}",
                                       batch_id, body.ids, "held" if body.action == "hold" else "excluded", user["id"], list(ti.DECIDABLE))
            else:
                # 포함으로 변경 — 전 상태로. 표본에서 "틀림"이었던 자동 분류는 틀렸다고 한 곳으로 돌아가지 않게 검토 필요로(리뷰 10/29)
                r = await conn.execute(
                    # 손대지 않은 공간 기본값 페이지(U: 기본값 그대로 · 표본 판정 없음)만 지금 기본 위치로 — 보류 뒤 기본 위치를 바꿨으면 옛 곳으로
                    # 돌아가지 않게(리뷰 10/29 L3). 사람이 옮긴 페이지·표본 판정한 페이지는 고른 곳 그대로(재리뷰 N1 — 덮어쓰던 결함).
                    # 기본 위치가 없어졌으면 U는 검토 필요로, 주체·표본 묶음도 지운다(N2 — 사람이 옛 기본값과 같은 곳을 골라도 사람 결정으로)
                    f"""UPDATE rag_import_page p SET
                          status = CASE WHEN p.prev_status = 'auto' AND p.sample_state = 'wrong' THEN 'review'
                                        WHEN {_U} AND b.default_namespace_id IS NULL THEN 'review'
                                        ELSE p.prev_status END,
                          reason = CASE WHEN p.prev_status = 'auto' AND p.sample_state = 'wrong' THEN 'low_confidence'
                                        WHEN {_U} AND b.default_namespace_id IS NULL THEN 'low_confidence'
                                        ELSE p.reason END,
                          final_namespace_id = CASE WHEN p.prev_status = 'auto' AND p.sample_state = 'wrong' THEN NULL
                                                    WHEN {_U} THEN b.default_namespace_id
                                                    ELSE p.final_namespace_id END,
                          assigned_by = CASE WHEN {_U} AND b.default_namespace_id IS NULL THEN NULL ELSE p.assigned_by END,
                          sample_ns = CASE WHEN {_U} THEN b.default_namespace_id ELSE p.sample_ns END,
                          prev_status = NULL, decided_by_user_id = $3
                        FROM rag_import_batch b WHERE b.id = p.batch_id AND
                          p.batch_id = $1 AND p.id = ANY($2::bigint[]) AND p.status IN ('held', 'excluded') AND p.prev_status IS NOT NULL
                          AND {_IN_PREVIEW}""", batch_id, body.ids, user["id"])
            await ti.top_up_samples(conn, batch_id)
    return {"updated": int(r.split()[-1])}


class _SampleBody(BaseModel):
    id: int
    ok: bool
    namespace_id: Optional[int] = None   # 틀렸으면 맞는 곳(없으면 보류)


@router.post("/{batch_id}/samples")
async def review_sample(batch_id: int, body: _SampleBody, user: dict = Depends(get_current_user)):
    """표본 검토 한 건 — 맞음 / 틀림(맞는 곳으로 분류 변경, 없으면 보류). 틀림이 나오면 그 컬렉션 표본 목표가 2배가 되고 필수로 바뀐다."""
    async with get_conn() as conn:
        b = await _batch_for(conn, batch_id, user, write=True)
        if b["status"] != "preview":
            raise HTTPException(status_code=409, detail="분류 미리보기 상태에서만 검토할 수 있습니다.")
        if not body.ok and body.namespace_id is not None:
            await _allowed(conn, b, user, body.namespace_id)
        async with conn.transaction():
            await _lock_preview(conn, batch_id)
            # 아직 안 본 표본만(다시 보내도 표본이 늘지 않게, 리뷰 10/29) — 행을 잠가 동시 판정도 하나만
            p = await conn.fetchrow(f"SELECT id FROM rag_import_page WHERE batch_id = $1 AND id = $2 AND status = 'auto' "
                                    f"AND sample_state = 'pending' AND {_IN_PREVIEW} FOR UPDATE", batch_id, body.id)
            if p is None:
                raise HTTPException(status_code=409, detail="검토할 표본이 아닙니다(이미 판정했거나 표본이 아님).")
            if body.ok:
                await conn.execute("UPDATE rag_import_page SET sample_state = 'ok', decided_by_user_id = $2 WHERE id = $1", p["id"], user["id"])
                return {"added": 0}
            if body.namespace_id is not None:
                await conn.execute("UPDATE rag_import_page SET sample_state = 'wrong', final_namespace_id = $2, decided_by_user_id = $3 "
                                   "WHERE id = $1", p["id"], body.namespace_id, user["id"])
            else:
                await conn.execute("UPDATE rag_import_page SET sample_state = 'wrong', prev_status = status, status = 'held', "
                                   "decided_by_user_id = $2 WHERE id = $1", p["id"], user["id"])
            # 정정 = 기준이 이 팀에선 틀릴 수 있다는 신호 → 필수로(더 뽑을 페이지가 없어도, 리뷰 10/29) + 목표까지 더 뽑기
            await conn.execute("UPDATE rag_import_batch SET sampling_required = TRUE WHERE id = $1", batch_id)
            added = await ti.top_up_samples(conn, batch_id)
    return {"added": added}


@router.post("/{batch_id}/confirm", status_code=202)
async def confirm(batch_id: int, user: dict = Depends(get_current_user)):
    """분류 확정 · 적재(백그라운드). 먼저 적재 중으로 바꿔 고치기를 막은 뒤 검사하고(검사 뒤 바뀐 위치로 적재되지 않게, 리뷰 10/29),
    막는 것(검토 필요 미분류·필수 표본 남음)이 있거나 넣을 곳 중 담당이 아닌 곳이 있으면 미리보기로 되돌리고 거절."""
    async with get_conn() as conn:
        await _batch_for(conn, batch_id, user, write=True)
        try:   # 필수 여부는 상태를 바꾼 그 순간 값으로(먼저 읽으면 그사이 정정이 필수로 바꾼 걸 놓친다, 재리뷰 10/29)
            required = await conn.fetchval("UPDATE rag_import_batch SET status = 'loading', error = NULL WHERE id = $1 AND status = 'preview' "
                                           "RETURNING sampling_required", batch_id)
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail="이 팀의 다른 배치가 진행 중입니다 — 끝난 뒤 다시.")
        if required is None:
            raise HTTPException(status_code=409, detail="분류 미리보기 상태에서만 확정할 수 있습니다.")
        try:
            pages = [dict(r) for r in await conn.fetch(
                "SELECT status, final_namespace_id, sample_state, ai_namespace_id, sample_ns, assigned_by FROM rag_import_page "
                "WHERE batch_id = $1", batch_id)]
            block = ti.blockers(pages, required)
            if block:
                raise HTTPException(status_code=409, detail=" · ".join(block))
            names = [r["name"] for r in await conn.fetch(
                "SELECT DISTINCT n.name FROM rag_import_page p JOIN ops_namespace n ON n.id = p.final_namespace_id "
                "WHERE p.batch_id = $1 AND p.status = ANY($2::text[])", batch_id, list(ti.LOADABLE))]
            denied = []
            for name in names:
                try:
                    await check_namespace_ownership(name, user)
                except HTTPException as e:
                    denied.append(f"{name}: {e.detail}")
            if denied:
                raise HTTPException(status_code=403, detail="담당이 아닌 컬렉션이 있어 적재할 수 없습니다 — " + " / ".join(denied))
        except HTTPException:
            await conn.execute("UPDATE rag_import_batch SET status = 'preview' WHERE id = $1 AND status = 'loading'", batch_id)
            raise
    _spawn(ti.load_batch(batch_id, user))
    return {"status": "loading"}


@router.post("/{batch_id}/rollback")
async def rollback(batch_id: int, user: dict = Depends(get_current_user)):
    """이번 배치만 되돌림 — 게이트에서 나빠졌을 때. 배치가 만든 조각·자동 용어를 지우고 원문은 교체됨으로."""
    async with get_conn() as conn:
        await _batch_for(conn, batch_id, user, write=True)
        names = [r["name"] for r in await conn.fetch(
            "SELECT DISTINCT n.name FROM rag_import_page p JOIN ops_namespace n ON n.id = p.final_namespace_id "
            "WHERE p.batch_id = $1 AND p.status = 'loaded'", batch_id)]
        out = await ti.rollback_batch(conn, batch_id)
    if out is None:
        raise HTTPException(status_code=409, detail="적재가 끝난 배치만 되돌릴 수 있습니다(이미 되돌렸거나 진행 중).")
    from shared.cache import invalidate_namespace_family
    for name in names:   # 지운 조각이 든 캐시 답이 나가지 않게
        await invalidate_namespace_family(name)
    return out
