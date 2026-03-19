"""Text2SQL 서비스 — 스키마 스캔, 벡터 인덱싱, RAG 검색, 암호화."""
import hashlib
import json
import logging
from typing import Optional

from cryptography.fernet import Fernet

from core.config import settings
from core.database import get_conn
from shared.embedding import embedding_service

logger = logging.getLogger(__name__)


# ── 암호화 ──────────────────────────────────────────────────────────────────

_fernet_instance: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        import base64
        key = settings.fernet_secret_key or settings.jwt_secret_key
        raw = hashlib.sha256(key.encode()).digest()
        _fernet_instance = Fernet(base64.urlsafe_b64encode(raw))
    return _fernet_instance


def encrypt_password(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_password(encrypted: str) -> str:
    return _get_fernet().decrypt(encrypted.encode()).decode()


# ── 대상 DB 설정 ─────────────────────────────────────────────────────────────

async def get_target_db_config(namespace_id: int) -> Optional[dict]:
    async with get_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM sql_target_db WHERE namespace_id = $1", namespace_id
        )
    if not row:
        return None
    d = dict(row)
    d["password"] = decrypt_password(d["encrypted_password"])
    del d["encrypted_password"]
    return d


async def upsert_target_db_config(namespace_id: int, payload: dict) -> None:
    encrypted = encrypt_password(payload.get("password", ""))
    async with get_conn() as conn:
        await conn.execute("""
            INSERT INTO sql_target_db
                (namespace_id, db_type, host, port, db_name, username, encrypted_password, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (namespace_id)
            DO UPDATE SET db_type = EXCLUDED.db_type,
                          host = EXCLUDED.host,
                          port = EXCLUDED.port,
                          db_name = EXCLUDED.db_name,
                          username = EXCLUDED.username,
                          encrypted_password = EXCLUDED.encrypted_password,
                          updated_at = NOW()
        """,
            namespace_id,
            payload["db_type"],
            payload["host"],
            payload["port"],
            payload["db_name"],
            payload["username"],
            encrypted,
        )


def build_target_db(cfg: dict):
    """설정 dict → TargetDBManager 인스턴스."""
    from domain.text2sql.target import TargetDBManager
    return TargetDBManager(
        db_type=cfg["db_type"],
        host=cfg["host"],
        port=cfg["port"],
        db_name=cfg["db_name"],
        username=cfg["username"],
        password=cfg["password"],
    )


# ── 스키마 스캔 & 저장 ───────────────────────────────────────────────────────

async def scan_and_save_schema(namespace_id: int) -> dict:
    """원격 DB 스캔 → 로컬 저장 → 벡터 인덱싱.

    Returns:
        {"tables": int, "columns": int}
    """
    cfg = await get_target_db_config(namespace_id)
    if not cfg:
        raise ValueError("대상 DB 연결 정보가 없습니다.")

    db = build_target_db(cfg)
    raw_tables = await db.get_tables()

    async with get_conn() as conn:
        for tbl in raw_tables:
            tname = tbl["table_name"]
            # 테이블 upsert — 기존 description 보존
            table_id = await conn.fetchval("""
                INSERT INTO sql_schema_table (namespace_id, table_name, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (namespace_id, table_name)
                DO UPDATE SET updated_at = NOW()
                RETURNING id
            """, namespace_id, tname)

            for col in tbl["columns"]:
                # 컬럼 upsert — 기존 description 보존
                await conn.execute("""
                    INSERT INTO sql_schema_column
                        (table_id, name, data_type, is_pk, fk_reference)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT DO NOTHING
                """,
                    table_id, col["name"], col["type"],
                    col["is_pk"], col.get("fk_reference"),
                )

    table_count, col_count = await _reindex_schema_vectors(namespace_id)
    return {"tables": table_count, "columns": col_count}


async def _reindex_schema_vectors(namespace_id: int) -> tuple[int, int]:
    """sql_schema_column 전체를 임베딩하여 sql_schema_vector에 upsert."""
    async with get_conn() as conn:
        rows = await conn.fetch("""
            SELECT sc.id, sc.name, sc.data_type, sc.description, sc.is_pk, sc.fk_reference,
                   st.table_name
            FROM sql_schema_column sc
            JOIN sql_schema_table st ON sc.table_id = st.id
            WHERE st.namespace_id = $1
        """, namespace_id)

    if not rows:
        return 0, 0

    texts = [
        f"{r['table_name']}.{r['name']} - {r['description'] or ''} ({r['data_type']})"
        for r in rows
    ]
    embeddings = await embedding_service.embed_batch(texts)

    async with get_conn() as conn:
        for row, emb in zip(rows, embeddings):
            await conn.execute("""
                INSERT INTO sql_schema_vector (column_id, namespace_id, embedding)
                VALUES ($1, $2, $3::vector)
                ON CONFLICT (column_id)
                DO UPDATE SET embedding = EXCLUDED.embedding
            """, row["id"], namespace_id, str(emb))

    tables = {r["table_name"] for r in rows}
    return len(tables), len(rows)


# ── 벡터 검색 ────────────────────────────────────────────────────────────────

