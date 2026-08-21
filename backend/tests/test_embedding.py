"""Tests for shared/embedding.py — _chunk_token_ids/_mean_pool_normalize (embed_long 헬퍼).

긴 텍스트(VOC 이메일 등)가 임베딩 모델의 max_seq_length(128토큰)를 넘으면 embed()는
앞부분만 반영하고 나머지를 조용히 버린다(실측: 2만 토큰짜리 이메일의 99.4% 무시,
2026-08-21) — embed_long()이 청크 분할+평균 풀링으로 이를 완화한다. 여기서는 모델
로딩 없이 순수 로직(청크 분할, 벡터 풀링)만 검증한다 — 실제 모델을 통한 종단 검증은
`docker compose exec`로 별도 확인(무거운 모델 로딩을 유닛테스트 스위트에 넣지 않기 위함).

conftest.py가 sys.modules["shared"]를 MagicMock으로 치환해두므로 파일 경로 기반으로
직접 로드한다. sentence_transformers는 실제 설치된 패키지라 stub 없이도 import된다.
"""
import importlib.util as _ilu
import sys
from pathlib import Path

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
