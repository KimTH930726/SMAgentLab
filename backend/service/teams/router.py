"""Teams 수집 API — 데스크톱 헬퍼 기반.

토큰 수집은 사용자 PC에서 실행되는 `scripts/teams_desktop_login.py`가 담당.
서버는 헬퍼가 POST한 토큰을 인메모리 스토어에 저장하고, 이후 chatsvc API 호출만 담당.

서버사이드 Playwright/subprocess는 사용하지 않는다 (Docker 배포 호환성).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.dependencies import get_current_user
from agents.knowledge_rag.ingestion import teams_crawler, teams_token_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/teams-collect", tags=["teams"])


# ── Pydantic 모델 ───────────────────────────────────────────────────────────

class TeamsTokenSubmit(BaseModel):
    ic3_token: str = Field(..., min_length=10)
    csa_token: str = ""
    chats: list[dict] = Field(default_factory=list)


class MessageFetchRequest(BaseModel):
    chat_id: str
    page_size: int = 50
    before: str = ""  # ISO 8601 시각 — 이 시점 이전 메시지 조회 (무한 스크롤용)


# ── 인증 ────────────────────────────────────────────────────────────────────

@router.get("/auth/status")
async def get_auth_status(user: dict = Depends(get_current_user)):
    """Teams 인증 상태 확인 (토큰 유효성까지 검증)."""
    user_id = user["id"]
    tokens = await teams_token_store.get_tokens(user_id)
    ic3_token = tokens.get("ic3_token") if tokens else None
    chats = tokens.get("chats", []) if tokens else []

    if not ic3_token:
        return {
            "authenticated": False,
            "chat_count": len(chats),
            "token_expired": None,
        }

    valid = await teams_crawler.validate_token(ic3_token)
    return {
        "authenticated": valid,
        "chat_count": len(chats),
        "token_expired": not valid,
    }


@router.post("/auth/tokens")
async def submit_tokens(body: TeamsTokenSubmit, user: dict = Depends(get_current_user)):
    """데스크톱 헬퍼가 캡처한 Teams 토큰을 제출받아 인메모리 스토어에 저장.

    사용자는 `scripts/teams_desktop_login.py`를 자기 PC에서 실행 → Playwright로
    Teams 로그인 → 잡힌 IC3/CSA 토큰 + 채팅방 목록을 이 엔드포인트로 POST한다.
    """
    user_id = user["id"]
    data: teams_token_store.TeamsTokenData = {
        "ic3_token": body.ic3_token,
        "csa_token": body.csa_token,
        "chats": body.chats,
    }

    # 수신 직후 토큰 유효성 검증 (실제 chatsvc API 호출)
    valid = await teams_crawler.validate_token(body.ic3_token)
    if not valid:
        raise HTTPException(401, "제출된 토큰이 유효하지 않습니다. 브라우저에서 Teams 로그인이 완료됐는지 확인하세요.")

    await teams_token_store.set_tokens(user_id, data)
    return {
        "status": "ok",
        "authenticated": True,
        "chat_count": len(body.chats),
    }


@router.post("/auth/logout")
async def logout(user: dict = Depends(get_current_user)):
    """Teams 로그아웃 (인메모리 토큰/캐시 삭제)."""
    await teams_token_store.clear_tokens(user["id"])
    return {"status": "logged_out"}


# ── 채팅방 ─────────────────────────────────────────────────────────────────

@router.get("/chats")
async def list_chats(user: dict = Depends(get_current_user)):
    """캡처된 채팅방 목록."""
    user_id = user["id"]
    ic3_token = await teams_token_store.get_ic3_token(user_id)
    if not ic3_token:
        raise HTTPException(401, "Teams 인증이 필요합니다")
    return {"chats": await teams_token_store.get_chats(user_id)}


# ── 메시지 조회 ────────────────────────────────────────────────────────────

@router.post("/messages")
async def fetch_messages(body: MessageFetchRequest, user: dict = Depends(get_current_user)):
    """특정 채팅방의 메시지를 조회 (syncState 기반 페이징).

    before 미지정이면 초기 로드, 있으면 캐시에서 반환하되 부족하면 syncState로 이전 페이지 추가 로드.
    """
    user_id = user["id"]
    ic3_token = await teams_token_store.get_ic3_token(user_id)
    if not ic3_token:
        raise HTTPException(401, "Teams 인증이 필요합니다")

    try:
        cache = await teams_token_store.get_message_cache(user_id, body.chat_id)

        if not body.before:
            # 초기 로드
            messages, sync_state, last_seg_start = await teams_crawler.fetch_page_from_teams(
                ic3_token, body.chat_id,
            )
            cache = {
                "messages": messages,
                "sync_state": sync_state,
                "last_segment_start": last_seg_start,
                "exhausted": len(messages) == 0,
            }
            await teams_token_store.set_message_cache(user_id, body.chat_id, cache)
            all_messages = messages
        else:
            if not cache:
                return {"messages": [], "count": 0, "has_more": False}

            all_messages = cache["messages"]
            older = [m for m in all_messages if m["time"] < body.before]

            # 캐시 부족 + API 미소진 → syncState로 이전 페이지 로드 (최대 5회)
            max_retries = 5
            while len(older) < body.page_size + 1 and not cache["exhausted"] and max_retries > 0:
                max_retries -= 1
                new_msgs, sync_state, last_seg_start = await teams_crawler.fetch_page_from_teams(
                    ic3_token, body.chat_id,
                    sync_state=cache["sync_state"],
                    last_segment_start=cache["last_segment_start"],
                )
                if not new_msgs:
                    cache["exhausted"] = True
                    break

                existing_ids = {m["id"] for m in all_messages}
                unique_new = [m for m in new_msgs if m["id"] not in existing_ids]
                if not unique_new:
                    cache["exhausted"] = True
                    break

                all_messages = sorted(all_messages + unique_new, key=lambda m: m.get("time", ""))
                cache["messages"] = all_messages
                cache["sync_state"] = sync_state
                cache["last_segment_start"] = last_seg_start
                if last_seg_start <= 1:
                    cache["exhausted"] = True
                await teams_token_store.set_message_cache(user_id, body.chat_id, cache)
                older = [m for m in all_messages if m["time"] < body.before]
    except PermissionError:
        raise HTTPException(401, "Teams 세션이 만료되었습니다. 재로그인이 필요합니다.")

    # before 커서 적용
    filtered = (
        [m for m in all_messages if m["time"] < body.before]
        if body.before else all_messages
    )

    # page_size만큼 최근 것을 반환
    has_more = len(filtered) > body.page_size
    result_messages = filtered[-body.page_size:] if has_more else filtered

    # 캐시 부족이지만 API 미소진이면 has_more 유지
    if not has_more and filtered:
        c = await teams_token_store.get_message_cache(user_id, body.chat_id) or {}
        if not c.get("exhausted", True):
            has_more = True

    return {
        "messages": result_messages,
        "count": len(result_messages),
        "has_more": has_more,
    }
