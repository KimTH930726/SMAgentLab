"""LLM에 넘길 문맥과 답 구조 다듬기 — 검색이 맞아도 문맥이 섞이거나 지시가 모호하면 답이 틀린다.

- list_scoped: 범위가 정해진 목록 답(메타데이터로 고른 분류 등)의 문맥에서 범위 밖 검색 결과를 순위로 거른다
  (self-query / metadata filtering). 다 버리지 않고 "첫 범위 안 결과보다 위에 온 범위 밖 결과"는 남긴다 — "전체 취소하면
  쿠폰은?"처럼 목록 신호 단어만 든 구체 질문의 진짜 정답을 지키려고. OpsLens 탐색형 62→75%.
- CONDITION_ANSWER_HINT_KO: 조건 한정 질문의 답 구조. "없으면 없다고 밝혀라"는 오답을 늘렸다(조건을 글자 그대로 읽고 문서
  내용을 "따로 정한 규정 없음"으로 부정) — 해당 내용 찾기 + 공통 규정 구분 + 단정 금지로. OpsLens 조건형 58→78%.
"""
from __future__ import annotations

from typing import Callable, Hashable, Sequence, TypeVar

T = TypeVar("T")


def list_scoped(hits: Sequence[T], keep: set, key: Callable[[T], Hashable]) -> list[T]:
    """범위 안(keep) 결과 전부 + 첫 범위 안 결과보다 순위가 높은 범위 밖 결과(입력 순서 유지)."""
    first = next((i for i, h in enumerate(hits) if key(h) in keep), len(hits))
    return [h for i, h in enumerate(hits) if key(h) in keep or i < first]


def with_note(body: str, note, label: str = "비고") -> str:
    """답변 문맥용 원문 = 본문 + 비고. 비고(예외·외부 시스템 안내)는 답을 바꾸므로 LLM이 봐야 하지만 검색 조각엔 넣지 않는다
    — 붙이면 비고 있는 문서의 1위가 떨어졌다(OpsLens 정책서 v0, 88→82%). 검색 텍스트와 답변 문맥 텍스트를 따로 둔다."""
    note = (note or "").strip()
    return f"{body}\n{label}: {note}" if note else body


CONDITION_ANSWER_HINT_KO = (
    "[답 구조: 이 질문은 특정 조건에 적용되는 규정을 묻는다. 아래 문서에서 그 조건에 적용되는 내용(그 조건이 적힌 규정, "
    "그 조건 값에 해당하는 기준 — 금액 구간·유형 코드·대상 상품 등)을 찾아 먼저 답하고, 그 조건에도 함께 적용되는 일반 규정은 "
    "\"공통 적용\"으로 구분해 짧게 적는다. 문서에 있는 내용을 두고 \"따로 정한 규정이 없다\"고 단정하지 않는다. "
    "아래 문서에 관련 내용이 있으면 \"관련 지식을 찾지 못했습니다\"로 답하지 않는다.]"
)
