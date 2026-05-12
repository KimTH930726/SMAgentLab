-- ============================================================
-- 마이그레이션: namespace/part string FK → integer FK
-- 기존 DB에 적용 (멱등 설계 — 이미 적용된 경우 재실행 안전)
-- ============================================================

BEGIN;

-- ── STEP 1: ops_part 테이블 보장 (이미 존재하면 무시) ──────────────────────
CREATE TABLE IF NOT EXISTS ops_part (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── STEP 2: ops_namespace에 owner_part_id 컬럼 추가 ────────────────────────
ALTER TABLE ops_namespace ADD COLUMN IF NOT EXISTS owner_part_id INT;

-- owner_part(string)에서 owner_part_id(int) 채우기
UPDATE ops_namespace n
SET owner_part_id = p.id
FROM ops_part p
WHERE n.owner_part = p.name
  AND n.owner_part_id IS NULL;

-- owner_part_id FK 제약 추가 (멱등)
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_namespace_owner_part'
    ) THEN
        ALTER TABLE ops_namespace
            ADD CONSTRAINT fk_namespace_owner_part
            FOREIGN KEY (owner_part_id) REFERENCES ops_part(id) ON DELETE SET NULL;
    END IF;
END $$;

-- ── STEP 3: ops_user에 part_id 컬럼 추가 ────────────────────────────────────
ALTER TABLE ops_user ADD COLUMN IF NOT EXISTS part_id INT;

-- part(string)에서 part_id(int) 채우기
UPDATE ops_user u
SET part_id = p.id
FROM ops_part p
WHERE u.part = p.name
  AND u.part_id IS NULL;

-- part_id FK 제약 추가 (멱등)
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_user_part'
    ) THEN
        ALTER TABLE ops_user
            ADD CONSTRAINT fk_user_part
            FOREIGN KEY (part_id) REFERENCES ops_part(id) ON DELETE SET NULL;
    END IF;
END $$;

-- ── STEP 4: ops_glossary에 namespace_id 컬럼 추가 ───────────────────────────
ALTER TABLE ops_glossary ADD COLUMN IF NOT EXISTS namespace_id INT;

UPDATE ops_glossary g
SET namespace_id = n.id
FROM ops_namespace n
WHERE g.namespace = n.name
  AND g.namespace_id IS NULL;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_glossary_namespace_id'
    ) THEN
        ALTER TABLE ops_glossary
            ADD CONSTRAINT fk_glossary_namespace_id
            FOREIGN KEY (namespace_id) REFERENCES ops_namespace(id) ON DELETE CASCADE;
    END IF;
END $$;

-- ── STEP 5: ops_knowledge에 namespace_id 컬럼 추가 ──────────────────────────
ALTER TABLE ops_knowledge ADD COLUMN IF NOT EXISTS namespace_id INT;

UPDATE ops_knowledge k
SET namespace_id = n.id
FROM ops_namespace n
WHERE k.namespace = n.name
  AND k.namespace_id IS NULL;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_knowledge_namespace_id'
    ) THEN
        ALTER TABLE ops_knowledge
            ADD CONSTRAINT fk_knowledge_namespace_id
            FOREIGN KEY (namespace_id) REFERENCES ops_namespace(id) ON DELETE CASCADE;
    END IF;
END $$;

-- ── STEP 6: ops_knowledge_category에 namespace_id 컬럼 추가 ─────────────────
ALTER TABLE ops_knowledge_category ADD COLUMN IF NOT EXISTS namespace_id INT;

UPDATE ops_knowledge_category kc
SET namespace_id = n.id
FROM ops_namespace n
WHERE kc.namespace = n.name
  AND kc.namespace_id IS NULL;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_knowledge_cat_namespace_id'
    ) THEN
        ALTER TABLE ops_knowledge_category
            ADD CONSTRAINT fk_knowledge_cat_namespace_id
            FOREIGN KEY (namespace_id) REFERENCES ops_namespace(id) ON DELETE CASCADE;
    END IF;
END $$;

-- UNIQUE 제약 (namespace_id, name) 추가
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_knowledge_cat_ns_name'
    ) THEN
        ALTER TABLE ops_knowledge_category
            ADD CONSTRAINT uq_knowledge_cat_ns_name UNIQUE (namespace_id, name);
    END IF;
END $$;

