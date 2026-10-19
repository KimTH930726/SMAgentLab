"""한국어 텍스트 정규화 — 형태소 분석기 없이 규칙으로(폐쇄망·사전 유지보수 부담 없음).

- 조사·어미 제거: 길이 긴 접미사부터 그리디, 남는 어간 2글자 보장. 불규칙 활용(듣다→들어)은 못 잡는다(규칙 방식의 한계).
  OpsLens 실측: 키워드 채널 단독 hit@10 34.8→73.9%(2026-09, bench_suffix_stripping).
- 금액 정규화: "3만 원" = "30,000원" = "30000 원" — 질문과 원문이 같은 토큰이 되게.
- tokenize: 정규화 → 소문자 → 단어마다 접미사 제거(최대 passes번, "투고백에만" → "투고백") → 2글자 이상 또는 숫자.

DB 안에서 같은 규칙을 쓰는 경우(OpsLens의 SQL 함수 policy_strip_ko) SUFFIXES와 동작이 같아야 한다 — "가장 긴 매치"와
"정렬 후 첫 매치"는 같은 답을 주므로 집합만 같으면 된다.
"""
from __future__ import annotations

import functools
import re

SUFFIXES: list[str] = sorted([
    "까지는", "에서는", "으로는", "한테는", "에게서", "으로써",
    "이라도", "에서", "으로", "부터", "한테", "에게", "까지", "처럼", "보다",
    "이랑", "랑", "하고", "이며",
    "습니다", "입니다", "겠습니다", "습니까",
    "으며", "면서", "니까", "아서", "어서", "인데", "은데",
    "어요", "아요", "네요", "군요",
    "았고", "었고",
    "은", "는", "이", "가", "을", "를", "의", "도", "만", "에", "로", "와", "과",
    "고", "게", "지", "며", "니", "자", "다", "죠", "기", "아", "어",
], key=len, reverse=True)

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


@functools.lru_cache(maxsize=65536)
def strip_suffix(word: str) -> str:
    """단어 하나에서 가장 긴 매칭 접미사 1개를 제거. 남는 어간이 2글자 미만이면 자르지 않는다.
    캐시: 색인 생성 시간의 92%가 이 함수였다(같은 단어 반복) — 3,000건×800자 2.5s → 0.67s, 결과 동일(리뷰 10/19)."""
    for suf in SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 2:
            return word[: -len(suf)]
    return word


def strip_text(text: str) -> str:
    """공백 단위로 쪼개 단어마다 조사/어미를 제거하고 다시 합친다."""
    return " ".join(strip_suffix(w) for w in text.split())


def normalize_numbers(text: str) -> str:
    """"3만 원" → "30000 원", "1.5만" → "15000", "30,000원" → "30000 원", "5천" → "5000"."""
    t = re.sub(r"(?<=\d),(?=\d{3})", "", text or "")
    t = re.sub(r"(\d+(?:\.\d+)?)\s*만", lambda m: str(int(round(float(m.group(1)) * 10000))), t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*천", lambda m: str(int(round(float(m.group(1)) * 1000))), t)
    return re.sub(r"(\d)([가-힣A-Za-z])", r"\1 \2", t)


def tokenize(text: str, passes: int = 2) -> list[str]:
    out = []
    for w in _WORD.findall(normalize_numbers(text).lower()):
        for _ in range(passes):   # "투고백에만": "만" → "에" 순으로 두 번까지
            s = strip_suffix(w)
            if s == w:
                break
            w = s
        if len(w) >= 2 or w.isdigit():
            out.append(w)
    return out
