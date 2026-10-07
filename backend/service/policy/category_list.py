"""탐색형 질문의 분류 목록 (2026-10-07) — "○○ 관련 정책 다 보여줘"에 그 분류의 정책을 빠짐없이 AI에게 준다.

검색(하이브리드·벡터·리랭커 무엇이든)은 "가장 관련 깊은 몇 개"를 고르는 도구라 "분류에 속한 것 전부"를 보장하지 못한다 — 실측(골든
탐색형 24문항): 정답 분류의 정책 중 AI 문맥에 들어간 비율 평균 42%, 작은 분류(3~8개)도 25~67%, 최종 답 정답 4/24(부분 18).
분류 전체가 문맥에 들어간 문항은 5개 중 4개가 정답이었다 → 고칠 곳은 "무엇을 가져오느냐".

분류 찾기(측정 후 결정, scripts 측정 기준): 질문에 분류 이름(번호 접두사 "4-2." 제거·띄어쓰기 무시)이 글자로 나오면 그중 가장 긴 이름,
없으면 질문과 의미가 가장 가까운 분류 1개(유사도 _MIN_SIM 이상일 때만). 같은 이름의 분류가 여러 곳(시트·단계)에 있으면 합친다.
측정: 포함률 42% → 91%, 가져온 것 중 정답 86%(임베딩만 쓰면 55%/58%, 합집합은 96%/74%). 탐색형으로 잘못 판별된 비탐색 1문항은
의미 유사도 0.570이라 _MIN_SIM(0.58)에서 걸러진다 — 표본이 작아 운영 중 다시 본다.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from core.database import get_conn

_NUM = re.compile(r"^\s*[\d\-]+[.)]\s*")
_NON_WORD = re.compile(r"[\s\W_]+")
_MIN_SIM = 0.58
_MAX_ITEMS = 40
_CACHE_TTL = 600.0   # 정책 임포트·정정이 분류를 바꿀 수 있어 10분마다 다시 만든다(분류 수백 개 임베딩 ~수 초)


def norm_category(s: str) -> str:
    """"4-2.배송정책" == "배송 정책" — 번호 접두사·띄어쓰기·문장부호 무시."""
    return _NON_WORD.sub("", _NUM.sub("", s or "").lower())


@dataclass
class CategoryIndex:
    item_ids: dict[str, set[int]] = field(default_factory=dict)   # 정규화 이름 → 정책 id
    display: dict[str, str] = field(default_factory=dict)          # 정규화 이름 → 화면용 이름(번호 뺀 첫 표기)
    vectors: dict[str, list[float]] = field(default_factory=dict)
    built_at: float = 0.0


def pick_categories(question: str, names: list[str], sims: Optional[dict[str, float]]) -> list[str]:
    """글자 매칭(가장 긴 이름 — 같은 길이면 전부) → 없으면 의미 1위(_MIN_SIM 이상). 못 찾으면 빈 목록."""
    qn = norm_category(question)
    lex = [n for n in names if len(n) >= 2 and n in qn]
    if lex:
        longest = max(len(n) for n in lex)
        return [n for n in lex if len(n) == longest]
    if sims:
        best = max(sims, key=sims.get)
        if sims[best] >= _MIN_SIM:
            return [best]
    return []


_cache: dict[int, CategoryIndex] = {}


async def _index(conn, ns_id: int) -> CategoryIndex:
    idx = _cache.get(ns_id)
    if idx and time.time() - idx.built_at < _CACHE_TTL:
        return idx
    from shared.embedding import embedding_service
    idx = CategoryIndex(built_at=time.time())
    for r in await conn.fetch(
            "SELECT id, category_path FROM policy_item WHERE namespace_id = $1 AND status NOT IN ('deprecated', 'rejected')", ns_id):
        for cat in r["category_path"] or []:
            n = norm_category(cat)
            if len(n) < 2:
                continue
            idx.item_ids.setdefault(n, set()).add(r["id"])
            idx.display.setdefault(n, _NUM.sub("", cat).strip())
    for n, label in idx.display.items():
        idx.vectors[n] = await embedding_service.embed(label)
    _cache[ns_id] = idx
    return idx


@dataclass
class CategoryList:
    names: list[str]
    total: int
    lines: list[str]

    def block(self) -> str:
        more = f" — 그중 {len(self.lines)}개만 표시" if self.total > len(self.lines) else ""
        return (f"[분류 목록: {', '.join(self.names)} — 정책 {self.total}개{more}. \"전부·목록\" 질문이면 이 목록을 빠짐없이 근거로]\n"
                + "\n".join(self.lines))


async def find_category_list(ns_id: int, question: str, query_vec: list[float]) -> Optional[CategoryList]:
    """탐색형 질문의 대상 분류를 찾아 그 정책 목록(분류 경로 > 정책명: 본문 첫 줄)을 돌려준다. 못 찾으면 None."""
    async with get_conn() as conn:
        idx = await _index(conn, ns_id)
        if not idx.item_ids:
            return None
        sims = {n: sum(a * b for a, b in zip(query_vec, v)) for n, v in idx.vectors.items()}
        picked = pick_categories(question, list(idx.item_ids), sims)
        if not picked:
            return None
        ids = sorted(set().union(*(idx.item_ids[n] for n in picked)))
        rows = await conn.fetch(
            "SELECT array_to_string(category_path, ' > ') AS cat, policy_name, split_part(coalesce(raw_body, ''), E'\\n', 1) AS first "
            "FROM policy_item WHERE id = ANY($1::int[]) ORDER BY category_path, source_row LIMIT $2", ids, _MAX_ITEMS)
    lines = [f"- {r['cat']} > {r['policy_name']}: {(r['first'] or '').strip()[:100]}" for r in rows]
    return CategoryList(names=[idx.display[n] for n in picked], total=len(ids), lines=lines)