async def search_schema(namespace_id: int, query: str, top_k: int = 20, vec: list[float] | None = None) -> list[dict]:
    if vec is None:
        vec = await embedding_service.embed(query)
    async with get_conn() as conn:
        rows = await conn.fetch("""
            SELECT sc.id, st.table_name, sc.name, sc.data_type, sc.description,
                   sc.is_pk, sc.fk_reference,
                   1 - (sv.embedding <=> $1::vector) AS score
            FROM sql_schema_vector sv
            JOIN sql_schema_column sc ON sv.column_id = sc.id
            JOIN sql_schema_table st ON sc.table_id = st.id
            WHERE sv.namespace_id = $2 AND st.is_selected = TRUE
            ORDER BY sv.embedding <=> $1::vector
            LIMIT $3
        """, str(vec), namespace_id, top_k)
    return [dict(r) for r in rows]


async def search_synonyms(namespace_id: int, query: str, top_k: int = 5, vec: list[float] | None = None) -> list[dict]:
    if vec is None:
        vec = await embedding_service.embed(query)
    async with get_conn() as conn:
        rows = await conn.fetch("""
            SELECT id, term, target, description,
                   1 - (embedding <=> $1::vector) AS score
            FROM sql_synonym
            WHERE namespace_id = $2 AND embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $3
        """, str(vec), namespace_id, top_k)
    return [dict(r) for r in rows]


async def search_fewshots(namespace_id: int, query: str, top_k: int = 3, vec: list[float] | None = None) -> list[dict]:
    if vec is None:
        vec = await embedding_service.embed(query)
    async with get_conn() as conn:
        rows = await conn.fetch("""
            SELECT id, question, sql, category, hits,
                   1 - (embedding <=> $1::vector) AS score
            FROM sql_fewshot
            WHERE namespace_id = $2 AND embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $3
        """, str(vec), namespace_id, top_k)
    return [dict(r) for r in rows]


async def get_relations(namespace_id: int) -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT * FROM sql_relation WHERE namespace_id = $1 ORDER BY id",
            namespace_id,
        )
    return [dict(r) for r in rows]


# ── 캐시 ─────────────────────────────────────────────────────────────────────

async def get_cached_sql(namespace_id: int, question: str) -> Optional[str]:
    q_hash = hashlib.sha256(question.strip().lower().encode()).hexdigest()
    async with get_conn() as conn:
        row = await conn.fetchrow("""
            UPDATE sql_cache
            SET hits = hits + 1
            WHERE namespace_id = $1 AND question_hash = $2
              AND (expires_at IS NULL OR expires_at > NOW())
            RETURNING sql
        """, namespace_id, q_hash)
    return row["sql"] if row else None


async def set_cached_sql(namespace_id: int, question: str, sql: str, ttl_minutes: int = 60) -> None:
    q_hash = hashlib.sha256(question.strip().lower().encode()).hexdigest()
    async with get_conn() as conn:
        await conn.execute("""
            INSERT INTO sql_cache (namespace_id, question_hash, question, sql, expires_at)
            VALUES ($1, $2, $3, $4, NOW() + $5 * interval '1 minute')
            ON CONFLICT (namespace_id, question_hash)
            DO UPDATE SET sql = EXCLUDED.sql, hits = sql_cache.hits + 1,
                          expires_at = EXCLUDED.expires_at
        """, namespace_id, q_hash, question, sql, ttl_minutes)


# ── 파이프라인 스테이지 설정 ──────────────────────────────────────────────────

async def get_pipeline_stages() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT * FROM sql_pipeline_stage ORDER BY order_num"
        )
    return [dict(r) for r in rows]


async def update_pipeline_stage(stage_id: str, patch: dict) -> None:
    allowed = {"is_enabled", "prompt", "system_prompt", "extra_prompts"}
    sets = []
    vals = []
    for k, v in patch.items():
        if k in allowed:
            sets.append(f"{k} = ${len(vals)+1}")
            vals.append(v)
    if not sets:
        return
    vals.append(stage_id)
    async with get_conn() as conn:
        await conn.execute(
            f"UPDATE sql_pipeline_stage SET {', '.join(sets)}, updated_at = NOW() WHERE id = ${len(vals)}",
            *vals,
        )


# ── 감사 로그 ────────────────────────────────────────────────────────────────

async def save_audit_log(namespace_id: int, entry: dict) -> int:
    async with get_conn() as conn:
        return await conn.fetchval("""
            INSERT INTO sql_audit_log
                (namespace_id, question, sql, status, duration_ms, cached, tokens, error, result_preview, stages_json)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id
        """,
            namespace_id,
            entry.get("question", ""),
            entry.get("sql"),
            entry.get("status", "success"),
            entry.get("duration_ms", 0),
            entry.get("cached", False),
            entry.get("tokens", 0),
            entry.get("error"),
            json.dumps(entry.get("result_preview"), ensure_ascii=False) if entry.get("result_preview") else None,
            json.dumps(entry.get("stages"), ensure_ascii=False) if entry.get("stages") else None,
        )
