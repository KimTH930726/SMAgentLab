"""Ops-Navigator FastAPI 진입점 — DDD 구조."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.database import init_pool, close_pool, get_conn
from core.security import hash_password
from shared.embedding import embedding_service
from service.llm.factory import get_llm_provider

from service.auth.router import router as auth_router
from service.chat.router import router as chat_router
from agents.knowledge_rag.knowledge.router import router as knowledge_router
from agents.knowledge_rag.fewshot.router import router as fewshot_router
from service.feedback.router import router as feedback_router
from service.admin.router import router as admin_router
from service.mcp_tool.router import router as mcp_tool_router
from service.prompt.router import router as prompt_router
from agents.text2sql.admin.router import router as text2sql_router

from shared import cache as sem_cache
from agents.base import AgentRegistry
from agents.knowledge_rag.agent import KnowledgeRagAgent
from agents.mcp_tool.agent import McpToolAgent
from agents.text2sql.agent import Text2SqlAgent

logger = logging.getLogger(__name__)

_ROUTERS = [
    auth_router, chat_router, knowledge_router,
    fewshot_router, feedback_router, admin_router,
    mcp_tool_router,
    prompt_router,
    text2sql_router,
]


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
    await conn.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='ops_user' AND column_name='part') THEN
                UPDATE ops_user SET part = '슈퍼어드민' WHERE part = '기본';
            END IF;
        END $$;
    """)
    await conn.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='ops_namespace' AND column_name='owner_part') THEN
                UPDATE ops_namespace SET owner_part = '슈퍼어드민' WHERE owner_part = '기본';
            END IF;
        END $$;
    """)
    await conn.execute("""
        DELETE FROM ops_part WHERE name = '기본'
    """)

    # ── ops_user.part → part_id 동기화 (part 컬럼이 있을 때만) ──────
    await conn.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='ops_user' AND column_name='part') THEN
                UPDATE ops_user u
                SET part_id = p.id
                FROM ops_part p
                WHERE u.part = p.name AND u.part_id IS NULL;
            END IF;
        END $$;
    """)

    # ── ops_namespace.owner_part → owner_part_id 동기화 ────────────
    await conn.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name='ops_namespace' AND column_name='owner_part') THEN
                UPDATE ops_namespace n
                SET owner_part_id = p.id
                FROM ops_part p
                WHERE n.owner_part = p.name AND n.owner_part_id IS NULL;
            END IF;
        END $$;
    """)

    admin_exists = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM ops_user WHERE username = 'admin')"
    )
    hashed = hash_password(settings.admin_default_password)
    if not admin_exists:
        await conn.execute(
            "INSERT INTO ops_user (username, hashed_password, role, part_id) VALUES ($1, $2, $3, $4)",
            "admin", hashed, "admin", superadmin_part_id,
        )
        logger.info("기본 관리자 계정 생성됨 (admin / %s)", settings.admin_default_password)
    else:
        # 기존 admin 비밀번호를 설정값으로 갱신, part_id도 동기화
        await conn.execute(
            "UPDATE ops_user SET hashed_password = $1, role = 'admin', part_id = $2 WHERE username = 'admin'",
            hashed, superadmin_part_id,
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
    await conn.execute("""
        DO $$ DECLARE
            ns_col_exists BOOLEAN;
        BEGIN
            SELECT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='rag_knowledge' AND column_name='namespace')
            INTO ns_col_exists;
            IF ns_col_exists THEN
                INSERT INTO ops_namespace (name)
                SELECT DISTINCT ns FROM (
                    SELECT namespace AS ns FROM rag_glossary WHERE namespace IS NOT NULL
                    UNION SELECT namespace FROM rag_knowledge WHERE namespace IS NOT NULL
                    UNION SELECT namespace FROM ops_query_log WHERE namespace IS NOT NULL
                    UNION SELECT namespace FROM ops_conversation WHERE namespace IS NOT NULL
                    UNION SELECT namespace FROM ops_feedback WHERE namespace IS NOT NULL
                    UNION SELECT namespace FROM rag_fewshot WHERE namespace IS NOT NULL
                ) t WHERE ns IS NOT NULL
                ON CONFLICT (name) DO NOTHING;
            END IF;
        END $$;
    """)

    # ── agent_type 컬럼 추가 (멀티 에이전트 확장 준비) ───────────────
    await conn.execute("ALTER TABLE ops_conversation ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'")
    await conn.execute("ALTER TABLE ops_query_log ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'")
    await conn.execute("ALTER TABLE ops_feedback ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'")
    await conn.execute("ALTER TABLE ops_feedback ADD COLUMN IF NOT EXISTS meta JSONB")
    await conn.execute("ALTER TABLE ops_mcp_tool ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'")
    await conn.execute("ALTER TABLE sql_fewshot ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'approved'")

    # ── query_log answer 역매칭 ────────────────────────────────────
    await conn.execute("""
        UPDATE ops_query_log ql
        SET answer = m.content
        FROM ops_message m
        JOIN ops_conversation c ON m.conversation_id = c.id
        WHERE ql.answer IS NULL
          AND m.role = 'assistant'
          AND c.namespace_id = ql.namespace_id
          AND EXISTS (
              SELECT 1 FROM ops_message um
              WHERE um.conversation_id = m.conversation_id
                AND um.role = 'user'
                AND um.content = ql.question
                AND um.created_at < m.created_at
          )
          AND m.id = (
              SELECT m2.id FROM ops_message m2
              JOIN ops_conversation c2 ON m2.conversation_id = c2.id
              WHERE m2.role = 'assistant'
                AND c2.namespace_id = ql.namespace_id
                AND EXISTS (
                    SELECT 1 FROM ops_message um2
                    WHERE um2.conversation_id = m2.conversation_id
                      AND um2.role = 'user'
                      AND um2.content = ql.question
                      AND um2.created_at < m2.created_at
                )
              ORDER BY m2.created_at DESC
              LIMIT 1
          )
    """)

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
        col_exists = await conn.fetchval(f"""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = '{tbl}' AND column_name = 'namespace'
            )
        """)
        if col_exists:
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
            is_active           BOOLEAN NOT NULL DEFAULT TRUE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (namespace_id)
        )
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
            ('generate',      'SQL 생성',   'LLM 기반 SQL 쿼리 생성',          'Code',          '#10b981', TRUE,  TRUE,  3,
             'You are an expert SQL generator. Think step-by-step, then return the SQL.',
             $2),
            ('validate',      'SQL 검증',   'Safety + AST 기반 SQL 검증',       'ShieldCheck',   '#f59e0b', FALSE, TRUE,  4,
             NULL, NULL),
            ('fix',           '자동 수정',   '검증 실패 시 LLM 자동 수정',       'Wrench',        '#ef4444', FALSE, TRUE,  5,
             NULL, NULL),
            ('execute',       '쿼리 실행',   '대상 DB에 SQL 실행',              'Play',          '#3b82f6', TRUE,  TRUE,  6,
             NULL, NULL),
            ('summarize',     '결과 요약',   'LLM 결과 요약 + 차트 추천',        'BarChart2',     '#06b6d4', FALSE, FALSE, 7,
             'You are a data analyst. Respond ONLY with valid JSON.',
             $3),
            ('schema_link',   '스키마 연결', 'LLM 관련 테이블 식별',            'Link',          '#a78bfa', FALSE, FALSE, 8,
             NULL, NULL),
            ('schema_explore','스키마 탐색', '실제 DB sample values 탐색',      'Layers',        '#34d399', FALSE, FALSE, 9,
             NULL, NULL),
            ('candidates',    '후보 평가',   '복수 SQL 후보 중 최적 선택',       'GitBranch',     '#fb923c', FALSE, FALSE, 10,
             NULL, NULL)
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
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # 기본 프롬프트 시드 데이터
    await conn.execute("""
        INSERT INTO ops_prompt (func_key, func_name, content, description) VALUES
        ('chat_system', 'RAG 채팅 시스템', $1, 'RAG 기반 지식 검색 채팅의 시스템 프롬프트'),
        ('tool_select', 'MCP 도구 선택', $2, 'MCP 도구를 선택하고 파라미터를 추출하는 프롬프트'),
        ('tool_answer', 'MCP 응답 답변', $3, 'MCP API 응답 데이터 기반으로 답변을 생성하는 프롬프트'),
        ('autocomplete', '도구 등록 자동완성', $4, 'MCP 도구 등록 시 자연어→JSON 변환 프롬프트'),
        ('category_suggest', '카테고리 자동 추천', $5, '지식 내용을 분석해 적합한 업무구분을 추천하는 프롬프트. {categories}·{content} 플레이스홀더 유지 필수'),
        ('glossary_suggest', '용어 추천 시스템', $6, '미매핑 질문에서 업무 용어를 추출하는 시스템 프롬프트'),
        ('conv_summarize', '대화 요약', $7, '대화 기록을 요약하는 프롬프트. {dialogue} 플레이스홀더 유지 필수')
        ON CONFLICT (func_key) DO NOTHING
    """,
        # chat_system
        """IT 운영 보조 에이전트. 아래 규칙을 따르세요.

