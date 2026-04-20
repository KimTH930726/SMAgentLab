"""Teams chatsvc API 크롤러 — 메시지 조회, HTML→텍스트, 회신 파싱.

원본: novalink/backend/src/api/routes/teams_collect.py, connectors/teams.py
대상 환경: ops-navigator. 토큰은 인메모리 스토어에서 읽고, syncState 페이징으로
이전 구간까지 조회한다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from html import unescape
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse

import httpx

from agents.knowledge_rag.ingestion import teams_token_store

logger = logging.getLogger(__name__)

TEAMS_REGION = os.environ.get("TEAMS_REGION", "kr")
TEAMS_API_PAGE_SIZE = 200
_THIRTY_DAYS_MS = 30 * 24 * 60 * 60 * 1000
_DATE_FMT = "%Y-%m-%d %H:%M"

# 미리 컴파일한 정규식 — extract_reply_info / remove_blockquote에서 재사용
_RE_BLOCKQUOTE = re.compile(r"<blockquote[^>]*>(.*?)</blockquote>", re.DOTALL)
_RE_STRONG = re.compile(r"<strong[^>]*>([^<]+)</strong>")
_RE_STRONG_FULL = re.compile(r"<strong[^>]*>.*?</strong>", re.DOTALL)
_RE_SPAN_FULL = re.compile(r"<span[^>]*>.*?</span>", re.DOTALL)
_RE_SPACES = re.compile(r" {4,}")


def _base_url(region: str = TEAMS_REGION) -> str:
    return f"https://teams.microsoft.com/api/chatsvc/{region}/v1"


# ── 토큰 유효성 검증 ────────────────────────────────────────────────────────

async def validate_token(ic3_token: str) -> bool:
    """Teams API에 가벼운 요청을 보내 토큰 유효성을 검증 (60초 캐시)."""
    cached = teams_token_store.get_token_valid_cache(ic3_token)
    if cached and (time.time() - cached[1]) < teams_token_store.TOKEN_VALIDATION_TTL:
        return cached[0]

    url = f"{_base_url()}/users/ME/conversations"
    headers = {
        "Authorization": f"Bearer {ic3_token}",
        "behavioroverride": "redirectAs404",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers, params={"pageSize": 1})
        valid = resp.status_code == 200
    except Exception:
        valid = False

    teams_token_store.set_token_valid_cache(ic3_token, valid)
    return valid


# ── 메시지 조회 ────────────────────────────────────────────────────────────

async def fetch_page_from_teams(
    ic3_token: str,
    chat_id: str,
    sync_state: str = "",
    last_segment_start: int = 0,
) -> tuple[list[dict], str, int]:
    """Teams API에서 한 페이지의 메시지를 가져온다.

    Returns:
        (메시지 목록 (시간순), 다음 syncState, lastCompleteSegmentStartTime)
    """
    url = f"{_base_url()}/users/ME/conversations/{quote(chat_id, safe='')}/messages"
    headers = {
        "Authorization": f"Bearer {ic3_token}",
        "behavioroverride": "redirectAs404",
    }

    params: dict = {
        "view": "msnp24Equivalent|supportsMessageProperties",
        "pageSize": TEAMS_API_PAGE_SIZE,
        "startTime": 1,
    }
    if sync_state and last_segment_start > 1:
        params["startTime"] = max(1, last_segment_start - _THIRTY_DAYS_MS)
        params["syncState"] = sync_state

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers, params=params)
        if resp.status_code in (401, 403):
            logger.warning("Teams 토큰 만료 또는 권한 없음: status=%d", resp.status_code)
            raise PermissionError(f"teams_auth_expired:{resp.status_code}")
        if resp.status_code != 200:
            logger.error("Teams API 에러: status=%d, body=%s", resp.status_code, resp.text[:500])
            return [], "", 0
    except PermissionError:
        raise
    except Exception as e:
        logger.error("Teams API 호출 실패: %s", e)
        return [], "", 0

    data = resp.json()
    metadata = data.get("_metadata", {})

    # syncState 추출
    next_sync_state = ""
    raw_sync = metadata.get("syncState", "")
    if raw_sync and "syncState=" in raw_sync:
        parsed = urlparse(raw_sync)
        qs = parse_qs(parsed.query)
        next_sync_state = qs.get("syncState", [""])[0]
    elif raw_sync:
        next_sync_state = raw_sync

    next_last_seg_start = metadata.get("lastCompleteSegmentStartTime", 0)

    messages = []
    for msg in data.get("messages", []):
        msg_type = msg.get("messagetype", "")
        if msg_type not in ("RichText/Html", "Text"):
            continue

        compose_time = msg.get("composetime", "")
        date_str = ""
        if compose_time:
            try:
                dt = datetime.fromisoformat(compose_time.replace("Z", "+00:00"))
                date_str = dt.strftime(_DATE_FMT)
            except ValueError:
                date_str = compose_time[:16]

        raw_content = msg.get("content", "")
        reply_to = extract_reply_info(raw_content, msg.get("properties", {}))
        content = html_to_text(raw_content)
        if reply_to:
            content = remove_blockquote(raw_content)

        parsed_msg: dict = {
            "id": msg["id"],
            "from": msg.get("imdisplayname", "Unknown"),
            "content": content,
            "time": compose_time,
            "date": date_str,
        }
        if reply_to:
            parsed_msg["reply_to"] = reply_to
        messages.append(parsed_msg)

    messages.sort(key=lambda m: m.get("time", ""))
    return messages, next_sync_state, next_last_seg_start


# ── HTML/회신 파싱 ─────────────────────────────────────────────────────────

def html_to_text(html: str) -> str:
    """Teams 메시지 HTML → plain text.

    Teams는 <p> 태그를 단락이 아닌 줄 단위로 쓰므로 <p>는 변환하지 않는다.
    <br>, <li>, <h>, <tr> 등 명확한 블록 태그만 개행 처리.
    """
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</(li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<td[^>]*>", " | ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_reply_info(raw_content: str, properties: dict) -> Optional[dict]:
    """HTML + properties에서 회신/전달 정보 추출."""
    if not isinstance(properties, dict):
        return None

    # 전달 메시지
    if properties.get("forwardTemplateId"):
        preview = ""
        bq_match = _RE_BLOCKQUOTE.search(raw_content)
        if bq_match:
            preview = html_to_text(bq_match.group(1))[:300]
        return {"type": "forward", "from": "", "preview": preview}

    # 회신 메시지
    qtd = properties.get("qtdMsgs")
    if not qtd:
        return None
    if isinstance(qtd, str):
        try:
            qtd = json.loads(qtd)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(qtd, list) or not qtd:
        return None

    # blockquote를 한 번만 검색해 from/preview 모두 추출
    bq_match = _RE_BLOCKQUOTE.search(raw_content)
    reply_from = ""
    reply_preview = ""
    if bq_match:
        strong_match = _RE_STRONG.search(bq_match.group(0))
        if strong_match:
            reply_from = unescape(strong_match.group(1)).strip()
        bq_inner = _RE_STRONG_FULL.sub("", bq_match.group(1))
        bq_inner = _RE_SPAN_FULL.sub("", bq_inner)
        reply_preview = _RE_SPACES.sub("\n", html_to_text(bq_inner))[:300]

    if not reply_from and not reply_preview:
        return None
    return {"type": "reply", "from": reply_from, "preview": reply_preview}


def remove_blockquote(raw_content: str) -> str:
    return html_to_text(_RE_BLOCKQUOTE.sub("", raw_content))


# ── 문서 포맷 변환 ─────────────────────────────────────────────────────────

def thread_to_content(messages: list[dict]) -> str:
    """선택된 메시지 리스트를 대화 로그 텍스트로 변환.

    Novalink의 /docs/save 로직을 그대로 이식.
    """
    lines: list[str] = []
    for msg in messages:
        time_str = msg.get("date", msg.get("time", ""))
        sender = msg.get("from", "Unknown")
        text = msg.get("content", "")
        reply = msg.get("reply_to")

        if reply:
            rtype = reply.get("type", "reply")
            reply_preview = reply.get("preview", "")
            quoted = "\n".join(f"> {line}" for line in reply_preview.split("\n"))
            if rtype == "forward":
                lines.append(f"[{time_str}] {sender}: (전달된 메시지)\n{quoted}\n{text}")
            else:
                reply_from = reply.get("from", "")
                lines.append(
                    f"[{time_str}] {sender}: ({reply_from}의 메시지에 대한 회신)\n{quoted}\n{text}"
                )
        else:
            lines.append(f"[{time_str}] {sender}: {text}")
    return "\n".join(lines)
