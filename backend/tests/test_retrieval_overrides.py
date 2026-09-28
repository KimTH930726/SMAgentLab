"""관리자 검색 설정 영속화(2026-09-28) — 예전엔 임계치/top_k 오버라이드가 메모리에만 있어
재시작·재배포마다 조용히 기본값으로 돌아갔다(화면에선 저장된 것처럼 보임)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.knowledge_rag.knowledge import retrieval


@pytest.fixture(autouse=True)
def _reset_overrides():
    retrieval._runtime_thresholds.clear()
    retrieval._runtime_search_defaults.clear()
    yield
    retrieval._runtime_thresholds.clear()
    retrieval._runtime_search_defaults.clear()


def _conn():
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.executemany = AsyncMock()
    return conn


class TestPersist:
    @pytest.mark.asyncio
    async def test_writes_prefixed_known_keys_only(self):
        conn = _conn()
        with patch.object(retrieval, "get_conn", return_value=conn):
            await retrieval.persist_runtime_overrides(
                {"knowledge_min_score": 0.4, "default_top_k": 7, "unknown_key": 1.0},
            )
        rows = conn.executemany.await_args.args[1]
        assert sorted(rows) == [("retrieval_default_top_k", "7"), ("retrieval_knowledge_min_score", "0.4")]
        assert "ON CONFLICT (key) DO UPDATE" in conn.executemany.await_args.args[0]

    @pytest.mark.asyncio
    async def test_db_failure_propagates(self):
        """호출부(관리자 API)가 저장 실패 시 메모리 값을 안 바꾸도록 예외를 삼키지 않는다."""
        conn = _conn()
        conn.executemany = AsyncMock(side_effect=RuntimeError("db down"))
        with patch.object(retrieval, "get_conn", return_value=conn), pytest.raises(RuntimeError):
            await retrieval.persist_runtime_overrides({"knowledge_min_score": 0.4})


class TestLoad:
    @pytest.mark.asyncio
    async def test_restores_values_on_startup(self):
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[
            {"key": "retrieval_knowledge_min_score", "value": "0.42"},
            {"key": "retrieval_default_top_k", "value": "7"},
            {"key": "retrieval_default_w_vector", "value": "0.6"},
        ])
        await retrieval.load_runtime_overrides_from_db(conn)
        assert retrieval.get_thresholds()["knowledge_min_score"] == 0.42
        defaults = retrieval.get_search_defaults()
        assert defaults["default_top_k"] == 7 and isinstance(defaults["default_top_k"], int)
        assert defaults["default_w_vector"] == 0.6

    @pytest.mark.asyncio
    async def test_round_trip_persist_then_load(self):
        """저장한 값이 다음 기동에서 그대로 복원되는지 — 실사고의 전/후 비교."""
        store: dict[str, str] = {}
        wconn = _conn()

        async def executemany(_q, rows):
            store.update(dict(rows))
        wconn.executemany = AsyncMock(side_effect=executemany)
        with patch.object(retrieval, "get_conn", return_value=wconn):
            await retrieval.persist_runtime_overrides({"knowledge_min_score": 0.5, "default_top_k": 4})

        retrieval._runtime_thresholds.clear()  # "재시작" — 메모리 초기화
        retrieval._runtime_search_defaults.clear()
        assert retrieval.get_thresholds()["knowledge_min_score"] != 0.5

        rconn = MagicMock()
        rconn.fetch = AsyncMock(return_value=[{"key": k, "value": v} for k, v in store.items()])
        await retrieval.load_runtime_overrides_from_db(rconn)
        assert retrieval.get_thresholds()["knowledge_min_score"] == 0.5
        assert retrieval.get_search_defaults()["default_top_k"] == 4

    @pytest.mark.asyncio
    async def test_load_failure_keeps_defaults(self):
        conn = MagicMock()
        conn.fetch = AsyncMock(side_effect=RuntimeError("no table"))
        await retrieval.load_runtime_overrides_from_db(conn)  # 예외 안 남
        assert retrieval._runtime_thresholds == {}
