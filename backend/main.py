"""Ops-Navigator FastAPI 진입점."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import database
from config import settings
from routers import chat, conversations, feedback, fewshots, knowledge, llm_settings, namespaces, stats
from services.embedding import embedding_service
from services.llm import get_llm_provider

logger = logging.getLogger(__name__)

_ROUTERS = [
    chat.router, conversations.router, knowledge.router,
    feedback.router, fewshots.router, llm_settings.router,
    stats.router, namespaces.router,
]


async def _run_migrations() -> None:
    """기존 DB 호환용 스키마 마이그레이션 (멱등)."""
    async with database.get_conn() as conn:
        await conn.execute("ALTER TABLE ops_query_log ADD COLUMN IF NOT EXISTS answer TEXT")
        await conn.execute("ALTER TABLE ops_conversation ADD COLUMN IF NOT EXISTS trimmed BOOLEAN NOT NULL DEFAULT FALSE")
        await conn.execute("ALTER TABLE ops_feedback ADD COLUMN IF NOT EXISTS message_id INT REFERENCES ops_message(id) ON DELETE SET NULL")

        # 기존 데이터에 ops_namespace 누락 행 보충 (FK 추가 전 필수)
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

        # namespace FK 제약 추가 (기존 DB 호환 — 멱등)
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

        # answer가 없는 query_log에 ops_message에서 답변 역매칭
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
    await database.init_pool()
    await _run_migrations()
    embedding_service.load()

    llm_ok = await get_llm_provider().health_check()
    level, msg = ("INFO", "연결 확인됨") if llm_ok else ("WARNING", "연결 불가 — LLM 기능 제한")
    logger.log(logging.getLevelName(level), "LLM(%s) %s", settings.llm_provider, msg)

    yield
    await database.close_pool()


app = FastAPI(title="Ops-Navigator API", version="1.0.0", lifespan=lifespan)

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
