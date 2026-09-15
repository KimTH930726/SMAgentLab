"""질의 유형 추정 — navigation형(카테고리 전체조회) 질문만 값싸게 판별한다.

리랭커(CrossEncoder)가 navigation형 질문에서 구조적으로 손해를 낸다는 게 실측으로
확인됨(`backend/scripts/bench_reranker_models.py`, 2026-09-11 — dragonkue/bge-reranker-
v2-m3-ko 적용 시 navigation -10.0%p, BAAI/bge-reranker-v2-m3 -5.0%p, Dongjin-kr/
ko-reranker -25.0%p — **한국어 리랭커 3종 전부 이 유형에서만 예외 없이 악화**). 정답이
여러 개인 카테고리 전체조회 질문에 "가장 딱 맞는 문서 1개"를 고르는 CrossEncoder 방식
자체가 구조적으로 안 맞기 때문으로 추정.

그래서 정밀한 질의 유형 분류기가 아니라 "이 질문엔 리랭킹을 건너뛸지"만 판단하는
저비용 규칙 기반 신호 탐지로 충분하다 — 89문항 골든셋으로 실측 검증(정확도 아래 참고).
"""
from __future__ import annotations

import re

# "한 번에"는 원래 후보에 넣었다가 뺐다 — "한 번에 몇 개 살 수 있어?"류 수량(param) 질문과
# 충돌해 정밀도가 82.1%→96.0%로 떨어졌었다(FP 5건, 전부 "한 번에"가 원인). 나머지 신호로도
# 재현율 100%가 나와서 뺀 채로 확정.
_NAV_SIGNAL_RE = re.compile(r"전체|모든|모두|리스트|목록|한눈에|다\s*(볼|보여|알려|확인)")


def looks_like_navigation_query(query: str) -> bool:
    """89문항 골든셋 실측: precision 96.0%, recall 100.0%(navigation 24건 중 24건 탐지,
    비-navigation 65건 중 오탐 1건 — "여러 제품 전체에 한 번에 적용" 질문)."""
    return bool(_NAV_SIGNAL_RE.search(query))
