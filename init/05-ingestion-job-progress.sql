-- ────────────────────────────────────────────────────────────────────
-- v2.29 마이그레이션 — 대용량 지식 등록 진행률/취소 지원
--
-- 청크 수가 많은 등록(파일 업로드 등)이 백그라운드로 돌아가면서
-- 중간 진행률을 보여주고, 사용자가 중지하면 이미 넣은 데이터를
-- 롤백할 수 있도록 컬럼 추가.
--
-- 운영 적용 (init/ 디렉토리는 빈 pgdata에서만 자동 실행되므로 수동 실행):
--   docker exec -i ops-postgres psql -U ops -d opsdb < init/05-ingestion-job-progress.sql
-- ────────────────────────────────────────────────────────────────────

BEGIN;

-- 1. 사용자가 중지를 요청했는지 표시하는 플래그 (백그라운드 작업이 배치마다 폴링)
ALTER TABLE rag_ingestion_job
    ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE;

-- 2. 각 지식 행이 어느 인제스천 작업에서 만들어졌는지 추적 (중지 시 롤백용)
ALTER TABLE rag_knowledge
    ADD COLUMN IF NOT EXISTS ingestion_job_id INT REFERENCES rag_ingestion_job(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_rag_knowledge_ingestion_job ON rag_knowledge(ingestion_job_id);

COMMIT;
