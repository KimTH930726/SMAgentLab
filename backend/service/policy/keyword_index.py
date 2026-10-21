"""정책 항목 키워드 색인(BM25) — 정책 서술 검색의 키워드 채널 (2026-10-13).

막힌 일: 정책 서술은 벡터 검색만 있었다. 드문 고유명사·조건어("투고백에만")는 원문에 글자 그대로 있어도 벡터로는 71개 중 36위였고,
금액 표기("3만 원" ↔ "30,000원")는 벡터로도 키워드로도 안 맞았다(q084·q079 진단). 기존 키워드 검색(PostgreSQL ts_rank)은
IDF가 없어 드문 단어와 흔한 단어("정책")가 같은 무게다 — 업계 기본은 BM25 + 벡터를 순위로 합치는 것(RRF).
정책 항목은 파트당 수백 건이라 메모리 BM25로 충분하다(확장 설치 없음 — 폐쇄망). 10분마다 다시 만든다(category_list와 같은 방식).
BM25·토큰화 본체는 trustkit(신뢰도 레이어) — 이 모듈은 정책 테이블 읽기·캐시만(2026-10-19).

토큰: 조사·어미 제거(korean_text 규칙, 최대 두 번 — "투고백에만" → "투고백"), 금액 정규화(만·천 단위·쉼표 → 숫자, 숫자와 단위 분리).
특정 데이터에 맞춘 규칙이 아니라 한국어 숫자 표기 일반 처리다.
"""
from __future__ import annotations

import time
from typing import Optional, Sequence, Union

from core.database import get_conn
from service.policy.visibility import searchable_sql
from trustkit.retrieval.bm25 import BM25Index

_CACHE_TTL = 600.0
_cache: dict[tuple[int, ...], BM25Index] = {}


def invalidate() -> None:
    """정책 가져오기·정정 뒤 색인을 비운다 — 안 비우면 TTL 동안 새 버전이 키워드 채널에 안 잡힌다(옛 id는 상태 필터로 걸러짐)."""
    _cache.clear()


async def search(ns_ids: Union[int, Sequence[int]], query: str, top_k: int,
                 category: Optional[str] = None) -> list[tuple[int, float]]:
    """ns_ids = 파트 하나 또는 여러 개(채팅 정책서 범위, 2026-10-20). 여러 파트는 색인 하나로 합친다 — 파트마다 따로 매기면 IDF가 달라
    점수를 비교할 수 없다(v0 실험의 "전체" 범위도 합친 색인으로 측정)."""
    key = (ns_ids,) if isinstance(ns_ids, int) else tuple(sorted(set(ns_ids)))
    if not key:
        return []
    idx = _cache.get(key)
    if not idx or time.time() - idx.built_at > _CACHE_TTL:
        async with get_conn() as conn:
            rows = await conn.fetch(
                "SELECT id, policy_name, array_to_string(category_path, ' ') AS cat, coalesce(raw_body, '') AS body "
                f"FROM policy_item WHERE namespace_id = ANY($1::int[]) AND {searchable_sql('policy_item')}", list(key))
        idx = BM25Index([(r["id"], f"{r['policy_name']} {r['cat']} {r['body']}") for r in rows])
        _cache[key] = idx
    hits = idx.score(query, top_k * (4 if category else 1))
    if category:   # 분류를 고른 검색은 그 분류 항목만(벡터 쪽 조건과 같게)
        async with get_conn() as conn:
            ok = {r["id"] for r in await conn.fetch(
                "SELECT id FROM policy_item WHERE id = ANY($1::int[]) AND $2 = ANY(category_path)", [h[0] for h in hits], category)}
        hits = [h for h in hits if h[0] in ok]
    return hits[:top_k]
