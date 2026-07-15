"""원격 대상 DB 연결 관리 — Dialect 패턴 (PostgreSQL / MySQL / SQLite / Oracle)."""
import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

_SUPPORTED = {"postgresql", "mysql", "sqlite", "oracle"}
_MAX_IDLE_SECONDS = 300  # 이 시간 이상 재사용 안 된 연결은 재연결(끊긴 연결을 오래 붙들지 않기 위함)


def _is_docker() -> bool:
    return os.path.exists("/.dockerenv")


def _resolve_host(host: str) -> str:
    """Docker 컨테이너 내부에서 localhost → host.docker.internal 변환."""
    if _is_docker() and host in ("localhost", "127.0.0.1"):
        return "host.docker.internal"
    return host


# ── Dialect 추상 클래스 ────────────────────────────────────────────────────

class BaseDialect(ABC):
    """DB별 연결·스키마 탐색·쿼리 실행 인터페이스."""

    @abstractmethod
    async def connect(self, host: str, port: int, db_name: str,
                      username: str, password: str, schema: str | None) -> Any:
        ...

    @abstractmethod
    async def close(self, conn: Any) -> None:
        ...

    @abstractmethod
    async def get_schemas(self, conn: Any) -> list[str]:
        """사용 가능한 스키마 목록 반환."""
        ...

    @abstractmethod
    async def get_tables(self, conn: Any, schema: str | None) -> list[dict]:
        ...

    async def get_table_summary(self, conn: Any, schema: str | None) -> list[dict]:
        """테이블 이름 + 컬럼 수만 빠르게 반환 (기본: get_tables fallback)."""
        tables = await self.get_tables(conn, schema)
        return [{"table": t["table_name"], "column_count": len(t.get("columns", []))} for t in tables]

    @abstractmethod
    async def execute_query(self, conn: Any, sql: str, max_rows: int) -> dict:
        ...


# ── PostgreSQL ─────────────────────────────────────────────────────────────

