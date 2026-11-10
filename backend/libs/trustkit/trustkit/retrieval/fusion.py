"""여러 검색 채널의 결과를 합치는 순수 함수 — 입력은 id 목록(순위 순)과 키 함수, 출력은 id 순서.

- best_per_doc: 조각 단위 결과를 문서(정책 항목 등) 단위로 — 문서 점수 = 가장 가까운 조각(MaxP, Dai & Callan 2019).
  같은 문서의 조각들이 상위 자리를 나눠 차지하지 않게.
- rescue_order: "구제" 합치기 — 주 채널(벡터) 순서는 그대로, 주 채널 상위 top_k에 없는 보조 채널(키워드) 상위 문서만
  아래 자리(최대 max_slots, 1위는 절대 안 바꿈)에. OpsLens 실측: 같은 무게 RRF는 서술형 Top-1을 91→65%로 떨어뜨렸고
  구제 방식은 Recall@10 92.0→95.5%(나빠진 문항 0).
- rrf_order: Reciprocal Rank Fusion(Cormack et al., SIGIR 2009) — 비교 측정용.
"""
from __future__ import annotations

import re
from typing import Callable, Hashable, Iterable, Optional, Sequence, TypeVar

T = TypeVar("T")
RRF_K = 60   # 원 논문 기본값


def best_per_doc(hits: Iterable[T], key: Callable[[T], Hashable]) -> list[T]:
    """문서마다 첫 번째(= 점수 순으로 들어오면 최고) 조각 하나, 입력 순서 유지."""
    seen, out = set(), []
    for h in hits:
        k = key(h)
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


def default_rescue_slots(top_k: int) -> int:
    """구제 자리 기본값 = 문맥 자리의 약 30%(최소 1). OpsLens 실측: 문맥 10자리 → 3(정책, 나빠진 문항 0), 문맥 3자리에 2자리를 주면
    Recall@3 95.3→75.5%로 무너졌다(키워드 단독은 1위 정답이 절반 남짓 — 주 채널이 맞힌 2·3위를 밀어냄), 1자리는 95.3→96.2%."""
    return max(1, top_k * 3 // 10) if top_k > 1 else 0


def rescue_order(primary: Sequence[Hashable], secondary: Sequence[Hashable], top_k: int, max_slots: int = 3,
                 available: Optional[set] = None) -> list[Hashable]:
    """primary(순위 순 id) 앞 top_k 중 아래 자리를 secondary가 놓친 상위로 구제한 id 순서.
    available: 실제로 문맥에 쓸 수 있는 id(보조 채널에만 걸렸는데 본문을 못 가져온 것 제외) — None이면 제한 없음.
    결과 = 주 채널 앞쪽(top_k − 구제 수) + 구제 + 주 채널 나머지. 구제 안 된 보조 채널 전용 id는 버린다."""
    slots = min(max_slots, max(top_k - 1, 0))   # 1위는 늘 주 채널 그대로
    top = list(primary[:top_k])
    rescue = [i for i in secondary if i not in top and (available is None or i in available)][:slots]
    keep = top[:top_k - len(rescue)]
    return keep + rescue + [i for i in primary if i not in keep and i not in rescue]


def rrf_order(rankings: Sequence[Sequence[Hashable]], weights: Optional[Sequence[float]] = None, k: int = RRF_K) -> list[Hashable]:
    """순위 목록 여러 개 → RRF 점수 높은 순 id(동점은 먼저 나온 목록·순위 순)."""
    weights = weights or [1.0] * len(rankings)
    score: dict = {}
    for ranking, w in zip(rankings, weights):
        for r, i in enumerate(ranking):
            score[i] = score.get(i, 0) + w / (k + r + 1)
    return sorted(score, key=lambda i: -score[i])


_SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")


def sentences(text: str) -> tuple:
    """글 → 문장 열(공백만 정리, 뜻이 바뀌는 정규화 없음). 문장 끝(. ! ? 。) 뒤 공백이나 줄바꿈에서 자른다."""
    return tuple(s for s in (" ".join(p.split()) for p in _SENT_SPLIT.split(text or "")) if s)


def _within(small: tuple, big: tuple) -> bool:
    """small의 문장들이 big 안에 같은 순서로 이어서 그대로 있다(연속 부분열). 집합으로만 보면 "[실버] 적립 3%"가
    "[실버] 적립 1% [골드] 적립 3%"에 들어가 실버=3%라는 연결이 사라진다(독립 리뷰 L1 11/10 — 사용 중 포함 쌍 3,060 중 92쌍이 이 꼴)."""
    n = len(small)
    return 0 < n <= len(big) and any(big[i:i + n] == small for i in range(len(big) - n + 1))


def collapse_contained(hits: Sequence[T], text: Callable[[T], str]) -> list[T]:
    """검색 결과에서 "다른 조각에 통째로 들어 있는" 조각을 한 자리로 접는다 — 문장들이 남은 조각 안에 같은 순서로 이어서 있으면 뺀다.
    뒤 조각이 앞 조각을 그렇게 다 담고 더 길면 앞 자리에 뒤 조각을 넣는다(그 자리 순위로 더 많은 내용).
    업계: 거의 같은 문서는 다 색인하고 보여 줄 때 하나로 묶는다(검색엔진 "유사한 결과 생략"·field collapsing), 척도는 포함도
    (Broder 1997 resemblance/containment). 임베딩 유사도로 접지 않는다 — OpsLens 실측(11/09, 업무지식 106문항): 포함만 접기 100→100,
    +코사인 ≥0.95(LangChain EmbeddingsRedundantFilter 기본값) 100→94 — 틀이 같고 값이 다른 조각(등급별 혜택 등)까지 사라졌다.
    문맥에 쓸 조각끼리만 넘긴다(쓰지 않을 조각이 쓸 조각을 지우지 않게 — 호출하는 쪽 몫, 독립 리뷰 11/10).
    성질: 남은 조각끼리는 서로 안에 들어 있지 않다 · 빠진 조각은 모두 남은 어떤 조각 안에 이어서 그대로 있다 · 순서는 첫 등장 자리 순."""
    kept: list[T] = []
    seqs: list[tuple] = []
    for h in hits:
        s = sentences(text(h))
        if any(_within(s, t) for t in seqs):
            continue
        j = next((k for k, t in enumerate(seqs) if len(t) < len(s) and _within(t, s)), None)
        if j is None:
            kept.append(h)
            seqs.append(s)
            continue
        kept[j], seqs[j] = h, s
        # 넓어진 자리가 다른 남은 조각까지 담으면 그것도 접는다
        drop = {k for k, t in enumerate(seqs) if k != j and _within(t, s)}
        kept = [x for k, x in enumerate(kept) if k not in drop]
        seqs = [x for k, x in enumerate(seqs) if k not in drop]
    return kept
