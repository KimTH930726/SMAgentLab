"""Sentence-Transformers 임베딩 서비스 (싱글톤)."""
import asyncio
from functools import partial

from sentence_transformers import SentenceTransformer

from core.config import settings


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


embedding_service = EmbeddingService()
