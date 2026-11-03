"""실제 Postgres 시험 공용 장치(11/03 표준 — "가짜 연결 테스트는 SQL이 실제로 맞는지 못 본다").

run(scenario, modules=[...]): 실제 DB에 연결해 트랜잭션을 열고, 넘긴 모듈들의 get_conn이 그 연결을 쓰게 바꾼 뒤 scenario(conn)을 돌리고,
끝나면 되돌린다(시험 데이터가 남지 않음 — 이름은 "시험_"으로 시작). DB가 없으면(DATABASE_URL 없음·연결 안 됨) 건너뛴다.
load(name, rel): conftest가 core·service를 가짜로 바꿔 두므로 실제 파일을 경로로 읽는다.
"""
import asyncio
import importlib.util
import os
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
VEC = "[" + ",".join(["0"] * 1024) + "]"


def load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(scenario, modules=()):
    asyncpg = pytest.importorskip("asyncpg")
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL 없음")

    async def go():
        conn = await asyncpg.connect(url)
        tx = conn.transaction()
        await tx.start()
        try:
            @asynccontextmanager
            async def get_conn():
                yield conn
            with ExitStack() as st:
                for m in modules:
                    st.enter_context(patch.object(m, "get_conn", get_conn))
                return await scenario(conn)
        finally:
            await tx.rollback()   # 시험 데이터를 남기지 않는다
            await conn.close()
    try:
        return asyncio.run(go())
    except OSError:
        pytest.skip("DB 연결 안 됨")


async def ns(conn, name: str, area: str = "system", parent=None) -> int:
    return await conn.fetchval("INSERT INTO ops_namespace (name, kind, area, parent_id) VALUES ($1, 'domain', $2, $3) RETURNING id",
                               name, area, parent)


async def knowledge(conn, ns_id: int, content: str = "시험 지식", status: str = "active", **cols) -> int:
    keys = ["namespace_id", "content", "embedding", "status", *cols]
    vals = [ns_id, content, VEC, status, *cols.values()]
    ph = ", ".join(f"${i + 1}" + ("::vector" if k == "embedding" else "") for i, k in enumerate(keys))
    return await conn.fetchval(f"INSERT INTO rag_knowledge ({', '.join(keys)}) VALUES ({ph}) RETURNING id", *vals)
