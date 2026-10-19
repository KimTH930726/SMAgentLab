"""여러 검색 채널의 결과를 합치는 순수 함수 — 입력은 id 목록(순위 순)과 키 함수, 출력은 id 순서.

- best_per_doc: 조각 단위 결과를 문서(정책 항목 등) 단위로 — 문서 점수 = 가장 가까운 조각(MaxP, Dai & Callan 2019).
  같은 문서의 조각들이 상위 자리를 나눠 차지하지 않게.
- rescue_order: "구제" 합치기 — 주 채널(벡터) 순서는 그대로, 주 채널 상위 top_k에 없는 보조 채널(키워드) 상위 문서만
  아래 자리(최대 max_slots, 1위는 절대 안 바꿈)에. OpsLens 실측: 같은 무게 RRF는 서술형 Top-1을 91→65%로 떨어뜨렸고
  구제 방식은 Recall@10 92.0→95.5%(나빠진 문항 0).
- rrf_order: Reciprocal Rank Fusion(Cormack et al., SIGIR 2009) — 비교 측정용.
"""
from __future__ import annotations

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
