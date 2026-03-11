# Ops-Navigator 테이블 정의서

> **Version**: 3.0
> **DBMS**: PostgreSQL 16 + pgvector
> **Extensions**: `vector`, `pg_trgm`
> **벡터 차원**: 768 (paraphrase-multilingual-mpnet-base-v2)
> **작성일**: 2026-03-11
> **DDL 위치**: `init/01-init.sql`

---

## 목차

1. [ERD 개요](#1-erd-개요)
2. [ops_part](#2-ops_part)
3. [ops_user](#3-ops_user)
4. [ops_namespace](#4-ops_namespace)
5. [ops_glossary](#5-ops_glossary)
6. [ops_knowledge](#6-ops_knowledge)
7. [ops_knowledge_category](#7-ops_knowledge_category)
8. [ops_query_log](#8-ops_query_log)
9. [ops_conversation](#9-ops_conversation)
10. [ops_message](#10-ops_message)
11. [ops_feedback](#11-ops_feedback)
12. [ops_fewshot](#12-ops_fewshot)
13. [ops_conv_summary](#13-ops_conv_summary)
14. [트리거 및 함수](#14-트리거-및-함수)
15. [마이그레이션](#15-마이그레이션)

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
    │        │        │                       └── ops_knowledge (knowledge_id FK)
    │        │        │
    │        │        └── ops_conv_summary
    │        │
    │        ├─── ops_knowledge       (created_by_user_id)
    │        ├─── ops_glossary        (created_by_user_id)
    │        └─── ops_fewshot         (created_by_user_id)
    │
    └─── ops_namespace                (owner_part_id FK → ops_part.id ON DELETE SET NULL)
             │
             ├─── ops_glossary            (namespace_id FK CASCADE)
             ├─── ops_knowledge ◄──┐      (namespace_id FK CASCADE)
             │        │            │
             │        └── ops_fewshot     (knowledge_id FK)
             │
             ├─── ops_knowledge_category  (namespace_id FK CASCADE)
             ├─── ops_conversation        (namespace_id FK CASCADE)
             ├─── ops_feedback            (namespace_id FK CASCADE)
             └─── ops_query_log           (namespace_id FK CASCADE)
```

**테이블 수**: 12개
**FK 관계**: CASCADE 11건 (namespace_id 7건 + conversation 2건 + user 1건 + part 1건), SET NULL 3건

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

**목적**: 시스템 사용자(관리자/일반)를 관리한다. 인증, 권한 제어, LLM API 키 암호화 저장에 사용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `username` | VARCHAR(100) | NO | - | UNIQUE | 로그인 ID |
| 3 | `hashed_password` | TEXT | NO | - | - | bcrypt 해시 비밀번호 |
| 4 | `role` | VARCHAR(20) | YES | `'user'` | - | 역할 (`admin` \| `user`) |
| 5 | `part_id` | INT | YES | NULL | FK → ops_part(id) ON DELETE SET NULL | 소속 파트 ID |
| 6 | `is_active` | BOOLEAN | YES | `TRUE` | - | 계정 활성 여부 |
| 7 | `encrypted_llm_api_key` | TEXT | YES | NULL | - | 사용자별 LLM API 키 (암호화) |
| 8 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

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

## 5. ops_glossary

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

## 6. ops_knowledge

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
| 9 | `category` | VARCHAR(100) | YES | NULL | - | 지식 카테고리 (ops_knowledge_category 연동) |
| 10 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 11 | `updated_at` | TIMESTAMPTZ | NO | `NOW()` | - | 수정일시 (트리거 자동갱신) |
| 12 | `created_by_part` | VARCHAR(100) | YES | NULL | - | 최종 수정자의 소속 파트 (수정 시 갱신) |
| 13 | `created_by_user_id` | INT | YES | NULL | - | 최종 수정자 ID (수정 시 갱신) |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_knowledge_ns` | namespace_id | B-Tree | 네임스페이스 필터 |
| `idx_knowledge_emb` | embedding | HNSW (vector_cosine_ops) | 벡터 유사도 검색 |
| `idx_knowledge_fts` | to_tsvector('simple', content) | GIN | 전문 검색 (Full-Text Search) |

**트리거**: `trg_knowledge_updated_at` → UPDATE 시 `updated_at` 자동 갱신

**점수 산출 공식**:
```
final_score = (w_vector * v_score + w_keyword * k_score) * (1 + base_weight)
```

---

## 7. ops_knowledge_category

**목적**: 네임스페이스별 지식 카테고리를 관리한다. `ops_knowledge.category` 컬럼의 유효값 목록으로 활용된다.

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

## 8. ops_query_log

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

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_query_log_ns` | namespace_id | B-Tree | 네임스페이스 필터 |
| `idx_query_log_created` | created_at | B-Tree | 시간순 정렬 |

**status 상태값**:

| 값 | 의미 | 전이 조건 |
|----|------|----------|
| `pending` | 보류 | 초기 상태 (검색 결과 있거나 LLM 답변 생성됨) |
| `resolved` | 해결 | 긍정 피드백 또는 관리자 해결 처리 |
| `unresolved` | 미해결 | 검색 결과 없음 AND LLM 실질 답변 없음, 또는 부정 피드백 |

---

## 9. ops_conversation

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

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_conversation_ns` | namespace_id | B-Tree | 네임스페이스 필터 |
| `idx_conversation_user` | user_id | B-Tree | 사용자별 대화 조회 |

---

## 10. ops_message

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

**status 상태값**:

| 값 | 의미 |
|----|------|
| `generating` | LLM 답변 생성 중 (백그라운드 Task 실행 중) |
| `completed` | 생성 완료 |

**FK 동작**: 대화 삭제 시 메시지 CASCADE 삭제

---

## 11. ops_feedback

**목적**: 답변 품질에 대한 사용자 피드백(좋아요/싫어요)을 기록한다. 지식 가중치 자동 조정과 통계에 활용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `knowledge_id` | INT | YES | NULL | FK → ops_knowledge(id) SET NULL | 관련 지식 ID |
| 3 | `message_id` | INT | YES | NULL | FK → ops_message(id) SET NULL (마이그레이션 추가) | 관련 메시지 ID |
| 4 | `namespace_id` | INT | YES | NULL | FK → ops_namespace(id) ON DELETE CASCADE | 네임스페이스 ID |
| 5 | `question` | TEXT | YES | NULL | - | 원본 질문 |
| 6 | `is_positive` | BOOLEAN | NO | - | - | 긍정 여부 |
| 7 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**FK 동작**: 네임스페이스 삭제 시 CASCADE, 지식/메시지 삭제 시 해당 필드 NULL 처리 (SET NULL)

---

## 12. ops_fewshot

**목적**: LLM 프롬프트에 포함할 질문-답변 예제 쌍을 저장한다. 질문 벡터로 유사한 예제를 검색하여 few-shot prompting에 활용한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace_id` | INT | NO | - | FK → ops_namespace(id) ON DELETE CASCADE | 소속 네임스페이스 ID |
| 3 | `question` | TEXT | NO | - | - | 예제 질문 (임베딩 대상) |
| 4 | `answer` | TEXT | NO | - | - | 예제 답변 |
| 5 | `knowledge_id` | INT | YES | NULL | FK → ops_knowledge(id) SET NULL | 연결된 지식 ID |
| 6 | `embedding` | VECTOR(768) | YES | NULL | - | question 임베딩 벡터 |
| 7 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 8 | `created_by_part` | VARCHAR(100) | YES | NULL | - | 최종 수정자의 소속 파트 (수정 시 갱신) |
| 9 | `created_by_user_id` | INT | YES | NULL | - | 최종 수정자 ID (수정 시 갱신) |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_fewshot_ns` | namespace_id | B-Tree | 네임스페이스 필터 |
| `idx_fewshot_emb` | embedding | HNSW (vector_cosine_ops) | 벡터 유사도 검색 |

**FK 동작**: 지식 삭제 시 `knowledge_id` NULL 처리 (SET NULL)

---

## 13. ops_conv_summary

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

## 14. 트리거 및 함수

### update_updated_at()

`ops_knowledge.updated_at`를 자동 갱신하는 트리거 함수.

```sql
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_knowledge_updated_at
    BEFORE UPDATE ON ops_knowledge
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();
```

---

## 15. 마이그레이션

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
| 9 | `ops_knowledge` | `ADD COLUMN created_by_part VARCHAR(100), ADD COLUMN created_by_user_id INT` | 지식 생성자 추적 |
| 10 | `ops_glossary` | `ADD COLUMN created_by_part VARCHAR(100), ADD COLUMN created_by_user_id INT` | 용어 생성자 추적 |
| 11 | `ops_fewshot` | `ADD COLUMN created_by_part VARCHAR(100), ADD COLUMN created_by_user_id INT` | few-shot 생성자 추적 |
| 12 | `ops_namespace` | `ADD COLUMN owner_part VARCHAR(100), ADD COLUMN created_by_user_id INT` | 네임스페이스 소유 파트 기반 권한 제어 |
| 13 | 전체 테이블 | integer FK 전환 (`init/02-migrate-fk.sql`) | `namespace VARCHAR` → `namespace_id INT FK`, `owner_part VARCHAR` → `owner_part_id INT FK`, `part VARCHAR` → `part_id INT FK`. 기존 데이터를 보존하며 integer FK로 전환 |
| 14 | `ops_knowledge` | `ADD COLUMN category VARCHAR(100)` | 지식 카테고리 컬럼 추가 (nullable) |
| 15 | - | `CREATE TABLE ops_knowledge_category` | 네임스페이스별 카테고리 목록 관리 테이블 생성 |
| 16 | `ops_conversation` | `ADD COLUMN inhouse_conv_id VARCHAR(200)` | 사내 LLM 대화 ID 연결 컬럼 추가 |

**데이터 마이그레이션**:
- `ops_query_log.answer`가 NULL인 레코드에 대해 `ops_message`에서 매칭되는 답변을 역보충(backfill)한다.
- namespace FK 추가 전, 각 테이블의 namespace 값 중 `ops_namespace`에 없는 값을 자동 생성한다.

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
INSERT INTO ops_knowledge (content, embedding)
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
FROM ops_knowledge
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
ON ops_knowledge USING hnsw (embedding vector_cosine_ops);

-- 옵션 조정 (기본값 사용 중)
-- m: 그래프 연결 수 (기본 16) — 높을수록 정확, 느린 빌드
-- ef_construction: 빌드 시 탐색 폭 (기본 64) — 높을수록 정확, 느린 빌드
```

### 벡터 인덱스 목록

| 인덱스 | 대상 테이블 | 거리 함수 | 용도 |
|--------|------------|----------|------|
| `idx_glossary_emb` | ops_glossary.embedding | cosine | 용어집 Term Mapping (유사도 ≥ 0.5) |
| `idx_knowledge_emb` | ops_knowledge.embedding | cosine | 지식 하이브리드 검색 (벡터 파트) |
| `idx_fewshot_emb` | ops_fewshot.embedding | cosine | Few-shot Q&A 매칭 (유사도 ≥ 0.6) |
| `idx_conv_summary_vec` | ops_conv_summary.embedding | cosine | 대화 요약 Semantic Recall (유사도 ≥ 0.45) |

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
-- ops_knowledge에만 GIN 인덱스 존재 (키워드 검색 대상)
CREATE INDEX idx_knowledge_fts
ON ops_knowledge USING GIN (to_tsvector('simple', content));

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
