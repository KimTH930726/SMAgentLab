"""적재 중복 판정 — 의미 유사도(임베딩)가 고른 후보를 **글자**로 다시 본다(표준 라이브러리만).

업계 방식: 정확 중복은 정규화 후 글자 비교(해시), 거의 같은 글은 의미가 아니라 글자 기준(shingle/MinHash 계열),
여러 문서에 반복되는 양식 줄(boilerplate)은 비교에서 뺀다(CCNet 단락 해시 빈도 제거와 같은 생각).
양식 줄은 하드코딩하지 않고 **그 컬렉션의 데이터에서** 센다 — 팀·공간마다 양식이 달라서(11/04 태훈).
설계: docs/domains/knowledge/chunk-structure-dedupe-design.md ②·③.
"""
from __future__ import annotations

import difflib
import math
from collections import Counter
from typing import Iterable


def norm_text(text: str) -> str:
    """공백·줄바꿈 차이만 없앤 글 — 완전 일치 비교용(뜻이 바뀌는 정규화는 안 한다)."""
    return " ".join((text or "").split())


def _lines(text: str) -> list[str]:
    return [ln for ln in (" ".join(l.split()) for l in (text or "").splitlines()) if ln]


def template_lines(pages: Iterable[tuple[str, str]], *, min_pages: int = 3, min_share: float = 0.01) -> frozenset[str]:
    """여러 페이지에 똑같이 나오는 줄 = 양식 줄. pages = (페이지 키, 그 페이지 조각 글) — 한 페이지에 여러 조각이면 여러 번 넘겨도 된다.
    기준 = max(min_pages, 페이지 수 × min_share)쪽 이상 — 페이지 수에 비례해서, 작은 컬렉션은 3쪽·큰 컬렉션은 1%."""
    by_page: dict[str, set[str]] = {}
    for key, text in pages:
        by_page.setdefault(key, set()).update(_lines(text))
    df = Counter(ln for lines in by_page.values() for ln in lines)
    need = max(min_pages, math.ceil(len(by_page) * min_share))
    return frozenset(ln for ln, n in df.items() if n >= need)


def text_similarity(a: str, b: str, ignore: frozenset[str] = frozenset()) -> float:
    """양식 줄을 뺀 글자 유사도(0~1, difflib 비율). 둘 다 양식 줄뿐이면 원래 글로 비교(양식만 있는 조각끼리는 그대로 같은 것)."""
    return _matcher(a, b, ignore).ratio()


def _matcher(a: str, b: str, ignore: frozenset[str]) -> difflib.SequenceMatcher:
    ka = [ln for ln in _lines(a) if ln not in ignore]
    kb = [ln for ln in _lines(b) if ln not in ignore]
    if not ka and not kb:
        ka, kb = _lines(a), _lines(b)
    x, y = sorted(("\n".join(ka), "\n".join(kb)))   # difflib 비율은 인자 순서에 따라 다를 수 있다 — 어느 쪽이 먼저 와도 같은 판정(성질 테스트가 잡음)
    return difflib.SequenceMatcher(None, x, y, autojunk=False)


def similar_at_least(a: str, b: str, threshold: float, ignore: frozenset[str] = frozenset()) -> bool:
    """text_similarity(a, b, ignore) >= threshold 와 같은 판정 — 상한(real_quick_ratio·quick_ratio)이 기준 아래면 비싼 비율 계산을 건너뛴다.
    같은 양식의 형제 조각이 많으면 짝마다 difflib가 돌아 적재가 몇 분씩 멈출 수 있었다(독립 리뷰 11/04: 1.4천 자 한 쌍 0.08초)."""
    m = _matcher(a, b, ignore)
    return m.real_quick_ratio() >= threshold and m.quick_ratio() >= threshold and m.ratio() >= threshold
