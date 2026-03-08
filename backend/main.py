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
    """기존 DB 호환용 스키마 마이그레이션 (멱등)."""
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

        # ── ops_user 테이블 ────────────────────────────────────────────
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ops_user (
                id                      SERIAL PRIMARY KEY,
                username                VARCHAR(100) NOT NULL UNIQUE,
                hashed_password         TEXT NOT NULL,
                role                    VARCHAR(20) NOT NULL DEFAULT 'user',
                part                    VARCHAR(100) REFERENCES ops_part(name),
                is_active               BOOLEAN NOT NULL DEFAULT TRUE,
                encrypted_llm_api_key   TEXT,
                created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        # ── 기본 파트 + 관리자 시드 ────────────────────────────────────
        await conn.execute("""
            INSERT INTO ops_part (name) VALUES ('기본') ON CONFLICT (name) DO NOTHING
        """)
        admin_exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM ops_user WHERE username = 'admin')"
        )
        hashed = hash_password(settings.admin_default_password)
        if not admin_exists:
            await conn.execute(
                "INSERT INTO ops_user (username, hashed_password, role, part) VALUES ($1, $2, $3, $4)",
                "admin", hashed, "admin", "기본",
            )
            logger.info("기본 관리자 계정 생성됨 (admin / %s)", settings.admin_default_password)
        else:
            # 기존 admin 비밀번호를 설정값으로 갱신
            await conn.execute(
                "UPDATE ops_user SET hashed_password = $1, role = 'admin' WHERE username = 'admin'",
                hashed,
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

        # ── ops_namespace에 owner_part, created_by_user_id 추가 ──
        await conn.execute("ALTER TABLE ops_namespace ADD COLUMN IF NOT EXISTS owner_part VARCHAR(100)")
        await conn.execute("ALTER TABLE ops_namespace ADD COLUMN IF NOT EXISTS created_by_user_id INT")

        # ── 기존 ops_namespace 데이터 보충 (FK 추가 전 필수) ────────────
        await conn.execute("""
            INSERT INTO ops_namespace (name)
            SELECT DISTINCT ns FROM (
                SELECT namespace AS ns FROM ops_glossary
                UNION SELECT namespace FROM ops_knowledge
                UNION SELECT namespace FROM ops_query_log WHERE namespace IS NOT NULL
                UNION SELECT namespace FROM ops_conversation
                UNION SELECT namespace FROM ops_feedback WHERE namespace IS NOT NULL
                UNION SELECT namespace FROM ops_fewshot
            ) t WHERE ns IS NOT NULL
            ON CONFLICT (name) DO NOTHING
        """)

        # ── namespace FK 제약 추가 (멱등) ──────────────────────────────
        for tbl in ("ops_glossary", "ops_knowledge", "ops_query_log",
                     "ops_conversation", "ops_feedback", "ops_fewshot"):
            constraint = f"fk_{tbl}_namespace"
            await conn.execute(f"""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = '{constraint}'
                    ) THEN
                        ALTER TABLE {tbl}
                            ADD CONSTRAINT {constraint}
                            FOREIGN KEY (namespace) REFERENCES ops_namespace(name) ON DELETE CASCADE;
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
              AND c.namespace = ql.namespace
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
                    AND c2.namespace = ql.namespace
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