class PgDialect(BaseDialect):
    async def connect(self, host, port, db_name, username, password, schema):
        import asyncpg
        return await asyncpg.connect(
            host=host, port=port, database=db_name,
            user=username, password=password, timeout=10,
        )

    async def close(self, conn):
        await conn.close()

    async def get_schemas(self, conn) -> list[str]:
        rows = await conn.fetch("""
            SELECT schema_name FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
            ORDER BY schema_name
        """)
        return [r["schema_name"] for r in rows]

    async def get_table_summary(self, conn, schema) -> list[dict]:
        schema = schema or "public"
        rows = await conn.fetch("""
            SELECT t.table_name AS table,
                   COUNT(c.column_name) AS column_count
            FROM information_schema.tables t
            LEFT JOIN information_schema.columns c
                ON t.table_name = c.table_name AND t.table_schema = c.table_schema
            WHERE t.table_schema = $1 AND t.table_type = 'BASE TABLE'
            GROUP BY t.table_name ORDER BY t.table_name
        """, schema)
        return [dict(r) for r in rows]

    async def get_tables(self, conn, schema) -> list[dict]:
        # 테이블별로 컬럼/PK/FK를 따로 조회하면 테이블 N개 기준 1+2N번 왕복이 됨 —
        # 스키마 전체 컬럼/FK를 각각 단일 쿼리로 가져와 Python에서 테이블별로 묶는다.
        schema = schema or "public"
        table_rows = await conn.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = $1 AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """, schema)
        table_names = [r["table_name"] for r in table_rows]

        col_rows = await conn.fetch("""
            SELECT c.table_name, c.column_name, c.data_type, c.ordinal_position,
                   CASE WHEN pk.column_name IS NOT NULL THEN TRUE ELSE FALSE END AS is_pk
            FROM information_schema.columns c
            LEFT JOIN (
                SELECT ku.table_name, ku.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage ku
                    ON tc.constraint_name = ku.constraint_name AND tc.table_schema = ku.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = $1
            ) pk ON c.table_name = pk.table_name AND c.column_name = pk.column_name
            WHERE c.table_schema = $1
            ORDER BY c.table_name, c.ordinal_position
        """, schema)

        fk_rows = await conn.fetch("""
            SELECT tc.table_name, kcu.column_name, ccu.table_name AS ref_table, ccu.column_name AS ref_col
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = $1
        """, schema)

        fk_map: dict[tuple[str, str], str] = {
            (r["table_name"], r["column_name"]): f"{r['ref_table']}.{r['ref_col']}" for r in fk_rows
        }
        cols_by_table: dict[str, list[dict]] = {t: [] for t in table_names}
        for c in col_rows:
            tname = c["table_name"]
            if tname not in cols_by_table:
                continue
            cols_by_table[tname].append({
                "name": c["column_name"],
                "type": c["data_type"],
                "is_pk": c["is_pk"],
                "fk_reference": fk_map.get((tname, c["column_name"])),
            })

        return [{"table_name": t, "columns": cols_by_table[t]} for t in table_names]

    async def execute_query(self, conn, sql, max_rows):
        rows = await conn.fetch(sql)
        if not rows:
            return {"columns": [], "rows": [], "row_count": 0, "truncated": False}
        columns = list(rows[0].keys())
        data = [dict(r) for r in rows]
        return _format_result(columns, data, max_rows)


# ── MySQL ──────────────────────────────────────────────────────────────────

class MysqlDialect(BaseDialect):
    async def connect(self, host, port, db_name, username, password, schema):
        import aiomysql
        return await aiomysql.connect(
            host=host, port=port, db=db_name,
            user=username, password=password,
            charset="utf8mb4", connect_timeout=10,
        )

    async def close(self, conn):
        conn.close()

    async def get_schemas(self, conn) -> list[str]:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys') "
                "ORDER BY schema_name"
            )
            return [r[0] for r in await cur.fetchall()]

    async def get_table_summary(self, conn, schema) -> list[dict]:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT t.table_name AS `table`, COUNT(c.column_name) AS column_count "
                "FROM information_schema.tables t "
                "LEFT JOIN information_schema.columns c "
                "  ON t.table_name = c.table_name AND t.table_schema = c.table_schema "
                "WHERE t.table_schema = DATABASE() AND t.table_type = 'BASE TABLE' "
                "GROUP BY t.table_name ORDER BY t.table_name"
            )
            return [{"table": r[0], "column_count": r[1]} for r in await cur.fetchall()]

    async def get_tables(self, conn, schema) -> list[dict]:
        # MySQL: schema = database name (DATABASE() 사용).
        # 테이블별 컬럼/FK 조회를 각각 반복하면 테이블 N개 기준 1+2N번 왕복이 됨 —
        # 스키마 전체 컬럼/FK를 단일 쿼리로 가져와 Python에서 테이블별로 묶는다.
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
            table_names = [r[0] for r in await cur.fetchall()]

            await cur.execute(
                "SELECT table_name, column_name, column_type, column_key "
                "FROM information_schema.columns "
                "WHERE table_schema = DATABASE() "
                "ORDER BY table_name, ordinal_position"
            )
            col_rows = await cur.fetchall()

            await cur.execute(
                "SELECT table_name, column_name, referenced_table_name, referenced_column_name "
                "FROM information_schema.key_column_usage "
                "WHERE table_schema = DATABASE() AND referenced_table_name IS NOT NULL"
            )
            fk_rows = await cur.fetchall()

        fk_map = {(r[0], r[1]): f"{r[2]}.{r[3]}" for r in fk_rows}
        cols_by_table: dict[str, list[dict]] = {t: [] for t in table_names}
        for tname, col_name, col_type, col_key in col_rows:
            if tname not in cols_by_table:
                continue
            cols_by_table[tname].append({
                "name": col_name,
                "type": col_type,
                "is_pk": col_key == "PRI",
                "fk_reference": fk_map.get((tname, col_name)),
            })

        return [{"table_name": t, "columns": cols_by_table[t]} for t in table_names]

    async def execute_query(self, conn, sql, max_rows):
        async with conn.cursor() as cur:
            await cur.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            data = [dict(zip(columns, r)) for r in await cur.fetchall()]
        return _format_result(columns, data, max_rows)


# ── SQLite ─────────────────────────────────────────────────────────────────

class SqliteDialect(BaseDialect):
    async def connect(self, host, port, db_name, username, password, schema):
        import aiosqlite
        return await aiosqlite.connect(db_name)

    async def close(self, conn):
        await conn.close()

    async def get_schemas(self, conn) -> list[str]:
        return ["main"]

    async def get_table_summary(self, conn, schema) -> list[dict]:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            table_rows = await cur.fetchall()
        result = []
        for (tname,) in table_rows:
            async with conn.execute(f"PRAGMA table_info({tname})") as cur2:
                cols = await cur2.fetchall()
            result.append({"table": tname, "column_count": len(cols)})
        return result

    async def get_tables(self, conn, schema) -> list[dict]:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            table_rows = await cur.fetchall()
        tables = []
        for (tname,) in table_rows:
            async with conn.execute(f"PRAGMA table_info({tname})") as cur:
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

    async def execute_query(self, conn, sql, max_rows):
        async with conn.execute(sql) as cur:
            columns = [d[0] for d in cur.description] if cur.description else []
            data = [dict(zip(columns, r)) for r in await cur.fetchall()]
        return _format_result(columns, data, max_rows)


# ── Oracle ─────────────────────────────────────────────────────────────────

class OracleDialect(BaseDialect):
    async def connect(self, host, port, db_name, username, password, schema):
        import oracledb
        dsn = oracledb.makedsn(host, port, service_name=db_name)
        conn = await asyncio.to_thread(
            oracledb.connect, user=username, password=password, dsn=dsn,
        )
        return conn

    async def close(self, conn):
        await asyncio.to_thread(conn.close)

    async def get_schemas(self, conn) -> list[str]:
        def _fetch():
            cur = conn.cursor()
            cur.execute(
                "SELECT username FROM all_users "
                "WHERE username NOT IN ('SYS','SYSTEM','DBSNMP','OUTLN','XDB','WMSYS','CTXSYS','MDSYS','ORDDATA','ORDSYS') "
                "ORDER BY username"
            )
            result = [r[0] for r in cur.fetchall()]
            cur.close()
            return result
        return await asyncio.to_thread(_fetch)

    async def get_table_summary(self, conn, schema) -> list[dict]:
        owner = (schema or conn.username).upper()
        def _fetch():
            cur = conn.cursor()
            cur.execute(
                "SELECT t.table_name, COUNT(c.column_name) "
                "FROM all_tables t "
                "LEFT JOIN all_tab_columns c ON t.table_name = c.table_name AND t.owner = c.owner "
                "WHERE t.owner = :o "
                "GROUP BY t.table_name ORDER BY t.table_name",
                {"o": owner},
            )
            result = [{"table": r[0], "column_count": r[1]} for r in cur.fetchall()]
            cur.close()
            return result
        return await asyncio.to_thread(_fetch)

    async def get_tables(self, conn, schema) -> list[dict]:
        # 테이블별 컬럼/FK 조회를 각각 반복하면 테이블 N개 기준 1+2N번 왕복이 됨 —
        # owner(스키마) 전체 컬럼/FK를 각각 단일 쿼리로 가져와 Python에서 테이블별로 묶는다.
        owner = (schema or conn.username).upper()

        def _fetch():
            cur = conn.cursor()
            cur.execute(
                "SELECT table_name FROM all_tables WHERE owner = :o ORDER BY table_name",
                {"o": owner},
            )
            table_names = [r[0] for r in cur.fetchall()]

            cur.execute("""
                SELECT c.table_name, c.column_name, c.data_type,
                       CASE WHEN pk.column_name IS NOT NULL THEN 1 ELSE 0 END AS is_pk
                FROM all_tab_columns c
                LEFT JOIN (
                    SELECT acc.table_name, acc.column_name
                    FROM all_constraints ac
                    JOIN all_cons_columns acc ON ac.constraint_name = acc.constraint_name AND ac.owner = acc.owner
                    WHERE ac.constraint_type = 'P' AND ac.owner = :o
                ) pk ON c.table_name = pk.table_name AND c.column_name = pk.column_name
                WHERE c.owner = :o
                ORDER BY c.table_name, c.column_id
            """, {"o": owner})
            col_rows = cur.fetchall()

            cur.execute("""
                SELECT ac.table_name, acc.column_name, rc.table_name AS ref_table, rcc.column_name AS ref_col
                FROM all_constraints ac
                JOIN all_cons_columns acc ON ac.constraint_name = acc.constraint_name AND ac.owner = acc.owner
                JOIN all_constraints rc ON ac.r_constraint_name = rc.constraint_name AND ac.r_owner = rc.owner
                JOIN all_cons_columns rcc ON rc.constraint_name = rcc.constraint_name AND rc.owner = rcc.owner
                WHERE ac.constraint_type = 'R' AND ac.owner = :o
            """, {"o": owner})
            fk_rows = cur.fetchall()
            cur.close()

            fk_map = {(r[0], r[1]): f"{r[2]}.{r[3]}" for r in fk_rows}
            cols_by_table: dict[str, list[dict]] = {t: [] for t in table_names}
            for tname, col_name, col_type, is_pk in col_rows:
                if tname not in cols_by_table:
                    continue
                cols_by_table[tname].append({
                    "name": col_name,
                    "type": col_type,
                    "is_pk": bool(is_pk),
                    "fk_reference": fk_map.get((tname, col_name)),
                })

            return [{"table_name": t, "columns": cols_by_table[t]} for t in table_names]

        return await asyncio.to_thread(_fetch)

    async def execute_query(self, conn, sql, max_rows):
        def _fetch():
            cur = conn.cursor()
            cur.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            data = [dict(zip(columns, r)) for r in cur.fetchall()]
            cur.close()
            return columns, data

        columns, data = await asyncio.to_thread(_fetch)
        return _format_result(columns, data, max_rows)


# ── 공통 헬퍼 ──────────────────────────────────────────────────────────────

def _format_result(columns: list[str], data: list[dict], max_rows: int) -> dict:
    """쿼리 결과를 직렬화 가능한 형태로 변환."""
    truncated = len(data) > max_rows
    rows_out = data[:max_rows]
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


_DIALECTS: dict[str, type[BaseDialect]] = {
    "postgresql": PgDialect,
    "mysql": MysqlDialect,
    "sqlite": SqliteDialect,
    "oracle": OracleDialect,
}


# ── TargetDBManager ────────────────────────────────────────────────────────

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
        schema_name: str | None = None,
    ):
        self.db_type = db_type.lower()
        self.host = _resolve_host(host)
        self.port = port
        self.db_name = db_name
        self.username = username
        self.password = password
        self.schema_name = schema_name
        self._conn: Any = None
        self._last_used: float = 0.0
        self._retired: bool = False

        if self.db_type not in _DIALECTS:
            raise ValueError(f"지원하지 않는 DB 타입: {self.db_type}. 지원: {', '.join(_DIALECTS)}")
        self._dialect: BaseDialect = _DIALECTS[self.db_type]()

    def retire(self) -> None:
        """대상 DB 설정이 바뀌어 이 인스턴스를 더 이상 쓰면 안 될 때 호출.

        캐시 dict에서 pop하는 것만으로는, 이미 이 인스턴스에 대한 참조를 들고
        멀티스텝 파이프라인을 실행 중이던 요청이 재접속 시 여기 고정된(옛)
        host/username/password로 조용히 재연결해버리는 걸 막지 못한다.
        """
        self._retired = True

    async def connect(self) -> None:
        self._conn = await self._dialect.connect(
            self.host, self.port, self.db_name,
            self.username, self.password, self.schema_name,
        )

    async def close(self) -> None:
        if self._conn:
            try:
                await self._dialect.close(self._conn)
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

    async def get_schemas(self) -> list[str]:
        """사용 가능한 스키마 목록 반환."""
        await self.connect()
        try:
            return await self._dialect.get_schemas(self._conn)
        finally:
            await self.close()

    async def get_table_summary(self) -> list[dict]:
        """테이블 이름 + 컬럼 수만 빠르게 반환 (전체 inspect 대비 훨씬 빠름)."""
        await self.connect()
        try:
            return await self._dialect.get_table_summary(self._conn, self.schema_name)
        finally:
            await self.close()

    async def get_tables(self, only: list[str] | None = None) -> list[dict]:
        """테이블 목록 + 컬럼 정보 반환. only가 지정되면 해당 테이블만 inspect."""
        await self.connect()
        try:
            tables = await self._dialect.get_tables(self._conn, self.schema_name)
            if only:
                only_lower = {t.lower() for t in only}
                tables = [t for t in tables if t["table_name"].lower() in only_lower]
            return tables
        finally:
            await self.close()

    async def execute_query(
        self,
        sql: str,
        timeout_sec: int = 30,
        max_rows: int = 1000,
    ) -> dict:
        """SELECT 쿼리 실행 후 결과 반환.

        매번 새로 connect/close 하면 채팅 턴마다 원격 DB에 TCP+인증 핸드셰이크가
        발생한다. 이 매니저가 재사용(캐시)되는 호출 경로(pipeline/execute.py)에서는
        일정 시간(_MAX_IDLE_SECONDS) 안에는 기존 연결을 그대로 쓴다. 단발성 호출
        (예: 어드민 쿼리 테스트)에서도 매번 새로 connect() 하므로 동작은 동일하게
        유지된다.
        """
        if self._retired:
            raise RuntimeError(
                "대상 DB 설정이 변경되어 이 연결은 더 이상 유효하지 않습니다. 질문을 다시 시도해주세요."
            )
        if self._conn is None or (time.monotonic() - self._last_used) >= _MAX_IDLE_SECONDS:
            await self.close()
            await self.connect()
        try:
            result = await asyncio.wait_for(
                self._dialect.execute_query(self._conn, sql, max_rows),
                timeout=timeout_sec,
            )
            self._last_used = time.monotonic()
            return result
        except Exception:
            # 연결이 끊어졌을 가능성이 있으니 다음 호출에서 재연결하도록 정리
            await self.close()
            raise
