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

# 판별 규칙 본체는 trustkit.query.intent(신뢰도 레이어, 2026-10-19 이동). 실측 근거(위·아래 주석)는 이 프로젝트 기록이라 여기 둔다.
# "한 번에"는 원래 후보에 넣었다가 뺐다 — "한 번에 몇 개 살 수 있어?"류 수량(param) 질문과 충돌해 정밀도가 82.1%→96.0%로
# 떨어졌었다(FP 5건). 골든 89문항: precision 96.0%, recall 100.0%(navigation 24/24, 비-navigation 오탐 1건).
# 조건 한정("X에만/때만/전용", v2.138): 조건형 18/18 · 다른 유형 1/70 — 골든 질문이 한 틀이라 실제 질문 재현율은 운영에서 확인.
from trustkit.query.intent import looks_like_condition_query, looks_like_navigation_query  # noqa: F401
