-- ────────────────────────────────────────────────────────────────────
-- v2.73 마이그레이션 — DB 스키마 사전 / 공통코드 구조화 저장
--
-- 배경: rag_knowledge의 DB/공통코드 카테고리(2026-09-10 기준 3,040건, 지식베이스의
-- 99%)가 원래 마크다운 표(SR 테이블,컬럼.md / SR 공통코드.md)를 일반 문서 청커로
-- 잘라 넣은 오염 데이터였다는 게 밝혀져 전량 soft-delete 후 영구 삭제했다
-- (docs/tech/data-storage-philosophy.md §8-E, §9 갭 이슈 ③). 원본 파일 자체가
-- 업로드 시점에 디스크 저장 없이 메모리에서 청킹만 되고 버려지는 구조라 복구
-- 불가 — 이 마이그레이션은 "예전 데이터 복구"가 아니라 **앞으로 이런 종류의
-- 데이터가 다시 들어올 때 쓸 구조**다(정확·고정값 = 컬럼, §2 결정 규칙).
--
-- 정확 조회용 데이터라 벡터 임베딩이 아예 필요 없다 — embedding 컬럼 자체를 안 둠.
--
-- 운영 적용 (init/ 디렉토리는 빈 pgdata에서만 자동 실행되므로 수동 실행):
--   docker exec -i ops-postgres psql -U ops -d opsdb < init/07-ref-data-tables.sql
-- ────────────────────────────────────────────────────────────────────

BEGIN;

-- ── DB 스키마 사전 — 컬럼당 1행 ──
CREATE TABLE IF NOT EXISTS ref_db_column (
    id             SERIAL PRIMARY KEY,
    namespace_id   INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
    table_name     VARCHAR(200) NOT NULL,
    table_comment  TEXT,
    column_name    VARCHAR(200) NOT NULL,
    column_comment TEXT,
    data_type      VARCHAR(100),   -- 원본 표기 그대로 보존(예: "VARCHAR2(20)") — 파싱해 쪼개지 않음
    nullable       VARCHAR(10),    -- 원본이 'Y'/'N' 문자열이라 그대로(boolean 캐스팅 안 함, 원문 표기 보존 우선)
    source_file    VARCHAR(500),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ref_db_column_table ON ref_db_column(namespace_id, table_name);
CREATE INDEX IF NOT EXISTS idx_ref_db_column_fts ON ref_db_column
    USING gin (to_tsvector('simple',
        table_name || ' ' || COALESCE(table_comment, '') || ' ' ||
        column_name || ' ' || COALESCE(column_comment, '')));

-- ── 공통코드 — 코드값당 1행. 관리항목(MNGMN_ITM01_VALUE 등)은 원본에서 컬럼 개수가
-- 코드그룹마다 달라(유동·희소 필드, §2 결정 규칙) JSONB로 ──
CREATE TABLE IF NOT EXISTS ref_common_code (
    id              SERIAL PRIMARY KEY,
    namespace_id    INT NOT NULL REFERENCES ops_namespace(id) ON DELETE CASCADE,
    work_code       VARCHAR(50),
    group_code      VARCHAR(50) NOT NULL,
    group_code_name VARCHAR(200),
    code_id         VARCHAR(50) NOT NULL,
    code_name       VARCHAR(200),
    mgmt_values     JSONB,          -- {"MNGMN_ITM01_VALUE": "...", "MNGMN_ITM02_VALUE": "...", ...}
    source_file     VARCHAR(500),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ref_common_code_group ON ref_common_code(namespace_id, group_code, code_id);
CREATE INDEX IF NOT EXISTS idx_ref_common_code_fts ON ref_common_code
    USING gin (to_tsvector('simple', COALESCE(group_code_name, '') || ' ' || COALESCE(code_name, '')));

COMMIT;
