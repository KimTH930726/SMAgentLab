"""기법 켜고 끄기(11/03, 품질 › 기법 대장 흐름도) — 실행 중 바꿀 수 있는 기법 스위치. 관리자가 화면에서 바꾸면 바로 적용된다(재시작 없음).

값은 ops_system_config `technique.<key>` 줄({"on": bool, "by": 사용자, "at": 시각}) — 기동 때 읽어 메모리에 두고(백엔드 단일 프로세스 전제,
_cleanup_orphaned_ingestion_jobs와 같은 전제), 바꿀 때 메모리와 DB를 같이 고친다. 줄이 없으면 기본값(코드·설정 그대로).
여기 없는 기법은 필수(끄면 기본 동작이 깨짐)거나 코드에 고정 — 스위치를 다는 건 기본값이 지금과 같고, 끄면 그 단계만 빠지는 것만.
다른 컬렉션 제안은 예전부터 쓰던 설정 줄(chat.collection_suggest)을 그대로 쓴다(그 모듈이 그 줄을 읽음).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

PREFIX = "technique."
SUGGEST_KEY = "chat.collection_suggest"   # service/chat/collection_suggest.SUGGEST_KEY와 같은 줄


def _default_ref_chat() -> bool:
    from core.config import settings
    return bool(settings.policy_ref_chat)


# key → (기본값 함수, 한 줄 설명) — 기본값은 코드가 지금 쓰는 값과 같아야 한다(스위치를 달아도 동작이 안 바뀌게)
SWITCHES: dict[str, tuple] = {
    "linked_scope": (lambda: True, "관련 정책 연결로 이어진 컬렉션까지 지식 검색(채팅 기본)"),
    "context_budget": (lambda: True, "문맥을 한 줄 순위로 세워 예산(1만 2천 자)까지만"),
    "ref_table_chat": (_default_ref_chat, "참고표·개요를 찾아 문맥에"),
    "collection_suggest": (lambda: False, "답 뒤 다른 컬렉션에 더 맞는 지식이 있으면 버튼으로 제안"),
    "cite_prompt": (lambda: True, "답 문맥에 운영 지식이 실리면 답 문장마다 근거 번호 [#n] 프롬프트(chat_system_knowledge)"),
}

# 검증된 최적(11/03 태훈 "지금이 최적 — 바꾸면 최적이 깨진다는 경고") — 게이트로 잰 조합은 이것 하나. 다른 조합은 정확도를 모른다.
# 바꿀 땐 이 값과 근거를 같이 고친다(새 게이트 번호). 끄면 손실이 큰 기법은 경고 문장을 따로 둔다(화면 확인 창).
BASELINE: dict[str, bool] = {"linked_scope": True, "context_budget": True, "ref_table_chat": True, "collection_suggest": False, "cite_prompt": True}
BASELINE_EVIDENCE = "게이트 #71 업무지식 77.4% · #75 정책서 86.0% (11/02) · 근거 번호 프롬프트 업무지식 75.5→85.8% (11/04, 로컬 LLM)"
BASELINE_WARN: dict[str, str] = {
    "linked_scope": "끄면 정책 컬렉션에서 물은 질문이 시스템의 운영 지식을 하나도 못 찾아요(측정 0%) — 운영 지식 시스템 이동과 한 묶음이에요.",
    "context_budget": "끄면 문맥이 길어져 단정 오답이 다시 늘 수 있어요(예산 켬: 업무지식 단정 오답 26.4→20.3%).",
    "ref_table_chat": "끄면 참고표·개요로 답하던 질문이 근거를 잃어요.",
    "collection_suggest": "켜면 답 뒤 다른 컬렉션 검색이 질문마다 한 번 더 돌아요(최대 2초) — 최적 조합에선 꺼짐.",
    "cite_prompt": "끄면 운영 지식 답의 정답률이 내려가요(켬: 업무지식 75.5→85.8%, 단정 오답 19.8→14.2%).",
}

_state: dict[str, dict] = {}   # key → {"on", "by", "at"}


def off_baseline() -> list[str]:
    """지금 최적에서 벗어난 스위치 키 — 비어 있으면 최적 상태."""
    return [k for k, v in BASELINE.items() if is_on(k) != v]


def is_on(key: str) -> bool:
    """지금 켜져 있나 — 바꾼 기록이 있으면 그 값, 없으면 기본값. 스위치가 아닌 키는 늘 켜짐(필수)."""
    if key not in SWITCHES:
        return True
    st = _state.get(key)
    return bool(st["on"]) if st is not None else bool(SWITCHES[key][0]())


def changed(key: str) -> Optional[dict]:
    """마지막으로 바꾼 사람·시각(화면 표시) — 바꾼 적 없으면 None."""
    st = _state.get(key)
    return {"by": st.get("by"), "at": st.get("at")} if st else None


async def load(conn) -> None:
    """기동 때 저장된 스위치를 메모리로. 실패하면 기본값으로 계속(로그)."""
    try:
        legacy = None
        for r in await conn.fetch("SELECT key, value FROM ops_system_config WHERE key LIKE $1 OR key = $2", PREFIX + "%", SUGGEST_KEY):
            if r["key"] == SUGGEST_KEY:
                legacy = str(r["value"]).strip().lower() == "true"
                continue
            k = r["key"][len(PREFIX):]
            if k in SWITCHES:
                v = json.loads(r["value"]) if isinstance(r["value"], str) and r["value"].startswith("{") else {"on": r["value"] == "true"}
                _state[k] = {"on": bool(v.get("on")), "by": v.get("by"), "at": v.get("at")}
        if legacy is not None:   # 다른 컬렉션 제안은 그 기능이 읽는 옛 줄이 기준 — SQL로 옛 줄만 바꿔도 화면이 실제와 같게(리뷰 M2)
            st = _state.get("collection_suggest") or {"by": None, "at": None}
            _state["collection_suggest"] = {**st, "on": legacy}
    except Exception:
        logger.warning("[기법 스위치] 저장된 값 읽기 실패 — 기본값으로", exc_info=True)


async def set_switch(conn, key: str, on: bool, by: Optional[str]) -> dict:
    """켜고 끄기 — 메모리·DB 같이. 다른 컬렉션 제안은 그 기능이 읽는 줄(chat.collection_suggest)도 같이 쓴다. 없는 키면 KeyError."""
    if key not in SWITCHES:
        raise KeyError(key)
    at = datetime.now(timezone.utc).isoformat()
    row = {"on": bool(on), "by": by, "at": at}
    upsert = ("INSERT INTO ops_system_config (key, value) VALUES ($1, $2) "
              "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()")
    await conn.execute(upsert, PREFIX + key, json.dumps(row, ensure_ascii=False))
    if key == "collection_suggest":
        await conn.execute(upsert, SUGGEST_KEY, "true" if on else "false")
    _state[key] = row
    logger.info("[기법 스위치] %s → %s (by %s)", key, "켬" if on else "끔", by)
    return row
