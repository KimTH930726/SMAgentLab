"""질문 유형 판별(규칙) — 유형마다 문맥 범위·답 구조·리랭커 사용을 달리하기 위한 값싼 신호.

LLM 분류기 대신 규칙인 이유: 판별이 틀려도 "평소대로 답"으로 떨어지게 설계해(해가 없는 방향) 정밀도만 지키면 된다.
패턴은 인자로 바꿀 수 있다(다른 도메인·언어). 기본값은 한국어, OpsLens 골든 88 실측:
- 탐색형(목록 전체): 정밀도 96%·재현율 100% ("한 번에"는 수량 질문과 충돌해 제외)
- 조건 한정("X에만/때만/전용"): 조건형 18/18 · 다른 유형 1/70 — 골든 질문이 한 틀이라 실제 질문 재현율은 운영에서 확인 필요
"""
from __future__ import annotations

import re
from typing import Pattern

NAV_SIGNAL_RE: Pattern = re.compile(r"전체|모든|모두|리스트|목록|한눈에|다\s*(볼|보여|알려|확인)")
# "만"은 앞이 한글일 때만("쿠폰만 따로" O, "3만 적용" X), "전용"은 끝·띄어쓰기·조사가 올 때만("임직원 전용 쿠폰"·"전용으로" O, "전용계좌" X)
CONDITION_RE: Pattern = re.compile(r"에만|(?<=[가-힣])만\s*(따로|해당|적용|별도)|때만|경우에만|전용(?=$|[\s.,?!]|으로|은|는|이|에|의)")


def looks_like_navigation_query(query: str, pattern: Pattern = NAV_SIGNAL_RE) -> bool:
    return bool(pattern.search(query))


def looks_like_condition_query(query: str, pattern: Pattern = CONDITION_RE) -> bool:
    return bool(pattern.search(query))
