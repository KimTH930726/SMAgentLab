"""채팅 "다른 컬렉션에서 찾아보기" 제안 (C2, collection-design §18 — 사용자 10/23 "딜리버스에서 물었는데 제휴서비스 지식이면 AI가 판단해 제안").

기본 검색 범위는 그대로(합치면 섞임 0→6.6%, §1). 답을 낸 뒤 다른 업무 컬렉션에서 같은 검색을 해 더 맞는 지식이 보이면
**버튼으로만 제안**한다 — 자동으로 섞지 않는다. 누르면 같은 질문을 그 컬렉션에서 한 번 더 답한다(보는 컬렉션은 그대로).
제안·누름은 AI 판단 원장(kind `collection_suggest`)에 — 제안했는데 안 눌린 비율·누른 답의 👍/신고로 하한을 다시 본다.

규칙(LLM 0): 다른 곳 최고 관련도 ≥ 하한 이고 (여기 채택 0 또는 다른 곳 최고 ≥ 여기 최고). 하한 0.48(10/26):
- 합성(scripts/eval_collection_suggest.py): 거짓 제안 0/106 · 다른 곳에서 물을 때 맞게 89/106(84%) · 답 없는 질문 잡음 1/120
- 실사용(scripts/eval_collection_suggest_real.py, 질의 기록 174 · 로컬 LLM 판정): 0.45면 제안 11 중 맞음 8 · 틀림 2 · 애매 1 —
  틀린 2건이 가장 낮은 점수(0.46·0.451)라 0.48로 올림 → 맞음 8 · 틀림 0(애매 1도 빠짐). 표본이 작아 켠 뒤 원장으로 다시 본다.
**기본 꺼짐**(설정 `chat.collection_suggest`) — 사람이 켠다(§18 켜는 조건).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from core.database import get_conn

logger = logging.getLogger(__name__)

SUGGEST_KEY = "chat.collection_suggest"
KIND = "collection_suggest"
FLOOR = 0.48
MARGIN = 0.0


def should_suggest(here_best: float, here_adopted: int, other_best: float, floor: float = FLOOR, margin: float = MARGIN) -> bool:
    """측정한 규칙 그대로(eval_collection_suggest.py와 같은 식)."""
    return other_best >= floor and (here_adopted == 0 or other_best - here_best >= margin)


_FLAG_TTL = 30.0          # 설정 확인은 30초에 한 번 — 꺼져 있어도 답마다 DB를 한 번 더 부르던 것(리뷰 L4)
_flag_cache: tuple[float, bool] = (0.0, False)
CONCURRENCY = 2           # 다른 컬렉션 검색 동시 수(프로세스 전체) — 컬렉션마다 커넥션 2개(용어·검색)라 한꺼번에 돌리면 풀(최대 10)을 채웠다(리뷰 M3)
_sem = asyncio.Semaphore(CONCURRENCY)   # 답 여러 건이 동시에 끝나도 합쳐 2개 — 호출마다 만들면 답 수만큼 늘었다(재리뷰)


async def enabled(conn) -> bool:
    global _flag_cache
    now = time.monotonic()
    if now - _flag_cache[0] < _FLAG_TTL:
        return _flag_cache[1]
    flag = await conn.fetchval("SELECT value FROM ops_system_config WHERE key = $1", SUGGEST_KEY)
    _flag_cache = (now, str(flag or "").strip().lower() == "true")
    return _flag_cache[1]


async def find(namespace: str, question: str, query_vec: list[float], here_results: Optional[list], *,
               w_vector: float, w_keyword: float, top_k: int, force: bool = False) -> Optional[dict]:
    """제안할 컬렉션 하나(가장 높은 곳) 또는 None. 꺼져 있거나(force = 측정용), 물은 곳이 업무 컬렉션이 아니면 None.
    here_results = 이번 답의 지식 검색 결과. None이면(캐시 답 — 검색 결과 객체가 없음) 물은 곳도 같은 검색으로 다시 잰다."""
    from agents.knowledge_rag.knowledge import glossary_terms, retrieval
    from service.policy import scope as policy_scope

    if not force and _flag_cache[0] and time.monotonic() - _flag_cache[0] < _FLAG_TTL and not _flag_cache[1]:
        return None   # 꺼짐(캐시) — 커넥션도 안 잡는다
    async with get_conn() as conn:
        if not force and not await enabled(conn):
            return None
        me = await conn.fetchrow("SELECT kind FROM ops_namespace WHERE name = $1", namespace)
        if not me or me["kind"] != "domain":
            return None
        # 검색되는 지식이 있는 다른 업무 컬렉션(조직·테스트 제외) — 측정과 같은 조건
        others = [r["name"] for r in await conn.fetch(
            """SELECT n.name FROM ops_namespace n WHERE n.kind = 'domain' AND n.name <> $1 AND n.name NOT LIKE '%test%'
               AND EXISTS (SELECT 1 FROM rag_knowledge k WHERE k.namespace_id = n.id AND (k.status IS NULL OR k.status = 'active'))
               ORDER BY n.name""", namespace)]
        if not others:
            return None
        term_scope = {c: await policy_scope.policy_namespace_ids(conn, c) for c in [*others, namespace]}
    th = retrieval.get_thresholds()

    async def search(ns: str) -> list:
        _, _, enriched = await glossary_terms.resolve_query_terms(ns, question, query_vec, extra_ns_ids=term_scope[ns])
        return await retrieval.search_knowledge(ns, query_vec, enriched, w_vector, w_keyword, top_k, keyword_rescue=retrieval.KEYWORD_RESCUE)

    async def best(ns: str) -> float:
        async with _sem:
            return max((retrieval.relevance_score(r) for r in await search(ns)), default=0.0)

    if here_results is None:
        here_results = await search(namespace)
    here_best = max((retrieval.relevance_score(r) for r in here_results), default=0.0)
    here_adopted = sum(retrieval.is_adopted(r, th) for r in here_results)
    scores = dict(zip(others, await asyncio.gather(*(best(c) for c in others))))
    ns, score = max(scores.items(), key=lambda kv: kv[1])
    if not should_suggest(here_best, here_adopted, score):
        return None
    return {"namespace": ns, "score": round(score, 3), "here": round(here_best, 3), "here_adopted": here_adopted}


async def record(*, conversation_id: int, message_id: int, suggestion: dict, user_id: Optional[int]) -> Optional[int]:
    """제안 한 줄(누르면 picked로 닫힘). 실패해도 대화는 계속(로그만)."""
    try:
        from service.decision import ledger
        async with get_conn() as conn:
            return await ledger.record(conn, KIND, ai_value={"namespace": suggestion["namespace"]}, confident=False,
                                       signals={"conversation_id": str(conversation_id), "message_id": message_id,
                                                "score": suggestion["score"], "here": suggestion["here"]},
                                       user_id=user_id, prompt_version=f"suggest-c2-{FLOOR}", target_type="message", target_ids=[message_id])
    except Exception:   # noqa: BLE001
        logger.warning("컬렉션 제안 원장 기록 실패(대화는 계속)", exc_info=True)
        return None


async def picked(*, message_id: int, user_id: Optional[int]) -> bool:
    """제안 버튼을 눌러 다시 물음 — 그 메시지의 열린 제안 줄을 "누름"으로 닫는다(자기 제안만, ledger.finalize 규칙)."""
    try:
        from service.decision import ledger
        async with get_conn() as conn:
            row = await conn.fetchrow(
                """SELECT id FROM ops_ai_decision WHERE kind = $1 AND final_value IS NULL AND (signals->>'message_id')::bigint = $2
                   ORDER BY id DESC LIMIT 1""", KIND, message_id)
            if not row:
                return False
            return await ledger.finalize(conn, row["id"], kind=KIND, final_value={"picked": True}, changed=False, user_id=user_id)
    except Exception:   # noqa: BLE001
        logger.warning("컬렉션 제안 누름 기록 실패(대화는 계속)", exc_info=True)
        return False
