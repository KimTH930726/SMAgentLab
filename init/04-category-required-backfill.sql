-- ────────────────────────────────────────────────────────────────────
-- v2.27 마이그레이션 — 지식 업무구분(category) 필수화 백필
--
-- rag_knowledge.category가 그동안 선택값이라 사실상 전부 NULL로 남아있었음.
-- 이제 등록/수정 시 필수값으로 강제하므로(backend/agents/knowledge_rag/knowledge/service.py
-- _require_category), 기존 데이터도 기본 카테고리로 일괄 정리.
--
-- 운영 적용 (init/ 디렉토리는 빈 pgdata에서만 자동 실행되므로 수동 실행):
--   docker exec -i ops-postgres psql -U ops -d opsdb < init/04-category-required-backfill.sql
-- ────────────────────────────────────────────────────────────────────

BEGIN;

-- 1. 카테고리가 하나도 없는 네임스페이스에 기본값 '공통지식' 추가
INSERT INTO rag_knowledge_category (namespace_id, name)
SELECT n.id, '공통지식'
FROM ops_namespace n
WHERE NOT EXISTS (
    SELECT 1 FROM rag_knowledge_category c
    WHERE c.namespace_id = n.id AND c.name = '공통지식'
);

-- 2. 미분류(NULL) 지식을 '공통지식'으로 백필
UPDATE rag_knowledge SET category = '공통지식' WHERE category IS NULL;

COMMIT;
