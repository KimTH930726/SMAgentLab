"""키워드 검색 저장 칸(#109, 11/03 성능 리뷰 #1) — 실제 DB에서 "저장 칸 = 그 자리 계산"이 모든 행에서 같은지(불변식).

저장 칸은 함수 규칙(policy_strip_ko 조사 목록)을 바꿔도 저절로 다시 계산되지 않는다. 규칙을 바꾸고 다시 채우는 걸 잊으면 검색이 조용히
옛 규칙으로 돈다 — 그래서 사례가 아니라 전 행 일치를 본다(읽기만). DB가 없으면 건너뜀.
"""
import asyncio
import os

import pytest


def _run(sql):
    asyncpg = pytest.importorskip("asyncpg")
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL 없음")

    async def go():
        conn = await asyncpg.connect(url)
        try:
            return await conn.fetchval(sql)
        finally:
            await conn.close()
    try:
        return asyncio.run(go())
    except OSError:
        pytest.skip("DB 연결 안 됨")


def test_stored_tsv_matches_function_for_every_row():
    if not _run("SELECT 1 FROM information_schema.columns WHERE table_name = 'rag_knowledge' AND column_name = 'content_tsv'"):
        pytest.skip("#109 전 DB")
    assert _run("SELECT count(*) FROM rag_knowledge WHERE content_tsv IS DISTINCT FROM to_tsvector('simple', policy_strip_ko(content))") == 0