[원칙]
- 반드시 제공된 [참고 문서]만 근거로 답변. 문서에 없는 내용은 절대 만들어내지 마세요.
- 관련 문서가 없으면 "관련 지식을 찾지 못했습니다"로 답변.
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
    )


async def _run_migrations() -> None:
    """기존 DB 호환용 스키마 마이그레이션 (멱등)."""
    async with get_conn() as conn:
        await _migrate_core_tables(conn)
        await _migrate_namespace_ids(conn)
        await _migrate_mcp_tables(conn)
        await _migrate_text2sql_tables(conn)
        await _migrate_system_tables(conn)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool()
    await _run_migrations()
    async with get_conn() as conn:
        await sem_cache.load_config_from_db(conn)
    embedding_service.load()

    # ── 에이전트 등록 ──
    AgentRegistry.register(KnowledgeRagAgent())
    AgentRegistry.register(McpToolAgent())
    AgentRegistry.register(Text2SqlAgent())

    llm_ok = await get_llm_provider().health_check()
    level, msg = ("INFO", "연결 확인됨") if llm_ok else ("WARNING", "연결 불가 — LLM 기능 제한")
    logger.log(logging.getLevelName(level), "LLM(%s) %s", settings.llm_provider, msg)

    yield
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


@app.get("/health")
async def health():
    llm_ok = await get_llm_provider().health_check()
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "llm": "connected" if llm_ok else "unavailable",
    }
