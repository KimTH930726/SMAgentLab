"""BM25 키워드 색인(메모리) — Robertson & Zaragoza, "The Probabilistic Relevance Framework: BM25 and Beyond"(2009).

벡터 검색은 문장 전체 뜻을 한 벡터로 압축해 드문 고유명사·정확한 숫자를 흐린다. BM25는 ① 드문 단어일수록 무겁게(IDF)
② 같은 단어 반복은 포화(K1) ③ 긴 문서 보정(B). 문서 수백~수천 건이면 메모리 색인으로 충분(확장 설치 없음).
색인 수명(캐시·무효화)과 문서 읽기는 쓰는 쪽 몫.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from typing import Callable, Hashable, Iterable

from trustkit.text.ko import tokenize as _ko_tokenize

K1, B = 1.2, 0.75   # 표준값


class BM25Index:
    def __init__(self, rows: Iterable[tuple[Hashable, str]], tokenizer: Callable[[str], list[str]] = _ko_tokenize,
                 k1: float = K1, b: float = B):
        self.tokenizer, self.k1, self.b = tokenizer, k1, b
        self.ids: list = []
        self.tfs: list[Counter] = []
        self.lens: list[int] = []
        self.df: Counter = Counter()
        for doc_id, text in rows:
            toks = tokenizer(text)
            tf = Counter(toks)
            self.ids.append(doc_id)
            self.tfs.append(tf)
            self.lens.append(len(toks))
            self.df.update(tf.keys())
        self.avg_len = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        self.built_at = time.time()   # 쓰는 쪽 캐시 수명(TTL) 판단용

    def score(self, query: str, top_k: int) -> list[tuple[Hashable, float]]:
        """(문서 id, 점수) 점수 높은 순, 점수 0은 제외. 동점은 색인 순서."""
        q = set(self.tokenizer(query))
        n = len(self.ids)
        out = []
        for i, tf in enumerate(self.tfs):
            s = 0.0
            for t in q:
                f = tf.get(t)
                if not f:
                    continue
                idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg_len))
            if s > 0:
                out.append((self.ids[i], s))
        out.sort(key=lambda x: -x[1])
        return out[:top_k]
