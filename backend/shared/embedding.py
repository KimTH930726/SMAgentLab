"""Sentence-Transformers 임베딩 서비스 (싱글톤)."""
import asyncio
from functools import partial

import numpy as np
from sentence_transformers import SentenceTransformer

from core.config import settings

# v2.72(2026-09-11) 임베딩 모델을 paraphrase-multilingual-mpnet-base-v2(max_seq_length
# 128토큰)에서 nlpai-lab/KURE-v1(8192토큰)로 교체 — 89문항 골든셋 실측(embedding-
# reranker-upgrade-plan.md)에서 hit@10 55.1%→82.0%로 전 유형 개선 확인, 공개 벤치마크
# (MTEB-ko-retrieval) 1위 순위와도 일치해 확정. 예전엔 VOC 이메일(전달 체인 포함 시
# 실측 2만 토큰까지)의 0.6%만 반영되던 문제(2026-08-21 실측)가 8192토큰 컨텍스트로
# 근본적으로 완화됨 — 그래도 그 이상(전달 체인 아주 긴 극단 케이스)을 위해 청킹
# 로직 자체는 남겨두되 청크 크기만 새 한도에 맞춰 키운다.
_CHUNK_TOKENS = 8000  # max_seq_length(8192)보다 약간 작게 잡아 특수토큰 여유를 둠
# 청크당 임베딩 1회(로컬 CPU 추론)이므로 최대 4회(32000토큰)까지 허용 — 8192토큰
# 한도 안에서 이미 대부분 텍스트가 1청크로 끝나(embed_long()과 embed()가 동일 결과),
# 여러 청크로 쪼개지는 경우는 사실상 없어짐.
_MAX_CHUNKS = 4


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
    _lock: asyncio.Lock | None = None

    def __new__(cls) -> "EmbeddingService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._lock = asyncio.Lock()
        return cls._instance

    def load(self) -> None:
        if self._model is None:
            print(f"[Embedding] Loading model: {settings.embedding_model}")
            self._model = SentenceTransformer(settings.embedding_model)
            print("[Embedding] Model loaded.")

    async def embed(self, text: str) -> list[float]:
        assert self._model is not None, "EmbeddingService.load() must be called first"
        async with self._lock:
            vec = await asyncio.get_running_loop().run_in_executor(
                None, partial(self._model.encode, text, normalize_embeddings=True)
            )
        return vec.tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        assert self._model is not None
        async with self._lock:
            vecs = await asyncio.get_running_loop().run_in_executor(
                None, partial(self._model.encode, texts, normalize_embeddings=True)
            )
        return [v.tolist() for v in vecs]

    async def embed_long(self, text: str) -> list[float]:
        """모델의 max_seq_length(KURE-v1 기준 8192토큰)를 넘는 긴 텍스트(VOC 이메일 등)를
        위한 임베딩.

        일반 embed()는 앞부분 max_seq_length만 반영하고 나머지는 조용히 버려진다 —
        8192토큰(대략 한글 기준 수만 자)을 넘는 극단적으로 긴 텍스트에서만 영향이
        있고(구 mpnet 모델 128토큰 시절엔 실측 2만 토큰짜리 이메일의 99.4%가
        무시되는 문제였으나 KURE-v1 전환 후 크게 완화), 여기서는 그 이상의 경우를
        위해 텍스트를 여러 청크로 나눠 각각 임베딩한 뒤 평균으로 합쳐 하나의 벡터를
        만든다 — 호출부(search_knowledge 등)는 벡터 하나만 받는 기존 인터페이스를
        그대로 쓰므로 DB 조회 횟수는 늘지 않는다. 토큰 수가 한도 이내인 대부분의
        텍스트는 청크가 1개뿐이라 embed()와 결과가 같다.

        전체를 self._lock 하나로 감싼다(tokenizer.encode/decode 포함) — HuggingFace
        fast tokenizer(Rust)는 GIL을 놓고 동작해, 이 메서드가 메인 이벤트루프
        스레드에서 tokenizer.encode()를 부르는 동안 다른 임베딩 호출이 executor
        스레드에서 같은 tokenizer 객체의 model.encode()를 동시에 건드리면
        "RuntimeError: Already borrowed"로 죽는 게 실사용 중 확인됨(2026-08-27,
        VOC 전체 재분석 중 234건 중 4건이 이 경합으로 이력에도 안 남고 조용히
        유실됨). embed()/embed_batch()를 내부에서 또 부르면 락 재획득으로 교착
        상태가 되므로, 여기서는 run_in_executor를 직접 호출한다.
        """
        assert self._model is not None, "EmbeddingService.load() must be called first"
        async with self._lock:
            tokenizer = self._model.tokenizer
            token_ids = tokenizer.encode(text, add_special_tokens=False)
            chunks = _chunk_token_ids(token_ids, _CHUNK_TOKENS, _MAX_CHUNKS)
            if len(chunks) <= 1:
                vec = await asyncio.get_running_loop().run_in_executor(
                    None, partial(self._model.encode, text, normalize_embeddings=True)
                )
                return vec.tolist()
            chunk_texts = [tokenizer.decode(c, skip_special_tokens=True) for c in chunks]
            vecs = await asyncio.get_running_loop().run_in_executor(
                None, partial(self._model.encode, chunk_texts, normalize_embeddings=True)
            )
        return _mean_pool_normalize([v.tolist() for v in vecs])


embedding_service = EmbeddingService()
