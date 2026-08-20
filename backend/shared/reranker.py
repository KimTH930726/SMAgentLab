"""CrossEncoder 리랭커 — 1차 하이브리드 검색 결과를 재정렬.

권장 모델:
  - cross-encoder/ms-marco-MiniLM-L-6-v2        (영문 최적화, ~80MB)
  - cross-encoder/mmarco-mMiniLMv2-L12-H384-v1  (다국어, ~120MB)

설계:
  - main.py lifespan에서 load() 호출 (embedding_service와 동일 패턴)
  - 모델 로드 실패 시 graceful degradation (원본 순서 반환)
  - predict()는 CPU-bound → run_in_executor로 이벤트 루프 블로킹 방지
"""
from __future__ import annotations

import asyncio
import logging
from functools import partial

logger = logging.getLogger(__name__)

_model = None
_loaded = False


def is_available() -> bool:
    return _model is not None


def load(model_name: str) -> None:
    """앱 시작 시 1회 호출. 이미 로드됐거나 모델명이 비어있으면 no-op."""
    global _model, _loaded
    if _loaded:
        return
    _loaded = True
    if not model_name:
        logger.info("[Reranker] 모델명 미설정 — 비활성화")
        return
    try:
        from sentence_transformers import CrossEncoder
        logger.info("[Reranker] Loading model: %s", model_name)
        _model = CrossEncoder(model_name)
        logger.info("[Reranker] Model loaded.")
    except Exception as e:
        logger.warning("[Reranker] 모델 로드 실패 (원본 순서 fallback): %s", e)
        _model = None


async def rerank(query: str, results: list, top_k: int) -> list:
    """CrossEncoder로 results 재정렬 후 top_k 반환.

    모델 미로드/로드 실패 시 원본 results[:top_k] 반환 (graceful degradation).
    content는 512자로 잘라 CrossEncoder 입력 길이를 제한.
    """
    if _model is None or len(results) <= top_k:
        return results[:top_k]

    scored = await score(query, results)
    top = [r for r, _ in scored[:top_k]]
    logger.info(
        "[Reranker] %d → %d | top scores: %s",
        len(results), top_k,
        [f"{s:.3f}" for _, s in scored[:3]],
    )
    return top


async def score(query: str, results: list) -> list[tuple]:
    """CrossEncoder 원점수를 (result, score) 쌍으로 반환 (내림차순 정렬).

    rerank()와 달리 목적이 "재정렬"이 아니라 "이 결과가 실제로 얼마나 관련
    있는지"를 알아야 하는 호출자(예: VOC 관련성 게이트)를 위한 함수 — 결과
    개수가 top_k 이하여도 스킵하지 않고 항상 채점한다. 모델 미로드/실패 시
    모든 결과에 score=0.0을 부여해 호출자가 게이트를 "무조건 통과 안 시킴"
    쪽으로 안전하게 처리하도록 한다(graceful degradation).
    """
    if _model is None or not results:
        return [(r, 0.0) for r in results]

    pairs = [(query, r.content[:512]) for r in results]
    try:
        raw_scores = await asyncio.get_running_loop().run_in_executor(
            None, partial(_model.predict, pairs)
        )
        return sorted(
            ((r, float(s)) for r, s in zip(results, raw_scores)),
            key=lambda x: x[1], reverse=True,
        )
    except Exception as e:
        logger.warning("[Reranker] 채점 실패 (0점 반환): %s", e)
        return [(r, 0.0) for r in results]
