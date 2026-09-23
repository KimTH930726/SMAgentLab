# Ops-Navigator 마이그레이션 이력

> 2026-09-16 경량화 — 전체 컬럼 스펙(옛 §1~19, §부록)은 삭제했다. 현재 스키마는 항상
> 실제 DB(`\d 테이블명`)가 정확하다 — 손으로 유지보수하는 사본은 코드 변경 때마다
> 동기화가 밀려 신뢰할 수 없어지는 게 반복됐다(이 문서에 남아있던 "768차원" 표기가
> v2.72 이후에도 안 고쳐진 채 방치됐던 게 실제 사례). 대신 **"뭘 왜 언제 바꿨는지"**
> 이력 자체는 코드만 봐서는 안 나오는 의사결정 기록이라 계속 남긴다 — `docs/architecture.md`
> 변경이력과 같은 성격.

애플리케이션 시작 시 `backend/main.py`의 `_run_migrations()`에서 자동 실행된다. 모든 마이그레이션은 멱등(idempotent)하다.

| # | 대상 테이블 | 변경 내용 | 설명 |
|---|-----------|----------|------|
| 1 | `ops_query_log` | `ADD COLUMN answer TEXT` | 답변 기록용 컬럼 추가 |
| 2 | `ops_conversation` | `ADD COLUMN trimmed BOOLEAN NOT NULL DEFAULT FALSE` | 메모리 요약 수행 여부 플래그 |
| 3 | `ops_feedback` | `ADD COLUMN message_id INT REFERENCES ops_message(id) ON DELETE SET NULL` | 메시지-피드백 연결 |
| 4 | 6개 테이블 | `ADD CONSTRAINT fk_{table}_namespace FOREIGN KEY (namespace) REFERENCES ops_namespace(name) ON DELETE CASCADE` | namespace FK 제약 추가 (고아 데이터 방지) |
| 5 | - | `CREATE TABLE ops_part` | 파트(부서) 관리 테이블 생성 |
| 6 | - | `CREATE TABLE ops_user` | 사용자 인증/권한 테이블 생성 |
| 7 | `ops_user` | `INSERT admin` | 기본 관리자 계정 시드 (admin/admin) |
| 8 | `ops_conversation` | `ADD COLUMN user_id INT REFERENCES ops_user(id) ON DELETE CASCADE` | 대화-사용자 연결, `idx_conversation_user` 인덱스 추가 |
| 9 | `rag_knowledge` | `ADD COLUMN created_by_part VARCHAR(100), ADD COLUMN created_by_user_id INT` | 지식 생성자 추적 |
| 10 | `rag_glossary` | `ADD COLUMN created_by_part VARCHAR(100), ADD COLUMN created_by_user_id INT` | 용어 생성자 추적 |
| 11 | `rag_fewshot` | `ADD COLUMN created_by_part VARCHAR(100), ADD COLUMN created_by_user_id INT` | few-shot 생성자 추적 |
| 12 | `ops_namespace` | `ADD COLUMN owner_part VARCHAR(100), ADD COLUMN created_by_user_id INT` | 네임스페이스 소유 파트 기반 권한 제어 |
| 13 | 전체 테이블 | integer FK 전환 (`init/02-migrate-fk.sql`) | `namespace VARCHAR` → `namespace_id INT FK`, `owner_part VARCHAR` → `owner_part_id INT FK`, `part VARCHAR` → `part_id INT FK`. 기존 데이터를 보존하며 integer FK로 전환 |
| 14 | `rag_knowledge` | `ADD COLUMN category VARCHAR(100)` | 지식 카테고리 컬럼 추가 (nullable) |
| 15 | - | `CREATE TABLE rag_knowledge_category` | 네임스페이스별 카테고리 목록 관리 테이블 생성 |
| 16 | `ops_conversation` | `ADD COLUMN inhouse_conv_id VARCHAR(200)` | 사내 LLM 대화 ID 연결 컬럼 추가 |
| 17 | `ops_conversation` | `ADD COLUMN agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'` | 에이전트 유형 구분 (멀티 에이전트 확장) |
| 18 | `ops_query_log` | `ADD COLUMN agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'` | 에이전트 유형 구분 |
| 19 | `ops_feedback` | `ADD COLUMN agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'` | 에이전트 유형 구분 |
| 20 | `ops_feedback` | `ADD COLUMN meta JSONB` | 에이전트별 추가 메타데이터 |
| 21 | 6개 테이블 | `CREATE INDEX IF NOT EXISTS idx_*` | 성능 인덱스 6개 추가 (message, conversation, query_log, fewshot, feedback) |
| 22 | `ops_mcp_tool` | `ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NOT NULL DEFAULT 'knowledge_rag'` | MCP 도구 에이전트 분리 — 에이전트별 독립 도구 관리 |
| 23 | `sql_fewshot` | `ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'approved'` | SQL Few-shot 피드백 연동 — `pending`(후보)/`approved`(승인됨)/`rejected`(반려됨) |
| 24 | `ops_prompt` | `ADD COLUMN IF NOT EXISTS agent_type VARCHAR(50) NOT NULL DEFAULT 'all'` | 에이전트별 프롬프트 스코핑 — 시스템설정 탭에서 현재 에이전트 프롬프트만 표시 |
| 25 | `sql_target_db` | `ADD COLUMN IF NOT EXISTS schema_name VARCHAR(255) DEFAULT NULL` | 대상 DB 스키마 분리 — PostgreSQL: schema, Oracle: owner |
| 26 | `ops_user` | `ADD COLUMN IF NOT EXISTS encrypted_confluence_pat TEXT` | 사용자별 Confluence PAT Fernet 암호화 저장 (v3.7) |
| 27 | - | `init/04-category-required-backfill.sql` | `rag_knowledge.category` 필수화(v2.27) 백필 — 카테고리 없는 네임스페이스에 `'공통지식'` 기본 카테고리 생성, 기존 NULL 지식을 `'공통지식'`으로 일괄 갱신 |
| 28 | `rag_ingestion_job`, `rag_knowledge` | `init/05-ingestion-job-progress.sql` — `ADD COLUMN cancel_requested BOOLEAN`, `ADD COLUMN ingestion_job_id INT FK` | 대용량 지식 등록 진행률/중지 지원(v2.29) — 백그라운드 처리 + 배치별 진행률 갱신 + 중지 시 롤백 |
| 29 | `rag_knowledge`, `rag_ingestion_job` | `ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active'`, `ADD COLUMN pending_chunks INT NOT NULL DEFAULT 0`, `CREATE TABLE rag_knowledge_duplicate_match` | 지식 중복 등록 방지 — 청크 단위 유사도 검사 + 승인 대기(pending_review) 리뷰 (v2.34) |
| 30 | `ops_query_log` | `ADD COLUMN resolved_knowledge_id INT REFERENCES rag_knowledge(id) ON DELETE SET NULL` | 나빠요 피드백 후 지식 등록으로 해결한 질의를 등록된 지식과 연결 (v2.36) |
| 31 | - | `CREATE TABLE ops_voc_routing`, `CREATE TABLE ops_email_analysis`, `CREATE TABLE ops_email_poll_cycle` | VOC 이메일 분석 채널 Track A 스키마 — 파트별 메일함 라우팅, 건별 분석 결과, 폴링 사이클 이력 (v3.8) |
| 32 | `ops_system_config` | `INSERT email_collection_enabled/email_polling_interval_minutes/email_lookback_days` | VOC 이메일 폴링 정책 시드 (재시작에도 유지되도록 DB 영속 방식 채택) (v3.8) |
| 33 | `ops_email_poll_cycle` | `ADD COLUMN IF NOT EXISTS total_skipped_low_relevance INT NOT NULL DEFAULT 0` | 관련지식 사전 필터 스킵 건수 집계 (v3.9) |
| 34 | `ops_system_config` | `INSERT email_relevance_min_score` | VOC 이메일 관련지식 임계치 시드 — 미달 시 LLM 호출·Teams 발송 없이 이력만 기록(비용·알림 노이즈 억제) (v3.9) |
| 35 | `ops_voc_routing` | `ADD COLUMN IF NOT EXISTS mail_folder_id VARCHAR(300)`, `ADD COLUMN IF NOT EXISTS mail_folder_name VARCHAR(200)` | 폴링 조회 범위를 특정 Outlook 폴더로 제한 — 관리자가 Graph API로 실조회한 폴더 목록에서 선택 (v3.12) |
| 36 | `ops_system_config` | `UPDATE email_relevance_min_score` | 관련지식 임계치 계산식이 base_weight 부스팅 섞인 `final_score`를 쓰고 있어 게이트가 사실상 무력화됐던 버그 수정 후, 원점수 기준 실측 재조정: `0.35` → `0.38` (v3.13) |
| 37 | `ops_user` | `ADD COLUMN IF NOT EXISTS auth_provider VARCHAR(20) NOT NULL DEFAULT 'local'`, `ADD COLUMN IF NOT EXISTS external_id VARCHAR(255)`, `ADD COLUMN IF NOT EXISTS email VARCHAR(255)`, `ALTER COLUMN hashed_password DROP NOT NULL`, `CREATE UNIQUE INDEX ux_user_provider_external_id ON ops_user(auth_provider, external_id) WHERE external_id IS NOT NULL` | SSO(Azure AD) 연동 기반 스키마 선추가 — 로컬 계정은 `auth_provider='local'`로 그대로 유지, SSO 전용 계정은 로컬 비밀번호가 없을 수 있어 nullable로 완화(로그인 흐름 자체는 아직 미구현) (v2.50) |
| 38 | `sql_*` 10개 테이블 | `_migrate_text2sql_tables()` 호출 제거(함수 자체도 삭제) | Text-to-SQL 에이전트 제거(현재 과업 아님) — 기존 설치의 테이블은 삭제하지 않고 그대로 두되, 더 이상 마이그레이션되지 않음. 코드는 `archive/with-text2sql` 브랜치 보존 (v2.51) |
| 39 | - | `CREATE TABLE policy_item`, `policy_param`, `policy_chunk` + `trg_policy_item_logical_id` 트리거 | 정책서 데이터화 파이프라인 v1(`_migrate_policy_tables()`, `docs/policy-doc-pipeline-plan.md` §2) — 엑셀 row를 3층(원문+메타/파라미터/서술청크)으로 분해 저장. 버전 관리는 UPDATE 대신 새 row INSERT(logical_id 유지, version+1, supersedes_id) 방식(§2-1) (v2.52) |
| 40 | `rag_knowledge` + 신규 2개 테이블 | `ADD COLUMN logical_document_id/version/supersedes_id/embedding_model/quality_score/reviewed_at/owner`, `CREATE TABLE rag_knowledge_history`, `CREATE TABLE rag_knowledge_review_flag` | 지식 생명주기 관리(`_migrate_knowledge_lifecycle()`, `docs/tech/knowledge-lifecycle-design.md` §6) — Phase 0 스키마 선추가 + 병합 이력 보존(§6-2) + 피드백→리뷰 신호(§6-3). `logical_document_id`는 멱등 UPDATE로 매 기동마다 미설정 행만 자기 id로 백필 (v2.68) |
| 41 | - | `init/06-policy-strip-ko.sql` — `CREATE OR REPLACE FUNCTION policy_strip_ko_word()`, `policy_strip_ko()` | 정책 RDB(policy_param) 검색에 한국어 조사/어미 규칙 기반 제거 적용 — 테이블 변경 없이 조회 시점 변환 함수만 추가, `search.py`의 `search_policy()`가 콘텐츠/쿼리 양쪽에 적용. 89문항 실측: hit@10 77.5%→82.0% (v2.71) |
| 42 | `rag_knowledge`, `policy_chunk`, `rag_glossary`, `rag_fewshot`, `rag_conv_summary`, `ops_email_analysis`, `ops_voc_cluster`, `rag_knowledge_history` | `ALTER COLUMN embedding TYPE VECTOR(1024)` (8개 테이블, HNSW 인덱스 재생성 포함) | 임베딩 모델 교체(mpnet 768차원 → KURE-v1 1024차원) — 89문항 실측 hit@10 55.1%→82.0%(단독 벡터), 재색인 후 하이브리드 82.0%→88.8%. 전량 재임베딩(`backend/scripts/migrate_embedding_model.py`), Text2SQL 미등록으로 `sql_*` 3개 테이블은 제외 (v2.72) |
| 43 | - | `init/07-ref-data-tables.sql` — `CREATE TABLE ref_db_column`, `CREATE TABLE ref_common_code` | DB 스키마 사전(컬럼당 1행) / 공통코드(코드값당 1행, 관리항목은 JSONB) 구조화 저장 — 2026-09-10 정리한 `rag_knowledge` DB/공통코드 오염 데이터(3,040건 영구 삭제) 대신 앞으로 이런 데이터가 들어올 때 쓸 구조. 정확 조회 전용이라 embedding 컬럼 없음. 파서: `service/refdata/parser.py`(결정론, LLM 미사용) (v2.73) |
| 44 | - | `init/08-policy-track2-history.sql` — `CREATE TABLE policy_track2_run` | Track2 실행 이력 스냅샷 저장(실험실 게이트 작업3, 모니터링 뷰 재료) — `by_type`은 JSONB로 통째 저장해 지표가 늘어나도 컬럼 변경 불필요. `POST /track2/run` 호출마다 자동 저장, `GET /track2/history`로 조회 (v2.74) |
| 45 | `rag_knowledge` | `ADD COLUMN IF NOT EXISTS confluence_page_id VARCHAR(50)`, `ADD COLUMN IF NOT EXISTS confluence_version INT`, `CREATE INDEX idx_knowledge_confluence_page` | Confluence 페이지 버전 추적 — 재임포트 시 `page.version.number`가 안 바뀐 페이지는 스킵, 바뀐 페이지는 기존 행을 `deprecated` 처리 후 재삽입 (v2.81) |
| 46 | `rag_knowledge` | `DROP COLUMN IF EXISTS container_name`, `DROP COLUMN IF EXISTS target_tables`, `DROP COLUMN IF EXISTS query_template` | 실사용 데이터 감사(전체 50건 중 25/13/2건만 채워짐, 그마저 랭킹에 안 쓰이고 `build_context()`가 LLM 컨텍스트에 장식으로 덧붙이는 용도뿐이었음) 후 완전 제거(YAGNI) — 필요해지면 그때 재추가 (v2.82) |
| 47 | - | `CREATE TABLE ops_prompt_category_guide` | fewshot 대체용 카테고리별 정적 답변 안내문(namespace_id, category, guide_text, `UNIQUE(namespace_id, category)`) — 승인 절차 없는 관리자 직접 입력 방식. **스키마만 선추가, CRUD/UI 미착수**(v2.83) |
| 48 | `rag_fewshot`, `rag_ingestion_job` | `DROP TABLE IF EXISTS rag_fewshot`, `ALTER TABLE rag_ingestion_job DROP COLUMN IF EXISTS auto_fewshot` | fewshot 기능 전체 제거 — 후보 12건/활성 1건, 2개월째 방치 실측(승격 담당자 부재) 후 "업계 표준 패턴"이라는 일반론이 아니라 "우리 조직에서 실제 효과가 있었나"를 기준으로 완전 제거 결정(`feedback_effectiveness_bar_for_org_specific_features` 메모리). 관련 API(`agents/knowledge_rag/fewshot/`)·UI(`FewshotTable.tsx`)·큐 생성 로직(`qa_gen.py`, 피드백→INSERT)도 함께 삭제 (v2.84) |
| 49 | - | `CREATE OR REPLACE FUNCTION policy_strip_ko_word()`, `policy_strip_ko()` (main.py, `_migrate_ensure_ko_text_search_helpers`) | `init/06-policy-strip-ko.sql`은 완전히 빈 pgdata에서만 자동 실행돼 기존 배포엔 이 함수가 없을 수 있었음(수동 적용 필요) — 재시작마다 실행되는 멱등 마이그레이션에도 동일 함수를 `CREATE OR REPLACE`로 보장. 동시에 `retrieval.py`의 일반지식 키워드 검색에도 이 함수를 처음 적용(기존엔 정책 검색에만 적용돼 있었음) — "DS14가 뭐야?"의 조사 융합 토큰('ds14가')이 문서의 깔끔한 'ds14'와 매칭 안 되던 버그 수정. 조사 뒤에 물음표 등 문장부호가 공백 없이 바로 붙는 경우("ds14는?")도 처리하도록 함수 자체를 보강(끝 문장부호를 떼고 조사 판정 후 재부착) (v2.87) |
| 50 | - | (스키마 변경 없음 — `retrieval.py`/`agent.py` 로직만) | 지식 채택 게이트를 `final_score`(가중치 반영값) 대신 `relevance_score()`(base_weight를 나눠 걷어낸 원점수)로 전환 — 신규 지식 base_weight 기본값이 1.0이라 결합점수가 항상 2배가 돼 완전 무관한 질문도 채택 임계치를 통과하던 버그 실측 확인("오늘 날씨 어때?"가 후보 20/20건 통과). `_KEYWORD_ONLY_CATEGORIES`(DB/공통코드)는 ts_rank가 코사인과 스케일이 달라 같은 임계치를 못 써서 `is_adopted()`에서 매칭 여부(이진)로 별도 판정 (v2.88) |
| 51 | `rag_knowledge` | `UPDATE status='deprecated' WHERE id=20` (1회성 데이터 이전, `backend/scripts/migrate_id20_to_refdata.py`) | "공통코드" 카테고리 지식 1건(id=20, 딜리버스 DB)을 `ref_common_code`(252건, #43에서 스키마만 선추가돼 있었으나 0건이던 테이블)로 이전 — `_KEYWORD_ONLY_CATEGORIES`가 벡터축과 같은 테이블 안에서 final_score로 경쟁하다 보니 ts_rank 스케일이 작아 `ORDER BY ... LIMIT top_k` 단계에서부터 후보 풀에 못 들어가는 구조적 문제가 실측 확인됨(자기 매칭 대상 질문 "DS14가 뭐야?"에서 27건 중 27등, top_k=5 후보에 전혀 없음). CMDB 데이터도 같은 성격(정확 조회용 RDB)이라 이 문제가 반복될 것으로 예상 — `rag_knowledge` SQL을 더 복잡하게 만드는 대신 원래 이 용도로 만들어졌던 별도 테이블로 완전히 분리해 처음부터 독립 RRF 축으로 둠. `service/refdata/service.py`의 `search_common_codes()`/`search_db_columns()`(#43에서 구현은 됐지만 chat에서 호출을 안 하고 있었음)에도 같은 조사 제거 함수 적용 + `code_id`를 검색 대상 텍스트에 추가(기존엔 설명 텍스트만 검색돼 코드값 자체로는 못 찾았음), `has_refdata()` 게이트 신규, `agent.py`의 `_build_rrf_context()`에 네 번째 RRF 축으로 연결 (v2.89) |

| 52 | `ops_query_log` | `ADD COLUMN IF NOT EXISTS user_id INT REFERENCES ops_user(id) ON DELETE SET NULL`, `CREATE INDEX idx_query_log_user` | 질의응답 감사로그 — 누가 물었는지 연결(거버넌스 선제 설계 검토 §3-1 A안). 기존 행은 admin으로 소급 귀속하지 않고 NULL 유지(감사로그 사실 왜곡 방지) (v2.92) |

| 53 | `ops_system_config` | `INSERT (key, value) VALUES ('chat_retention_days', '0')` | 채팅 보존정책 스캐폴딩(거버넌스 선제 설계 검토 §3-3) — 기본값 비활성(0), 거버넌스팀이 실 보존일수를 정하면 값만 갱신하면 됨. 정리 로직은 `service/chat/helpers.py`의 `cleanup_old_conversations()` (v2.94) |

| 54 | `rag_knowledge` | `ADD COLUMN IF NOT EXISTS heading_path TEXT[]` | Parent-Child 문맥 유실 방지(컨플루언스 전용) — 조상 헤딩 제목 목록, 결정론적 파서로 채움(LLM 미사용). 기존 25건은 `scripts/backfill_heading_path.py`로 소급 적용 (v2.98) |

| 55 | `ops_namespace`, `policy_param`, `ops_http_tool`, `ops_mcp_tool`, `ops_mcp_tool_log`, `sql_*`(10개) | `DROP COLUMN`/`DROP TABLE IF EXISTS` | 전체 스키마 죽은 컬럼/테이블 감사(코드 grep + 실 population 실측 + 문서 대조) 후 정리. `ops_namespace.owner_part`(VARCHAR, `owner_part_id` FK 전환용 1회성 브릿지, 전환 완료돼 0/4건) — `_migrate_core_tables`의 `ADD COLUMN IF NOT EXISTS owner_part`와 그 하위 동기화 UPDATE 2곳도 같이 제거(안 하면 재생성됨). `policy_param.approved`(INSERT 경로에 컬럼이 빠져있어 전부 기본값, 389/389 false, 아무도 안 읽음). `ops_http_tool`/`ops_mcp_tool`/`ops_mcp_tool_log`(MCP 도구 에이전트 제거 v2.67 후 방치). `sql_*` 10개(Text2SQL 제거 v2.51 후 완전히 죽음, 코드는 `archive/with-text2sql` 브랜치 보존). `rag_knowledge.supersedes_id`/`version`/`logical_document_id`는 원래 의도(#40)와 다른 방향으로 기능이 진화한 정황이 있어 이번엔 보류(별도 재검토 필요) (v2.100) |

**데이터 마이그레이션**:
- `ops_query_log.answer`가 NULL인 레코드에 대해 `ops_message`에서 매칭되는 답변을 역보충(backfill)한다.
- namespace FK 추가 전, 각 테이블의 namespace 값 중 `ops_namespace`에 없는 값을 자동 생성한다.
- `rag_knowledge.category`가 NULL인 레코드를 `'공통지식'`으로 일괄 갱신한다 (`init/04-category-required-backfill.sql`).
