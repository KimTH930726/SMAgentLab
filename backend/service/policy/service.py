"""정책 서술 조각 임베딩 — 머리 붙인 임베딩 글(chunk_embed_text)과 기동 때 한 번 재임베딩(reembed_chunks_if_needed).

옛 엑셀 가져오기 파이프라인(엑셀 파싱 → LLM 분해 → 적재, 2026-09~10)은 v0 재구축 2단계(2026-10-21)에서 화면과 함께 뺐다 —
v0 정책은 scripts/load_policy_v0.py로 적재하고, 내용 변경은 정정 원장(improvement/service.py)에서 승인할 때만 새 버전이 된다.
남은 건 적재기·정정 승인·기동이 같이 쓰는 임베딩 규칙뿐이다.
"""
from __future__ import annotations

import logging

from core.database import get_conn
from shared.embedding import embedding_service
from trustkit.retrieval.chunk_header import contextual_header

logger = logging.getLogger(__name__)


# 정책 서술 조각의 임베딩 입력 = "정책명 (분류 > …): 조각"(2026-10-13). 저장 chunk_text·답 문맥은 조각 그대로.
# 막힌 일: 조각만 임베딩해("최대 20개"처럼 중앙값 28자, 정책명 포함 35%) 무엇에 대한 값인지가 벡터에 없었다 — 서술 없는 항목만
# 머리를 붙여(옛 엑셀 경로의 대체 조각) 한 색인에 두 방식이 섞여 있었다. 골든 88 검색 측정: Recall@10 88.6→92.0%, 나빠진 문항 0
# (contextual chunk header — 업계 방식, LLM 없이 결정적). 바꾸면 CHUNK_EMBED_VERSION을 올린다 → 기동 때 기존 조각을 한 번 다시 임베딩.
CHUNK_EMBED_VERSION = "header1"


def chunk_embed_text(policy_name: str, category_path, chunk: str) -> str:
    """임베딩에 넣을 글 — 이미 머리가 붙은 대체 조각("정책명 (…): 원문")은 그대로(trustkit.contextual_header)."""
    return contextual_header(policy_name, category_path, chunk)


async def reembed_chunks_if_needed() -> None:
    """기존 조각 임베딩을 지금 규칙(CHUNK_EMBED_VERSION)으로 한 번 다시 만든다 — 운영 DB 반입 뒤 첫 기동에 자동(로컬 임베딩, 토큰 0).
    끝나면 ops_data_marker에 버전을 남겨 다시 돌지 않는다. 실패해도 서비스는 그대로(옛 임베딩으로 검색) — 다음 기동에 다시."""
    try:
        async with get_conn() as conn:
            done = await conn.fetchval("SELECT value FROM ops_data_marker WHERE key = 'policy_chunk_embed'")
            if done == CHUNK_EMBED_VERSION:
                return
            rows = await conn.fetch("SELECT c.id, c.chunk_text, i.policy_name, i.category_path FROM policy_chunk c "
                                    "JOIN policy_item i ON i.id = c.policy_item_id")
        texts = [chunk_embed_text(r["policy_name"], r["category_path"], r["chunk_text"]) for r in rows]
        embs: list = []
        for i in range(0, len(texts), 64):
            embs += await embedding_service.embed_batch(texts[i:i + 64])
        async with get_conn() as conn:
            async with conn.transaction():
                # 비교 후 갱신 — 임베딩하는 몇 분 사이 편집된 조각(같은 id 제자리 UPDATE)을 옛 글 임베딩으로 덮지 않게(리뷰 10/13).
                # 편집 경로는 이미 새 규칙으로 임베딩하므로 건너뛰어도 맞다.
                await conn.executemany("UPDATE policy_chunk SET embedding = $2::vector WHERE id = $1 AND chunk_text = $3",
                                       [(r["id"], str(e), r["chunk_text"]) for r, e in zip(rows, embs)])
                await conn.execute("INSERT INTO ops_data_marker (key, value, updated_at) VALUES ('policy_chunk_embed', $1, NOW()) "
                                   "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()", CHUNK_EMBED_VERSION)
        logger.info("[정책 조각 재임베딩] %d개 → %s", len(rows), CHUNK_EMBED_VERSION)
    except Exception:
        logger.warning("정책 조각 재임베딩 실패 — 옛 임베딩으로 계속(다음 기동에 다시)", exc_info=True)
