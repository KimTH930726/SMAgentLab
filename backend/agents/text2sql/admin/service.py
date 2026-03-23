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
                (namespace_id, db_type, host, port, db_name, username, encrypted_password, schema_name, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
            ON CONFLICT (namespace_id)
            DO UPDATE SET db_type = EXCLUDED.db_type,
                          host = EXCLUDED.host,
                          port = EXCLUDED.port,
                          db_name = EXCLUDED.db_name,
                          username = EXCLUDED.username,
                          encrypted_password = EXCLUDED.encrypted_password,
                          schema_name = EXCLUDED.schema_name,
                          updated_at = NOW()
        """,
            namespace_id,
            payload["db_type"],
            payload["host"],
            payload["port"],
            payload["db_name"],
            payload["username"],
            encrypted,
            payload.get("schema_name") or None,
        )


def build_target_db(cfg: dict):
    """설정 dict → TargetDBManager 인스턴스."""
    from agents.text2sql.admin.target import TargetDBManager
    return TargetDBManager(
        db_type=cfg["db_type"],
        host=cfg["host"],
        port=cfg["port"],
        db_name=cfg["db_name"],
        username=cfg["username"],
        password=cfg["password"],
        schema_name=cfg.get("schema_name"),
    )


# ── 스키마 스캔 & 저장 (diff 방식) ────────────────────────────────────────────

async def scan_and_save_schema(namespace_id: int) -> dict:
    """원격 DB 스캔 → diff 비교 → 변경분만 저장/임베딩.

    Returns:
        {tables_added, tables_removed, columns_added, columns_removed,
         columns_updated, columns_skipped, embeddings_created,
         orphan_synonyms_deleted, orphan_synonyms_warn, changed_tables}
    """
    cfg = await get_target_db_config(namespace_id)
    if not cfg:
        raise ValueError("대상 DB 연결 정보가 없습니다.")

    db = build_target_db(cfg)
    raw_tables = await db.get_tables()

    # 원격 DB 스키마를 dict로 정리
    remote: dict[str, list[dict]] = {}
    for tbl in raw_tables:
        remote[tbl["table_name"]] = tbl["columns"]

    # 기존 저장된 스키마 로드
    async with get_conn() as conn:
        existing_tables = await conn.fetch(
            "SELECT id, table_name FROM sql_schema_table WHERE namespace_id = $1",
            namespace_id,
        )
        existing_map: dict[str, int] = {r["table_name"]: r["id"] for r in existing_tables}

        # 기존 컬럼 로드 (table_id → [컬럼 목록])
        all_cols = await conn.fetch("""
            SELECT sc.id, sc.table_id, sc.name, sc.data_type, sc.is_pk, sc.fk_reference
            FROM sql_schema_column sc
            JOIN sql_schema_table st ON sc.table_id = st.id
            WHERE st.namespace_id = $1
        """, namespace_id)

    existing_cols: dict[int, list[dict]] = {}
    for c in all_cols:
        existing_cols.setdefault(c["table_id"], []).append(dict(c))

    remote_names = set(remote.keys())
    existing_names = set(existing_map.keys())

    # ── 테이블 diff ──
    tables_to_add = remote_names - existing_names
    tables_to_remove = existing_names - remote_names
    tables_common = remote_names & existing_names

    report = {
        "tables_added": len(tables_to_add),
        "tables_removed": len(tables_to_remove),
        "columns_added": 0,
        "columns_removed": 0,
        "columns_updated": 0,
        "columns_skipped": 0,
        "embeddings_created": 0,
        "orphan_synonyms_deleted": 0,
        "orphan_synonyms_warn": [],
        "changed_tables": [],  # 신규 + FK변경 테이블 이름 목록
    }

    embed_queue: list[int] = []  # 임베딩 필요한 column_id 목록
    deleted_cols_info: list[dict] = []  # 삭제된 컬럼 {table_name, col_name}

    async with get_conn() as conn:
        # ── 신규 테이블 추가 ──
        for tname in tables_to_add:
            table_id = await conn.fetchval("""
                INSERT INTO sql_schema_table (namespace_id, table_name, updated_at)
                VALUES ($1, $2, NOW()) RETURNING id
            """, namespace_id, tname)
            report["changed_tables"].append(tname)
            for col in remote[tname]:
                col_id = await conn.fetchval("""
                    INSERT INTO sql_schema_column (table_id, name, data_type, is_pk, fk_reference)
                    VALUES ($1, $2, $3, $4, $5) RETURNING id
                """, table_id, col["name"], col["type"], col["is_pk"], col.get("fk_reference"))
                report["columns_added"] += 1
                embed_queue.append(col_id)

        # ── 삭제 테이블 제거 (컬럼 + 벡터 + 관계 정리) ──
        for tname in tables_to_remove:
            table_id = existing_map[tname]
            # 삭제될 컬럼 정보 수집
            for c in existing_cols.get(table_id, []):
                deleted_cols_info.append({"table_name": tname, "col_name": c["name"]})
            # 벡터 삭제
            await conn.execute("""
                DELETE FROM sql_schema_vector WHERE column_id IN (
                    SELECT id FROM sql_schema_column WHERE table_id = $1
                )
            """, table_id)
            # 컬럼 삭제
            await conn.execute("DELETE FROM sql_schema_column WHERE table_id = $1", table_id)
            # ERD 관계 정리
            await conn.execute("""
                DELETE FROM sql_relation
                WHERE namespace_id = $1 AND (from_table = $2 OR to_table = $2)
            """, namespace_id, tname)
            # 테이블 삭제
            await conn.execute("DELETE FROM sql_schema_table WHERE id = $1", table_id)

        # ── 기존 테이블 컬럼 diff ──
        for tname in tables_common:
            table_id = existing_map[tname]
            remote_cols = remote[tname]
            old_cols = existing_cols.get(table_id, [])

            remote_col_map = {c["name"]: c for c in remote_cols}
            old_col_map = {c["name"]: c for c in old_cols}
            remote_col_names = set(remote_col_map.keys())
            old_col_names = set(old_col_map.keys())

            table_changed = False

            # 신규 컬럼
            for cname in (remote_col_names - old_col_names):
                rc = remote_col_map[cname]
                col_id = await conn.fetchval("""
                    INSERT INTO sql_schema_column (table_id, name, data_type, is_pk, fk_reference)
                    VALUES ($1, $2, $3, $4, $5) RETURNING id
                """, table_id, rc["name"], rc["type"], rc["is_pk"], rc.get("fk_reference"))
                report["columns_added"] += 1
                embed_queue.append(col_id)
                if rc.get("fk_reference"):
                    table_changed = True

            # 삭제 컬럼
            for cname in (old_col_names - remote_col_names):
                oc = old_col_map[cname]
                deleted_cols_info.append({"table_name": tname, "col_name": cname})
                # FK 컬럼 삭제 시 관계도 삭제
                if oc.get("fk_reference"):
                    await conn.execute("""
                        DELETE FROM sql_relation
                        WHERE namespace_id = $1 AND from_table = $2 AND from_col = $3
                    """, namespace_id, tname, cname)
                    table_changed = True
                await conn.execute(
                    "DELETE FROM sql_schema_vector WHERE column_id = $1", oc["id"]
                )
                await conn.execute(
                    "DELETE FROM sql_schema_column WHERE id = $1", oc["id"]
                )
                report["columns_removed"] += 1

            # 변경 감지 (타입/PK/FK)
            for cname in (remote_col_names & old_col_names):
                rc = remote_col_map[cname]
                oc = old_col_map[cname]
                type_changed = rc["type"] != oc["data_type"]
                pk_changed = rc["is_pk"] != oc["is_pk"]
                fk_changed = (rc.get("fk_reference") or None) != (oc.get("fk_reference") or None)

                if type_changed or pk_changed or fk_changed:
                    await conn.execute("""
                        UPDATE sql_schema_column
                        SET data_type = $1, is_pk = $2, fk_reference = $3
                        WHERE id = $4
                    """, rc["type"], rc["is_pk"], rc.get("fk_reference"), oc["id"])
                    report["columns_updated"] += 1
                    embed_queue.append(oc["id"])
                    if fk_changed:
                        # 기존 FK 관계 삭제
                        await conn.execute("""
                            DELETE FROM sql_relation
                            WHERE namespace_id = $1 AND from_table = $2 AND from_col = $3
                        """, namespace_id, tname, cname)
                        table_changed = True
                else:
                    report["columns_skipped"] += 1

            # 테이블 updated_at 갱신
            await conn.execute(
                "UPDATE sql_schema_table SET updated_at = NOW() WHERE id = $1", table_id
            )
            if table_changed:
                report["changed_tables"].append(tname)

    # ── 변경분만 임베딩 ──
    if embed_queue:
        async with get_conn() as conn:
            rows = await conn.fetch("""
                SELECT sc.id, sc.name, sc.data_type, sc.description, st.table_name, st.namespace_id
                FROM sql_schema_column sc
                JOIN sql_schema_table st ON sc.table_id = st.id
                WHERE sc.id = ANY($1)
            """, embed_queue)
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
                    ON CONFLICT (column_id) DO UPDATE SET embedding = EXCLUDED.embedding
                """, row["id"], namespace_id, str(emb))
        report["embeddings_created"] = len(embed_queue)

    # ── 용어사전 고아 처리 ──
    if deleted_cols_info:
        orphan_result = await _cleanup_orphan_synonyms(namespace_id, deleted_cols_info)
        report["orphan_synonyms_deleted"] = orphan_result["deleted"]
        report["orphan_synonyms_warn"] = orphan_result["warn"]

    # changed_tables에 신규 테이블도 포함되어 있음
    return report


async def _cleanup_orphan_synonyms(namespace_id: int, deleted_cols: list[dict]) -> dict:
    """삭제된 컬럼을 참조하는 용어를 자동 삭제하고, 변경된 컬럼 참조 용어를 경고 목록으로 반환."""
    deleted = 0
    warn: list[dict] = []
    if not deleted_cols:
        return {"deleted": deleted, "warn": warn}

    async with get_conn() as conn:
        synonyms = await conn.fetch(
            "SELECT id, term, target, description FROM sql_synonym WHERE namespace_id = $1",
            namespace_id,
        )
        for syn in synonyms:
            target = syn["target"] or ""
            for dc in deleted_cols:
                # target에 "table_name.col_name" 또는 "table_name.col_name " 패턴 포함 시
                ref = f"{dc['table_name']}.{dc['col_name']}"
                if ref in target:
                    await conn.execute("DELETE FROM sql_synonym WHERE id = $1", syn["id"])
                    deleted += 1
                    break

    return {"deleted": deleted, "warn": warn}


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
