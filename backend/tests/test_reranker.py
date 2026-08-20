"""Tests for shared/reranker.py — score()/is_available() 안전한 fallback 검증.

check_relevance()의 VOC 관련성 게이트가 이 score()를 신뢰하므로, 모델이 로드되지
않았을 때 "관련 있다고 착각"시키는 값을 반환하지 않는지가 특히 중요하다.

conftest.py가 sys.modules["shared.reranker"]를 MagicMock으로 치환해두므로(다른
agent들이 이 모듈을 격리된 상태로 임포트하도록), json_utils 테스트와 동일하게
파일 경로 기반으로 직접 로드해 실제 코드를 검증한다.
"""
import importlib.util as _ilu
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_backend_dir = Path(__file__).resolve().parent.parent
_spec = _ilu.spec_from_file_location(
    "shared_reranker_under_test", str(_backend_dir / "shared" / "reranker.py"),
)
reranker = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = reranker
_spec.loader.exec_module(reranker)


@dataclass
class _FakeResult:
    id: int
    content: str


@pytest.fixture(autouse=True)
def _reset_model_state():
    original_model, original_loaded = reranker._model, reranker._loaded
    reranker._model, reranker._loaded = None, False
    yield
    reranker._model, reranker._loaded = original_model, original_loaded


class TestIsAvailable:
    def test_false_when_model_not_loaded(self):
        assert reranker.is_available() is False

    def test_true_when_model_set(self):
        reranker._model = object()
        assert reranker.is_available() is True


class TestScore:
    @pytest.mark.asyncio
    async def test_returns_zero_scores_when_model_unavailable(self):
        results = [_FakeResult(1, "a"), _FakeResult(2, "b")]
        scored = await reranker.score("query", results)
        assert scored == [(results[0], 0.0), (results[1], 0.0)]

    @pytest.mark.asyncio
    async def test_empty_results_returns_empty(self):
        assert await reranker.score("query", []) == []


class TestRerank:
    @pytest.mark.asyncio
    async def test_returns_original_slice_when_model_unavailable(self):
        results = [_FakeResult(i, str(i)) for i in range(5)]
        top = await reranker.rerank("query", results, top_k=2)
        assert top == results[:2]

    @pytest.mark.asyncio
    async def test_skips_scoring_when_already_within_top_k(self):
        results = [_FakeResult(1, "a")]
        top = await reranker.rerank("query", results, top_k=3)
        assert top == results
