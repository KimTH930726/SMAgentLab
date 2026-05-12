-- ────────────────────────────────────────────────────────────────────
-- v2.17 마이그레이션 — DevX OAuth2 사용자별 자격증명
--
-- 기존 정적 API Key 단일 컬럼 → OAuth2 (client_id, client_secret, user_id) 트리플
-- Fernet 암호화된 JSON 한 컬럼으로 보관.
--
-- 운영 적용 (init/ 디렉토리는 빈 pgdata에서만 자동 실행되므로 수동 실행):
--   docker exec -i ops-postgres psql -U ops -d opsdb < init/03-llm-credentials.sql
-- ────────────────────────────────────────────────────────────────────

BEGIN;

-- 1. 새 컬럼 추가
ALTER TABLE ops_user
    ADD COLUMN IF NOT EXISTS encrypted_llm_credentials TEXT;

-- 2. 옛 컬럼 제거 (정적 API Key 패턴 폐기)
ALTER TABLE ops_user
    DROP COLUMN IF EXISTS encrypted_llm_api_key;

COMMIT;
