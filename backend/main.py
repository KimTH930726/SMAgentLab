"""Ops-Navigator FastAPI 진입점 — DDD 구조."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.database import init_pool, close_pool, get_conn
from core.security import hash_password
from shared.embedding import embedding_service
from domain.llm.factory import get_llm_provider

from domain.auth.router import router as auth_router
from domain.chat.router import router as chat_router
from domain.knowledge.router import router as knowledge_router
from domain.fewshot.router import router as fewshot_router
from domain.feedback.router import router as feedback_router
from domain.admin.router import router as admin_router

logger = logging.getLogger(__name__)

_ROUTERS = [
    auth_router, chat_router, knowledge_router,
    fewshot_router, feedback_router, admin_router,
]


async def _run_migrations() -> None:
    """기존 DB 호환용 스키마 마이그레이션 (멱등).

    integer FK 방식으로 전환 후 구 string 컬럼도 유지하여
    구 버전과의 호환성을 보장한다.
    """
    async with get_conn() as conn:
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
        for tbl in ("ops_knowledge", "ops_glossary", "ops_fewshot"):
            await conn.execute(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS created_by_part VARCHAR(100)")
            await conn.execute(f"ALTER TABLE {tbl} ADD COLUMN IF NOT EXISTS created_by_user_id INT")

        # ── 기존 ops_namespace 데이터 보충 (namespace 컬럼이 있는 경우만) ──
        await conn.execute("""
            DO $$ DECLARE
                ns_col_exists BOOLEAN;
            BEGIN
                SELECT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_name='ops_knowledge' AND column_name='namespace')
                INTO ns_col_exists;
                IF ns_col_exists THEN
                    INSERT INTO ops_namespace (name)
                    SELECT DISTINCT ns FROM (
                        SELECT namespace AS ns FROM ops_glossary WHERE namespace IS NOT NULL
                        UNION SELECT namespace FROM ops_knowledge WHERE namespace IS NOT NULL
                        UNION SELECT namespace FROM ops_query_log WHERE namespace IS NOT NULL
                        UNION SELECT namespace FROM ops_conversation WHERE namespace IS NOT NULL
                        UNION SELECT namespace FROM ops_feedback WHERE namespace IS NOT NULL
                        UNION SELECT namespace FROM ops_fewshot WHERE namespace IS NOT NULL
                    ) t WHERE ns IS NOT NULL
                    ON CONFLICT (name) DO NOTHING;
                END IF;
            END $$;
        """)

        # ── namespace_id 컬럼 추가 및 데이터 채우기 ────────────────────
        for tbl in ("ops_glossary", "ops_knowledge", "ops_knowledge_category",
                    "ops_query_log", "ops_conversation", "ops_feedback", "ops_fewshot"):
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
            "ops_glossary": "fk_glossary_namespace_id",
            "ops_knowledge": "fk_knowledge_namespace_id",
            "ops_knowledge_category": "fk_knowledge_cat_namespace_id",
            "ops_query_log": "fk_query_log_namespace_id",
            "ops_conversation": "fk_conversation_namespace_id",
            "ops_feedback": "fk_feedback_namespace_id",
            "ops_fewshot": "fk_fewshot_namespace_id",
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_pool()
    await _run_migrations()
    embedding_service.load()

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