-- ── STEP 7: ops_query_log에 namespace_id 컬럼 추가 ──────────────────────────
ALTER TABLE ops_query_log ADD COLUMN IF NOT EXISTS namespace_id INT;

UPDATE ops_query_log ql
SET namespace_id = n.id
FROM ops_namespace n
WHERE ql.namespace = n.name
  AND ql.namespace_id IS NULL;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_query_log_namespace_id'
    ) THEN
        ALTER TABLE ops_query_log
            ADD CONSTRAINT fk_query_log_namespace_id
            FOREIGN KEY (namespace_id) REFERENCES ops_namespace(id) ON DELETE CASCADE;
    END IF;
END $$;

-- ── STEP 8: ops_conversation에 namespace_id 컬럼 추가 ───────────────────────
ALTER TABLE ops_conversation ADD COLUMN IF NOT EXISTS namespace_id INT;

UPDATE ops_conversation c
SET namespace_id = n.id
FROM ops_namespace n
WHERE c.namespace = n.name
  AND c.namespace_id IS NULL;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_conversation_namespace_id'
    ) THEN
        ALTER TABLE ops_conversation
            ADD CONSTRAINT fk_conversation_namespace_id
            FOREIGN KEY (namespace_id) REFERENCES ops_namespace(id) ON DELETE CASCADE;
    END IF;
END $$;

-- ── STEP 9: ops_feedback에 namespace_id 컬럼 추가 ───────────────────────────
ALTER TABLE ops_feedback ADD COLUMN IF NOT EXISTS namespace_id INT;

UPDATE ops_feedback f
SET namespace_id = n.id
FROM ops_namespace n
WHERE f.namespace = n.name
  AND f.namespace_id IS NULL;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_feedback_namespace_id'
    ) THEN
        ALTER TABLE ops_feedback
            ADD CONSTRAINT fk_feedback_namespace_id
            FOREIGN KEY (namespace_id) REFERENCES ops_namespace(id) ON DELETE CASCADE;
    END IF;
END $$;

-- ── STEP 10: ops_fewshot에 namespace_id 컬럼 추가 ───────────────────────────
ALTER TABLE ops_fewshot ADD COLUMN IF NOT EXISTS namespace_id INT;

UPDATE ops_fewshot fs
SET namespace_id = n.id
FROM ops_namespace n
WHERE fs.namespace = n.name
  AND fs.namespace_id IS NULL;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_fewshot_namespace_id'
    ) THEN
        ALTER TABLE ops_fewshot
            ADD CONSTRAINT fk_fewshot_namespace_id
            FOREIGN KEY (namespace_id) REFERENCES ops_namespace(id) ON DELETE CASCADE;
    END IF;
END $$;

-- ── STEP 11: 인덱스 추가 (namespace_id 기준) ────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_glossary_ns_id ON ops_glossary (namespace_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_ns_id ON ops_knowledge (namespace_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_cat_ns_id ON ops_knowledge_category (namespace_id);
CREATE INDEX IF NOT EXISTS idx_query_log_ns_id ON ops_query_log (namespace_id);
CREATE INDEX IF NOT EXISTS idx_conversation_ns_id ON ops_conversation (namespace_id);
CREATE INDEX IF NOT EXISTS idx_fewshot_ns_id ON ops_fewshot (namespace_id);

COMMIT;

-- ── 참고: 구 string 컬럼 삭제는 검증 후 수동으로 실행 ──────────────────────
-- 아래 명령은 백엔드 서비스가 integer FK 방식으로 완전히 전환된 후 적용할 것
--
-- ALTER TABLE ops_namespace DROP COLUMN IF EXISTS owner_part;
-- ALTER TABLE ops_user DROP COLUMN IF EXISTS part;
-- ALTER TABLE ops_glossary DROP COLUMN IF EXISTS namespace;
-- ALTER TABLE ops_knowledge DROP COLUMN IF EXISTS namespace;
-- ALTER TABLE ops_knowledge_category DROP COLUMN IF EXISTS namespace;
-- ALTER TABLE ops_query_log DROP COLUMN IF EXISTS namespace;
-- ALTER TABLE ops_conversation DROP COLUMN IF EXISTS namespace;
-- ALTER TABLE ops_feedback DROP COLUMN IF EXISTS namespace;
-- ALTER TABLE ops_fewshot DROP COLUMN IF EXISTS namespace;
