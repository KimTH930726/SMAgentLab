"""Teams 토큰 인메모리 스토어 — 사용자별 휘발성 토큰 보관.

Teams 토큰(IC3/CSA)은 만료되는 성질이 있어 DB에 영속화하지 않는다.
프로세스 메모리에만 보관하며, 재시작/만료 시 사용자가 재로그인하도록 유도.

토큰 수집은 데스크톱 헬퍼(`scripts/teams_desktop_login.py`)가 담당하고,
서버는 헬퍼가 POST한 토큰을 받아 이 스토어에 저장만 한다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, TypedDict

logger = logging.getLogger(__name__)


class TeamsTokenData(TypedDict, total=False):
    ic3_token: str
    csa_token: str
    chats: list[dict]
    captured_at: float  # epoch seconds


# user_id -> token data
_tokens: dict[int, TeamsTokenData] = {}
# chat_id별 메시지 캐시는 사용자 범위로 격리: (user_id, chat_id) -> cache
_message_cache: dict[tuple[int, str], dict] = {}
# 토큰 유효성 검증 캐시: ic3_token -> (valid, checked_at)
_token_valid_cache: dict[str, tuple[bool, float]] = {}

_lock = asyncio.Lock()

TOKEN_VALIDATION_TTL = 60  # seconds


async def get_tokens(user_id: int) -> TeamsTokenData:
    async with _lock:
        return dict(_tokens.get(user_id, {}))  # type: ignore[return-value]


async def set_tokens(user_id: int, data: TeamsTokenData) -> None:
    async with _lock:
        data["captured_at"] = time.time()
        _tokens[user_id] = data
        logger.info("Teams 토큰 저장 (user_id=%s, chats=%d)", user_id, len(data.get("chats", [])))


async def clear_tokens(user_id: int) -> None:
    async with _lock:
        _tokens.pop(user_id, None)
        # 관련 캐시도 함께 정리
        keys_to_remove = [k for k in _message_cache if k[0] == user_id]
        for k in keys_to_remove:
            _message_cache.pop(k, None)
        logger.info("Teams 토큰/캐시 삭제 (user_id=%s)", user_id)


async def get_ic3_token(user_id: int) -> Optional[str]:
    async with _lock:
        data = _tokens.get(user_id)
        return data.get("ic3_token") if data else None


async def get_chats(user_id: int) -> list[dict]:
    async with _lock:
        data = _tokens.get(user_id)
        return list(data.get("chats", [])) if data else []


async def get_message_cache(user_id: int, chat_id: str) -> Optional[dict]:
    async with _lock:
        cache = _message_cache.get((user_id, chat_id))
        return dict(cache) if cache else None


async def set_message_cache(user_id: int, chat_id: str, cache: dict) -> None:
    async with _lock:
        _message_cache[(user_id, chat_id)] = cache


def get_token_valid_cache(ic3_token: str) -> Optional[tuple[bool, float]]:
    return _token_valid_cache.get(ic3_token)


def set_token_valid_cache(ic3_token: str, valid: bool) -> None:
    _token_valid_cache[ic3_token] = (valid, time.time())
