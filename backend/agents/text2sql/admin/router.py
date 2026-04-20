"""Text2SQL Admin API 라우터 — 8개 탭 (대상DB/스키마/관계/SQL용어/SQL예제/파이프라인/감사로그/캐시)."""
import json
import logging
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.database import get_conn
from core.dependencies import get_current_admin as require_admin, get_current_user
from core.security import get_user_api_key
from service.llm.factory import get_llm_provider
from agents.text2sql.admin import service
from shared.embedding import embedding_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/text2sql", tags=["text2sql-admin"])


def _parse_llm_json(text: str) -> list:
    """LLM 응답에서 JSON 배열을 파싱하는 공통 헬퍼."""
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    result = json.loads(text)
    if not isinstance(result, list):
        raise ValueError("Expected JSON array")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 1. 대상 DB 연결
# ─────────────────────────────────────────────────────────────────────────────

class TargetDbPayload(BaseModel):
    db_type: str = "postgresql"
    host: str
    port: int = 5432
    db_name: str
    username: str
    password: str = ""
    schema_name: str | None = None


@router.get("/namespaces/{namespace}/target-db")
async def get_target_db(namespace: str, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    cfg = await service.get_target_db_config(ns_id)
    if not cfg:
        return None
    cfg.pop("password", None)
    return cfg


@router.put("/namespaces/{namespace}/target-db")
async def upsert_target_db(namespace: str, body: TargetDbPayload, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    await service.upsert_target_db_config(ns_id, body.model_dump())
    return {"ok": True}


@router.post("/namespaces/{namespace}/target-db/test")
async def test_target_db(namespace: str, body: TargetDbPayload, _=Depends(require_admin)):
    db = service.build_target_db(body.model_dump())
    ok = await db.test_connection()
    return {"ok": ok, "message": "연결 성공" if ok else "연결 실패"}


@router.post("/namespaces/{namespace}/target-db/schemas")
async def list_schemas(namespace: str, _=Depends(require_admin)):
    """대상 DB에 저장된 연결 정보로 스키마 목록을 조회합니다."""
    ns_id = await _get_ns_id(namespace)
    cfg = await service.get_target_db_config(ns_id)
    if not cfg:
        raise HTTPException(status_code=400, detail="대상 DB 연결 정보가 없습니다. 먼저 저장하세요.")
    try:
        db = service.build_target_db(cfg)
        schemas = await db.get_schemas()
        return {"ok": True, "schemas": schemas}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/namespaces/{namespace}/target-db/scan")
async def scan_schema(namespace: str, _=Depends(require_admin)):
    """스키마 스캔 (diff 방식) — 변경분만 반영 + ERD/용어 정합성 자동 처리."""
    ns_id = await _get_ns_id(namespace)
    try:
        result = await service.scan_and_save_schema(ns_id)
        return {"ok": True, **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 2. 스키마 관리
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/namespaces/{namespace}/schema")
async def get_schema(namespace: str, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    async with get_conn() as conn:
        tables = await conn.fetch(
            "SELECT * FROM sql_schema_table WHERE namespace_id = $1 ORDER BY table_name",
            ns_id,
        )
        result = []
        for t in tables:
            cols = await conn.fetch(
                "SELECT * FROM sql_schema_column WHERE table_id = $1 ORDER BY id",
                t["id"],
            )
            result.append({**dict(t), "columns": [dict(c) for c in cols]})
    return result


class TableDescPayload(BaseModel):
    description: str


class ColumnDescPayload(BaseModel):
    description: str


@router.put("/namespaces/{namespace}/schema/tables/{table_id}")
async def update_table_desc(namespace: str, table_id: int, body: TableDescPayload, _=Depends(require_admin)):
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE sql_schema_table SET description = $1, updated_at = NOW() WHERE id = $2",
            body.description, table_id,
        )
    return {"ok": True}


@router.put("/namespaces/{namespace}/schema/columns/{col_id}")
async def update_column_desc(namespace: str, col_id: int, body: ColumnDescPayload, _=Depends(require_admin)):
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE sql_schema_column SET description = $1 WHERE id = $2",
            body.description, col_id,
        )
        # 벡터 재인덱싱 (해당 컬럼만)
        row = await conn.fetchrow("""
            SELECT sc.id, sc.name, sc.data_type, sc.description, st.table_name, st.namespace_id
            FROM sql_schema_column sc
            JOIN sql_schema_table st ON sc.table_id = st.id
            WHERE sc.id = $1
        """, col_id)
        if row:
            text = f"{row['table_name']}.{row['name']} - {row['description'] or ''} ({row['data_type']})"
            emb = await embedding_service.embed(text)
            await conn.execute("""
                INSERT INTO sql_schema_vector (column_id, namespace_id, embedding)
                VALUES ($1, $2, $3::vector)
                ON CONFLICT (column_id) DO UPDATE SET embedding = EXCLUDED.embedding
            """, col_id, row["namespace_id"], str(emb))
    return {"ok": True}


class PositionsPayload(BaseModel):
    positions: dict[str, dict]  # {"table_name": {"x": 100, "y": 200}}


@router.put("/namespaces/{namespace}/schema/positions")
async def save_schema_positions(namespace: str, body: PositionsPayload, _=Depends(require_admin)):
    """ERD 테이블 위치를 DB에 저장합니다."""
    ns_id = await _get_ns_id(namespace)
    async with get_conn() as conn:
        for table_name, pos in body.positions.items():
            await conn.execute(
                "UPDATE sql_schema_table SET pos_x = $1, pos_y = $2, updated_at = NOW() "
                "WHERE namespace_id = $3 AND table_name = $4",
                float(pos.get("x", 0)), float(pos.get("y", 0)), ns_id, table_name,
            )
    return {"ok": True}


@router.post("/namespaces/{namespace}/schema/reindex")
async def reindex_schema(namespace: str, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    tables, cols = await service._reindex_schema_vectors(ns_id)
    return {"ok": True, "tables": tables, "columns": cols}


@router.get("/namespaces/{namespace}/schema/tables-available")
async def get_available_tables(namespace: str, _=Depends(require_admin)):
    """대상 DB에서 사용 가능한 테이블 목록을 빠르게 조회합니다."""
    ns_id = await _get_ns_id(namespace)
    try:
        return await service.get_table_summary(ns_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class TablesAddPayload(BaseModel):
    tables: list[str]


@router.post("/namespaces/{namespace}/schema/tables/add")
async def add_tables(namespace: str, body: TablesAddPayload, _=Depends(require_admin)):
    """선택한 테이블만 증분 추가합니다."""
    ns_id = await _get_ns_id(namespace)
    try:
        result = await service.add_tables(ns_id, body.tables)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/namespaces/{namespace}/schema/tables/{table_name}")
async def delete_table_by_name(namespace: str, table_name: str, _=Depends(require_admin)):
    """앱 DB에서 테이블을 삭제합니다 (컬럼, 벡터, 관계 포함)."""
    ns_id = await _get_ns_id(namespace)
    deleted = await service.delete_table(ns_id, table_name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"테이블을 찾을 수 없습니다: {table_name}")
    return {"ok": True}


@router.put("/namespaces/{namespace}/schema/tables/{table_id}/toggle")
async def toggle_table(namespace: str, table_id: int, _=Depends(require_admin)):
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE sql_schema_table SET is_selected = NOT is_selected WHERE id = $1",
            table_id,
        )
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# 3. 관계 (Relations)
# ─────────────────────────────────────────────────────────────────────────────

class RelationPayload(BaseModel):
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    relation_type: str = "N:1"
    description: str = ""


@router.get("/namespaces/{namespace}/relations")
async def get_relations(namespace: str, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    rows = await service.get_relations(ns_id)
    return rows


@router.post("/namespaces/{namespace}/relations")
async def create_relation(namespace: str, body: RelationPayload, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    async with get_conn() as conn:
        row_id = await conn.fetchval("""
            INSERT INTO sql_relation
                (namespace_id, from_table, from_col, to_table, to_col, relation_type, description)
            VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id
        """, ns_id, body.from_table, body.from_col, body.to_table, body.to_col,
            body.relation_type, body.description)
    return {"id": row_id, "ok": True}


@router.put("/namespaces/{namespace}/relations/{relation_id}")
async def update_relation(namespace: str, relation_id: int, body: RelationPayload, _=Depends(require_admin)):
    async with get_conn() as conn:
        await conn.execute("""
            UPDATE sql_relation
            SET from_table=$1, from_col=$2, to_table=$3, to_col=$4,
                relation_type=$5, description=$6
            WHERE id=$7
        """, body.from_table, body.from_col, body.to_table, body.to_col,
            body.relation_type, body.description, relation_id)
    return {"ok": True}


@router.delete("/namespaces/{namespace}/relations/{relation_id}")
async def delete_relation(namespace: str, relation_id: int, _=Depends(require_admin)):
    async with get_conn() as conn:
        await conn.execute("DELETE FROM sql_relation WHERE id = $1", relation_id)
    return {"ok": True}


class SuggestRelationsPayload(BaseModel):
    target_tables: list[str] = []  # 빈 배열이면 전체 스키마 대상


@router.post("/namespaces/{namespace}/relations/suggest-ai")
async def suggest_relations_ai(namespace: str, body: SuggestRelationsPayload = None, admin: dict = Depends(require_admin)):
    """LLM으로 스키마를 분석하여 관계 후보를 추천합니다.
    target_tables가 주어지면 해당 테이블 관련 관계만 추천 (토큰 절약).
    """
    ns_id = await _get_ns_id(namespace)
    llm = get_llm_provider()
    if not llm:
        raise HTTPException(status_code=400, detail="LLM이 설정되지 않았습니다.")

    target_tables = set(body.target_tables) if body and body.target_tables else set()

    async with get_conn() as conn:
        tables = await conn.fetch(
            "SELECT id, table_name FROM sql_schema_table WHERE namespace_id = $1 ORDER BY table_name", ns_id
        )
        if not tables:
            raise HTTPException(status_code=400, detail="스키마가 없습니다.")

        schema_lines = []
        for t in tables:
            is_target = not target_tables or t["table_name"] in target_tables
            cols = await conn.fetch(
                "SELECT name, data_type, is_pk, fk_reference FROM sql_schema_column WHERE table_id = $1 ORDER BY id",
                t["id"],
            )
            if is_target:
                # 대상 테이블: 전체 컬럼 정보
                col_strs = []
                for c in cols:
                    tag = "PK" if c["is_pk"] else ("FK" if c["fk_reference"] else "")
                    col_strs.append(f"  - {c['name']} {c['data_type']}" + (f" [{tag}]" if tag else ""))
                schema_lines.append(f"## {t['table_name']} [대상]")
                schema_lines.extend(col_strs)
            else:
                # 기존 테이블: 컬럼명만 (타입 생략으로 토큰 절약)
                col_names = ", ".join(c["name"] for c in cols)
                schema_lines.append(f"## {t['table_name']}: {col_names}")

        existing = await conn.fetch(
            "SELECT from_table, from_col, to_table, to_col FROM sql_relation WHERE namespace_id = $1", ns_id
        )

    existing_set = {(r["from_table"], r["from_col"], r["to_table"], r["to_col"]) for r in existing}
    schema_text = "\n".join(schema_lines)

    focus_msg = ""
    if target_tables:
        focus_msg = f"\n\n## 주의: [대상] 표시된 테이블({', '.join(target_tables)})과 관련된 관계만 추천하세요.\n"

    prompt = (
        "아래 데이터베이스 스키마를 분석하여 테이블 간 관계(FK 참조)를 추천해주세요.\n\n"
        f"{schema_text}\n"
        f"{focus_msg}\n"
        "## 규칙\n"
        "- 실제 존재하는 테이블명과 컬럼명만 사용\n"
        "- _id, _no, _code 등 참조 패턴을 분석하여 관계 추론\n"
        "- relation_type: N:1(다대일), 1:N(일대다), 1:1(일대일), N:M(다대다) 중 하나\n"
        "- reason: 추천 이유를 한국어로 간단히\n\n"
        "## 응답 형식 (JSON 배열만 반환)\n"
        '[{"from_table":"orders","from_col":"customer_id","to_table":"customers","to_col":"id","relation_type":"N:1","reason":"주문은 고객을 참조"}, ...]\n'
    )

    try:
        text = await llm.generate_once(
            prompt=prompt,
            system="You are a database schema expert. Analyze column naming patterns to infer foreign key relationships. Return ONLY a valid JSON array.",
            max_tokens=2000,
            api_key=get_user_api_key(admin),
        )
        suggestions = _parse_llm_json(text)

        # 기존 관계 제외
        new_suggestions = [
            s for s in suggestions
            if isinstance(s, dict) and s.get("from_table") and s.get("to_table")
            and (s["from_table"], s.get("from_col", ""), s["to_table"], s.get("to_col", "")) not in existing_set
        ]
        return {"ok": True, "suggestions": new_suggestions}

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"LLM 응답 파싱 실패: {e}")
    except Exception as e:
        logger.exception("Relation suggestion failed")
        raise HTTPException(status_code=500, detail=f"관계 추천 실패: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. SQL 용어 사전 (Synonyms)
# ─────────────────────────────────────────────────────────────────────────────

class SynonymPayload(BaseModel):
    term: str
    target: str
    description: str = ""


@router.get("/namespaces/{namespace}/synonyms")
async def get_synonyms(namespace: str, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT id, term, target, description, updated_at FROM sql_synonym "
            "WHERE namespace_id = $1 ORDER BY updated_at DESC",
            ns_id,
        )
    return [dict(r) for r in rows]


@router.post("/namespaces/{namespace}/synonyms")
async def create_synonym(namespace: str, body: SynonymPayload, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    text = f"{body.term} - {body.target} - {body.description}"
    emb = await embedding_service.embed(text)
    async with get_conn() as conn:
        row_id = await conn.fetchval("""
            INSERT INTO sql_synonym (namespace_id, term, target, description, embedding)
            VALUES ($1, $2, $3, $4, $5::vector) RETURNING id
        """, ns_id, body.term, body.target, body.description, str(emb))
    return {"id": row_id, "ok": True}


@router.put("/namespaces/{namespace}/synonyms/{syn_id}")
async def update_synonym(namespace: str, syn_id: int, body: SynonymPayload, _=Depends(require_admin)):
    text = f"{body.term} - {body.target} - {body.description}"
    emb = await embedding_service.embed(text)
    async with get_conn() as conn:
        await conn.execute("""
            UPDATE sql_synonym
            SET term=$1, target=$2, description=$3, embedding=$4::vector, updated_at=NOW()
            WHERE id=$5
        """, body.term, body.target, body.description, str(emb), syn_id)
    return {"ok": True}


@router.delete("/namespaces/{namespace}/synonyms/{syn_id}")
async def delete_synonym(namespace: str, syn_id: int, _=Depends(require_admin)):
    async with get_conn() as conn:
        await conn.execute("DELETE FROM sql_synonym WHERE id = $1", syn_id)
    return {"ok": True}


class BulkDeletePayload(BaseModel):
    ids: list[int]


@router.post("/namespaces/{namespace}/synonyms/bulk-delete")
async def bulk_delete_synonyms(namespace: str, body: BulkDeletePayload, _=Depends(require_admin)):
    """용어 사전 일괄 삭제."""
    if not body.ids:
        return {"ok": True, "deleted": 0}
    async with get_conn() as conn:
        deleted = await conn.fetchval(
            "WITH d AS (DELETE FROM sql_synonym WHERE id = ANY($1) RETURNING 1) SELECT COUNT(*) FROM d",
            body.ids,
        )
    return {"ok": True, "deleted": deleted}


@router.post("/namespaces/{namespace}/synonyms/reindex")
async def reindex_synonyms(namespace: str, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT id, term, target, description FROM sql_synonym WHERE namespace_id = $1",
            ns_id,
        )
    # 임베딩을 먼저 모두 생성 (I/O 바운드이므로 conn 밖에서)
    updates = []
    for r in rows:
        text = f"{r['term']} - {r['target']} - {r['description']}"
        emb = await embedding_service.embed(text)
        updates.append((str(emb), r["id"]))
    # 한 번의 연결로 일괄 저장
    async with get_conn() as conn:
        for emb_str, row_id in updates:
            await conn.execute(
                "UPDATE sql_synonym SET embedding = $1::vector WHERE id = $2", emb_str, row_id
            )
    return {"ok": True, "count": len(rows)}


class GenerateSynonymsPayload(BaseModel):
    target_tables: list[str] = []  # 빈 배열이면 전체 스키마 대상


@router.post("/namespaces/{namespace}/synonyms/generate-ai")
async def generate_synonyms_ai(namespace: str, body: GenerateSynonymsPayload = None, admin: dict = Depends(require_admin)):
    """LLM으로 스키마를 분석하여 SQL 용어 사전을 자동 생성합니다.
    target_tables가 주어지면 해당 테이블 관련 용어만 생성 (토큰 절약).
    """
    ns_id = await _get_ns_id(namespace)
    llm = get_llm_provider()
    if not llm:
        raise HTTPException(status_code=400, detail="LLM이 설정되지 않았습니다.")

    target_tables = set(body.target_tables) if body and body.target_tables else set()

    # 스키마 로드
    async with get_conn() as conn:
        tables = await conn.fetch(
            "SELECT id, table_name, description FROM sql_schema_table WHERE namespace_id = $1 ORDER BY table_name",
            ns_id,
        )
        if not tables:
            raise HTTPException(status_code=400, detail="스키마가 없습니다. 먼저 DB를 연결하고 스키마를 스캔하세요.")
        schema_lines = []
        for t in tables:
            is_target = not target_tables or t["table_name"] in target_tables
            cols = await conn.fetch(
                "SELECT name, data_type, description, is_pk, fk_reference FROM sql_schema_column WHERE table_id = $1 ORDER BY id",
                t["id"],
            )
            if is_target:
                # 대상 테이블: 전체 컬럼 정보
                schema_lines.append(f"## {t['table_name']} [대상]" + (f" ({t['description']})" if t["description"] else ""))
                for c in cols:
                    parts = [c["name"], c["data_type"]]
                    if c["is_pk"]: parts.append("PK")
                    if c["fk_reference"]: parts.append(f"FK→{c['fk_reference']}")
                    if c["description"]: parts.append(f"({c['description']})")
                    schema_lines.append("  - " + " ".join(parts))
            else:
                # 기존 테이블: 테이블명 + 컬럼명만 (토큰 절약)
                col_names = ", ".join(c["name"] for c in cols)
                schema_lines.append(f"## {t['table_name']}: {col_names}")
        # 관계
        rels = await conn.fetch(
            "SELECT from_table, from_col, to_table, to_col, relation_type FROM sql_relation WHERE namespace_id = $1",
            ns_id,
        )
        if rels:
            schema_lines.append("\n## Relationships")
            for r in rels:
                schema_lines.append(f"  - {r['from_table']}.{r['from_col']} → {r['to_table']}.{r['to_col']} ({r['relation_type']})")
        # 기존 용어 (중복 방지)
        existing_rows = await conn.fetch("SELECT term FROM sql_synonym WHERE namespace_id = $1", ns_id)
        existing_terms = {r["term"].lower() for r in existing_rows}

    schema_text = "\n".join(schema_lines)

    prompt = (
        "아래 데이터베이스 스키마를 분석하여 비즈니스 용어 사전을 생성해주세요.\n\n"
        "용어 사전은 LLM이 자연어를 SQL로 변환할 때 참조하는 '빌딩 블록'입니다.\n"
        "각 용어는 다른 용어와 자유롭게 조합할 수 있어야 하므로,\n"
        "완성된 쿼리가 아닌 SQL 조각(컬럼, 집계식, 조건식) 형태로 작성해야 합니다.\n\n"
        f"## 데이터베이스 스키마\n{schema_text}\n\n"
        "## 핵심 제약 (반드시 지켜야 함)\n"
        "- term은 1~4 단어의 짧은 명사/명사구여야 합니다 (문장 금지)\n"
        "- target은 아래 3가지 유형 중 하나여야 합니다:\n"
        "  (A) 컬럼 참조: table.column\n"
        "  (B) 집계식: SUM/COUNT/AVG/MIN/MAX(table.column)\n"
        "  (C) 필터 조건: table.column = 'VALUE' 또는 비교식\n"
        "- target에 다음 키워드가 포함되면 무조건 잘못된 것입니다:\n"
        "  SELECT, FROM, JOIN, WHERE, GROUP BY, ORDER BY, LIMIT, HAVING\n"
        "- 하나의 용어 = 하나의 역할. 집계+필터를 동시에 넣지 마세요\n\n"
        "## 좋은 예시 (이 형태를 따라하세요)\n"
        'term: 매출 → target: SUM(orders.payment_amount) → desc: 총 결제금액 합계\n'
        'term: VVIP → target: customers.grade_code = \'VVIP\' → desc: VVIP 등급 고객 필터\n\n'
        "## 생성 가이드\n"
        "- 최소 30개 이상 생성\n"
        "- desc는 간결하게 (10자 내외)\n\n"
        "## 응답 형식 (JSON 배열만 반환)\n"
        '[{"term": "용어", "target": "SQL조각", "desc": "설명"}, ...]\n'
    )

    try:
        text = await llm.generate_once(
            prompt=prompt,
            system=(
                "You are a database domain expert creating a glossary of reusable SQL building blocks. "
                "Each term must be a short noun (1-4 words), and each target must be ONLY one of: "
                "a column reference (table.column), an aggregate (SUM/COUNT/AVG/MIN/MAX), "
                "or a filter condition (table.column = 'VALUE'). "
                "NEVER include SELECT, FROM, JOIN, WHERE, GROUP BY, ORDER BY, LIMIT, or HAVING in target. "
                "Return ONLY a valid JSON array."
            ),
            max_tokens=4000,
            api_key=get_user_api_key(admin),
        )
        synonyms_data = _parse_llm_json(text)

        _BANNED_KW = re.compile(
            r"\b(SELECT|FROM|JOIN|WHERE|GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING)\b",
            re.IGNORECASE,
        )

        created = []
        skipped_invalid = 0
        for item in synonyms_data:
            term = item.get("term", "").strip()
            target = item.get("target", "").strip()
            desc = item.get("desc", item.get("description", "")).strip()
            if not term or not target:
                continue
            if len(term.split()) > 5:
                skipped_invalid += 1
                continue
            if _BANNED_KW.search(target):
                skipped_invalid += 1
                continue
            if term.lower() in existing_terms:
                continue
            emb = await embedding_service.embed(f"{term} - {target} - {desc}")
            async with get_conn() as conn:
                row_id = await conn.fetchval("""
                    INSERT INTO sql_synonym (namespace_id, term, target, description, embedding)
                    VALUES ($1, $2, $3, $4, $5::vector) RETURNING id
                """, ns_id, term, target, desc, str(emb))
            created.append({"id": row_id, "term": term, "target": target, "description": desc})
            existing_terms.add(term.lower())

        return {
            "ok": True,
            "generated": len(synonyms_data),
            "created": len(created),
            "skipped_invalid": skipped_invalid,
        }

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"LLM 응답 파싱 실패: {e}")
    except Exception as e:
        logger.exception("Synonym AI generation failed")
        raise HTTPException(status_code=500, detail=f"용어 사전 자동 생성 실패: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 5. SQL 예제 (Fewshots)
# ─────────────────────────────────────────────────────────────────────────────

class FewshotPayload(BaseModel):
    question: str
    sql: str
    category: str = ""
    status: str = "approved"  # 'approved' | 'pending' | 'rejected'


@router.get("/namespaces/{namespace}/fewshots")
async def get_fewshots(namespace: str, status: str = "all", _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    async with get_conn() as conn:
        if status == "all":
            rows = await conn.fetch(
                "SELECT id, question, sql, category, hits, status, created_at "
                "FROM sql_fewshot WHERE namespace_id = $1 ORDER BY created_at DESC",
                ns_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, question, sql, category, hits, status, created_at "
                "FROM sql_fewshot WHERE namespace_id = $1 AND status = $2 ORDER BY created_at DESC",
                ns_id, status,
            )
    return [dict(r) for r in rows]


@router.post("/namespaces/{namespace}/fewshots")
async def create_fewshot(namespace: str, body: FewshotPayload, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    emb = await embedding_service.embed(body.question)
    async with get_conn() as conn:
        row_id = await conn.fetchval("""
            INSERT INTO sql_fewshot (namespace_id, question, sql, category, status, embedding)
            VALUES ($1, $2, $3, $4, $5, $6::vector) RETURNING id
        """, ns_id, body.question, body.sql, body.category, body.status, str(emb))
    return {"id": row_id, "ok": True}


class FeedbackFewshotPayload(BaseModel):
    question: str
    sql: str


@router.post("/namespaces/{namespace}/fewshots/from-feedback")
async def create_fewshot_from_feedback(namespace: str, body: FeedbackFewshotPayload, _=Depends(get_current_user)):
    """사용자 피드백(좋아요)으로 SQL 예제 후보를 등록합니다. 관리자 승인 필요."""
    ns_id = await _get_ns_id(namespace)
    emb = await embedding_service.embed(body.question)
    async with get_conn() as conn:
        # 중복 방지: 동일 질문이 이미 pending/approved 상태로 존재하면 스킵
        exists = await conn.fetchval(
            "SELECT id FROM sql_fewshot WHERE namespace_id = $1 AND question = $2 AND status IN ('pending', 'approved') LIMIT 1",
            ns_id, body.question,
        )
        if exists:
            return {"id": exists, "ok": True, "skipped": True}
        row_id = await conn.fetchval("""
            INSERT INTO sql_fewshot (namespace_id, question, sql, category, status, embedding)
            VALUES ($1, $2, $3, '', 'pending', $4::vector) RETURNING id
        """, ns_id, body.question, body.sql, str(emb))
    return {"id": row_id, "ok": True, "skipped": False}


@router.patch("/namespaces/{namespace}/fewshots/{fs_id}/status")
async def update_fewshot_status(namespace: str, fs_id: int, status: str, _=Depends(require_admin)):
    if status not in ("approved", "pending", "rejected"):
        raise HTTPException(status_code=400, detail="status must be approved|pending|rejected")
    async with get_conn() as conn:
        await conn.execute("UPDATE sql_fewshot SET status = $1 WHERE id = $2", status, fs_id)
    return {"ok": True}


@router.put("/namespaces/{namespace}/fewshots/{fs_id}")
async def update_fewshot(namespace: str, fs_id: int, body: FewshotPayload, _=Depends(require_admin)):
    emb = await embedding_service.embed(body.question)
    async with get_conn() as conn:
        await conn.execute("""
            UPDATE sql_fewshot SET question=$1, sql=$2, category=$3, embedding=$4::vector WHERE id=$5
        """, body.question, body.sql, body.category, str(emb), fs_id)
    return {"ok": True}


@router.delete("/namespaces/{namespace}/fewshots/{fs_id}")
async def delete_fewshot(namespace: str, fs_id: int, _=Depends(require_admin)):
    async with get_conn() as conn:
        await conn.execute("DELETE FROM sql_fewshot WHERE id = $1", fs_id)
    return {"ok": True}


@router.post("/namespaces/{namespace}/fewshots/bulk-delete")
async def bulk_delete_fewshots(namespace: str, body: BulkDeletePayload, _=Depends(require_admin)):
    """SQL 예제 일괄 삭제."""
    if not body.ids:
        return {"ok": True, "deleted": 0}
    async with get_conn() as conn:
        deleted = await conn.fetchval(
            "WITH d AS (DELETE FROM sql_fewshot WHERE id = ANY($1) RETURNING 1) SELECT COUNT(*) FROM d",
            body.ids,
        )
    return {"ok": True, "deleted": deleted}


@router.post("/namespaces/{namespace}/fewshots/reindex")
async def reindex_fewshots(namespace: str, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT id, question FROM sql_fewshot WHERE namespace_id = $1", ns_id
        )
    # 임베딩을 먼저 모두 생성 (I/O 바운드이므로 conn 밖에서)
    updates = []
    for r in rows:
        emb = await embedding_service.embed(r["question"])
        updates.append((str(emb), r["id"]))
    # 한 번의 연결로 일괄 저장
    async with get_conn() as conn:
        for emb_str, row_id in updates:
            await conn.execute(
                "UPDATE sql_fewshot SET embedding = $1::vector WHERE id = $2", emb_str, row_id
            )
    return {"ok": True, "count": len(rows)}


@router.post("/namespaces/{namespace}/fewshots/generate-ai")
async def generate_fewshots_ai(namespace: str, admin: dict = Depends(require_admin)):
    """LLM으로 스키마와 용어 사전을 분석하여 SQL 예제(Few-shot)를 자동 생성합니다."""
    ns_id = await _get_ns_id(namespace)
    llm = get_llm_provider()
    if not llm:
        raise HTTPException(status_code=400, detail="LLM이 설정되지 않았습니다.")

    async with get_conn() as conn:
        tables = await conn.fetch(
            "SELECT id, table_name, description FROM sql_schema_table WHERE namespace_id = $1 ORDER BY table_name",
            ns_id,
        )
        if not tables:
            raise HTTPException(status_code=400, detail="스키마가 없습니다. 먼저 DB를 연결하고 스키마를 스캔하세요.")
        schema_lines = []
        for t in tables:
            cols = await conn.fetch(
                "SELECT name, data_type, description, is_pk, fk_reference FROM sql_schema_column WHERE table_id = $1 ORDER BY id",
                t["id"],
            )
            schema_lines.append(f"## {t['table_name']}" + (f" ({t['description']})" if t["description"] else ""))
            for c in cols:
                parts = [c["name"], c["data_type"]]
                if c["is_pk"]: parts.append("PK")
                if c["fk_reference"]: parts.append(f"FK→{c['fk_reference']}")
                if c["description"]: parts.append(f"({c['description']})")
                schema_lines.append("  - " + " ".join(parts))
        rels = await conn.fetch(
            "SELECT from_table, from_col, to_table, to_col, relation_type FROM sql_relation WHERE namespace_id = $1",
            ns_id,
        )
        if rels:
            schema_lines.append("\n## Relationships")
            for r in rels:
                schema_lines.append(f"  - {r['from_table']}.{r['from_col']} → {r['to_table']}.{r['to_col']} ({r['relation_type']})")
        syns = await conn.fetch(
            "SELECT term, target FROM sql_synonym WHERE namespace_id = $1 LIMIT 50", ns_id
        )
        existing_qs = await conn.fetch("SELECT question FROM sql_fewshot WHERE namespace_id = $1", ns_id)
        existing_questions = {r["question"].lower().strip() for r in existing_qs}

    schema_text = "\n".join(schema_lines)
    syn_text = "\n".join(f"- {r['term']} → {r['target']}" for r in syns) if syns else "(없음)"

    prompt = (
        "아래 데이터베이스 스키마와 용어 사전을 분석하여, 사용자가 자주 물어볼 수 있는\n"
        "자연어 질문과 정답 SQL 페어를 생성해주세요.\n\n"
        f"## 데이터베이스 스키마\n{schema_text}\n\n"
        f"## 용어 사전\n{syn_text}\n\n"
        "## 규칙\n"
        "1. 실제 존재하는 테이블명과 컬럼명만 사용하세요\n"
        "2. 다양한 난이도의 질문을 생성하세요 (간단/중간/복잡)\n"
        "3. 다양한 카테고리의 질문을 생성하세요\n"
        "4. 질문은 한국어로, SQL은 PostgreSQL 문법으로 정확하게 작성하세요\n"
        "5. 최소 20개 이상의 QA 페어를 생성하세요\n"
        "6. category는 매출, 고객, 상품, 리뷰, 배송, 결제, 쿠폰, 재고 등으로 분류\n\n"
        "## 응답 형식 (JSON 배열만 반환)\n"
        '[{"question": "이번 달 총 매출은?", "sql": "SELECT SUM(...) FROM ...", "category": "매출"}, ...]\n'
    )

    try:
        text = await llm.generate_once(
            prompt=prompt,
            system=(
                "You are a PostgreSQL SQL expert. Generate diverse, realistic question-SQL pairs "
                "based on the given schema. Return ONLY a valid JSON array. "
                "Questions must be in Korean. SQL must be syntactically correct and use only existing tables/columns."
            ),
            max_tokens=6000,
            api_key=get_user_api_key(admin),
        )
        fewshots_data = _parse_llm_json(text)

        created = []
        for item in fewshots_data:
            question = item.get("question", "").strip()
            sql = item.get("sql", "").strip()
            category = item.get("category", "").strip()
            if not question or not sql:
                continue
            if question.lower().strip() in existing_questions:
                continue
            emb = await embedding_service.embed(question)
            async with get_conn() as conn:
                row_id = await conn.fetchval("""
                    INSERT INTO sql_fewshot (namespace_id, question, sql, category, embedding)
                    VALUES ($1, $2, $3, $4, $5::vector) RETURNING id
                """, ns_id, question, sql, category, str(emb))
            created.append({"id": row_id, "question": question, "sql": sql, "category": category})
            existing_questions.add(question.lower().strip())

        return {
            "ok": True,
            "generated": len(fewshots_data),
            "created": len(created),
            "skipped_duplicates": len(fewshots_data) - len(created),
        }

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"LLM 응답 파싱 실패: {e}")
    except Exception as e:
        logger.exception("Fewshot AI generation failed")
        raise HTTPException(status_code=500, detail=f"QA 자동 생성 실패: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. 파이프라인 설정
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipeline")
async def get_pipeline(_=Depends(require_admin)):
    return await service.get_pipeline_stages()


class PipelineTogglePayload(BaseModel):
    is_enabled: bool


@router.put("/pipeline/{stage_id}/toggle")
async def toggle_stage(stage_id: str, body: PipelineTogglePayload, _=Depends(require_admin)):
    # required 스테이지는 비활성화 불가
    async with get_conn() as conn:
        is_required = await conn.fetchval(
            "SELECT is_required FROM sql_pipeline_stage WHERE id = $1", stage_id
        )
    if is_required and not body.is_enabled:
        raise HTTPException(status_code=400, detail="필수 단계는 비활성화할 수 없습니다.")
    await service.update_pipeline_stage(stage_id, {"is_enabled": body.is_enabled})
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# 7. 감사 로그
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/namespaces/{namespace}/audit-logs")
async def get_audit_logs(
    namespace: str,
    page: int = 1,
    limit: int = 50,
    status: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    _=Depends(require_admin),
):
    ns_id = await _get_ns_id(namespace)
    offset = (page - 1) * limit
    where = "namespace_id = $1"
    params: list = [ns_id]
    if status != "all":
        params.append(status)
        where += f" AND status = ${len(params)}"

    # 날짜 범위 필터
    from datetime import datetime
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            params.append(dt_from)
            where += f" AND created_at >= ${len(params)}"
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            params.append(dt_to)
            where += f" AND created_at <= ${len(params)}"
        except ValueError:
            pass

    async with get_conn() as conn:
        rows = await conn.fetch(
            f"SELECT id, question, sql, status, duration_ms, cached, tokens, error, created_at "
            f"FROM sql_audit_log WHERE {where} ORDER BY created_at DESC LIMIT ${len(params)+1} OFFSET ${len(params)+2}",
            *params, limit, offset,
        )
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM sql_audit_log WHERE {where}", *params
        )
    return {"total": total, "items": [dict(r) for r in rows]}


# ─────────────────────────────────────────────────────────────────────────────
# 8. 캐시
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/namespaces/{namespace}/cache")
async def get_cache(namespace: str, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT id, question, sql, hits, created_at, expires_at "
            "FROM sql_cache WHERE namespace_id = $1 ORDER BY hits DESC",
            ns_id,
        )
    return [dict(r) for r in rows]


@router.delete("/namespaces/{namespace}/cache")
async def clear_cache(namespace: str, _=Depends(require_admin)):
    ns_id = await _get_ns_id(namespace)
    async with get_conn() as conn:
        deleted = await conn.fetchval(
            "WITH d AS (DELETE FROM sql_cache WHERE namespace_id = $1 RETURNING 1) SELECT COUNT(*) FROM d",
            ns_id,
        )
    return {"ok": True, "deleted": deleted}


@router.delete("/namespaces/{namespace}/cache/{cache_id}")
async def delete_cache_entry(namespace: str, cache_id: int, _=Depends(require_admin)):
    async with get_conn() as conn:
        await conn.execute("DELETE FROM sql_cache WHERE id = $1", cache_id)
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

async def _get_ns_id(namespace: str) -> int:
    async with get_conn() as conn:
        ns_id = await conn.fetchval(
            "SELECT id FROM ops_namespace WHERE name = $1", namespace
        )
    if not ns_id:
        raise HTTPException(status_code=404, detail=f"네임스페이스를 찾을 수 없습니다: {namespace}")
    return ns_id
