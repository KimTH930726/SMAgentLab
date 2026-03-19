"""원격 대상 DB 연결 관리 — PostgreSQL / MySQL / SQLite 지원."""
import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_SUPPORTED = {"postgresql", "mysql", "sqlite"}


def _is_docker() -> bool:
    return os.path.exists("/.dockerenv")


def _resolve_host(host: str) -> str:
    """Docker 컨테이너 내부에서 localhost → host.docker.internal 변환."""
    if _is_docker() and host in ("localhost", "127.0.0.1"):
        return "host.docker.internal"
    return host


class TargetDBManager:
    """네임스페이스별 원격 DB 연결 + 스키마 탐색 + 쿼리 실행."""

    def __init__(
        self,
        db_type: str,
        host: str,
        port: int,
        db_name: str,
        username: str,
        password: str,
    ):
        self.db_type = db_type.lower()
        self.host = _resolve_host(host)
        self.port = port
        self.db_name = db_name
        self.username = username
        self.password = password
        self._conn: Any = None

    # ── 연결 ────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        if self.db_type == "postgresql":
            import asyncpg
            self._conn = await asyncpg.connect(
                host=self.host, port=self.port, database=self.db_name,
                user=self.username, password=self.password, timeout=10,
            )
        elif self.db_type == "mysql":
            import aiomysql
            self._conn = await aiomysql.connect(
                host=self.host, port=self.port, db=self.db_name,
                user=self.username, password=self.password,
                charset="utf8mb4", connect_timeout=10,
            )
        elif self.db_type == "sqlite":
            import aiosqlite
            self._conn = await aiosqlite.connect(self.db_name)
        else:
            raise ValueError(f"지원하지 않는 DB 타입: {self.db_type}")

    async def close(self) -> None:
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def test_connection(self) -> bool:
        try:
            await self.connect()
            await self.close()
            return True
        except Exception as e:
            logger.warning("DB 연결 테스트 실패: %s", e)
            return False

    # ── 스키마 탐색 ─────────────────────────────────────────────────────────

    async def get_tables(self) -> list[dict]:
        """테이블 목록 + 컬럼 정보 반환.

        Returns:
            [{"table_name": str, "columns": [{"name", "type", "is_pk", "fk_reference"}]}]
        """
        await self.connect()
        try:
            if self.db_type == "postgresql":
                return await self._get_tables_pg()
            elif self.db_type == "mysql":
                return await self._get_tables_mysql()
            elif self.db_type == "sqlite":
                return await self._get_tables_sqlite()
        finally:
            await self.close()
        return []

    async def _get_tables_pg(self) -> list[dict]:
        # 테이블 목록
        rows = await self._conn.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        tables = []
        for row in rows:
            tname = row["table_name"]
            # 컬럼 정보
            cols = await self._conn.fetch("""
                SELECT c.column_name, c.data_type,
                       CASE WHEN pk.column_name IS NOT NULL THEN TRUE ELSE FALSE END AS is_pk
                FROM information_schema.columns c
                LEFT JOIN (
                    SELECT ku.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage ku
                        ON tc.constraint_name = ku.constraint_name
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_name = $1 AND tc.table_schema = 'public'
                ) pk ON c.column_name = pk.column_name
                WHERE c.table_name = $1 AND c.table_schema = 'public'
                ORDER BY c.ordinal_position
            """, tname)
            # FK 정보
            fk_rows = await self._conn.fetch("""
                SELECT kcu.column_name, ccu.table_name AS ref_table, ccu.column_name AS ref_col
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = $1
                  AND tc.table_schema = 'public'
            """, tname)
            fk_map = {r["column_name"]: f"{r['ref_table']}.{r['ref_col']}" for r in fk_rows}

            tables.append({
                "table_name": tname,
                "columns": [
                    {
                        "name": c["column_name"],
                        "type": c["data_type"],
                        "is_pk": c["is_pk"],
                        "fk_reference": fk_map.get(c["column_name"]),
                    }
                    for c in cols
                ],
            })
        return tables

    async def _get_tables_mysql(self) -> list[dict]:
        async with self._conn.cursor() as cur:
            await cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
            table_rows = await cur.fetchall()
            tables = []
            for (tname,) in table_rows:
                await cur.execute(
                    "SELECT column_name, column_type, column_key "
                    "FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = %s "
                    "ORDER BY ordinal_position",
                    (tname,),
                )
                col_rows = await cur.fetchall()
                # FK
                await cur.execute(
                    "SELECT column_name, referenced_table_name, referenced_column_name "
                    "FROM information_schema.key_column_usage "
                    "WHERE table_schema = DATABASE() AND table_name = %s "
                    "AND referenced_table_name IS NOT NULL",
                    (tname,),
                )
                fk_rows = await cur.fetchall()
                fk_map = {r[0]: f"{r[1]}.{r[2]}" for r in fk_rows}
                tables.append({
                    "table_name": tname,
                    "columns": [
                        {
                            "name": r[0],
                            "type": r[1],
                            "is_pk": r[2] == "PRI",
                            "fk_reference": fk_map.get(r[0]),
                        }
                        for r in col_rows
                    ],
                })
        return tables

    async def _get_tables_sqlite(self) -> list[dict]:
        async with self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            table_rows = await cur.fetchall()
        tables = []
        for (tname,) in table_rows:
            async with self._conn.execute(f"PRAGMA table_info({tname})") as cur:
                col_rows = await cur.fetchall()
            tables.append({
                "table_name": tname,
                "columns": [
                    {
                        "name": r[1],
                        "type": r[2],
                        "is_pk": bool(r[5]),
                        "fk_reference": None,
                    }
                    for r in col_rows
                ],
            })
        return tables

    # ── 쿼리 실행 ────────────────────────────────────────────────────────────

    async def execute_query(
        self,
        sql: str,
        timeout_sec: int = 30,
        max_rows: int = 1000,
    ) -> dict:
        """SELECT 쿼리 실행 후 결과 반환.

        Returns:
            {"columns": list[str], "rows": list[dict], "row_count": int, "truncated": bool}
        """
        await self.connect()
        try:
            return await asyncio.wait_for(
                self._execute_query_inner(sql, max_rows),
                timeout=timeout_sec,
            )
        finally:
            await self.close()

    async def _execute_query_inner(self, sql: str, max_rows: int) -> dict:
        if self.db_type == "postgresql":
            rows = await self._conn.fetch(sql)
            if not rows:
                return {"columns": [], "rows": [], "row_count": 0, "truncated": False}
            columns = list(rows[0].keys())
            data = [dict(r) for r in rows]
        elif self.db_type == "mysql":
            async with self._conn.cursor() as cur:
                await cur.execute(sql)
                columns = [d[0] for d in cur.description] if cur.description else []
                data = [dict(zip(columns, r)) for r in await cur.fetchall()]
        elif self.db_type == "sqlite":
            async with self._conn.execute(sql) as cur:
                columns = [d[0] for d in cur.description] if cur.description else []
                data = [dict(zip(columns, r)) for r in await cur.fetchall()]
        else:
            raise ValueError(f"지원하지 않는 DB 타입: {self.db_type}")

        truncated = len(data) > max_rows
        rows_out = data[:max_rows]
        # 직렬화 가능하도록 변환
        for r in rows_out:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
                elif not isinstance(v, (str, int, float, bool, type(None))):
                    r[k] = str(v)
        return {
            "columns": columns,
            "rows": rows_out,
            "row_count": len(rows_out),
            "truncated": truncated,
        }
