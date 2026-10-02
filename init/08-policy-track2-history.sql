-- ────────────────────────────────────────────────────────────────────
-- v2.74 마이그레이션 — Track2 실행 이력 저장 (실험실 게이트 작업3, 모니터링 뷰의 재료)
--
-- 배경: Track2는 지금까지 매번 라이브로만 실행되고 결과가 DB에 안 남았다(run_comparison()이
-- 계산값을 반환만 하고 어디에도 적재 안 함) — "retrieval 평가가 시간에 따라 어떻게
-- 변해왔는지" 추이를 볼 수가 없었다. by_type은 요약 없이 JSONB로 스냅샷 통째 저장해
-- (§8 대비 이력 조회 화면에서 유형별 세부까지 그대로 펼쳐볼 수 있게) 나중에 지표가
-- 늘어나도(MRR·nDCG 등) 컬럼을 매번 안 늘려도 되게 한다.
--
-- 운영 적용 (init/ 디렉토리는 빈 pgdata에서만 자동 실행되므로 수동 실행):
--   docker exec -i ops-postgres psql -U ops -d opsdb < init/08-policy-track2-history.sql
-- ────────────────────────────────────────────────────────────────────

BEGIN;

CREATE TABLE IF NOT EXISTS policy_track2_run (
    id                         SERIAL PRIMARY KEY,
    run_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    top_k                      INT NOT NULL,
    total_n                    INT NOT NULL,
    a_hit_rate                 DOUBLE PRECISION NOT NULL,
    b_hit_rate                 DOUBLE PRECISION NOT NULL,
    a_precision                DOUBLE PRECISION NOT NULL,
    b_precision                DOUBLE PRECISION NOT NULL,
    b_hit_rdb_only             DOUBLE PRECISION NOT NULL,
    b_hit_vector_only          DOUBLE PRECISION NOT NULL,
    b_hit_both                 DOUBLE PRECISION NOT NULL,
    a_top1_accuracy            DOUBLE PRECISION NOT NULL,
    b_top1_param_accuracy      DOUBLE PRECISION NOT NULL,
    b_top1_narrative_accuracy  DOUBLE PRECISION NOT NULL,
    by_type                    JSONB NOT NULL,
    golden_set_file            VARCHAR(200) NOT NULL,
    duration_seconds           DOUBLE PRECISION NOT NULL,
    triggered_by               INT REFERENCES ops_user(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_policy_track2_run_at ON policy_track2_run (run_at DESC);

COMMIT;
