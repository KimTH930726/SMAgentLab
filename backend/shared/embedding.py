"""Sentence-Transformers 임베딩 서비스 (싱글톤)."""
import asyncio
from functools import partial

import numpy as np
from sentence_transformers import SentenceTransformer

from core.config import settings

# paraphrase-multilingual-mpnet-base-v2의 max_seq_length는 128토큰이다(모델 자체
# 설정, 우리 코드가 자르는 게 아님 — sentence-transformers 이슈 #1266 참고). VOC
# 이메일은 전달/회신 체인이 길어 실측으로 4만자(2만 토큰)짜리도 나오는데, 그런
# 경우 전체의 0.6%만 임베딩에 반영되고 나머지는 완전히 무시된다(실측 확인,
# 2026-08-21) — 그마저 128토큰 안에 남는 건 본문 서두의 상투 문구뿐이고 실제
# 내용은 뒤에 있는 경우가 많아, 표 형식 지식 문서 오탐의 원인 중 하나로 추정됨.
_CHUNK_TOKENS = 100  # max_seq_length(128)보다 약간 작게 잡아 특수토큰 여유를 둠
_MAX_CHUNKS = 5  # 청크당 임베딩 1회(로컬 CPU 추론)이므로 이메일당 최대 5회로 제한


def _chunk_token_ids(token_ids: list[int], chunk_size: int, max_chunks: int) -> list[list[int]]:
    """토큰 ID 리스트를 chunk_size 단위로 겹치지 않게 잘라 최대 max_chunks개까지 반환."""
    chunks = []
    for i in range(0, len(token_ids), chunk_size):
        if len(chunks) >= max_chunks:
            break
        chunks.append(token_ids[i:i + chunk_size])
    return chunks


def _mean_pool_normalize(vectors: list[list[float]]) -> list[float]:
    """여러 청크 임베딩을 평균낸 뒤 다시 단위벡터로 정규화한다.

    각 청크 벡터는 이미 정규화돼 있어(normalize_embeddings=True) 코사인 유사도
    계산 기준이 맞지만, 평균을 내면 노름이 1보다 작아지므로 다시 정규화해야
    pgvector의 코사인 거리(<=>) 계산과 기존 임베딩들의 스케일이 어긋나지 않는다.
    """
    arr = np.mean(np.array(vectors, dtype=np.float32), axis=0)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.tolist()


class EmbeddingService:
    _instance: "EmbeddingService | None" = None
    _model: SentenceTransformer | None = None

    def __new__(cls) -> "EmbeddingService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self) -> None:
        if self._model is None:
            print(f"[Embedding] Loading model: {settings.embedding_model}")
            self._model = SentenceTransformer(settings.embedding_model)
            print("[Embedding] Model loaded.")

    async def embed(self, text: str) -> list[float]:
        assert self._model is not None, "EmbeddingService.load() must be called first"
        vec = await asyncio.get_running_loop().run_in_executor(
            None, partial(self._model.encode, text, normalize_embeddings=True)
        )
        return vec.tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        assert self._model is not None
        vecs = await asyncio.get_running_loop().run_in_executor(
            None, partial(self._model.encode, texts, normalize_embeddings=True)
        )
        return [v.tolist() for v in vecs]

    async def embed_long(self, text: str) -> list[float]:
        """모델의 max_seq_length(128토큰)를 넘는 긴 텍스트(VOC 이메일 등)를 위한 임베딩.

        일반 embed()는 앞부분 128토큰만 반영하고 나머지는 조용히 버려진다 — 긴
        이메일일수록 실제 내용을 놓칠 위험이 커진다(실측: 2만 토큰짜리 이메일의
        99.4%가 무시됨). 여기서는 텍스트를 여러 청크로 나눠 각각 임베딩한 뒤
        평균으로 합쳐 하나의 벡터를 만든다 — 호출부(search_knowledge 등)는 벡터
        하나만 받는 기존 인터페이스를 그대로 쓰므로 DB 조회 횟수는 늘지 않는다
        (임베딩 자체는 청크 수만큼 늘지만 짧은 텍스트라 로컬 CPU에서도 빠름).
        토큰 128개 이내인 짧은 텍스트는 청크가 1개뿐이라 embed()와 결과가 같다.
        """
        assert self._model is not None, "EmbeddingService.load() must be called first"
        tokenizer = self._model.tokenizer
        token_ids = tokenizer.encode(text, add_special_tokens=False)
        chunks = _chunk_token_ids(token_ids, _CHUNK_TOKENS, _MAX_CHUNKS)
        if len(chunks) <= 1:
            return await self.embed(text)
        chunk_texts = [tokenizer.decode(c, skip_special_tokens=True) for c in chunks]
        vectors = await self.embed_batch(chunk_texts)
        return _mean_pool_normalize(vectors)


embedding_service = EmbeddingService()
