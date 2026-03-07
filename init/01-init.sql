-- Ops-Navigator DB 초기화
-- pgvector 및 pg_trgm 확장 활성화
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- 네임스페이스 테이블 (ops_namespace)
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_namespace (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    description TEXT         NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ============================================================
-- 용어집 테이블 (ops_glossary)
-- 사용자 모호 표현 → 사내 표준 용어 치환용
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_glossary (
    id          SERIAL PRIMARY KEY,
    namespace   VARCHAR(100) NOT NULL REFERENCES ops_namespace(name) ON DELETE CASCADE,
    term        VARCHAR(200) NOT NULL,
    description TEXT         NOT NULL,
    embedding   VECTOR(768)
);

CREATE INDEX IF NOT EXISTS idx_glossary_ns ON ops_glossary (namespace);
-- 충분한 행이 쌓인 후 ivfflat이 효과적이므로 초기엔 HNSW 사용
CREATE INDEX IF NOT EXISTS idx_glossary_emb ON ops_glossary
    USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- 지식 베이스 테이블 (ops_knowledge)
-- 운영 가이드 및 처리 로직 저장
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_knowledge (
    id              SERIAL PRIMARY KEY,
    namespace       VARCHAR(100) NOT NULL REFERENCES ops_namespace(name) ON DELETE CASCADE,
    container_name  VARCHAR(200),
    target_tables   TEXT[],
    content         TEXT         NOT NULL,
    query_template  TEXT,
    embedding       VECTOR(768),
    base_weight     FLOAT        NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_ns ON ops_knowledge (namespace);
CREATE INDEX IF NOT EXISTS idx_knowledge_emb ON ops_knowledge
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_knowledge_fts ON ops_knowledge
    USING GIN (to_tsvector('simple', content));

-- updated_at 자동 갱신 트리거
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_knowledge_updated_at ON ops_knowledge;
CREATE TRIGGER trg_knowledge_updated_at
    BEFORE UPDATE ON ops_knowledge
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- 질의 로그 (ops_query_log)
-- 통계 및 미해결 케이스 추적용
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_query_log (
    id          SERIAL PRIMARY KEY,
    namespace   VARCHAR(100) REFERENCES ops_namespace(name) ON DELETE CASCADE,
    question    TEXT,
    answer      TEXT,
    status      VARCHAR(20) NOT NULL DEFAULT 'pending',
    mapped_term VARCHAR(200),
    message_id  INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_query_log_ns ON ops_query_log (namespace);
CREATE INDEX IF NOT EXISTS idx_query_log_created ON ops_query_log (created_at);

-- ============================================================
-- 대화방 테이블 (ops_conversation)
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_conversation (
    id          SERIAL PRIMARY KEY,
    namespace   VARCHAR(100) NOT NULL REFERENCES ops_namespace(name) ON DELETE CASCADE,
    title       VARCHAR(200) NOT NULL DEFAULT '',
    trimmed     BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_ns ON ops_conversation (namespace);

-- ============================================================
-- 메시지 테이블 (ops_message)
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_message (
    id              SERIAL PRIMARY KEY,
    conversation_id INT          NOT NULL REFERENCES ops_conversation(id) ON DELETE CASCADE,
    role            VARCHAR(20)  NOT NULL,
    content         TEXT         NOT NULL,
    mapped_term     VARCHAR(200),
    results         JSONB,
    status          VARCHAR(20)  NOT NULL DEFAULT 'completed',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_message_conv ON ops_message (conversation_id);

-- ============================================================
-- 피드백 테이블 (ops_feedback)
-- 좋아요/싫어요 기록 (message 뒤에 정의 — FK 의존)
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_feedback (
    id           SERIAL PRIMARY KEY,
    knowledge_id INT          REFERENCES ops_knowledge(id) ON DELETE SET NULL,
    message_id   INT          REFERENCES ops_message(id) ON DELETE SET NULL,
    namespace    VARCHAR(100) REFERENCES ops_namespace(name) ON DELETE CASCADE,
    question     TEXT,
    is_positive  BOOLEAN      NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Few-shot 테이블 (ops_fewshot)
-- 질의-답변 예시 쌍 저장
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_fewshot (
    id           SERIAL PRIMARY KEY,
    namespace    VARCHAR(100) NOT NULL REFERENCES ops_namespace(name) ON DELETE CASCADE,
    question     TEXT         NOT NULL,
    answer       TEXT         NOT NULL,
    knowledge_id INT          REFERENCES ops_knowledge(id) ON DELETE SET NULL,
    embedding    VECTOR(768),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fewshot_ns ON ops_fewshot (namespace);
CREATE INDEX IF NOT EXISTS idx_fewshot_emb ON ops_fewshot
    USING hnsw (embedding vector_cosine_ops);

-- ============================================================
-- 대화 요약 테이블 (ops_conv_summary)
-- 오래된 교환을 LLM으로 요약 후 벡터 저장 — Semantic Recall용
-- ============================================================
CREATE TABLE IF NOT EXISTS ops_conv_summary (
    id              SERIAL PRIMARY KEY,
    conversation_id INT          REFERENCES ops_conversation(id) ON DELETE CASCADE,
    summary         TEXT         NOT NULL,
    embedding       VECTOR(768),
    turn_start      INT          NOT NULL,  -- 요약 범위의 첫 번째 message.id
    turn_end        INT          NOT NULL,  -- 요약 범위의 마지막 message.id
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_summary_conv ON ops_conv_summary (conversation_id);
CREATE INDEX IF NOT EXISTS idx_conv_summary_vec ON ops_conv_summary
    USING hnsw (embedding vector_cosine_ops);
