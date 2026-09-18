"""Reciprocal Rank Fusion — 스케일이 다른 여러 순위 목록을 정규화 없이 하나로 합친다.

일반지식(코사인 유사도, 0~1)과 정책 파라미터(RDB ts_rank, 0~5+대)처럼 원점수 체계가
다른 목록을 그냥 정렬하면 사과-오렌지 비교가 된다. RRF는 원점수를 안 보고 "각 목록
안에서 몇 번째 순위인가"만 본다 — OpenSearch/Azure AI Search/MongoDB/Redis 등이 실제
쓰는 표준 기법(k=60이 원 논문 기본값).
"""

RRF_K = 60


def rrf_score(rank: int, k: int = RRF_K) -> float:
    """rank는 0-indexed(그 목록 안에서 몇 번째로 좋은 결과인지)."""
    return 1 / (k + rank + 1)
