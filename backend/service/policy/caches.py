"""정책이 바뀐 뒤 비울 캐시 — 한 곳에서(2026-10-21, v0 재구축 1단계).

막힌 일: 가져오기·정정 승인 뒤 캐시를 "고친 업무 영역" 것만 비웠다. 그런데 채팅은 정책서를 여러 영역에 걸쳐 찾는다(scope.py) —
다른 영역에서 한 질문의 답변 캐시엔 옛 정책 답이 TTL 동안 남았다. 그래서 정책 색인(키워드·분류, 프로세스 메모리)과
답변 캐시(모든 업무 영역)를 함께 비운다. 정책 변경은 드물어(사람 승인 단위) 전부 비워도 부담이 작다.
best-effort — 실패해도 승인 자체는 이미 반영됐으므로 요청을 죽이지 않고 로그만 남긴다.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def invalidate_policy_caches() -> None:
    try:   # 정책 색인 — 시맨틱 캐시 비우기와 실패가 엮이지 않게 따로
        from service.policy import category_list, keyword_index
        keyword_index.invalidate()
        category_list.invalidate()
    except Exception:
        logger.warning("정책 변경 후 정책 색인 비우기 실패", exc_info=True)
    try:   # 모든 업무 영역 답변 캐시 — 한 번에 훑어 지운다(영역마다 SCAN하던 것, 리뷰 P1)
        from shared.cache import invalidate_all
        await invalidate_all()
    except Exception:
        logger.warning("정책 변경 후 답변 캐시 비우기 실패", exc_info=True)
