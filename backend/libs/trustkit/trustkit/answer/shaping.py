"""LLM에 넘길 문맥과 답 구조 다듬기 — 검색이 맞아도 문맥이 섞이거나 지시가 모호하면 답이 틀린다.

- list_scoped: 범위가 정해진 목록 답(메타데이터로 고른 분류 등)의 문맥에서 범위 밖 검색 결과를 순위로 거른다
  (self-query / metadata filtering). 다 버리지 않고 "첫 범위 안 결과보다 위에 온 범위 밖 결과"는 남긴다 — "전체 취소하면
  쿠폰은?"처럼 목록 신호 단어만 든 구체 질문의 진짜 정답을 지키려고. OpsLens 탐색형 62→75%.
- CONDITION_ANSWER_HINT_KO: 조건 한정 질문의 답 구조. "없으면 없다고 밝혀라"는 오답을 늘렸다(조건을 글자 그대로 읽고 문서
  내용을 "따로 정한 규정 없음"으로 부정) — 해당 내용 찾기 + 공통 규정 구분 + 단정 금지로. OpsLens 조건형 58→78%.
- list_line_summary: 목록 답에 붙이는 정책 한 줄 요약(본문 앞쪽 줄을 기호만 떼고 이어 붙여 자름, LLM 없음).
"""
from __future__ import annotations

import re
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


# 글머리 기호·번호("- ", "1.", "1-2.", "1)", "(1)", "①", "가)") — 숫자 값("3.5%", "-5%")은 떼지 않게 뒤를 본다
_MARKER = re.compile(r"^\s*(?:[-*•·▪◦○●□■※](?=\s)|\d+(?:[-.]\d+)*[.)](?!\d)|\(\d{1,2}\)(?=\s)|[①-⑳]"
                     r"|[가나다라마바사아자차카타파하]\)|[가나다라]\.(?=\s))\s*")
_SPACES = re.compile(r"\s+")


def _bare(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s).lower()


def list_line_summary(name: str, body: str, limit: int = 80, sep: str = " · ") -> str:
    """목록 답의 정책 한 줄 요약(LLM 없음) — 본문 앞쪽 줄들을 글머리 기호·번호만 떼고 이어 붙여 limit자에서 자른다.
    정책 본문은 대개 "1. 제목 / 1) 소제목 : 값 / - 세부"라 첫 줄만 쓰면 이름과 같은 제목뿐이었다(중앙값 15자 — 목록 답이
    본문으로 내려가 세부 조건을 줄줄이 펼치는 원인). 이름과 같은·이름에 든 줄은 건너뛴다. 조각은 모두 본문 그대로라
    없는 말이 생기지 않는다. 쓸 줄이 없으면 본문 첫 줄(그것도 없으면 빈 문자열)."""
    limit = max(1, limit)
    nb = _bare(name)
    parts: list[str] = []
    size = 0
    for raw in (body or "").splitlines():
        line = raw
        while True:   # "1. - 가" 같은 겹친 기호
            m = _MARKER.match(line)
            if not m or not m.group(0):
                break
            line = line[m.end():]
        line = _SPACES.sub(" ", line).strip()
        b = _bare(line)
        # 이름과 같은 줄, 또는 맨 앞 제목 줄(이름 안에 들고 이름 절반 이상 길이)만 건너뛴다 — 이름에 든 짧은 값 줄("가능")까지
        # 버리면 "가능 / 불가"가 "불가"만 남아 뜻이 뒤집혔다(리뷰 10/21)
        if not b or b == nb or (not parts and nb and b in nb and len(b) * 2 >= len(nb)):
            continue
        parts.append(line)
        size += len(line) + len(sep)
        if size >= limit:
            break
    text = sep.join(parts) or _SPACES.sub(" ", next((ln for ln in (body or "").splitlines() if ln.strip()), "")).strip()
    if len(text) <= limit:
        return text
    cut, mark = text[:limit - 1].rstrip(), sep.strip()
    if mark and cut.endswith(mark):   # 구분자 바로 뒤에서 잘리면 구분자도 뗀다("… · …")
        cut = cut[:-len(mark)].rstrip()
    return cut + "…"


CONDITION_ANSWER_HINT_KO = (
    "[답 구조: 이 질문은 특정 조건에 적용되는 규정을 묻는다. 아래 문서에서 그 조건에 적용되는 내용(그 조건이 적힌 규정, "
    "그 조건 값에 해당하는 기준 — 금액 구간·유형 코드·대상 상품 등)을 찾아 먼저 답하고, 그 조건에도 함께 적용되는 일반 규정은 "
    "\"공통 적용\"으로 구분해 짧게 적는다. 문서에 있는 내용을 두고 \"따로 정한 규정이 없다\"고 단정하지 않는다. "
    "아래 문서에 관련 내용이 있으면 \"관련 지식을 찾지 못했습니다\"로 답하지 않는다.]"
)
