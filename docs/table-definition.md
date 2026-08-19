# Ops-Navigator 테이블 정의서

> **Version**: 3.11
> **DBMS**: PostgreSQL 16 + pgvector
> **Extensions**: `vector`, `pg_trgm`
> **벡터 차원**: 768 (paraphrase-multilingual-mpnet-base-v2)
> **작성일**: 2026-08-19 (v3.11 — `email_graph_delegated` JSON에 `client_secret` 선택 필드 추가(DDL 변경 없음, 기존 암호화 JSON 블롭 내부 키만 추가) — Confidential Client 지원. v3.10 — `email_graph_delegated`의 인증 방식을 Device Code Flow에서 Authorization Code Flow(PKCE)로 교체, `redirect_uri` 필드 추가. v3.9 — VOC 이메일 관련지식 사전 필터(`email_relevance_min_score`, `ops_email_analysis.status='skipped_relevance'`, `ops_email_poll_cycle.total_skipped_low_relevance`) 추가)
> **DDL 위치**: `init/01-init.sql` + `main.py` lifespan 마이그레이션

---

## 목차

1. [ERD 개요](#1-erd-개요)
2. [ops_part](#2-ops_part)
3. [ops_user](#3-ops_user)
4. [ops_namespace](#4-ops_namespace)
5. [rag_glossary](#5-rag_glossary)
6. [rag_knowledge](#6-rag_knowledge)
7. [rag_knowledge_category](#7-rag_knowledge_category)
8. [rag_ingestion_job](#8-rag_ingestion_job)
9. [ops_query_log](#9-ops_query_log)
10. [ops_conversation](#10-ops_conversation)
11. [ops_message](#11-ops_message)
12. [ops_feedback](#12-ops_feedback)
13. [rag_fewshot](#13-rag_fewshot)
14. [rag_conv_summary](#14-rag_conv_summary)
15. [ops_mcp_tool](#15-ops_mcp_tool)
16. [ops_mcp_tool_log](#16-ops_mcp_tool_log)
17. [ops_prompt](#17-ops_prompt)
18. [ops_system_config](#18-ops_system_config)
18-1. [ops_voc_routing](#18-1-ops_voc_routing)
18-2. [ops_email_analysis](#18-2-ops_email_analysis)
18-3. [ops_email_poll_cycle](#18-3-ops_email_poll_cycle)
19. [트리거 및 함수](#19-트리거-및-함수)
20. [마이그레이션](#20-마이그레이션)

---

## 1. ERD 개요

```
ops_part
    │
    ├─── ops_user                     (part_id FK → ops_part.id ON DELETE SET NULL)
    │        │
    │        ├─── ops_conversation    (user_id FK CASCADE)
    │        │        │
    │        │        ├── ops_message ◄── ops_feedback (message_id FK)
    │        │        │                       │
    │        │        │                       └── rag_knowledge (knowledge_id FK)
    │        │        │
    │        │        └── rag_conv_summary
    │        │
    │        ├─── rag_knowledge       (created_by_user_id)
    │        ├─── rag_glossary        (created_by_user_id)
    │        └─── rag_fewshot         (created_by_user_id)
    │
    └─── ops_namespace                (owner_part_id FK → ops_part.id ON DELETE SET NULL)
             │
             ├─── rag_glossary            (namespace_id FK CASCADE)
             ├─── rag_knowledge ◄──┐      (namespace_id FK CASCADE)
             │        │            │
             │        └── rag_fewshot     (knowledge_id FK)
             │
             ├─── rag_knowledge_category  (namespace_id FK CASCADE)
             ├─── rag_ingestion_job       (namespace_id FK CASCADE)
             ├─── ops_conversation        (namespace_id FK CASCADE)
             ├─── ops_feedback            (namespace_id FK CASCADE)
             ├─── ops_query_log           (namespace_id FK CASCADE)
             ├─── ops_mcp_tool            (namespace_id FK CASCADE)
             │        │
             │        └─── ops_mcp_tool_log  (tool_id FK SET NULL, namespace_id FK)

ops_prompt        (namespace 독립 — func_key 기반 전역 프롬프트 관리)
ops_system_config (시스템 전역 설정 — key-value, 재시작 후에도 영속)
```

**테이블 수**: 18개
**FK 관계**: CASCADE 13건 (namespace_id 9건 + conversation 2건 + user 1건 + part 1건), SET NULL 3건

---

## 2. ops_part

**목적**: 조직 내 파트(부서/팀)를 관리한다. 사용자 소속 정보의 기준 테이블이다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `name` | VARCHAR(100) | NO | - | UNIQUE | 파트(부서) 이름 |
| 3 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**: PK(id), UNIQUE(name)
**참조됨**: `ops_user.part_id`와 `ops_namespace.owner_part_id`가 `id`를 integer FK로 참조

---

## 3. ops_user

**목적**: 시스템 사용자(관리자/일반)를 관리한다. 인증, 권한 제어, LLM API 키 및 Confluence PAT 암호화 저장에 사용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `username` | VARCHAR(100) | NO | - | UNIQUE | 로그인 ID |
| 3 | `hashed_password` | TEXT | NO | - | - | bcrypt 해시 비밀번호 |
| 4 | `role` | VARCHAR(20) | YES | `'user'` | - | 역할 (`admin` \| `user`) |
| 5 | `part_id` | INT | YES | NULL | FK → ops_part(id) ON DELETE SET NULL | 소속 파트 ID |
| 6 | `is_active` | BOOLEAN | YES | `TRUE` | - | 계정 활성 여부 |
| 7 | `encrypted_llm_credentials` | TEXT | YES | NULL | - | 사용자별 LLM OAuth2 자격증명 트리플 (Fernet 암호화 JSON: `{client_id, client_secret, user_id}`). v2.17부터. 미등록 시 .env 팀 공통 자격증명 fallback |
| 8 | `encrypted_confluence_pat` | TEXT | YES | NULL | - | 사용자별 Confluence PAT (Fernet 암호화, v3.7 신규) |
| 9 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**: PK(id), UNIQUE(username)

**role 상태값**:

| 값 | 의미 |
|----|------|
| `admin` | 관리자 — 전체 기능 접근, 사용자 관리 가능 |
| `user` | 일반 사용자 — 채팅, 피드백 등 기본 기능만 |

---

## 4. ops_namespace

**목적**: 업무 도메인 격리. 모든 데이터를 네임스페이스 단위로 분리한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `name` | VARCHAR(100) | NO | - | UNIQUE | 네임스페이스 이름 |
| 3 | `description` | TEXT | NO | `''` | - | 설명 |
| 4 | `owner_part_id` | INT | YES | NULL | FK → ops_part(id) ON DELETE SET NULL | 소유 파트 ID (생성자의 파트, 권한 제어 기준) |
| 5 | `created_by_user_id` | INT | YES | NULL | - | 생성자 사용자 ID |
| 6 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**: PK(id)
**참조됨**: 7개 테이블의 `namespace_id` 컬럼이 `id`를 integer FK로 참조 (ON DELETE CASCADE)
**권한 모델**: `owner_part_id` 기반 파트별 CRUD 제어. 상세 규칙은 `api-specification.md § 3. 인증 및 권한` 참조.

---

## 5. rag_glossary

**목적**: 사용자의 모호한 표현을 내부 표준 용어로 매핑한다. 2단계 검색의 1단계(Term Mapping)에서 사용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace_id` | INT | NO | - | FK → ops_namespace(id) ON DELETE CASCADE | 소속 네임스페이스 ID |
| 3 | `term` | VARCHAR(200) | NO | - | - | 표준 용어 |
| 4 | `description` | TEXT | NO | - | - | 용어 설명 |
| 5 | `embedding` | VECTOR(768) | YES | NULL | - | description 임베딩 벡터 |
| 6 | `created_by_part` | VARCHAR(100) | YES | NULL | - | 최종 수정자의 소속 파트 (수정 시 갱신) |
| 7 | `created_by_user_id` | INT | YES | NULL | - | 최종 수정자 ID (수정 시 갱신) |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_glossary_ns` | namespace_id | B-Tree | 네임스페이스 필터 |
| `idx_glossary_emb` | embedding | HNSW (vector_cosine_ops) | 벡터 유사도 검색 |

---

## 6. rag_knowledge

**목적**: 운영 가이드, 처리 절차, SQL 템플릿 등 핵심 지식을 저장한다. 2단계 검색의 2단계(Hybrid Search)에서 벡터+키워드 결합 검색의 대상이 된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace_id` | INT | NO | - | FK → ops_namespace(id) ON DELETE CASCADE | 소속 네임스페이스 ID |
| 3 | `container_name` | VARCHAR(200) | YES | NULL | - | 관련 컨테이너/서비스명 |
| 4 | `target_tables` | TEXT[] | YES | NULL | - | 관련 DB 테이블 목록 |
| 5 | `content` | TEXT | NO | - | - | 지식 본문 (검색 대상) |
| 6 | `query_template` | TEXT | YES | NULL | - | SQL 쿼리 템플릿 |
| 7 | `embedding` | VECTOR(768) | YES | NULL | - | content 임베딩 벡터 |
| 8 | `base_weight` | FLOAT | NO | `1.0` | - | 검색 점수 가중치 |
| 9 | `category` | VARCHAR(100) | YES | NULL | - | 지식 카테고리 (rag_knowledge_category 연동) |
| 10 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 11 | `updated_at` | TIMESTAMPTZ | NO | `NOW()` | - | 수정일시 (트리거 자동갱신) |
| 12 | `created_by_part` | VARCHAR(100) | YES | NULL | - | 최종 수정자의 소속 파트 (수정 시 갱신) |
| 13 | `created_by_user_id` | INT | YES | NULL | - | 최종 수정자 ID (수정 시 갱신) |
| 14 | `ingestion_job_id` | INT | YES | NULL | FK → rag_ingestion_job(id) ON DELETE SET NULL | 어느 인제스천 작업에서 생성됐는지 추적 (중지 시 롤백용, v2.29) |
| 15 | `source_file` | VARCHAR(500) | YES | NULL | - | 대량 등록 시 원본 파일명 (단건 수동 등록은 NULL) |
| 16 | `source_chunk_idx` | INT | YES | NULL | - | 같은 인제스천 작업 내 청크 순번 — executemany INSERT 후 RETURNING 없이 방금 넣은 행을 역매칭하는 상관키로 사용 |
| 17 | `source_type` | VARCHAR(50) | NO | `'manual'` | - | 등록 경로 (`manual`, `csv_import`, `paste_split`, `file_upload`, `web`, `confluence`, `teams`) |
| 18 | `status` | VARCHAR(20) | NO | `'active'` | - | `active`(검색 노출) / `pending_review`(유사 지식과 중복 의심, 승인 대기 — 검색에서 숨김) / `rejected`(반려, 감사 기록으로 보존) — v2.34 |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_knowledge_ns` | namespace_id | B-Tree | 네임스페이스 필터 |
| `idx_rag_knowledge_ingestion_job` | ingestion_job_id | B-Tree | 인제스천 작업별 롤백 조회 (v2.29) |
| `idx_knowledge_emb` | embedding | HNSW (vector_cosine_ops) | 벡터 유사도 검색 |
| `idx_knowledge_fts` | to_tsvector('simple', content) | GIN | 전문 검색 (Full-Text Search) |

**트리거**: `trg_knowledge_updated_at` → UPDATE 시 `updated_at` 자동 갱신

**점수 산출 공식**:
```
final_score = (w_vector * v_score + w_keyword * k_score) * (1 + base_weight)
```

검색 쿼리(`search_knowledge`, `vector_search_knowledge`)는 `status IN ('active')`(또는 NULL, 과거 데이터 호환)인 행만 대상으로 한다 — `pending_review`/`rejected`는 항상 제외.

---

## 6-1. rag_knowledge_duplicate_match (v2.34)

**목적**: 지식 등록 시점에 기존 활성 지식과 유사도가 임계값(`duplicate_min_similarity`, 기본 0.88) 이상이면, 어떤 기존 지식과 얼마나 유사했는지 top-N(기본 3개)을 기록해 승인 대기 리뷰 화면에서 나란히 비교할 수 있게 한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `new_knowledge_id` | INT | NO | - | FK → rag_knowledge(id) ON DELETE CASCADE | 승인 대기 중인(신규) 지식 |
| 3 | `matched_knowledge_id` | INT | NO | - | FK → rag_knowledge(id) ON DELETE CASCADE | 매칭된 기존 활성 지식 |
| 4 | `similarity` | FLOAT | NO | - | - | 코사인 유사도 |
| 5 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**: `idx_dup_match_new` (new_knowledge_id, B-Tree) — 리뷰 화면에서 특정 신규 지식의 매칭 후보 목록 조회용

**관련 API**: `GET /api/knowledge/{id}/duplicate-matches`, `POST /api/knowledge/{id}/resolve` (action: approve/reject/merge)

---

## 7. rag_knowledge_category

**목적**: 네임스페이스별 지식 카테고리를 관리한다. `rag_knowledge.category` 컬럼의 유효값 목록으로 활용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace_id` | INT | NO | - | FK → ops_namespace(id) ON DELETE CASCADE | 소속 네임스페이스 ID |
| 3 | `name` | VARCHAR(100) | NO | - | UNIQUE(namespace_id, name) | 카테고리 이름 |
| 4 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_knowledge_cat_ns` | namespace_id | B-Tree | 네임스페이스 필터 |

**제약조건**: `UNIQUE(namespace_id, name)` — 같은 네임스페이스 내 카테고리명 중복 불가

---

## 8. rag_ingestion_job

**목적**: 지식 인제스천 작업 이력을 추적한다. CSV 임포트, 텍스트 분할, 파일 업로드 등의 배치 등록 결과를 기록하고 감사 로그로 활용한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace_id` | INT | NO | - | FK → ops_namespace(id) ON DELETE CASCADE | 소속 네임스페이스 ID |
| 3 | `source_file` | VARCHAR(500) | YES | - | - | 원본 파일명 |
| 4 | `source_type` | VARCHAR(50) | YES | - | - | 입력 유형 (`csv_import`, `paste_split`, `file_upload`, `manual`) |
| 5 | `status` | VARCHAR(20) | NO | `processing` | - | 작업 상태 (`processing` / `completed` / `failed` / `cancelled`) |
| 6 | `total_chunks` | INT | NO | 0 | - | 등록 예정 청크 수 |
| 7 | `created_chunks` | INT | NO | 0 | - | 실제 등록된 청크 수 |
| 8 | `auto_glossary` | INT | NO | 0 | - | LLM이 자동 추출한 용어 수 |
| 9 | `auto_fewshot` | INT | NO | 0 | - | LLM이 자동 생성한 Q&A 수 |
| 10 | `chunk_strategy` | VARCHAR(50) | YES | - | - | 사용된 청킹 전략 (`section`, `paragraph`, `fixed`, `auto`) |
| 11 | `embedding_model` | VARCHAR(200) | YES | - | - | 임베딩 모델명 |
| 12 | `analyzer_result` | JSONB | YES | - | - | Analyzer Agent 분석 결과 (doc_type, priority_score 등) |
| 13 | `error_message` | TEXT | YES | - | - | 실패 시 오류 메시지 |
| 14 | `created_by_user_id` | INT | YES | - | FK → ops_user(id) ON DELETE SET NULL | 작업 요청자 |
| 15 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 작업 시작 시각 |
| 16 | `completed_at` | TIMESTAMPTZ | YES | - | - | 작업 완료 시각 |
| 17 | `cancel_requested` | BOOLEAN | NO | `FALSE` | - | 사용자가 중지를 요청했는지 (백그라운드 작업이 배치마다 폴링, v2.29) |
| 18 | `pending_chunks` | INT | NO | 0 | - | 유사 지식과 중복 의심으로 승인 대기(pending_review) 처리된 청크 수 (v2.34) |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_ingestion_job_ns` | namespace_id, created_at DESC | B-Tree | 네임스페이스별 최신 작업 이력 조회 |

**관련 API**: `GET /api/knowledge/ingestion-jobs?namespace=...`

---

## 9. ops_query_log

**목적**: 사용자 질의를 기록하고, 해결 상태를 추적한다. 통계 대시보드와 미해결 케이스 관리에 활용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace_id` | INT | YES | NULL | FK → ops_namespace(id) ON DELETE CASCADE | 소속 네임스페이스 ID |
| 3 | `question` | TEXT | YES | NULL | - | 사용자 질문 |
| 4 | `answer` | TEXT | YES | NULL | - | LLM 답변 (마이그레이션 추가) |
| 5 | `status` | VARCHAR(20) | NO | `'pending'` | - | 처리 상태 |
| 6 | `mapped_term` | VARCHAR(200) | YES | NULL | - | 매핑된 용어 |
| 7 | `message_id` | INT | YES | NULL | - | 연결된 메시지 ID |
| 8 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 9 | `agent_type` | VARCHAR(50) | NO | `'knowledge_rag'` | - | 에이전트 유형 (멀티 에이전트 확장용) |
| 10 | `resolved_knowledge_id` | INT | YES | NULL | FK → rag_knowledge(id) ON DELETE SET NULL | 지식 등록으로 해결 처리된 경우, 그 지식을 가리킴 — 통계 화면이 원본 AI 답변 대신 이 지식의 최신 content를 보여줌(v2.36). 조회 시 `status='active'`인 경우만 join되어, 나중에 반려/병합돼도 죽은 내용이 계속 보이지 않음 |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_query_log_ns` | namespace_id | B-Tree | 네임스페이스 필터 |
| `idx_query_log_created` | created_at | B-Tree | 시간순 정렬 |
| `idx_query_log_ns_status` | (namespace_id, status) | B-Tree | 네임스페이스+상태 복합 필터 |
| `idx_query_log_resolved_knowledge` | resolved_knowledge_id | B-Tree | 해결 처리된 지식 역참조 (v2.36) |

**status 상태값**:

| 값 | 의미 | 전이 조건 |
|----|------|----------|
| `pending` | 보류 | 초기 상태 (검색 결과 있거나 LLM 답변 생성됨) |
| `resolved` | 해결 | 긍정 피드백, 지식 등록으로 해결 처리, 또는 관리자 승인 |
| `unresolved` | 미해결 | 검색 결과 없음 AND LLM 실질 답변 없음, 또는 부정 피드백(건너뛰기) |
| `no_knowledge` | 지식 공백 | 임계값 넘는 문서가 아예 없었거나, LLM 답변이 "관련 지식을 찾지 못했습니다"로 시작(v2.19, 판정 로직 v2.36에서 접두사 일치로 보강) |

---

## 10. ops_conversation

**목적**: 대화 스레드를 관리한다. 메시지와 요약의 상위 컨테이너 역할을 한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace_id` | INT | NO | - | FK → ops_namespace(id) ON DELETE CASCADE | 소속 네임스페이스 ID |
| 3 | `title` | VARCHAR(200) | NO | `''` | - | 대화 제목 |
| 4 | `trimmed` | BOOLEAN | NO | `FALSE` | - | 메모리 요약 수행 여부 |
| 5 | `user_id` | INT | YES | NULL | FK → ops_user(id) ON DELETE CASCADE | 대화 소유 사용자 |
| 6 | `inhouse_conv_id` | VARCHAR(200) | YES | NULL | - | 사내 LLM 대화 연결 ID |
| 7 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 8 | `agent_type` | VARCHAR(50) | NO | `'knowledge_rag'` | - | 에이전트 유형 (멀티 에이전트 확장용) |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_conversation_ns` | namespace_id | B-Tree | 네임스페이스 필터 |
| `idx_conversation_user` | user_id | B-Tree | 사용자별 대화 조회 |
| `idx_conversation_user_id` | (user_id, created_at DESC) | B-Tree | 사용자별 최신 대화 조회 |
| `idx_conversation_ns_user` | (namespace_id, user_id) | B-Tree | 네임스페이스+사용자 복합 필터 |

---

## 11. ops_message

**목적**: 대화 내 개별 메시지를 저장한다. 사용자 질문과 어시스턴트 답변을 쌍으로 관리한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `conversation_id` | INT | NO | - | FK → ops_conversation(id) CASCADE | 대화 ID |
| 3 | `role` | VARCHAR(20) | NO | - | - | 역할 (`user` \| `assistant`) |
| 4 | `content` | TEXT | NO | - | - | 메시지 내용 |
| 5 | `mapped_term` | VARCHAR(200) | YES | NULL | - | 매핑된 용어 (assistant만) |
| 6 | `results` | JSONB | YES | NULL | - | 검색 결과 JSON (assistant만) |
| 7 | `status` | VARCHAR(20) | NO | `'completed'` | - | 생성 상태 |
| 8 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_message_conv` | conversation_id | B-Tree | 대화별 메시지 조회 |
| `idx_message_conv_id` | (conversation_id, created_at) | B-Tree | 대화별 시간순 메시지 조회 |

**status 상태값**:

| 값 | 의미 |
|----|------|
| `generating` | LLM 답변 생성 중 (백그라운드 Task 실행 중) |
| `completed` | 생성 완료 |

**FK 동작**: 대화 삭제 시 메시지 CASCADE 삭제

---

## 12. ops_feedback

**목적**: 답변 품질에 대한 사용자 피드백(좋아요/싫어요)을 기록한다. 지식 가중치 자동 조정과 통계에 활용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `knowledge_id` | INT | YES | NULL | FK → rag_knowledge(id) SET NULL | 관련 지식 ID |
| 3 | `message_id` | INT | YES | NULL | FK → ops_message(id) SET NULL (마이그레이션 추가) | 관련 메시지 ID |
| 4 | `namespace_id` | INT | YES | NULL | FK → ops_namespace(id) ON DELETE CASCADE | 네임스페이스 ID |
| 5 | `question` | TEXT | YES | NULL | - | 원본 질문 |
| 6 | `is_positive` | BOOLEAN | NO | - | - | 긍정 여부 |
| 7 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 8 | `agent_type` | VARCHAR(50) | NO | `'knowledge_rag'` | - | 에이전트 유형 (멀티 에이전트 확장용) |
| 9 | `meta` | JSONB | YES | NULL | - | 에이전트별 추가 메타데이터 |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_feedback_ns_id` | namespace_id | B-Tree | 네임스페이스 필터 |

**FK 동작**: 네임스페이스 삭제 시 CASCADE, 지식/메시지 삭제 시 해당 필드 NULL 처리 (SET NULL)

---

## 13. rag_fewshot

**목적**: LLM 프롬프트에 포함할 질문-답변 예제 쌍을 저장한다. 질문 벡터로 유사한 예제를 검색하여 few-shot prompting에 활용한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace_id` | INT | NO | - | FK → ops_namespace(id) ON DELETE CASCADE | 소속 네임스페이스 ID |
| 3 | `question` | TEXT | NO | - | - | 예제 질문 (임베딩 대상) |
| 4 | `answer` | TEXT | NO | - | - | 예제 답변 |
| 5 | `knowledge_id` | INT | YES | NULL | FK → rag_knowledge(id) SET NULL | 연결된 지식 ID |
| 6 | `embedding` | VECTOR(768) | YES | NULL | - | question 임베딩 벡터 |
| 7 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 8 | `created_by_part` | VARCHAR(100) | YES | NULL | - | 최종 수정자의 소속 파트 (수정 시 갱신) |
| 9 | `created_by_user_id` | INT | YES | NULL | - | 최종 수정자 ID (수정 시 갱신) |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_fewshot_ns` | namespace_id | B-Tree | 네임스페이스 필터 |
| `idx_fewshot_ns_id` | namespace_id | B-Tree | 네임스페이스 필터 (성능 인덱스) |
| `idx_fewshot_emb` | embedding | HNSW (vector_cosine_ops) | 벡터 유사도 검색 |

**FK 동작**: 지식 삭제 시 `knowledge_id` NULL 처리 (SET NULL)

---

## 14. rag_conv_summary

**목적**: 대화 메모리 시스템(ConversationSummaryBuffer)의 요약을 저장한다. 새 질문에 대해 과거 대화 맥락을 시맨틱 리콜하는 데 사용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `conversation_id` | INT | NO | - | FK → ops_conversation(id) CASCADE | 대화 ID |
| 3 | `summary` | TEXT | NO | - | - | LLM이 생성한 대화 요약 |
| 4 | `embedding` | VECTOR(768) | YES | NULL | - | summary 임베딩 벡터 |
| 5 | `turn_start` | INT | NO | - | - | 요약 시작 턴 번호 |
| 6 | `turn_end` | INT | NO | - | - | 요약 종료 턴 번호 |
| 7 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_conv_summary_conv` | conversation_id | B-Tree | 대화별 요약 조회 |
| `idx_conv_summary_vec` | embedding | HNSW (vector_cosine_ops) | 벡터 유사도 검색 |

**FK 동작**: 대화 삭제 시 요약 CASCADE 삭제

**동작 파라미터**:

| 파라미터 | 값 | 설명 |
|---------|---|------|
| `SUMMARY_TRIGGER` | 4 | 요약 발생 주기 (교환 횟수) |
| `RECENT_EXCHANGES` | 2 | Working Memory 유지 교환 수 |
| 최소 유사도 | 0.45 | 리콜 최소 cosine 유사도 |
| 최대 리콜 | 2 | 리콜 최대 요약 수 |

---

## 15. ops_mcp_tool

**목적**: 네임스페이스별 외부 HTTP/MCP API 도구를 관리한다. McpToolAgent가 도구 선택·파라미터 검증·HTTP 호출에 활용한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace_id` | INT | NO | - | FK → ops_namespace(id) ON DELETE CASCADE | 소속 네임스페이스 ID |
| 3 | `name` | VARCHAR(100) | NO | - | - | 도구 이름 |
| 4 | `description` | TEXT | NO | `''` | - | 도구 설명 (LLM 도구 선택에 활용) |
| 5 | `method` | VARCHAR(10) | NO | `'GET'` | - | HTTP 메서드 (`GET` \| `POST` 등) |
| 6 | `hub_base_url` | TEXT | NO | `''` | - | 허브 베이스 URL (도구 레벨) |
| 7 | `tool_path` | TEXT | NO | `''` | - | 도구 경로 (`hub_base_url` + `tool_path` = 최종 URL) |
| 8 | `headers` | JSONB | NO | `{}` | - | 요청 헤더 (Authorization 등) |
| 9 | `param_schema` | JSONB | NO | `[]` | - | 파라미터 스키마 배열 (name, type, required, description, example) |
| 10 | `response_example` | JSONB | YES | NULL | - | 응답 예시 (LLM 컨텍스트 품질 향상용) |
| 11 | `timeout_sec` | INT | NO | `10` | - | HTTP 호출 타임아웃(초) |
| 12 | `max_response_kb` | INT | NO | `50` | - | 응답 크기 제한(KB) |
| 13 | `is_active` | BOOLEAN | NO | `TRUE` | - | 활성 여부 (채팅에서 비활성 도구 제외) |
| 14 | `created_by_user_id` | INT | YES | NULL | - | 등록 사용자 ID |
| 15 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 16 | `updated_at` | TIMESTAMPTZ | NO | `NOW()` | - | 수정일시 |

**param_schema 요소 구조** (JSONB 배열):
```json
[
  { "name": "userId", "type": "string", "required": true, "description": "사용자 ID", "example": "U001" },
  { "name": "limit",  "type": "number", "required": false, "description": "조회 개수", "example": "10" }
]
```
`type` 값: `string` | `number` | `boolean` | `array`
백엔드 `_coerce_params()`가 type 기반으로 string → 실제 타입 자동 변환

**FK 동작**: 네임스페이스 삭제 시 CASCADE

---

## 16. ops_mcp_tool_log

**목적**: MCP 도구 호출 감사 로그. 호출 성공/실패, 응답 크기, 소요 시간을 기록한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `tool_id` | INT | YES | NULL | FK → ops_mcp_tool(id) ON DELETE SET NULL | 호출 도구 ID |
| 3 | `tool_name` | VARCHAR(100) | YES | NULL | - | 도구 이름 (도구 삭제 후에도 보존) |
| 4 | `user_id` | INT | YES | NULL | FK → ops_user(id) ON DELETE SET NULL | 호출 사용자 ID |
| 5 | `namespace_id` | INT | YES | NULL | FK → ops_namespace(id) | 소속 네임스페이스 ID |
| 6 | `conversation_id` | INT | YES | NULL | - | 대화 ID |
| 7 | `params` | JSONB | YES | NULL | - | 호출 파라미터 |
| 8 | `response_status` | INT | YES | NULL | - | HTTP 응답 코드 |
| 9 | `response_kb` | FLOAT | YES | NULL | - | 응답 크기(KB) |
| 10 | `duration_ms` | INT | YES | NULL | - | 호출 소요 시간(ms) |
| 11 | `error` | TEXT | YES | NULL | - | 오류 메시지 |
| 12 | `called_at` | TIMESTAMPTZ | NO | `NOW()` | - | 호출 일시 |

---

## 17. ops_prompt

**목적**: LLM 프롬프트 텍스트를 DB에서 관리한다. Admin 시스템설정 탭에서 에이전트별로 필터링하여 실시간 편집 가능하며, 코드 배포 없이 프롬프트 튜닝이 가능하다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `func_key` | VARCHAR(100) | NO | - | UNIQUE | 프롬프트 식별 키 |
| 3 | `func_name` | VARCHAR(200) | NO | - | - | 프롬프트 표시명 |
| 4 | `content` | TEXT | NO | `''` | - | 프롬프트 내용 |
| 5 | `description` | TEXT | NO | `''` | - | 용도 설명 |
| 6 | `agent_type` | VARCHAR(50) | NO | `'all'` | - | 에이전트 스코프 (`all` \| `knowledge_rag` \| `text2sql` \| `mcp_tool`) |
| 7 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 8 | `updated_at` | TIMESTAMPTZ | NO | `NOW()` | - | 마지막 수정일시 |

**조회 방식**: `get_prompt(func_key, fallback)` — DB에 있으면 DB 값, 없으면 코드 내 fallback 사용. 결과는 인메모리 캐시, 편집 시 자동 무효화.

**Admin UI 동작**: `selectedAgent`에 따라 해당 에이전트 + `all` 항목만 표시. 에이전트 전환 시 목록 자동 필터.

**주요 func_key**:

| func_key | agent_type | 설명 |
|----------|-----------|------|
| `chat_system` | `knowledge_rag` | RAG 채팅 시스템 프롬프트 |
| `category_suggest` | `knowledge_rag` | 지식 카테고리 자동 추천 |
| `glossary_suggest` | `knowledge_rag` | 미매핑 질문에서 업무 용어 추출 |
| `tool_select` | `mcp_tool` | McpToolAgent 도구 선택 시스템 프롬프트 |
| `tool_answer` | `mcp_tool` | MCP 응답 기반 LLM 답변 프롬프트 |
| `autocomplete` | `mcp_tool` | 도구 등록 자동완성 (자연어→JSON 변환) |
| `conv_summarize` | `all` | 대화 기록 요약 (에이전트 공통) |
| `sql2_parse` | `text2sql` | Text2SQL Stage 1 질문 분석 프롬프트 (`{{question}}`) |
| `sql2_parse_system` | `text2sql` | Text2SQL Stage 1 시스템 프롬프트 |
| `sql2_generate` | `text2sql` | Text2SQL Stage 3 SQL 생성 프롬프트 (`{{question}}` · `{{schema}}` 등) |
| `sql2_generate_system` | `text2sql` | Text2SQL Stage 3 시스템 프롬프트 |
| `sql2_fix` | `text2sql` | Text2SQL Stage 5 자동 수정 프롬프트 (`{{sql}}` · `{{errors}}` · `{{schema}}`) |
| `sql2_fix_system` | `text2sql` | Text2SQL Stage 5 시스템 프롬프트 |
| `sql2_summarize` | `text2sql` | Text2SQL Stage 7 결과 요약 프롬프트 (`{{question}}` · `{{sql}}` · `{{result_preview}}` · `{{columns}}`) |
| `sql2_summarize_system` | `text2sql` | Text2SQL Stage 7 시스템 프롬프트 |

---

## 18. ops_system_config

**목적**: 시스템 전역 설정을 DB에서 관리한다. 재시작 후에도 설정이 유지되며, 런타임 변경 즉시 적용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `key` | VARCHAR(100) | NO | - | PK | 설정 키 |
| 2 | `value` | TEXT | NO | - | - | 설정 값 (문자열) |
| 3 | `updated_at` | TIMESTAMPTZ | NO | `NOW()` | - | 마지막 수정일시 |

**현재 저장 항목**:
| key | 설명 | 기본값 |
|-----|------|--------|
| `cache_enabled` | Semantic Cache 활성화 여부 | `true` |
| `cache_similarity_threshold` | 캐시 히트 cosine 유사도 임계값 | `0.92` |
| `cache_ttl` | 캐시 TTL(초) | `1800` |
| `email_collection_enabled` | VOC 이메일 폴링 스케줄러 활성화 여부 (v3.8) | `false` |
| `email_polling_interval_minutes` | 폴링 주기(분) (v3.8) | `5` |
| `email_lookback_days` | 폴링 시 조회할 과거 기간(일) (v3.8) | `7` |
| `email_graph_credentials` | Microsoft Graph API 자격증명(tenant_id/client_id/client_secret) — Fernet 암호화된 JSON 문자열. 값이 없으면 "행 없음"으로 미설정 상태 표현 (v3.8) | (없음, 관리자가 입력 시에만 생성) |
| `email_relevance_min_score` | VOC 이메일 관련지식 사전 필터 임계치 — 등록된 지식과의 최고 유사도(`final_score`)가 이 값 미만이면 LLM 분석·Teams 발송 없이 건너뜀(무관한 메일의 비용·알림 노이즈 억제) (v3.9) | `0.35` |
| `email_graph_delegated` | Delegated Permission(사용자 위임 권한) 로그인 세션 — `{tenant_id, client_id, redirect_uri, client_secret, cache}` Fernet 암호화 JSON. `client_secret`은 선택 필드(v3.11) — 리다이렉트 URI가 Azure AD "Web" 플랫폼으로 등록돼 PKCE만으론 토큰 교환이 거부되는(AADSTS7000218) 실사용 사례가 확인돼 추가, 값이 있으면 Confidential Client·없으면 기존과 동일하게 Public Client로 동작. `cache`는 msal `SerializableTokenCache.serialize()` 결과(refresh token 포함, 자격증명급 취급). 인증 방식은 Authorization Code Flow(PKCE) — Device Code Flow는 피싱 리스크로 보안팀이 비권장해 채택하지 않음(v3.10). Application 권한(Track B) 승인 전 임시 경로 — 있으면 무인 폴링/수동실행이 이 세션으로 대체 동작 | (없음, 로그인 완료 시에만 생성) |

**로드 흐름**: 앱 시작 → `main.py` lifespan → `sem_cache.load_config_from_db(conn)` → 런타임 전역변수 갱신
**저장 흐름**: `PUT /api/admin/cache/config` → 런타임 전역변수 즉시 반영 + DB upsert

---

## 18-1. ops_voc_routing

**목적**: VOC 이메일 분석 채널(`docs/email-analysis-channel-plan.md`)의 파트별 담당 메일함 ↔ Teams 웹훅 ↔ 온콜 연락처 라우팅 매핑. 메일함 단위 분리 방식(파트별 메일주소 자체를 분리) 채택 — §10 참조.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace_id` | INT | NO | - | FK → ops_namespace(id) ON DELETE CASCADE | 소속 네임스페이스 ID |
| 3 | `part` | VARCHAR(100) | NO | - | - | 담당 파트명 |
| 4 | `mailbox_upn` | VARCHAR(255) | NO | - | UNIQUE(namespace_id, mailbox_upn) | 대상 공용 메일함 UPN (예: `voc-payment@company.com`) |
| 5 | `teams_webhook_url` | TEXT | YES | NULL | - | 해당 파트 Teams 채널의 Workflows 웹훅 URL |
| 6 | `oncall_contact_name` | VARCHAR(100) | YES | NULL | - | 온콜 담당자명 (Teams 멘션 표시용) |
| 7 | `oncall_contact_phone` | VARCHAR(50) | YES | NULL | - | 온콜 담당자 연락처 (수동 전화용 — 자동 발신 없음) |
| 8 | `is_active` | BOOLEAN | NO | `TRUE` | - | 활성 여부 (비활성 시 폴링/수동실행 대상에서 제외) |
| 9 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 10 | `updated_at` | TIMESTAMPTZ | NO | `NOW()` | - | 수정일시 |

**FK 동작**: 네임스페이스 삭제 시 CASCADE. `ops_email_analysis.routing_id`는 라우팅 삭제 시 SET NULL(분석 이력은 보존).
**권한**: CRUD 시 `namespace_id`까지 함께 검증(크로스 네임스페이스 변조 방지 — v3.8에서 발견·수정된 보안 이슈).

---

## 18-2. ops_email_analysis

**목적**: 수집된 이메일 건별 RAG 분석 결과 + Teams 알림 발송 결과를 저장. `source_message_id` UNIQUE로 폴링 재조회 윈도우가 겹쳐도 같은 메일을 중복 분석하지 않는다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace_id` | INT | NO | - | FK → ops_namespace(id) ON DELETE CASCADE | 소속 네임스페이스 ID |
| 3 | `routing_id` | INT | YES | NULL | FK → ops_voc_routing(id) ON DELETE SET NULL | 매칭된 라우팅 매핑 |
| 4 | `source_message_id` | VARCHAR(300) | NO | - | UNIQUE(namespace_id, source_message_id) | Graph API 메시지 ID (중복 수집 방지 키) |
| 5 | `mailbox_upn` | VARCHAR(255) | NO | - | - | 수집 대상 메일함 |
| 6 | `subject` | TEXT | NO | `''` | - | 메일 제목 |
| 7 | `sender` | VARCHAR(255) | NO | `''` | - | 발신자 |
| 8 | `received_at` | TIMESTAMPTZ | YES | NULL | - | 메일 수신 시각 |
| 9 | `body` | TEXT | NO | `''` | - | 메일 본문 |
| 10 | `category` | VARCHAR(20) | YES | NULL | - | LLM 분류 결과 (`system_error`/`user_mistake`/`uncertain`) |
| 11 | `severity` | VARCHAR(10) | YES | NULL | - | 심각도 (`low`/`medium`/`high`/`urgent`) |
| 12 | `mismatch_flagged` | BOOLEAN | NO | `FALSE` | - | 담당 파트 오배치 의심 플래그 |
| 13 | `knowledge_ref_ids` | INT[] | YES | NULL | - | 답변 생성에 참고한 지식 ID 배열 |
| 14 | `resolution_draft` | TEXT | YES | NULL | - | LLM이 생성한 대응 답변 초안 |
| 15 | `status` | VARCHAR(20) | NO | `'analyzed'` | - | 처리 상태 (`analyzed`/`notified`/`notify_failed`/`skipped_relevance`(관련지식 임계치 미달로 LLM 미호출, v3.9) 등) |
| 16 | `teams_sent_at` | TIMESTAMPTZ | YES | NULL | - | Teams 알림 발송 성공 시각 |
| 17 | `notify_error` | TEXT | YES | NULL | - | Teams 알림 발송 실패 사유 (이력 화면에서 성공/실패+원인 함께 표시) |
| 18 | `reasoning` | TEXT | YES | NULL | - | LLM 분류 판단 근거 (LLM 호출 실패 시 재시도 후에도 실패하면 강제 `severity='medium'` + 실패 사유 기록) |
| 19 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 20 | `updated_at` | TIMESTAMPTZ | NO | `NOW()` | - | 수정일시 |

**인덱스**:
- `idx_email_analysis_status (status)`
- `idx_email_analysis_created_at (created_at)` — 30일 보관정책(`retention.py`) 정리 배치 전용, namespace 선행 복합 인덱스로는 못 타서 별도 추가
- `idx_email_analysis_ns_sort (namespace_id, (COALESCE(received_at, created_at)) DESC)` — 이력 화면 정렬(`ORDER BY COALESCE(received_at, created_at) DESC`)과 표현식이 정확히 일치해야 인덱스를 탐

**보관 정책**: 30일 고정 — 스케줄러가 매일 1회(`_maybe_run_cleanup`) `created_at` 기준으로 자동 삭제.

**관련지식 사전 필터 (v3.9)**: `status='skipped_relevance'`인 행은 등록된 지식과의 최고 유사도가 `email_relevance_min_score` 미만이라 LLM을 호출하지 않고 저장된 것 — `category`/`severity`/`knowledge_ref_ids`/`resolution_draft`는 모두 NULL(또는 빈 배열)이고 `reasoning`에만 스킵 사유(유사도 수치 포함)가 기록된다.

---

## 18-3. ops_email_poll_cycle

**목적**: 백그라운드 폴링 스케줄러가 한 번 돌 때마다의 결과(성공/실패 메일함 수, 처리 건수)를 기록. 개별 이메일 단위인 `ops_email_analysis`만으로는 "사이클이 언제 돌았고 몇 개 메일함이 실패했는지"가 보이지 않아 별도로 둔다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `started_at` | TIMESTAMPTZ | NO | - | - | 사이클 시작 시각 |
| 3 | `finished_at` | TIMESTAMPTZ | YES | NULL | - | 사이클 종료 시각 |
| 4 | `namespaces_processed` | INT | NO | `0` | - | 처리한 네임스페이스 수 |
| 5 | `mailboxes_ok` | INT | NO | `0` | - | 성공한 메일함 수 |
| 6 | `mailboxes_failed` | INT | NO | `0` | - | 실패한 메일함 수 |
| 7 | `total_fetched` | INT | NO | `0` | - | 조회된 총 메일 건수 |
| 8 | `total_analyzed` | INT | NO | `0` | - | 분석 완료 건수 |
| 9 | `total_notified` | INT | NO | `0` | - | Teams 알림 발송 성공 건수 |
| 10 | `total_notify_failed` | INT | NO | `0` | - | Teams 알림 발송 실패 건수 |
| 11 | `total_skipped_duplicate` | INT | NO | `0` | - | 중복(재조회 윈도우 겹침)으로 건너뛴 건수 |
| 12 | `total_skipped_low_relevance` | INT | NO | `0` | - | 관련지식 임계치 미달로 건너뛴 건수 (v3.9) |
| 13 | `error_summary` | TEXT | YES | NULL | - | 사이클 단위 오류 요약 |
| 14 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**: `idx_email_poll_cycle_started (started_at DESC)`
**보관 정책**: 30일 고정 — `ops_email_analysis`와 동일 정리 배치에서 `started_at` 기준 자동 삭제.

---

## 19. 트리거 및 함수

### update_updated_at()

`rag_knowledge.updated_at`를 자동 갱신하는 트리거 함수.

```sql
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_knowledge_updated_at
    BEFORE UPDATE ON rag_knowledge
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();
```

---

## 20. 마이그레이션

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

**데이터 마이그레이션**:
- `ops_query_log.answer`가 NULL인 레코드에 대해 `ops_message`에서 매칭되는 답변을 역보충(backfill)한다.
- namespace FK 추가 전, 각 테이블의 namespace 값 중 `ops_namespace`에 없는 값을 자동 생성한다.
- `rag_knowledge.category`가 NULL인 레코드를 `'공통지식'`으로 일괄 갱신한다 (`init/04-category-required-backfill.sql`).

---

## 부록: PostgreSQL 확장

| 확장 | 용도 |
|------|------|
| `vector` (pgvector) | VECTOR 타입, HNSW/IVFFlat 인덱스, cosine distance 연산 |
| `pg_trgm` | 트라이그램 기반 퍼지 문자열 매칭 |

```sql
-- init/01-init.sql에서 활성화
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

---

## 부록: pgvector 상세

### pgvector란

**pgvector**는 PostgreSQL에 벡터 데이터 타입과 유사도 검색 기능을 추가하는 확장이다.
별도의 벡터 DB(Pinecone, Milvus 등)를 두지 않고, 기존 PostgreSQL 안에서 벡터 저장·검색·JOIN을 모두 처리할 수 있다.

**이 프로젝트에서의 역할**: 임베딩 모델이 생성한 768차원 벡터를 저장하고, 사용자 질문과 코사인 유사도가 높은 문서를 빠르게 검색한다.

### VECTOR 타입

```sql
-- 768차원 벡터 컬럼 선언
embedding VECTOR(768)

-- 벡터 삽입
INSERT INTO rag_knowledge (content, embedding)
VALUES ('쿠폰 회수 처리...', '[0.12, -0.34, 0.87, ...]'::vector);

-- 차원 수는 임베딩 모델에 의해 결정됨
-- paraphrase-multilingual-mpnet-base-v2 → 768차원
```

### 거리 연산자

| 연산자 | 의미 | 용도 |
|--------|------|------|
| `<=>` | 코사인 거리 (1 - 유사도) | **본 프로젝트에서 사용** |
| `<->` | L2 (유클리드) 거리 | 미사용 |
| `<#>` | 내적의 음수 | 미사용 |

```sql
-- 코사인 유사도 검색 예시
-- 거리가 작을수록 유사 → (1 - 거리) = 유사도 점수
SELECT id, content,
       1 - (embedding <=> $query_vec) AS similarity
FROM rag_knowledge
WHERE namespace_id = $namespace_id
ORDER BY embedding <=> $query_vec
LIMIT 5;
```

> `normalize_embeddings=True`로 임베딩을 정규화하면 코사인 거리 = 1 - 내적이 되어 계산이 더 빠르고 안정적이다.

### 인덱스 전략: HNSW vs IVFFlat

| 항목 | HNSW | IVFFlat |
|------|------|---------|
| **알고리즘** | 계층적 그래프 탐색 | 클러스터 기반 역인덱스 |
| **검색 속도** | 빠름 (O(log N)) | 보통 |
| **정확도** | 높음 (recall ~99%) | 중간 (리스트 수에 의존) |
| **빌드 시간** | 느림 | 빠름 |
| **메모리** | 더 많이 사용 | 적음 |
| **데이터 추가** | 즉시 반영 | 재인덱싱 필요할 수 있음 |
| **적합한 경우** | 실시간 CRUD, 수만~수십만 건 | 대량 배치 삽입, 수백만 건 이상 |

**본 프로젝트 선택: HNSW**
- 지식/용어/퓨샷이 실시간으로 등록·수정·삭제되므로 즉시 반영이 중요
- 문서 수가 수만 건 이내로 예상되어 HNSW의 메모리 오버헤드 수용 가능
- 높은 recall 필요 (운영 가이드 누락 방지)

```sql
-- HNSW 인덱스 생성 (코사인 거리 기준)
CREATE INDEX idx_knowledge_emb
ON rag_knowledge USING hnsw (embedding vector_cosine_ops);

-- 옵션 조정 (기본값 사용 중)
-- m: 그래프 연결 수 (기본 16) — 높을수록 정확, 느린 빌드
-- ef_construction: 빌드 시 탐색 폭 (기본 64) — 높을수록 정확, 느린 빌드
```

### 벡터 인덱스 목록

| 인덱스 | 대상 테이블 | 거리 함수 | 용도 |
|--------|------------|----------|------|
| `idx_glossary_emb` | rag_glossary.embedding | cosine | 용어집 Term Mapping (유사도 ≥ 0.5) |
| `idx_knowledge_emb` | rag_knowledge.embedding | cosine | 지식 하이브리드 검색 (벡터 파트) |
| `idx_fewshot_emb` | rag_fewshot.embedding | cosine | Few-shot Q&A 매칭 (유사도 ≥ 0.6) |
| `idx_conv_summary_vec` | rag_conv_summary.embedding | cosine | 대화 요약 Semantic Recall (유사도 ≥ 0.45) |

### 유사도 임계값 설정

검색 시 사용하는 최소 유사도는 Admin > LLM 설정에서 런타임 조정 가능하다.

| 파라미터 | 기본값 | 대상 | 설명 |
|---------|--------|------|------|
| `glossary_min_similarity` | 0.5 | 용어집 매핑 | 이 이상이면 표준 용어로 매핑 |
| `fewshot_min_similarity` | 0.6 | Few-shot 검색 | 이 이상이면 LLM 프롬프트에 삽입 |
| `knowledge_min_score` | 0.1 | 지식 검색 | 최종 점수가 이 이상인 결과만 반환 |
| `knowledge_high_score` | 0.7 | 지식 검색 | 고신뢰 결과 판정 기준 |
| `knowledge_mid_score` | 0.4 | 지식 검색 | 중간 신뢰 결과 판정 기준 |

### 하이브리드 검색 점수 공식

pgvector의 벡터 검색과 PostgreSQL GIN 인덱스의 키워드 검색을 가중 결합한다.

```sql
-- 벡터 점수
v_score = 1 - (embedding <=> query_vec)     -- 코사인 유사도 (0~1)

-- 키워드 점수
k_score = ts_rank(
    to_tsvector('simple', content),
    plainto_tsquery('simple', enriched_query)
)                                            -- BM25 유사 점수

-- 최종 점수
final_score = (w_vector * v_score + w_keyword * k_score) * (1 + base_weight)
--             └─ 기본 0.7          └─ 기본 0.3              └─ 피드백 가중치
```

### GIN 인덱스 (Full-Text Search)

벡터 검색과 함께 키워드 기반 전문 검색에 사용된다.

```sql
-- rag_knowledge에만 GIN 인덱스 존재 (키워드 검색 대상)
CREATE INDEX idx_knowledge_fts
ON rag_knowledge USING GIN (to_tsvector('simple', content));

-- 'simple' 설정: 한국어 형태소 분석 없이 공백 기준 토큰화
-- 한국어 전용 FTS 설정이 없으므로 벡터 검색이 주력, 키워드는 보조 역할
```

### 운영 참고사항

| 항목 | 설명 |
|------|------|
| **Docker 이미지** | `pgvector/pgvector:pg16` — pgvector가 사전 설치된 PostgreSQL 16 |
| **벡터 차원 변경** | 임베딩 모델 교체 시 `VECTOR(768)` → 새 차원으로 DDL 변경 + 전체 재임베딩 필요 |
| **인덱스 재빌드** | `REINDEX INDEX idx_knowledge_emb;` — 데이터 대량 변경 후 성능 저하 시 |
| **백업** | `pg_dump`로 벡터 컬럼 포함 전체 백업 가능 (별도 처리 불필요) |
| **데이터 볼륨** | `pgdata` Docker 볼륨에 저장 — `docker compose down`으로도 보존, `down -v`하면 삭제 |
