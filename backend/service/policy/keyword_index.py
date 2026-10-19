"""정책 항목 키워드 색인(BM25) — 정책 서술 검색의 키워드 채널 (2026-10-13).

막힌 일: 정책 서술은 벡터 검색만 있었다. 드문 고유명사·조건어("투고백에만")는 원문에 글자 그대로 있어도 벡터로는 71개 중 36위였고,
금액 표기("3만 원" ↔ "30,000원")는 벡터로도 키워드로도 안 맞았다(q084·q079 진단). 기존 키워드 검색(PostgreSQL ts_rank)은
IDF가 없어 드문 단어와 흔한 단어("정책")가 같은 무게다 — 업계 기본은 BM25 + 벡터를 순위로 합치는 것(RRF).
정책 항목은 파트당 수백 건이라 메모리 BM25로 충분하다(확장 설치 없음 — 폐쇄망). 10분마다 다시 만든다(category_list와 같은 방식).

토큰: 조사·어미 제거(korean_text 규칙, 최대 두 번 — "투고백에만" → "투고백"), 금액 정규화(만·천 단위·쉼표 → 숫자, 숫자와 단위 분리).
특정 데이터에 맞춘 규칙이 아니라 한국어 숫자 표기 일반 처리다.
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from core.database import get_conn
from service.policy.korean_text import strip_suffix

K1, B = 1.2, 0.75          # BM25 표준값
_CACHE_TTL = 600.0
_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


def normalize_numbers(text: str) -> str:
    """"3만 원" → "30000 원", "1.5만" → "15000", "30,000원" → "30000 원", "5천" → "5000"."""
    t = re.sub(r"(?<=\d),(?=\d{3})", "", text or "")
    t = re.sub(r"(\d+(?:\.\d+)?)\s*만", lambda m: str(int(round(float(m.group(1)) * 10000))), t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*천", lambda m: str(int(round(float(m.group(1)) * 1000))), t)
    return re.sub(r"(\d)([가-힣A-Za-z])", r"\1 \2", t)


def tokenize(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(normalize_numbers(text).lower()):
        for _ in range(2):   # "투고백에만": "만" → "에" 순으로 두 번까지
            s = strip_suffix(w)
            if s == w:
                break
            w = s
        if len(w) >= 2 or w.isdigit():
            out.append(w)
    return out


@dataclass
class _Index:
    ids: list[int] = field(default_factory=list)
    tfs: list[Counter] = field(default_factory=list)
    lens: list[int] = field(default_factory=list)
    df: Counter = field(default_factory=Counter)
    avg_len: float = 1.0
    built_at: float = 0.0


_cache: dict[int, _Index] = {}


def build(rows: list[tuple[int, str]]) -> _Index:
    idx = _Index(built_at=time.time())
    for item_id, text in rows:
        toks = tokenize(text)
        tf = Counter(toks)
        idx.ids.append(item_id)
        idx.tfs.append(tf)
        idx.lens.append(len(toks))
        idx.df.update(tf.keys())
    idx.avg_len = (sum(idx.lens) / len(idx.lens)) if idx.lens else 1.0
    return idx


def score(idx: _Index, query: str, top_k: int) -> list[tuple[int, float]]:
    q = set(tokenize(query))
    n = len(idx.ids)
    out = []
    for i, tf in enumerate(idx.tfs):
        s = 0.0
        for t in q:
            f = tf.get(t)
            if not f:
                continue
            idf = math.log(1 + (n - idx.df[t] + 0.5) / (idx.df[t] + 0.5))
            s += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * idx.lens[i] / idx.avg_len))
        if s > 0:
            out.append((idx.ids[i], s))
    out.sort(key=lambda x: -x[1])
    return out[:top_k]


def invalidate() -> None:
    """정책 가져오기·정정 뒤 색인을 비운다 — 안 비우면 TTL 동안 새 버전이 키워드 채널에 안 잡힌다(옛 id는 상태 필터로 걸러짐)."""
    _cache.clear()


async def search(ns_id: int, query: str, top_k: int, category: Optional[str] = None) -> list[tuple[int, float]]:
    idx = _cache.get(ns_id)
    if not idx or time.time() - idx.built_at > _CACHE_TTL:
        async with get_conn() as conn:
            rows = await conn.fetch(
                "SELECT id, policy_name, array_to_string(category_path, ' ') AS cat, coalesce(raw_body, '') AS body "
                "FROM policy_item WHERE namespace_id = $1 AND status NOT IN ('deprecated', 'rejected')", ns_id)
        idx = build([(r["id"], f"{r['policy_name']} {r['cat']} {r['body']}") for r in rows])
        _cache[ns_id] = idx
    hits = score(idx, query, top_k * (4 if category else 1))
    if category:   # 분류를 고른 검색은 그 분류 항목만(벡터 쪽 조건과 같게)
        async with get_conn() as conn:
            ok = {r["id"] for r in await conn.fetch(
                "SELECT id FROM policy_item WHERE id = ANY($1::int[]) AND $2 = ANY(category_path)", [h[0] for h in hits], category)}
        hits = [h for h in hits if h[0] in ok]
    return hits[:top_k]
