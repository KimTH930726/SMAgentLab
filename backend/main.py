"""Ops-Navigator FastAPI 진입점 — DDD 구조."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.config import settings
from core.database import init_pool, close_pool, get_conn
from core.security import hash_password
from service.chat.helpers import NO_KNOWLEDGE_MARKER
from shared.embedding import embedding_service
from shared import reranker as reranker_service
from service.llm.factory import get_llm_provider

from service.auth.router import router as auth_router
from service.chat.router import router as chat_router
from agents.knowledge_rag.knowledge.router import router as knowledge_router
from agents.knowledge_rag.fewshot.router import router as fewshot_router
from service.feedback.router import router as feedback_router
from service.admin.router import router as admin_router
from service.mcp_tool.router import router as mcp_tool_router
from service.prompt.router import router as prompt_router
from service.teams.router import router as teams_router
from agents.text2sql.admin.router import router as text2sql_router
from service.email_voc.router import router as email_voc_router
from service.email_voc.scheduler import start_scheduler, stop_scheduler

from shared import cache as sem_cache
from agents.base import AgentRegistry
from agents.knowledge_rag.agent import KnowledgeRagAgent
from agents.mcp_tool.agent import McpToolAgent, close_http_client as close_mcp_http_client
from agents.text2sql.agent import Text2SqlAgent

logger = logging.getLogger(__name__)

_ROUTERS = [
    auth_router, chat_router, knowledge_router,
    fewshot_router, feedback_router, admin_router,
    mcp_tool_router,
    prompt_router,
    teams_router,
    text2sql_router,
    email_voc_router,
]


async def _column_exists(conn, table: str, column: str) -> bool:
    """마이그레이션에서 반복되는 컬럼 존재 확인 — 하위 호환 UPDATE를 조건부로 실행할 때 사용."""
    return await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=$1 AND column_name=$2)",
        table, column,
    )


async def _migrate_core_tables(conn) -> None:
    """ops_part, ops_user, part_id FK, 슈퍼어드민 seed, admin user seed,
    ops_conversation.user_id, ops_conversation.agent_type 등 핵심 테이블 마이그레이션."""
    # ── RAG-specific 테이블 이름 변경 (ops_* → rag_*) ────────────────
    await conn.execute("ALTER TABLE IF EXISTS ops_knowledge RENAME TO rag_knowledge")
    await conn.execute("ALTER TABLE IF EXISTS ops_glossary RENAME TO rag_glossary")
    await conn.execute("ALTER TABLE IF EXISTS ops_fewshot RENAME TO rag_fewshot")
    await conn.execute("ALTER TABLE IF EXISTS ops_knowledge_category RENAME TO rag_knowledge_category")
    await conn.execute("ALTER TABLE IF EXISTS ops_conv_summary RENAME TO rag_conv_summary")

    # ── 기존 컬럼 추가 (하위 호환) ─────────────────────────────────
    await conn.execute("ALTER TABLE ops_query_log ADD COLUMN IF NOT EXISTS answer TEXT")
    await conn.execute("ALTER TABLE ops_conversation ADD COLUMN IF NOT EXISTS trimmed BOOLEAN NOT NULL DEFAULT FALSE")
    await conn.execute("ALTER TABLE ops_feedback ADD COLUMN IF NOT EXISTS message_id INT REFERENCES ops_message(id) ON DELETE SET NULL")

    # ── ops_part 테이블 ────────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_part (
            id          SERIAL PRIMARY KEY,
            name        VARCHAR(100) NOT NULL UNIQUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ── ops_user 테이블 (구 string part 방식 유지, part_id 추가) ──
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_user (
            id                      SERIAL PRIMARY KEY,
            username                VARCHAR(100) NOT NULL UNIQUE,
            hashed_password         TEXT NOT NULL,
            role                    VARCHAR(20) NOT NULL DEFAULT 'user',
            part                    VARCHAR(100),
            is_active               BOOLEAN NOT NULL DEFAULT TRUE,
            encrypted_llm_api_key   TEXT,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ── part_id 컬럼 추가 (integer FK) ──────────────────────────
    await conn.execute("ALTER TABLE ops_user ADD COLUMN IF NOT EXISTS part_id INT")
    await conn.execute("ALTER TABLE ops_user ADD COLUMN IF NOT EXISTS encrypted_confluence_pat TEXT")
    await conn.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_user_part'
            ) THEN
                ALTER TABLE ops_user
                    ADD CONSTRAINT fk_user_part
                    FOREIGN KEY (part_id) REFERENCES ops_part(id) ON DELETE SET NULL;
            END IF;
        END $$;
    """)

    # ── SSO 연동 기반 컬럼 선추가 (2026-09-03, 스키마만 — 로그인 흐름은 별도 구현) ──
    # 지금(로컬 계정 소수) 추가하는 게 싸고, 팀 규모로 계정이 늘어난 뒤 추가하면
    # 비싸다 — knowledge-lifecycle-design.md의 "Phase 0 스키마 선추가"와 같은 논리.
    # auth_provider 기본값 'local'로 기존 계정은 전부 그대로 로컬 인증 유지.
    # external_id는 SSO 프로바이더(Azure AD 등)가 발급하는 불변 식별자(예: oid 클레임) —
    # username처럼 사람이 바꿀 수 있는 값이 아니라 이걸로 계정을 식별해야 한다.
    await conn.execute("ALTER TABLE ops_user ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20) NOT NULL DEFAULT 'local'")
    await conn.execute("ALTER TABLE ops_user ADD COLUMN IF NOT EXISTS external_id VARCHAR(255)")
    await conn.execute("ALTER TABLE ops_user ADD COLUMN IF NOT EXISTS email VARCHAR(255)")
    # SSO 전용 계정은 로컬 비밀번호가 없을 수 있다 — hashed_password를 nullable로 완화.
    # (주의: authenticate_user()는 아직 NULL을 다루지 않는다 — 실제 SSO 로그인 흐름을
    # 구현할 때 같이 고쳐야 한다. 지금은 스키마만 미리 열어둔다.)
    await conn.execute("ALTER TABLE ops_user ALTER COLUMN hashed_password DROP NOT NULL")
    # 같은 프로바이더 안에서 external_id 중복 방지 (NULL은 여러 개 허용 — 로컬 계정은 전부 NULL)
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_user_provider_external_id "
        "ON ops_user(auth_provider, external_id) WHERE external_id IS NOT NULL"
    )

    # ── ops_namespace.owner_part_id 추가 ───────────────────────────
    await conn.execute("ALTER TABLE ops_namespace ADD COLUMN IF NOT EXISTS owner_part VARCHAR(100)")
    await conn.execute("ALTER TABLE ops_namespace ADD COLUMN IF NOT EXISTS owner_part_id INT")
    await conn.execute("ALTER TABLE ops_namespace ADD COLUMN IF NOT EXISTS created_by_user_id INT")
    await conn.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_namespace_owner_part'
            ) THEN
                ALTER TABLE ops_namespace
                    ADD CONSTRAINT fk_namespace_owner_part
                    FOREIGN KEY (owner_part_id) REFERENCES ops_part(id) ON DELETE SET NULL;
            END IF;
        END $$;
    """)

    # ── 슈퍼어드민 파트 + 관리자 시드 ──────────────────────────────
    await conn.execute("""
        INSERT INTO ops_part (name) VALUES ('슈퍼어드민') ON CONFLICT (name) DO NOTHING
    """)
    # 슈퍼어드민 part_id 조회
    superadmin_part_id = await conn.fetchval(
        "SELECT id FROM ops_part WHERE name = '슈퍼어드민'"
    )
    # 구 '기본' 파트가 남아있으면 제거 (마이그레이션) — 컬럼이 없으면 skip
    if await _column_exists(conn, "ops_user", "part"):
        await conn.execute("UPDATE ops_user SET part = '슈퍼어드민' WHERE part = '기본'")
    if await _column_exists(conn, "ops_namespace", "owner_part"):
        await conn.execute("UPDATE ops_namespace SET owner_part = '슈퍼어드민' WHERE owner_part = '기본'")
    await conn.execute("""
        DELETE FROM ops_part WHERE name = '기본'
    """)

    # ── ops_user.part → part_id 동기화 (part 컬럼이 있을 때만) ──────
    if await _column_exists(conn, "ops_user", "part"):
        await conn.execute("""
            UPDATE ops_user u
            SET part_id = p.id
            FROM ops_part p
            WHERE u.part = p.name AND u.part_id IS NULL
        """)

    # ── ops_namespace.owner_part → owner_part_id 동기화 ────────────
    if await _column_exists(conn, "ops_namespace", "owner_part"):
        await conn.execute("""
            UPDATE ops_namespace n
            SET owner_part_id = p.id
            FROM ops_part p
            WHERE n.owner_part = p.name AND n.owner_part_id IS NULL
        """)

    admin_exists = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM ops_user WHERE username = 'admin')"
    )
    if not admin_exists:
        hashed = hash_password(settings.admin_default_password)
        await conn.execute(
            "INSERT INTO ops_user (username, hashed_password, role, part_id) VALUES ($1, $2, $3, $4)",
            "admin", hashed, "admin", superadmin_part_id,
        )
        logger.info("기본 관리자 계정 생성됨 (admin / %s)", settings.admin_default_password)
    else:
        # role/part_id만 동기화 — 비밀번호는 건드리지 않는다(2026-09-03 수정).
        # 예전엔 재시작마다 hashed_password를 admin_default_password로 무조건 덮어써서,
        # 관리자가 UI로 비밀번호를 바꿔도 다음 배포/재시작 때 조용히 원복되는 실제
        # 취약점이었다 — 팀 규모 SSO 인프라 점검 중 발견.
        await conn.execute(
            "UPDATE ops_user SET role = 'admin', part_id = $1 WHERE username = 'admin'",
            superadmin_part_id,
        )

    # ── ops_conversation.user_id 추가 ──────────────────────────────
    await conn.execute("ALTER TABLE ops_conversation ADD COLUMN IF NOT EXISTS user_id INT")
    await conn.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_conversation_user'
            ) THEN
                ALTER TABLE ops_conversation
                    ADD CONSTRAINT fk_conversation_user
                    FOREIGN KEY (user_id) REFERENCES ops_user(id) ON DELETE CASCADE;
            END IF;
        END $$;
    """)
    # 기존 대화에 user_id 없으면 admin에게 귀속
    admin_id = await conn.fetchval("SELECT id FROM ops_user WHERE username = 'admin'")
    if admin_id:
        await conn.execute(
            "UPDATE ops_conversation SET user_id = $1 WHERE user_id IS NULL", admin_id,
        )

    # ── 지식/용어/퓨샷 테이블에 created_by_part, created_by_user_id 추가 ──
    for tbl in ("rag_knowledge", "rag_glossary", "rag_fewshot"):
        await conn.execute(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS created_by_part VARCHAR(100)")
        await conn.execute(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS created_by_user_id INT")

    # ── rag_fewshot status 컬럼 추가 ──
    await conn.execute("ALTER TABLE rag_fewshot ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'")

    # ── 기존 ops_namespace 데이터 보충 (namespace 컬럼이 있는 경우만) ──
    if await _column_exists(conn, "rag_knowledge", "namespace"):
        await conn.execute("""
            INSERT INTO ops_namespace (name)
            SELECT DISTINCT ns FROM (
                SELECT namespace AS ns FROM rag_glossary WHERE namespace IS NOT NULL
                UNION SELECT namespace FROM rag_knowledge WHERE namespace IS NOT NULL
                UNION SELECT namespace FROM ops_query_log WHERE namespace IS NOT NULL
                UNION SELECT namespace FROM ops_conversation WHERE namespace IS NOT NULL
                UNION SELECT namespace FROM ops_feedback WHERE namespace IS NOT NULL
                UNION SELECT namespace FROM rag_fewshot WHERE namespace IS NOT NULL
            ) t WHERE ns IS NOT NULL
            ON CONFLICT (name) DO NOTHING
        """)

    # ── agent_type 컬럼 추가 (멀티 에이전트 확장 준비) ───────────────
    await conn.execute("ALTER TABLE ops_conversation ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'")
    await conn.execute("ALTER TABLE ops_query_log ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'")
    await conn.execute("ALTER TABLE ops_feedback ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'")
    await conn.execute("ALTER TABLE ops_feedback ADD COLUMN IF NOT EXISTS meta JSONB")
    await conn.execute("ALTER TABLE IF EXISTS ops_mcp_tool ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'")
    await conn.execute("ALTER TABLE IF EXISTS sql_fewshot ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'approved'")

    # ── ops_conversation.agent_type 소급 보정 ───────────────────────
    # 대화방 생성 코드가 이 컬럼을 실제로 세팅하지 않아 전부 DB 기본값
    # ('knowledge_rag')으로 남아있었음. text2sql 에이전트가 만든 메시지만
    # metadata를 채우므로(다른 에이전트는 절대 채우지 않음, service/chat/helpers.py
    # update_assistant_message 참고) 이를 근거로 역보정한다.
    await conn.execute("""
        UPDATE ops_conversation SET agent_type = 'text2sql'
        WHERE agent_type != 'text2sql' AND id IN (
            SELECT DISTINCT conversation_id FROM ops_message WHERE metadata IS NOT NULL
        )
    """)

    # ── query_log answer 역매칭 ────────────────────────────────────
    # namespace 내에서 ql.question과 동일한 내용의 user 메시지가 앞서 존재하는
    # assistant 메시지 중 가장 최근 것을 정답으로 채택 (DISTINCT ON으로 상관
    # 조건을 한 번만 평가 — 이전에는 "최신 매칭 찾기" 로직이 이중 EXISTS로 중복됨)
    await conn.execute("""
        WITH latest_match AS (
            SELECT DISTINCT ON (ql.id) ql.id AS ql_id, m.content
            FROM ops_query_log ql
            JOIN ops_conversation c ON c.namespace_id = ql.namespace_id
            JOIN ops_message m ON m.conversation_id = c.id AND m.role = 'assistant'
            JOIN ops_message um ON um.conversation_id = m.conversation_id
                AND um.role = 'user' AND um.content = ql.question AND um.created_at < m.created_at
            WHERE ql.answer IS NULL
            ORDER BY ql.id, m.created_at DESC
        )
        UPDATE ops_query_log ql
        SET answer = lm.content
        FROM latest_match lm
        WHERE ql.id = lm.ql_id
    """)

    # ── ops_query_log.status 소급 보정 (지식 공백 누락) ─────────────
    # 검색된 문서가 임계값은 넘었지만 실제로는 질문과 무관해서 LLM이 스스로
    # "관련 지식을 찾지 못했습니다"라고 답한 경우, had_context만 보는 기존
    # 로직은 이를 'pending'으로 잘못 분류해 지식 공백 대시보드에 안 잡혔음
    # (service/chat/helpers.py create_query_log 참고). answer 역매칭 이후에
    # 실행해야 예전에 answer가 NULL이던 행도 함께 잡힌다. 이미 'resolved'
    # 처리된 건은 관리자가 조치를 마친 것이므로 건드리지 않는다.
    # 답변 시작 부분에 마커가 있는 경우만 매칭 — service/chat/helpers.py의
    # startswith 판정과 기준을 맞춘다(문서 인용 등으로 문장 중간에 같은 문구가
    # 우연히 들어간 정상 답변까지 지식공백으로 오분류하는 걸 방지)
    await conn.execute(
        """
        UPDATE ops_query_log
        SET status = 'no_knowledge'
        WHERE status IN ('pending', 'unresolved')
          AND answer LIKE $1
        """,
        f"{NO_KNOWLEDGE_MARKER}%",
    )

    # ── 성능 인덱스 (멱등) ──────────────────────────────────────────
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_message_conv_id ON ops_message (conversation_id, created_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_user_id ON ops_conversation (user_id, created_at DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_ns_user ON ops_conversation (namespace_id, user_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_query_log_ns_status ON ops_query_log (namespace_id, status)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_fewshot_ns_id ON rag_fewshot (namespace_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_ns_id ON ops_feedback (namespace_id)")


async def _migrate_namespace_ids(conn) -> None:
    """namespace_id 컬럼 추가 및 FK 제약 조건 마이그레이션 (모든 관련 테이블)."""
    # ── namespace_id 컬럼 추가 및 데이터 채우기 ────────────────────
    for tbl in ("rag_glossary", "rag_knowledge", "rag_knowledge_category",
                "ops_query_log", "ops_conversation", "ops_feedback", "rag_fewshot"):
        await conn.execute(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS namespace_id INT")
        # string namespace → namespace_id 동기화 (namespace 컬럼이 있는 경우)
        if await _column_exists(conn, tbl, "namespace"):
            await conn.execute(f"""
                UPDATE {tbl} t
                SET namespace_id = n.id
                FROM ops_namespace n
                WHERE t.namespace = n.name AND t.namespace_id IS NULL
            """)

    # ── namespace_id FK 제약 추가 (멱등) ───────────────────────────
    fk_map = {
        "rag_glossary": "fk_glossary_namespace_id",
        "rag_knowledge": "fk_knowledge_namespace_id",
        "rag_knowledge_category": "fk_knowledge_cat_namespace_id",
        "ops_query_log": "fk_query_log_namespace_id",
        "ops_conversation": "fk_conversation_namespace_id",
        "ops_feedback": "fk_feedback_namespace_id",
        "rag_fewshot": "fk_fewshot_namespace_id",
    }
    for tbl, constraint in fk_map.items():
        await conn.execute(f"""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = '{constraint}'
                ) THEN
                    ALTER TABLE {tbl}
                        ADD CONSTRAINT {constraint}
                        FOREIGN KEY (namespace_id) REFERENCES ops_namespace(id) ON DELETE CASCADE;
                END IF;
            END $$;
        """)


async def _migrate_mcp_tables(conn) -> None:
    """ops_mcp_tool, ops_mcp_tool_log, ops_part_agent_access 테이블 마이그레이션."""
    # ── MCP 도구 테이블 (ops_http_tool 하위 호환 마이그레이션) ──────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_mcp_tool (
            id              SERIAL PRIMARY KEY,
            namespace_id    INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            name            VARCHAR(100) NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            method          VARCHAR(10) NOT NULL DEFAULT 'GET',
            hub_base_url    TEXT NOT NULL DEFAULT '',
            tool_path       TEXT NOT NULL DEFAULT '',
            headers         JSONB NOT NULL DEFAULT '{}',
            param_schema    JSONB NOT NULL DEFAULT '[]',
            response_example JSONB,
            timeout_sec     INT NOT NULL DEFAULT 10,
            max_response_kb INT NOT NULL DEFAULT 50,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id INT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_tool_ns_active ON ops_mcp_tool (namespace_id, is_active)")
    # ops_http_tool이 존재하면 데이터 이전 후 삭제
    try:
        await conn.execute("""
            INSERT INTO ops_mcp_tool (namespace_id, name, description, method, hub_base_url, tool_path, headers,
                param_schema, response_example, timeout_sec, max_response_kb, is_active, created_by_user_id, created_at, updated_at)
            SELECT namespace_id, name, description, method, '', url, headers,
                param_schema, response_example, timeout_sec, max_response_kb, is_active, created_by_user_id, created_at, updated_at
            FROM ops_http_tool
            WHERE NOT EXISTS (SELECT 1 FROM ops_mcp_tool WHERE ops_mcp_tool.namespace_id = ops_http_tool.namespace_id AND ops_mcp_tool.name = ops_http_tool.name)
        """)
    except Exception:
        pass  # ops_http_tool이 없는 경우 (신규 설치)

    # ── MCP 도구 감사 로그 테이블 ──────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_mcp_tool_log (
            id              SERIAL PRIMARY KEY,
            tool_id         INT REFERENCES ops_mcp_tool(id) ON DELETE SET NULL,
            tool_name       VARCHAR(100),
            user_id         INT REFERENCES ops_user(id) ON DELETE SET NULL,
            namespace_id    INT REFERENCES ops_namespace(id),
            conversation_id INT,
            params          JSONB,
            response_status INT,
            response_kb     FLOAT,
            duration_ms     INT,
            error           TEXT,
            called_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_tool_log_ns ON ops_mcp_tool_log (namespace_id, called_at DESC)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_tool_log_tool ON ops_mcp_tool_log (tool_id, called_at DESC)")
    # 기존 테이블에 컬럼 추가 (없으면 추가)
    await conn.execute("ALTER TABLE ops_mcp_tool_log ADD COLUMN IF NOT EXISTS request_url TEXT")
    await conn.execute("ALTER TABLE ops_mcp_tool_log ADD COLUMN IF NOT EXISTS http_method VARCHAR(10)")

    # ── 파트-에이전트 접근 제어 ──────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_part_agent_access (
            id          SERIAL PRIMARY KEY,
            part_id     INT NOT NULL REFERENCES ops_part(id) ON DELETE CASCADE,
            agent_type  VARCHAR(50) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (part_id, agent_type)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_part_agent_access ON ops_part_agent_access (part_id)")


async def _migrate_text2sql_tables(conn) -> None:
    """모든 sql_* 테이블, HNSW 인덱스 및 시드 데이터 마이그레이션."""
    # ── Text2SQL: 대상 DB 연결 정보 ─────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_target_db (
            id                  SERIAL PRIMARY KEY,
            namespace_id        INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            db_type             VARCHAR(20) NOT NULL DEFAULT 'postgresql',
            host                VARCHAR(255) NOT NULL DEFAULT '',
            port                INT NOT NULL DEFAULT 5432,
            db_name             VARCHAR(255) NOT NULL DEFAULT '',
            username            VARCHAR(255) NOT NULL DEFAULT '',
            encrypted_password  TEXT NOT NULL DEFAULT '',
            schema_name         VARCHAR(255) DEFAULT NULL,
            is_active           BOOLEAN NOT NULL DEFAULT TRUE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (namespace_id)
        )
    """)
    # 마이그레이션: schema_name 컬럼 추가
    await conn.execute("""
        ALTER TABLE sql_target_db ADD COLUMN IF NOT EXISTS schema_name VARCHAR(255) DEFAULT NULL
    """)

    # ── Text2SQL: 스키마 테이블 ──────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_schema_table (
            id              SERIAL PRIMARY KEY,
            namespace_id    INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            table_name      VARCHAR(255) NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            pos_x           FLOAT NOT NULL DEFAULT 0,
            pos_y           FLOAT NOT NULL DEFAULT 0,
            is_selected     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (namespace_id, table_name)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_schema_table_ns ON sql_schema_table (namespace_id)")

    # ── Text2SQL: 스키마 컬럼 ────────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_schema_column (
            id              SERIAL PRIMARY KEY,
            table_id        INT NOT NULL REFERENCES sql_schema_table(id) ON DELETE CASCADE,
            name            VARCHAR(255) NOT NULL,
            data_type       VARCHAR(100) NOT NULL DEFAULT '',
            description     TEXT NOT NULL DEFAULT '',
            is_pk           BOOLEAN NOT NULL DEFAULT FALSE,
            fk_reference    VARCHAR(500),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_schema_column_table ON sql_schema_column (table_id)")

    # ── Text2SQL: 스키마 벡터 (pgvector 768차원) ─────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_schema_vector (
            id          SERIAL PRIMARY KEY,
            column_id   INT NOT NULL REFERENCES sql_schema_column(id) ON DELETE CASCADE UNIQUE,
            namespace_id INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            embedding   VECTOR(768)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_schema_vector_ns ON sql_schema_vector (namespace_id)")
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sql_schema_vector_hnsw
        ON sql_schema_vector USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # ── Text2SQL: 테이블 관계 ────────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_relation (
            id              SERIAL PRIMARY KEY,
            namespace_id    INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            from_table      VARCHAR(255) NOT NULL,
            from_col        VARCHAR(255) NOT NULL,
            to_table        VARCHAR(255) NOT NULL,
            to_col          VARCHAR(255) NOT NULL,
            relation_type   VARCHAR(20) NOT NULL DEFAULT 'N:1',
            description     TEXT NOT NULL DEFAULT '',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_relation_ns ON sql_relation (namespace_id)")

    # ── Text2SQL: SQL 용어 사전 ──────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_synonym (
            id              SERIAL PRIMARY KEY,
            namespace_id    INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            term            VARCHAR(255) NOT NULL,
            target          TEXT NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            embedding       VECTOR(768),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_synonym_ns ON sql_synonym (namespace_id)")
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sql_synonym_hnsw
        ON sql_synonym USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # ── Text2SQL: SQL 예제 (Fewshot) ─────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_fewshot (
            id              SERIAL PRIMARY KEY,
            namespace_id    INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            question        TEXT NOT NULL,
            sql             TEXT NOT NULL,
            category        VARCHAR(100) NOT NULL DEFAULT '',
            hits            INT NOT NULL DEFAULT 0,
            last_hit        TIMESTAMPTZ,
            embedding       VECTOR(768),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_fewshot_ns ON sql_fewshot (namespace_id)")
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sql_fewshot_hnsw
        ON sql_fewshot USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # ── Text2SQL: 파이프라인 스테이지 설정 (전역) ───────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_pipeline_stage (
            id              VARCHAR(30) PRIMARY KEY,
            name            VARCHAR(100) NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            icon            VARCHAR(50) NOT NULL DEFAULT '',
            color           VARCHAR(20) NOT NULL DEFAULT '#888',
            is_required     BOOLEAN NOT NULL DEFAULT FALSE,
            is_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
            prompt          TEXT,
            system_prompt   TEXT,
            extra_prompts   TEXT,
            order_num       INT NOT NULL DEFAULT 0,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    # 기본 파이프라인 스테이지 시드
    await conn.execute("""
        INSERT INTO sql_pipeline_stage
            (id, name, description, icon, color, is_required, is_enabled, order_num, system_prompt, prompt)
        VALUES
            ('parse',         '질문 분석',   'Intent/difficulty/entities 추출', 'Search',        '#6366f1', TRUE,  TRUE,  1,
             'You are a query parser for a Text-to-SQL system. Always respond with valid JSON.',
             $1),
            ('rag',           'RAG 검색',   '스키마/용어/예제 벡터 검색',        'Database',      '#8b5cf6', TRUE,  TRUE,  2,
             NULL, NULL),
            ('schema_link',   '스키마 연결', '[미구현] LLM 관련 테이블 식별',    'Link',          '#a78bfa', FALSE, FALSE, 3,
             NULL, NULL),
            ('schema_explore','스키마 탐색', '[미구현] 실제 DB sample values 탐색', 'Layers',     '#34d399', FALSE, FALSE, 4,
             NULL, NULL),
            ('generate',      'SQL 생성',   'LLM 기반 SQL 쿼리 생성',          'Code',          '#10b981', TRUE,  TRUE,  5,
             'You are an expert SQL generator. Think step-by-step, then return the SQL.',
             $2),
            ('candidates',    '후보 평가',   '[미구현] 복수 SQL 후보 중 최적 선택', 'GitBranch',  '#fb923c', FALSE, FALSE, 6,
             NULL, NULL),
            ('validate',      'SQL 검증',   'Safety + AST 기반 SQL 검증',       'ShieldCheck',   '#f59e0b', FALSE, TRUE,  7,
             NULL, NULL),
            ('fix',           '자동 수정',   '검증 실패 시 LLM 자동 수정',       'Wrench',        '#ef4444', FALSE, TRUE,  8,
             NULL, NULL),
            ('execute',       '쿼리 실행',   '대상 DB에 SQL 실행',              'Play',          '#3b82f6', FALSE, TRUE,  9,
             NULL, NULL),
            ('summarize',     '결과 요약',   'LLM 결과 요약 + 차트 추천',        'BarChart2',     '#06b6d4', FALSE, FALSE, 10,
             'You are a data analyst. Respond ONLY with valid JSON.',
             $3)
        ON CONFLICT (id) DO NOTHING
    """,
        # parse prompt
        """다음 사용자 질문을 분석하여 JSON으로 반환하세요.

질문: {{question}}

반환 형식:
{
  "intent": "simple_select|aggregation|join|subquery|window_function|cte",
  "difficulty": "simple|moderate|complex",
  "entities": ["언급된 테이블/컬럼명 후보"],
  "conditions": [{"type": "date|filter", "column": "컬럼명", "value": "값"}],
  "aggregation": "집계 표현식 (없으면 null)",
  "keywords": ["핵심 키워드"]
}""",
        # generate prompt
        """다음 정보를 바탕으로 {{db_type}} SQL 쿼리를 작성하세요.

[질문]
{{question}}

[스키마]
{{schema}}

[테이블 관계]
{{relations}}

[유사 용어]
{{synonyms}}

[SQL 예제]
{{fewshots}}

[이전 대화]
{{history}}

[난이도]
{{difficulty}}

{{cot_instruction}}

{{enriched_schema}}

DB 방언 규칙:
{{dialect_rules}}

<reasoning>
(단계별 사고 과정)
</reasoning>

```sql
-- 최종 SQL
```""",
        # summarize prompt
        """다음 SQL 실행 결과를 분석하여 JSON으로 반환하세요.

질문: {{question}}
SQL: {{sql}}
결과 (최대 20행): {{result_preview}}
컬럼: {{columns}}

{
  "summary": "한국어 1~2문장 요약",
  "chart": null 또는 {"type": "bar|line|pie|scatter|area", "x": "컬럼명", "y": "컬럼명", "title": "차트 제목"}
}""",
    )

    # ── Text2SQL: 감사 로그 ──────────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_audit_log (
            id              SERIAL PRIMARY KEY,
            namespace_id    INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            question        TEXT NOT NULL,
            sql             TEXT,
            status          VARCHAR(20) NOT NULL DEFAULT 'success',
            duration_ms     INT NOT NULL DEFAULT 0,
            cached          BOOLEAN NOT NULL DEFAULT FALSE,
            tokens          INT NOT NULL DEFAULT 0,
            error           TEXT,
            result_preview  TEXT,
            stages_json     TEXT,
            feedback_type   VARCHAR(10),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_audit_ns ON sql_audit_log (namespace_id, created_at DESC)")

    # ── Text2SQL: SQL 캐시 ───────────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sql_cache (
            id              SERIAL PRIMARY KEY,
            namespace_id    INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            question_hash   VARCHAR(64) NOT NULL,
            question        TEXT NOT NULL,
            sql             TEXT NOT NULL,
            hits            INT NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at      TIMESTAMPTZ,
            UNIQUE (namespace_id, question_hash)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_cache_ns ON sql_cache (namespace_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_sql_cache_expires ON sql_cache (expires_at)")


async def _migrate_system_tables(conn) -> None:
    """ops_system_config, ops_prompt 테이블 및 시드 데이터 마이그레이션."""
    # ── ops_message.metadata 컬럼 추가 (text2sql 결과 영속화) ──────────
    await conn.execute("ALTER TABLE ops_message ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT NULL")
    # ── 시스템 설정 테이블 ────────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_system_config (
            key         VARCHAR(100) PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    # 캐시 설정 기본값 시드 (최초 1회만)
    await conn.execute("""
        INSERT INTO ops_system_config (key, value) VALUES
        ('cache_enabled', 'true'),
        ('cache_similarity_threshold', '0.88'),
        ('cache_ttl', '1800')
        ON CONFLICT (key) DO NOTHING
    """)

    # ── 프롬프트 관리 테이블 ──────────────────────────────────────────
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_prompt (
            id              SERIAL PRIMARY KEY,
            func_key        VARCHAR(100) NOT NULL UNIQUE,
            func_name       VARCHAR(200) NOT NULL,
            content         TEXT NOT NULL DEFAULT '',
            description     TEXT NOT NULL DEFAULT '',
            agent_type      VARCHAR(50) NOT NULL DEFAULT 'all',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # agent_type 컬럼 마이그레이션 (기존 DB 대응)
    await conn.execute("ALTER TABLE ops_prompt ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NOT NULL DEFAULT 'all'")
    # 기본 프롬프트 시드 데이터
    await conn.execute("""
        INSERT INTO ops_prompt (func_key, func_name, content, description, agent_type) VALUES
        ('chat_system',         'RAG 채팅 시스템',         $1,  'RAG 기반 지식 검색 채팅의 시스템 프롬프트',                              'knowledge_rag'),
        ('tool_select',         'MCP 도구 선택',           $2,  'MCP 도구를 선택하고 파라미터를 추출하는 프롬프트',                        'mcp_tool'),
        ('tool_answer',         'MCP 응답 답변',           $3,  'MCP API 응답 데이터 기반으로 답변을 생성하는 프롬프트',                   'mcp_tool'),
        ('autocomplete',        '도구 등록 자동완성',       $4,  'MCP 도구 등록 시 자연어→JSON 변환 프롬프트',                             'mcp_tool'),
        ('category_suggest',    '카테고리 자동 추천',       $5,  '지식 내용 분석 후 업무구분 추천. {categories}·{content} 플레이스홀더 필수', 'knowledge_rag'),
        ('glossary_suggest',    '용어 추천 시스템',         $6,  '미매핑 질문에서 업무 용어를 추출하는 시스템 프롬프트',                    'knowledge_rag'),
        ('conv_summarize',      '대화 요약',               $7,  '대화 기록을 요약하는 프롬프트. {dialogue} 플레이스홀더 유지 필수',          'all'),
        ('sql2_parse',          'SQL 질문 분석',           $8,  'Text2SQL 파이프라인 1단계: intent/difficulty/entities 추출 프롬프트. {{question}} 플레이스홀더 필수', 'text2sql'),
        ('sql2_parse_system',   'SQL 질문 분석 시스템',    $9,  'Text2SQL parse 단계 시스템 프롬프트',                                    'text2sql'),
        ('sql2_generate',       'SQL 생성',                $10, 'Text2SQL 파이프라인 3단계: SQL 생성 프롬프트. {{question}}·{{schema}} 등 플레이스홀더 필수', 'text2sql'),
        ('sql2_generate_system','SQL 생성 시스템',          $11, 'Text2SQL generate 단계 시스템 프롬프트',                                 'text2sql'),
        ('sql2_fix',            'SQL 자동 수정',           $12, 'Text2SQL 파이프라인 5단계: 검증 실패 SQL 수정 프롬프트. {{sql}}·{{errors}}·{{schema}} 필수', 'text2sql'),
        ('sql2_fix_system',     'SQL 자동 수정 시스템',    $13, 'Text2SQL fix 단계 시스템 프롬프트',                                      'text2sql'),
        ('sql2_summarize',      'SQL 결과 요약',           $14, 'Text2SQL 파이프라인 7단계: 실행 결과 요약+차트 추천 프롬프트. {{question}}·{{sql}}·{{result_preview}}·{{columns}} 필수', 'text2sql'),
        ('sql2_summarize_system','SQL 결과 요약 시스템',   $15, 'Text2SQL summarize 단계 시스템 프롬프트',                                 'text2sql')
        ON CONFLICT (func_key) DO NOTHING
    """,
        # chat_system — NO_KNOWLEDGE_MARKER를 그대로 삽입해 service/chat/helpers.py의
        # 지식공백 감지 문자열과 프롬프트 지시문이 절대 따로 놀지 않도록 한다
        f"""IT 운영 보조 에이전트. 아래 규칙을 따르세요.

[원칙]
- 반드시 제공된 [참고 문서]만 근거로 답변. 문서에 없는 내용은 절대 만들어내지 마세요.
- 관련 문서가 없으면 다른 설명이나 마크다운 서식 없이 정확히 "{NO_KNOWLEDGE_MARKER}"라고만
  답변하세요. 이 경우 아래 [형식] 규칙(마크다운, 근거 표시 등)은 적용하지 않습니다.
- 신뢰도 높음 문서를 우선 근거로 사용. 낮음은 보조 참고만.

[문맥 활용]
- [과거 유사 사례]가 있으면 답변 형식을 참고하되 현재 문서 내용 우선.
- 이전 대화가 있으면 맥락을 이어서 답변.

[형식]
- Markdown(표, 목록, 코드 블록, 볼드) 사용. 한국어 답변.
- 컨테이너명, 테이블명, SQL이 있으면 반드시 포함.
- 답변 끝에 근거 표시: 📎 문서 N, 문서 M 참고""",
        # tool_select
        """HTTP API 도구 선택 AI. 사용자 질문을 분석해 도구를 선택하고 파라미터를 추출한다.

규칙:
1. 파라미터 값은 사용자 메시지에서 명시된 값만 추출. 언급 없으면 missing_params에 등록.
2. example 값은 입력 힌트일 뿐 — 사용자가 말하지 않은 경우 절대 기본값으로 채우지 말 것.
3. 도구 설명이 질문 의도와 명확히 맞을 때만 선택. 불확실하면 no_tool 반환.
4. 반드시 순수 JSON만 출력. 마크다운·설명 없이.""",
        # tool_answer
        """실시간 API 데이터와 내부 지식베이스를 통합하여 사용자 질문에 답변하는 AI.

답변 원칙:
- API 데이터: 현재 상태·실시간 값의 1차 근거. 빈 배열·null은 "조회 결과 없음"으로 해석.
- 내부 지식베이스: 코드 정의·업무 규칙·배경 지식. API 응답에 코드값(예: "W", "40", "01")이 있으면 지식베이스에서 해당 정의를 찾아 함께 설명.
- 두 소스를 통합해 완성도 높게 답변. API가 비어있어도 지식베이스로 답변 가능하면 답변.
- 어느 소스에도 없는 내용은 생성하지 마세요.
- Markdown 형식, 한국어 답변.""",
        # autocomplete
        """당신은 JSON 변환 전문가입니다. 사용자가 자연어로 설명하는 HTTP API 정보를 구조화된 JSON으로 변환합니다.
반드시 JSON만 출력하세요. 설명, 인사말, 마크다운 코드 블록 없이 순수 JSON만 반환합니다.""",
        # category_suggest
        """다음 지식 내용을 읽고, 제시된 업무구분 중 가장 적합한 하나를 골라주세요. 반드시 제시된 업무구분 중 하나의 이름만 답하고, 다른 설명은 절대 하지 마세요.

업무구분 목록: {categories}

지식 내용:
{content}

가장 적합한 업무구분 이름:""",
        # glossary_suggest
        """당신은 업무 용어를 추출하는 전문가입니다. 답변은 반드시 JSON 형식으로만 출력하세요.""",
        # conv_summarize
        """다음은 IT 운영 지원 챗봇과의 대화 기록입니다. 핵심 질문, 파악된 원인, 제시된 해결책, 주요 기술 사실을 3~5문장으로 간결하게 요약해 주세요.

[대화 기록]
{dialogue}

요약:""",
        # sql2_parse
        """다음 사용자 질문을 분석하여 JSON으로 반환하세요.

질문: {{question}}

반환 형식:
{
  "intent": "simple_select|aggregation|join|subquery|window_function|cte",
  "difficulty": "simple|moderate|complex",
  "entities": ["언급된 테이블/컬럼명 후보"],
  "conditions": [{"type": "date|filter", "column": "컬럼명", "value": "값"}],
  "aggregation": "집계 표현식 (없으면 null)",
  "keywords": ["핵심 키워드"]
}""",
        # sql2_parse_system
        "You are a query parser for a Text-to-SQL system. Always respond with valid JSON.",
        # sql2_generate
        """다음 정보를 바탕으로 {{db_type}} SQL 쿼리를 작성하세요.

[질문]
{{question}}

[스키마]
{{schema}}

[테이블 관계]
{{relations}}

[유사 용어]
{{synonyms}}

[SQL 예제]
{{fewshots}}

[이전 대화]
{{history}}

난이도: {{difficulty}}
{{cot_instruction}}

DB 방언 규칙:
{{dialect_rules}}

<reasoning>
(단계별 사고 과정)
</reasoning>

```sql
-- 최종 SQL
```""",
        # sql2_generate_system
        "You are an expert SQL generator. Think step-by-step, then return the SQL.",
        # sql2_fix
        """다음 SQL에 오류가 있습니다. 수정하여 올바른 SQL만 반환하세요.

[원본 SQL]
{{sql}}

[오류 목록]
{{errors}}

[스키마 참고]
{{schema}}

수정된 SQL만 ```sql ... ``` 형식으로 반환하세요.""",
        # sql2_fix_system
        "You are an expert SQL debugger. Fix the SQL based on the errors provided.",
        # sql2_summarize
        """다음 SQL 실행 결과를 분석하여 JSON으로 반환하세요.

질문: {{question}}
SQL: {{sql}}
결과 (최대 20행): {{result_preview}}
컬럼: {{columns}}

{
  "summary": "한국어 1~2문장 요약",
  "chart": null 또는 {"type": "bar|line|pie|scatter|area", "x": "컬럼명", "y": "컬럼명", "title": "차트 제목"}
}""",
        # sql2_summarize_system
        "You are a data analyst. Respond ONLY with valid JSON.",
    )
    # 기존 rows에 agent_type 업데이트 (DEFAULT 'all'로 들어간 경우 보정)
    await conn.execute("""
        UPDATE ops_prompt SET agent_type = 'knowledge_rag'
        WHERE func_key IN ('chat_system','category_suggest','glossary_suggest') AND agent_type = 'all'
    """)
    await conn.execute("""
        UPDATE ops_prompt SET agent_type = 'mcp_tool'
        WHERE func_key IN ('tool_select','tool_answer','autocomplete') AND agent_type = 'all'
    """)
    await conn.execute("""
        UPDATE ops_prompt SET agent_type = 'text2sql'
        WHERE func_key LIKE 'sql2_%' AND agent_type = 'all'
    """)

    # v2.10: execute 필수 해제 + 파이프라인 순서 정리 + 미구현 표시
    await conn.execute("""
        UPDATE sql_pipeline_stage SET is_required = FALSE
        WHERE id = 'execute' AND is_required = TRUE
    """)
    await conn.execute("""
        UPDATE sql_pipeline_stage SET order_num = CASE id
            WHEN 'parse'          THEN 1
            WHEN 'rag'            THEN 2
            WHEN 'schema_link'    THEN 3
            WHEN 'schema_explore' THEN 4
            WHEN 'generate'       THEN 5
            WHEN 'candidates'     THEN 6
            WHEN 'validate'       THEN 7
            WHEN 'fix'            THEN 8
            WHEN 'execute'        THEN 9
            WHEN 'summarize'      THEN 10
            ELSE order_num
        END,
        description = CASE id
            WHEN 'schema_link'    THEN '[미구현] LLM 관련 테이블 식별'
            WHEN 'schema_explore' THEN '[미구현] 실제 DB sample values 탐색'
            WHEN 'candidates'     THEN '[미구현] 복수 SQL 후보 중 최적 선택'
            ELSE description
        END
    """)


async def _migrate_knowledge_ingestion(conn) -> None:
    """v2.13 지식 인제스천 고도화 — 소스 추적 필드 + 인제스천 작업 테이블."""
    # ── rag_knowledge 소스 추적 필드 추가 ──
    await conn.execute("ALTER TABLE rag_knowledge ADD COLUMN IF NOT EXISTS source_file VARCHAR(500)")
    await conn.execute("ALTER TABLE rag_knowledge ADD COLUMN IF NOT EXISTS source_chunk_idx INT")
    await conn.execute("ALTER TABLE rag_knowledge ADD COLUMN IF NOT EXISTS source_type VARCHAR(50) DEFAULT 'manual'")

    # ── 인제스천 작업 추적 테이블 ──
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_ingestion_job (
            id              SERIAL PRIMARY KEY,
            namespace_id    INT REFERENCES ops_namespace(id) ON DELETE CASCADE,
            source_file     VARCHAR(500),
            source_type     VARCHAR(50),
            status          VARCHAR(20) DEFAULT 'processing',
            total_chunks    INT DEFAULT 0,
            created_chunks  INT DEFAULT 0,
            auto_glossary   INT DEFAULT 0,
            auto_fewshot    INT DEFAULT 0,
            chunk_strategy  VARCHAR(50),
            embedding_model VARCHAR(200),
            analyzer_result JSONB,
            error_message   TEXT,
            created_by_user_id INT,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            completed_at    TIMESTAMPTZ
        )
    """)


async def _migrate_duplicate_review(conn) -> None:
    """지식 중복 등록 방지 — 청크 단위 유사도 검사 + 승인 대기(pending_review) 리뷰."""
    await conn.execute("ALTER TABLE rag_knowledge ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active'")
    await conn.execute("ALTER TABLE rag_ingestion_job ADD COLUMN IF NOT EXISTS pending_chunks INT NOT NULL DEFAULT 0")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_knowledge_duplicate_match (
            id                    SERIAL PRIMARY KEY,
            new_knowledge_id      INT NOT NULL REFERENCES rag_knowledge(id) ON DELETE CASCADE,
            matched_knowledge_id  INT NOT NULL REFERENCES rag_knowledge(id) ON DELETE CASCADE,
            similarity            FLOAT NOT NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_dup_match_new ON rag_knowledge_duplicate_match (new_knowledge_id)")


async def _migrate_query_log_resolution(conn) -> None:
    """나빠요 피드백 후 지식 등록으로 해결한 질의를, 등록된 지식 내용과 연결.

    이 컬럼이 없으면 통계 '해결됨' 탭이 원래의(잘못된) AI 답변만 계속 보여주게 된다.
    """
    await conn.execute(
        "ALTER TABLE ops_query_log ADD COLUMN IF NOT EXISTS resolved_knowledge_id "
        "INT REFERENCES rag_knowledge(id) ON DELETE SET NULL"
    )
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_query_log_resolved_knowledge ON ops_query_log (resolved_knowledge_id)")
    # 해결된 시각 — 없으면 "해결됨" 탭이 질문한 시각순으로만 정렬돼, 오래전 질문이
    # 방금 해결돼도 목록 맨 아래에 묻힌다. 기존 resolved 행은 언제 해결됐는지 알 길이
    # 없으므로 created_at으로 소급 채운다(완벽하진 않지만 NULL보다 유의미한 정렬 순서).
    await conn.execute("ALTER TABLE ops_query_log ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ")
    await conn.execute("UPDATE ops_query_log SET resolved_at = created_at WHERE status = 'resolved' AND resolved_at IS NULL")


async def _migrate_email_voc_tables(conn) -> None:
    """VOC 이메일 분석 채널 — 1단계 스키마 (docs/email-analysis-channel-plan.md §11 Track A #1).

    ops_voc_routing: 파트별 담당 메일함 ↔ Teams 웹훅 ↔ 온콜 연락처 매핑(§10).
    ops_email_analysis: 이메일 건별 분석 결과 저장. source_message_id UNIQUE로
    폴링 재조회 윈도우(§9)가 겹쳐도 같은 메일을 중복 분석하지 않도록 방지한다.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_voc_routing (
            id                    SERIAL PRIMARY KEY,
            namespace_id          INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            part                  VARCHAR(100) NOT NULL,
            mailbox_upn           VARCHAR(255) NOT NULL,
            teams_webhook_url     TEXT,
            oncall_contact_name   VARCHAR(100),
            oncall_contact_phone  VARCHAR(50),
            is_active             BOOLEAN NOT NULL DEFAULT TRUE,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (namespace_id, mailbox_upn)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_voc_routing_ns ON ops_voc_routing (namespace_id)")
    # 특정 폴더만 조회 — 기본은 메일함 전체 조회(NULL)이지만, 관리자가 관리자 화면에서
    # 실제 폴더 목록을 불러와 지정하면 그 폴더만 조회하도록 제한 가능(§ 메일함 전체 조회 시
    # 무관한 메일/스팸까지 섞여 들어오는 문제를 원천 차단하기 위함, 실사용 중 발견).
    await conn.execute("ALTER TABLE ops_voc_routing ADD COLUMN IF NOT EXISTS mail_folder_id VARCHAR(300)")
    await conn.execute("ALTER TABLE ops_voc_routing ADD COLUMN IF NOT EXISTS mail_folder_name VARCHAR(200)")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_email_analysis (
            id                  SERIAL PRIMARY KEY,
            namespace_id        INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            routing_id          INT REFERENCES ops_voc_routing(id) ON DELETE SET NULL,
            source_message_id   VARCHAR(300) NOT NULL,
            mailbox_upn         VARCHAR(255) NOT NULL,
            subject             TEXT NOT NULL DEFAULT '',
            sender              VARCHAR(255) NOT NULL DEFAULT '',
            received_at         TIMESTAMPTZ,
            body                TEXT NOT NULL DEFAULT '',
            category            VARCHAR(20),
            severity            VARCHAR(10),
            mismatch_flagged    BOOLEAN NOT NULL DEFAULT FALSE,
            knowledge_ref_ids   INT[],
            resolution_draft    TEXT,
            status              VARCHAR(20) NOT NULL DEFAULT 'analyzed',
            teams_sent_at       TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (namespace_id, source_message_id)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_email_analysis_status ON ops_email_analysis (status)")
    # 30일 보관 정책(retention.py) 삭제 쿼리가 created_at 단독으로 필터링하는데,
    # 기존 인덱스는 전부 namespace_id를 앞세운 복합/표현식 인덱스라 이 삭제문엔
    # 못 쓰인다 — 정리 배치가 매일 풀스캔하지 않도록 별도 인덱스를 둔다.
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_email_analysis_created_at ON ops_email_analysis (created_at)")
    # Teams 발송 실패 사유 기록 — 이력 화면(§12)에서 성공/실패와 원인을 함께 보여주기 위함
    await conn.execute("ALTER TABLE ops_email_analysis ADD COLUMN IF NOT EXISTS notify_error TEXT")
    # LLM 판단 근거 — 이력 화면에서 "왜 이렇게 분류했는지" 함께 보여주기 위함
    await conn.execute("ALTER TABLE ops_email_analysis ADD COLUMN IF NOT EXISTS reasoning TEXT")
    # 폴링 사이클(스케줄러가 도는 매 회차) 단위 성공/실패 이력 — 개별 이메일 이력과
    # 별개로, "사이클이 언제 돌았고 몇 개 메일함이 실패했는지"를 관리자 화면에서
    # 보여주기 위함(개별 이메일 단위 ops_email_analysis만으로는 이 그림이 안 보임).
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_email_poll_cycle (
            id                      SERIAL PRIMARY KEY,
            started_at              TIMESTAMPTZ NOT NULL,
            finished_at             TIMESTAMPTZ,
            namespaces_processed    INT NOT NULL DEFAULT 0,
            mailboxes_ok            INT NOT NULL DEFAULT 0,
            mailboxes_failed        INT NOT NULL DEFAULT 0,
            total_fetched           INT NOT NULL DEFAULT 0,
            total_analyzed          INT NOT NULL DEFAULT 0,
            total_notified          INT NOT NULL DEFAULT 0,
            total_notify_failed     INT NOT NULL DEFAULT 0,
            total_skipped_duplicate INT NOT NULL DEFAULT 0,
            total_skipped_low_relevance INT NOT NULL DEFAULT 0,
            error_summary           TEXT,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_email_poll_cycle_started ON ops_email_poll_cycle (started_at DESC)")
    # 관련지식 유사도 임계치 미달 스킵 건수 — 기존 설치본 대응 (v3.9)
    await conn.execute("ALTER TABLE ops_email_poll_cycle ADD COLUMN IF NOT EXISTS total_skipped_low_relevance INT NOT NULL DEFAULT 0")
    # LLM이 "IT/시스템과 무관한 단순 CS 불만"으로 판단해 Teams 발송을 생략한 건수 —
    # "IT와 무관한 불만이 너무 많이 온다"는 실사용 피드백으로 카테고리 기반 발송
    # 게이팅을 추가하며 신설. 기존 설치본 대응.
    await conn.execute("ALTER TABLE ops_email_poll_cycle ADD COLUMN IF NOT EXISTS total_skipped_not_it INT NOT NULL DEFAULT 0")

    # ── 반복 VOC 패턴 탐지(§ "IT와 무관한 불만도 너무 많이 온다" 피드백 이후,
    # "반복되는 유형인지 감지해서 알려달라" 요구) ──────────────────────────────
    # 관련성 게이트(check_relevance)가 이미 계산하는 임베딩을 그대로 저장해 재사용 —
    # 지식 베이스 비교(기존)와 별개로 "과거 VOC와의 비교"에 재활용한다. LLM 호출
    # 없이 pgvector 코사인 연산만으로 반복 여부를 판정하기 위한 컬럼/테이블이다.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ops_voc_cluster (
            id                      SERIAL PRIMARY KEY,
            namespace_id            INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
            representative_subject  TEXT NOT NULL DEFAULT '',
            representative_embedding VECTOR(768),
            member_count            INT NOT NULL DEFAULT 1,
            first_seen_at           TIMESTAMPTZ NOT NULL,
            last_seen_at            TIMESTAMPTZ NOT NULL,
            notified_at             TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_voc_cluster_ns ON ops_voc_cluster (namespace_id)")
    # 커버리지(해결방안 등록 여부) LLM 검증 캐시 — pattern_detection.get_cluster_coverage()
    # 참고. 순수 코사인 유사도만으론 "같은 어휘를 쓰는 무관한 문서"(예: 서로 다른 배송
    # 불만 클러스터 여러 개가 전혀 다른 주제인 "사이렌오더 결제 취소" 지식과 0.70~0.78
    # 유사도로 매칭)를 못 가른다는 게 실측으로 확인돼(2026-08-25), 임계치를 넘긴 후보에
    # 한해 LLM에게 "이게 진짜 이 VOC의 해결책이 맞는지" 1회 확인시킨다. 이 검증 결과를
    # (클러스터, 매칭된 지식 ID) 단위로 캐싱해둬야 한다 — 캐싱이 없으면 min_count를 넘긴
    # 이후 매 VOC 알림마다(2026-08-25 pattern_info 상시 표시 변경 이후) LLM을 다시 태우게
    # 된다. coverage_knowledge_id가 매칭된 후보와 다를 때만(즉 대표 임베딩이 갱신되며
    # 최고 매칭이 바뀌었을 때만) 재검증한다 — notified_at처럼 "1회성" 가드가 아니라
    # "이 답이 아직도 최신 매칭에 대한 답인가"를 실제로 재확인하는 가드라는 점이 다르다.
    await conn.execute("ALTER TABLE ops_voc_cluster ADD COLUMN IF NOT EXISTS coverage_knowledge_id INT")
    await conn.execute("ALTER TABLE ops_voc_cluster ADD COLUMN IF NOT EXISTS coverage_verified BOOLEAN")
    await conn.execute("ALTER TABLE ops_email_analysis ADD COLUMN IF NOT EXISTS embedding VECTOR(768)")
    await conn.execute(
        "ALTER TABLE ops_email_analysis ADD COLUMN IF NOT EXISTS voc_cluster_id "
        "INT REFERENCES ops_voc_cluster(id) ON DELETE SET NULL"
    )
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_email_analysis_embedding_hnsw
        ON ops_email_analysis USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # pipeline.list_history()의 ORDER BY COALESCE(received_at, created_at) DESC와
    # 컬럼이 정확히 일치해야 인덱스를 탄다 — (namespace_id, received_at) 단순 인덱스로는
    # 이 표현식 정렬에 못 쓰여 전체 정렬이 발생한다. 옛 인덱스는 대체하며 제거.
    await conn.execute("DROP INDEX IF EXISTS idx_email_analysis_ns")
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_analysis_ns_sort "
        "ON ops_email_analysis (namespace_id, (COALESCE(received_at, created_at)) DESC)"
    )

    # ── 폴링 정책 설정값 시드 (§9, ops_system_config 재사용 — 재시작에도 유지돼야 하므로 in-memory 스레숄드 패턴 대신 DB 영속 방식 채택) ──
    # email_relevance_min_score=0.38: 실 메일 데이터로 실측 보정한 값(원래 0.35).
    # service.check_relevance()가 base_weight 부스팅 없는 원점수를 쓰도록 고친 뒤
    # 재측정 — 완전 무관한 메일은 원점수 0.34~0.36, 실제로 관련 있는 메일은 0.42~0.49
    # 대역에 분포해 그 사이인 0.38로 게이트를 잡았다(docs/tech/voc-email-handoff.md 참고).
    await conn.execute("""
        INSERT INTO ops_system_config (key, value) VALUES
        ('email_collection_enabled', 'false'),
        ('email_polling_interval_minutes', '5'),
        ('email_lookback_days', '7'),
        ('email_relevance_min_score', '0.38')
        ON CONFLICT (key) DO NOTHING
    """)
    # 반복 패턴 탐지 임계치 — 실 VOC 데이터 시뮬레이션으로 검증한 값(§ 반복 VOC
    # 패턴 탐지 설계 문서 참고). 0.90은 너무 엄격(거의 안 잡힘), 0.80은 너무 느슨
    # (무관한 것끼리도 묶임) — 0.85/7일/3건이 노이즈 없이 진짜 반복 신호만 잡음.
    await conn.execute("""
        INSERT INTO ops_system_config (key, value) VALUES
        ('email_pattern_similarity_threshold', '0.85'),
        ('email_pattern_window_days', '7'),
        ('email_pattern_min_count', '3')
        ON CONFLICT (key) DO NOTHING
    """)
    # email_graph_credentials 키는 값이 있을 때만 존재 — 미설정 상태를 "행 없음"으로 표현
    # (encrypt_dict로 암호화된 JSON 문자열을 그대로 저장, §7 Q10 승인 후 관리자가 직접 입력)

    # ── 2단계 분석 프롬프트 — "시스템 설정 > 프롬프트 관리"에서 동적 수정 가능 ──
    await conn.execute(
        """
        INSERT INTO ops_prompt (func_key, func_name, content, description, agent_type) VALUES
        ('email_voc_analysis_system', 'VOC 이메일 분석 시스템', $1,
         'VOC 이메일 건별 분류/심각도/오배치 판정의 시스템 프롬프트', 'knowledge_rag'),
        ('email_voc_analysis_prompt', 'VOC 이메일 분석', $2,
         'VOC 이메일 건별 분석 프롬프트. {subject}·{body}·{part}·{context} 플레이스홀더 필수', 'knowledge_rag')
        ON CONFLICT (func_key) DO NOTHING
        """,
        """You are a VOC(Voice of Customer) triage expert for an IT operations team.
Given an internal support email and related knowledge base excerpts, classify the issue.
Always respond with valid JSON only.""",
        """아래는 사내 VOC(문의) 이메일과, 이와 관련해 검색된 참고 지식입니다.

[이메일 제목]
{subject}

[이메일 본문]
{body}

[수신 메일함 담당 파트]
{part}

[참고 지식]
{context}

다음을 판단해 JSON으로만 답하세요:
1. category: 다음 중 하나
   - "system_error": 시스템이 실제로 오작동했다는 구체적 근거가 있는 경우(예: 처리 이력이 누락됨, 정상 흐름이라면 안 나와야 할 오류가 발생함)
   - "user_mistake": 사용자가 절차를 잘못 따르거나 직접 조작을 잘못한 경우
   - "not_it_related": IT 시스템/기술적 오류에 대한 언급이나 정황이 전혀 없는 경우 — 상품 품질(맛, 신선도 등), 배송/배달 자체(지연, 파손), 가격·취소정책에 대한 불만처럼 순수 CS성 문의. 시스템은 설계/정책대로 정상 동작했는데 그 결과(예: 취소 가능 시간이 지나 취소가 거절됨)에 대한 불만인 경우도 여기로 분류하세요. "시스템이 오작동했다"는 구체적 근거 없이 그냥 화가 났다는 이유만으로 system_error로 분류하지 마세요. 이메일에 시스템/기술 이슈 언급이 아예 없다면 uncertain이 아니라 이 항목을 우선 고려하세요
   - "uncertain": system_error일 가능성이 있어 보이는데(오류·오작동 정황은 있지만) 정보가 부족해 확신할 수 없는 경우만 — "IT 업무와 무관해 보인다"는 이유로는 uncertain이 아니라 not_it_related를 쓰세요
2. severity: "low" / "medium" / "high" / "urgent" 중 하나 — 업무 영향도와 긴급성 기준
3. mismatch_flagged: 이메일 내용이 위 "수신 메일함 담당 파트"의 업무 영역과 명백히 다르면 true, 아니면 false
4. resolution_draft: category가 system_error일 때 참고 지식 기반 해결 방안 초안(2~3문장), 아니면 null
5. reasoning: 판단 근거 요약 (1문장)
6. issue_signature: 이 VOC의 핵심 이슈를 짧고 정규화된 문구로 요약(3~8단어, 예: "로그인 500 에러", "결제 후 주문내역 미반영", "배달 상태 준비중 고착"). 발신자마다 표현이 달라도 같은 유형의 문제라면 최대한 같은 문구로 통일하세요 — 이 요약은 서로 다른 사람이 다르게 쓴 같은 유형의 반복 이슈를 찾아내는 데 쓰입니다. category와 무관하게 항상 채우세요

[판단 예시 — 실제로 헷갈렸던 경계 사례]
예시 1:
제목: 배달이요
본문: 주문한 음료가 30분 넘게 늦게 배달됐습니다. 왜 이렇게 오래 걸리나요?
판단: 시스템이 오작동했다는 구체적 근거 없이 배송 자체가 늦은 것에 대한 불만 → category: not_it_related (system_error 아님)

예시 2:
제목: 주문 취소가 시스템에 반영이 안돼요
본문: 취소 버튼을 눌렀고 취소 완료 문자도 받았는데, 주문 내역에는 아직도 "배달 중"으로 뜨고 결제도 그대로 잡혀 있습니다.
판단: 정상 흐름이면 안 나와야 할 처리 누락이 구체적으로 확인됨 → category: system_error

예시 3:
제목: 로그인이 안돼요
본문: 비밀번호를 5번 연속 잘못 입력해서 계정이 잠겼습니다. 어떻게 풀 수 있나요?
판단: 시스템 오류가 아니라 사용자 본인의 조작(비밀번호 오입력)이 원인 → category: user_mistake

예시 4:
제목: 주문이 이상해요
본문: 결제는 됐는데 배송 정보가 계속 안 뜹니다. 시스템 오류인지 그냥 늦는 건지 모르겠어요.
판단: system_error일 가능성이 있어 보이지만 오작동 여부를 판단할 정보가 부족함 → category: uncertain

응답 형식 (JSON 객체만 반환):
{{"category": "...", "severity": "...", "mismatch_flagged": false, "resolution_draft": "...", "reasoning": "...", "issue_signature": "..."}}""",
    )


async def _cleanup_stale_generating_messages(conn) -> None:
    """프로세스가 막 기동했으니, 'generating' 상태로 남은 메시지는 전부 이전
    프로세스가 스트리밍 도중 죽으면서 남긴 고아 행이다(지금 막 시작했으므로 이
    프로세스가 만들었을 리 없음). _cleanup_ghost_messages는 'generating' 상태를
    일부러 삭제 대상에서 제외해왔는데, 그 예외가 오히려 이런 행을 영구히
    "생성 중"으로 화면에 붙박이게 만들고 대화 요약 대상에서도 계속 빠지게 했다.
    """
    result = await conn.execute(
        "UPDATE ops_message SET status = 'failed' WHERE status = 'generating'"
    )
    if result and "UPDATE 0" not in result:
        logger.info("[Startup] 이전 프로세스에서 멈춘 'generating' 메시지 정리: %s", result)


async def _run_migrations() -> None:
    """기존 DB 호환용 스키마 마이그레이션 (멱등)."""
    async with get_conn() as conn:
        await _migrate_core_tables(conn)
        await _migrate_namespace_ids(conn)
        await _migrate_mcp_tables(conn)
        await _migrate_text2sql_tables(conn)
        await _migrate_system_tables(conn)
        await _migrate_knowledge_ingestion(conn)
        await _migrate_duplicate_review(conn)
        await _migrate_query_log_resolution(conn)
        await _migrate_email_voc_tables(conn)
        await _cleanup_stale_generating_messages(conn)


def _warn_if_insecure_defaults() -> None:
    """JWT 시크릿/관리자 기본 비밀번호가 플레이스홀더 그대로면 배포마다 매번 눈에 띄게
    경고한다. 하드 실패(startup 중단)로는 안 만든다 — 지금 이 값 그대로 운영 중인
    배포가 실제로 있어(2026-09-03 확인), 강제 종료하면 그 배포부터 멈춰버린다.
    팀 규모 SSO 인프라 점검 중 발견 — 실제로 시크릿을 교체하는 건 활성 로그인 세션이
    전부 무효화되고 관리자 비밀번호가 바뀌는 파급이 있어 별도로 조율해서 진행한다."""
    if settings.jwt_secret_key == "change-this-secret-key-in-production":
        logger.warning(
            "[보안] JWT_SECRET_KEY가 기본 플레이스홀더입니다 — 이 값을 아는 사람은 "
            "누구든 임의 사용자로 위조된 토큰을 만들 수 있습니다. .env에서 반드시 교체하세요."
        )
    if settings.admin_default_password == "1111":
        logger.warning(
            "[보안] ADMIN_DEFAULT_PASSWORD가 기본값(1111)입니다 — 최초 admin 계정이 "
            "이 비밀번호로 생성됩니다. .env에서 반드시 교체하세요."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _warn_if_insecure_defaults()
    await init_pool()
    await _run_migrations()
    async with get_conn() as conn:
        await sem_cache.load_config_from_db(conn)
    embedding_service.load()
    if settings.reranker_enabled:
        reranker_service.load(settings.reranker_model)

    # ── 에이전트 등록 ──
    AgentRegistry.register(KnowledgeRagAgent())
    AgentRegistry.register(McpToolAgent())
    AgentRegistry.register(Text2SqlAgent())

    llm_ok = await get_llm_provider().health_check()
    level, msg = ("INFO", "연결 확인됨") if llm_ok else ("WARNING", "연결 불가 — LLM 기능 제한")
    logger.log(logging.getLevelName(level), "LLM(%s) %s", settings.llm_provider, msg)

    start_scheduler()

    yield
    await stop_scheduler()
    await close_mcp_http_client()
    await close_pool()


app = FastAPI(title="Ops-Navigator API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in _ROUTERS:
    app.include_router(r)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """서비스 계층에서 raise한 ValueError(잘못된 요청)를 500 대신 400으로 변환."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/health")
async def health():
    llm_ok = await get_llm_provider().health_check()
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "llm": "connected" if llm_ok else "unavailable",
    }
# TC test
# main version
