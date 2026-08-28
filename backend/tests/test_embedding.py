"""Tests for shared/embedding.py — _chunk_token_ids/_mean_pool_normalize (embed_long 헬퍼)
+ EmbeddingService의 동시성 직렬화(락) 검증.

긴 텍스트(VOC 이메일 등)가 임베딩 모델의 max_seq_length(128토큰)를 넘으면 embed()는
앞부분만 반영하고 나머지를 조용히 버린다(실측: 2만 토큰짜리 이메일의 99.4% 무시,
2026-08-21) — embed_long()이 청크 분할+평균 풀링으로 이를 완화한다. 여기서는 모델
로딩 없이 순수 로직(청크 분할, 벡터 풀링)만 검증한다 — 실제 모델을 통한 종단 검증은
`docker compose exec`로 별도 확인(무거운 모델 로딩을 유닛테스트 스위트에 넣지 않기 위함).

동시성 테스트(TestEmbeddingServiceConcurrency)는 실사용 중 실제로 터진 버그의 회귀
방지용이다(2026-08-27) — VOC 전체 재분석 중(5건 동시 처리) "RuntimeError: Already
borrowed"로 4건이 이력에도 안 남고 조용히 유실됨. HuggingFace fast tokenizer(Rust)가
GIL을 놓고 동작해, 서로 다른 스레드에서 같은 tokenizer/model 객체를 동시에 건드리면
발생한다 — self._lock(asyncio.Lock)으로 model/tokenizer 접근 전체를 직렬화해 해결.

conftest.py가 sys.modules["shared"]를 MagicMock으로 치환해두므로 파일 경로 기반으로
직접 로드한다. sentence_transformers는 실제 설치된 패키지라 stub 없이도 import된다.
"""
import asyncio
import importlib.util as _ilu
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

_backend_dir = Path(__file__).resolve().parent.parent
_spec = _ilu.spec_from_file_location(
    "shared_embedding_under_test", str(_backend_dir / "shared" / "embedding.py"),
)
embedding = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = embedding
_spec.loader.exec_module(embedding)


class TestChunkTokenIds:
    def test_short_input_returns_single_chunk(self):
        chunks = embedding._chunk_token_ids(list(range(50)), chunk_size=100, max_chunks=5)
        assert chunks == [list(range(50))]

    def test_splits_into_non_overlapping_chunks(self):
        chunks = embedding._chunk_token_ids(list(range(250)), chunk_size=100, max_chunks=5)
        assert len(chunks) == 3
        assert chunks[0] == list(range(0, 100))
        assert chunks[1] == list(range(100, 200))
        assert chunks[2] == list(range(200, 250))

    def test_caps_at_max_chunks(self):
        # 2만 토큰짜리 이메일 같은 극단적 케이스 — max_chunks를 넘는 나머지는 버려진다
        chunks = embedding._chunk_token_ids(list(range(20000)), chunk_size=100, max_chunks=5)
        assert len(chunks) == 5
        assert sum(len(c) for c in chunks) == 500

    def test_empty_input(self):
        assert embedding._chunk_token_ids([], chunk_size=100, max_chunks=5) == []


class TestMeanPoolNormalize:
    def test_single_vector_returns_normalized_copy(self):
        result = embedding._mean_pool_normalize([[3.0, 4.0]])
        assert result == pytest.approx([0.6, 0.8])

    def test_averages_multiple_vectors_and_renormalizes(self):
        # 이미 정규화된 두 벡터의 평균은 노름이 1보다 작아지므로 재정규화가 필요하다
        result = embedding._mean_pool_normalize([[1.0, 0.0], [0.0, 1.0]])
        norm = sum(x * x for x in result) ** 0.5
        assert norm == pytest.approx(1.0)
        assert result == pytest.approx([0.7071, 0.7071], abs=1e-3)

    def test_identical_vectors_pool_to_same_direction(self):
        v = [0.6, 0.8]
        result = embedding._mean_pool_normalize([v, v, v])
        assert result == pytest.approx(v)


def _make_tracking_fake_model(*, sleep_seconds: float = 0.05):
    """호출이 겹치면 max_concurrent > 1이 되는 가짜 모델 — 락이 없으면 잡아낸다.

    run_in_executor로 진짜 스레드풀에서 실행되므로 time.sleep()이 그 스레드를
    실제로 블로킹한다 — asyncio 협조적 스케줄링과 무관하게 진짜 경합을 재현한다.
    """
    state = {"concurrent": 0, "max_concurrent": 0}

    def fake_encode(*args, **kwargs):
        state["concurrent"] += 1
        state["max_concurrent"] = max(state["max_concurrent"], state["concurrent"])
        time.sleep(sleep_seconds)
        state["concurrent"] -= 1
        texts = args[0] if args else kwargs.get("sentences")
        if isinstance(texts, str):
            return np.array([0.6, 0.8], dtype=np.float32)
        return np.array([[0.6, 0.8]] * len(texts), dtype=np.float32)

    model = MagicMock()
    model.encode = fake_encode
    return model, state


class TestEmbeddingServiceConcurrency:
    """동시 임베딩 호출이 서로 겹치지 않고 직렬화되는지 — "Already borrowed" 회귀 방지.

    싱글톤(_instance)이 테스트 세션 내내 살아있으면 asyncio.Lock이 먼저 실행된
    테스트의 이벤트루프에 바인딩된 채로 남아, pytest-asyncio가 테스트마다 새
    루프를 쓸 때 "bound to a different event loop"로 깨진다(실제로 겪음) —
    프로덕션은 앱 생애주기 동안 루프가 하나뿐이라 해당 없는, 순수 테스트 격리
    문제라 매 테스트 전 싱글톤을 리셋해 새 락이 그 테스트의 루프에 바인딩되게 한다.
    """

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        embedding.EmbeddingService._instance = None
        embedding.EmbeddingService._model = None
        yield
        embedding.EmbeddingService._instance = None
        embedding.EmbeddingService._model = None

    @pytest.mark.asyncio
    async def test_concurrent_embed_calls_are_serialized(self):
        service = embedding.EmbeddingService()
        service._model, state = _make_tracking_fake_model()

        await asyncio.gather(*(service.embed(f"text-{i}") for i in range(5)))

        assert state["max_concurrent"] == 1

    @pytest.mark.asyncio
    async def test_embed_and_embed_long_share_the_same_lock(self):
        """실제 버그 재현 조건 — embed()(executor 스레드에서 model.encode 호출)와
        embed_long()(메인 이벤트루프에서 tokenizer.encode 호출 후 model.encode)가
        동시에 실행돼도 겹치면 안 된다."""
        service = embedding.EmbeddingService()
        service._model, state = _make_tracking_fake_model()
        # embed_long()의 토큰화 경로도 같은 상태를 공유하도록 tokenizer를 연결
        service._model.tokenizer = MagicMock()
        service._model.tokenizer.encode = MagicMock(side_effect=lambda t, **kw: list(range(50)))

        await asyncio.gather(
            service.embed("short text"),
            service.embed_long("another text"),
            service.embed("short text 2"),
        )

        assert state["max_concurrent"] == 1
