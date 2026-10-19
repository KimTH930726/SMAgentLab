"""정책 서술 검색 개선(2026-10-13) — 키워드 채널(BM25)·임베딩 머리·항목 묶기·구제 합치기.

골든 88 검색 측정(결정적): Recall@10 88.6→95.5%(나빠진 문항 0, McNemar p=0.031), Top-1 60.2→62.5%.
여기서는 그 동작의 성질을 고정한다(데이터에 맞춘 규칙이 아니라 일반 성질).
"""
import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.modules.setdefault("core", MagicMock())
sys.modules.setdefault("core.database", MagicMock())
_b = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(_b / rel))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# 단독 실행에서도 search.py의 `from service.policy import keyword_index`가 풀리게 부모 패키지를 잇는다(리뷰 10/13 —
# 전에는 다른 테스트가 service 패키지를 먼저 불러줄 때만 통과했다). conftest가 service를 MagicMock으로 두는 방식과 같다.
sys.modules.setdefault("service", MagicMock())
_pkg = sys.modules.setdefault("service.policy", MagicMock())
kt = _load("service.policy.korean_text", "service/policy/korean_text.py")
_qt = _load("service.policy.query_type", "service/policy/query_type.py")
K = _load("service.policy.keyword_index", "service/policy/keyword_index.py")
for _n, _m in (("korean_text", kt), ("query_type", _qt), ("keyword_index", K)):
    setattr(_pkg, _n, _m)


def test_numbers_normalized_generically():
    assert "30000" in K.normalize_numbers("3만 원을 넘을 때")
    assert K.normalize_numbers("30,000원 이상") == "30000 원 이상"
    assert "15000" in K.normalize_numbers("1.5만원") and "5000" in K.normalize_numbers("5천 원")
    # 양쪽이 같은 토큰이 된다 — 질문 "3만 원" ↔ 원문 "30,000원"
    assert set(K.tokenize("3만 원")) & set(K.tokenize("30,000원 이상 : 무료")) >= {"30000"}


def test_two_suffix_passes():
    assert "투고백" in K.tokenize("투고백에만 따로 적용되는")      # "만" → "에" 두 번
    assert "정책" in K.tokenize("정책은")


def test_bm25_prefers_rare_matching_term():
    rows = [(1, "배송 정책 일반 안내 정책"), (2, "SKU 구매 제한 투고백 2세트"), (3, "정책 정책 정책 쿠폰")] + \
           [(10 + i, f"정책 안내 {i}") for i in range(20)]
    idx = K.build(rows)
    top = K.score(idx, "투고백에만 적용되는 정책", 3)
    assert top[0][0] == 2     # 드문 단어(투고백)가 흔한 단어(정책)보다 무겁다 — IDF
    assert K.score(idx, "전혀 없는 단어", 3) == []


def test_chunk_embed_header_not_doubled():
    import re
    src = (_b / "service/policy/service.py").read_text(encoding="utf-8")
    ns: dict = {}
    exec(re.search(r"def chunk_embed_text\(.*?\n(?=\n\n)", src, re.S).group(0), ns)
    f = ns["chunk_embed_text"]
    assert f("배송비", ["배송", "배송비"], "3만원 이상 무료") == "배송비 (배송 > 배송비): 3만원 이상 무료"
    fallback = "배송비 (배송 / 배송비): 원문"
    assert f("배송비", ["배송"], fallback) == fallback          # 대체 조각은 이미 머리가 있다


def test_best_per_item_and_rescue_fusion():
    S = _load("_policy_search", "service/policy/search.py")
    H = lambda item, score=0.5: S.NarrativeHit(item_id=item, logical_id=item, policy_name=f"p{item}", category_path=[],  # noqa: E731
                                               status="active", chunk_text="t", score=score, raw_body="b", chunk_id=item * 10)
    hits = [H(1, .9), H(1, .8), H(2, .7), H(3, .6)]
    assert [h.item_id for h in S._best_per_item(hits)] == [1, 2, 3]
    vec = [H(i, 1 - i / 100) for i in range(1, 13)]                 # 벡터 순서 1..12
    S.KEYWORD_FUSION, S.KEYWORD_RESCUE_SLOTS = "rescue", 3
    kw = [(2, 9.0), (11, 8.0), (12, 7.0), (5, 1.0)]                 # 11·12는 벡터 상위 10에 없음
    out = asyncio.run(S._fuse_keyword(1, "[0]", vec, kw, 10))
    ids = [h.item_id for h in out]
    assert ids[0] == 1                                              # 벡터 1위 그대로(맨 앞 정답 유지)
    assert ids[:10] == [1, 2, 3, 4, 5, 6, 7, 8, 11, 12]             # 놓친 키워드 상위만 아래 자리에
    assert sorted(ids) == list(range(1, 13))                        # 유실·중복 없음


def test_rescue_respects_caller_top_k():
    """top_k=5 호출에서도 구제 항목이 잘리는 자리(6위 이후)가 아니라 상위 5 안에 든다 — 고정 10이던 결함(리뷰 10/13)."""
    S = sys.modules.get("_policy_search") or _load("_policy_search", "service/policy/search.py")
    H = lambda item: S.NarrativeHit(item_id=item, logical_id=item, policy_name="p", category_path=[],  # noqa: E731
                                    status="active", chunk_text="t", score=0.5, raw_body="b", chunk_id=item)
    vec = [H(i) for i in range(1, 13)]
    S.KEYWORD_FUSION, S.KEYWORD_RESCUE_SLOTS = "rescue", 3
    for top_k in (1, 2, 5, 10):
        kw = [(11, 9.0), (12, 8.0), (1, 1.0)]
        ids = [h.item_id for h in asyncio.run(S._fuse_keyword(1, "[0]", vec, kw, top_k))]
        assert ids[0] == 1                                          # 1위는 늘 벡터
        assert sorted(ids) == list(range(1, 13))                    # 유실·중복 없음
        slots = min(3, top_k - 1)
        assert set([11, 12][:slots]) <= set(ids[:top_k])           # 구제 항목은 top_k 안에


def test_keyword_index_invalidate():
    K._cache[1] = K.build([(1, "a")])
    K.invalidate()
    assert K._cache == {}
