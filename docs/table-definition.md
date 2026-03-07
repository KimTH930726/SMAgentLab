# Ops-Navigator 테이블 정의서

> **Version**: 1.0
> **DBMS**: PostgreSQL 16 + pgvector
> **Extensions**: `vector`, `pg_trgm`
> **벡터 차원**: 768 (paraphrase-multilingual-mpnet-base-v2)
> **작성일**: 2026-03-07
> **DDL 위치**: `init/01-init.sql`

---

## 목차

1. [ERD 개요](#1-erd-개요)
2. [ops_namespace](#2-ops_namespace)
3. [ops_glossary](#3-ops_glossary)
4. [ops_knowledge](#4-ops_knowledge)
5. [ops_query_log](#5-ops_query_log)
6. [ops_conversation](#6-ops_conversation)
7. [ops_message](#7-ops_message)
8. [ops_feedback](#8-ops_feedback)
9. [ops_fewshot](#9-ops_fewshot)
10. [ops_conv_summary](#10-ops_conv_summary)
11. [트리거 및 함수](#11-트리거-및-함수)
12. [마이그레이션](#12-마이그레이션)

---

## 1. ERD 개요

```
ops_namespace
    │
    ├─── ops_glossary          (namespace 참조)
    ├─── ops_knowledge ◄──┐    (namespace 참조)
    │        │            │
    │        └── ops_fewshot    (knowledge_id FK)
    │
    ├─── ops_conversation
    │        │
    │        ├── ops_message ◄── ops_feedback (message_id FK)
    │        │                       │
    │        │                       └── ops_knowledge (knowledge_id FK)
    │        │
    │        └── ops_conv_summary
    │
    └─── ops_query_log         (namespace 참조)
```

**테이블 수**: 9개
**FK 관계**: CASCADE 2건, SET NULL 3건

---

## 2. ops_namespace

**목적**: 업무 도메인 격리. 모든 데이터를 네임스페이스 단위로 분리한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `name` | VARCHAR(100) | NO | - | UNIQUE | 네임스페이스 이름 |
| 3 | `description` | TEXT | NO | `''` | - | 설명 |
| 4 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**: PK(id)

---

## 3. ops_glossary

**목적**: 사용자의 모호한 표현을 내부 표준 용어로 매핑한다. 2단계 검색의 1단계(Term Mapping)에서 사용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace` | VARCHAR(100) | NO | - | - | 소속 네임스페이스 |
| 3 | `term` | VARCHAR(200) | NO | - | - | 표준 용어 |
| 4 | `description` | TEXT | NO | - | - | 용어 설명 |
| 5 | `embedding` | VECTOR(768) | YES | NULL | - | description 임베딩 벡터 |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_glossary_ns` | namespace | B-Tree | 네임스페이스 필터 |
| `idx_glossary_emb` | embedding | HNSW (vector_cosine_ops) | 벡터 유사도 검색 |

---

## 4. ops_knowledge

**목적**: 운영 가이드, 처리 절차, SQL 템플릿 등 핵심 지식을 저장한다. 2단계 검색의 2단계(Hybrid Search)에서 벡터+키워드 결합 검색의 대상이 된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace` | VARCHAR(100) | NO | - | - | 소속 네임스페이스 |
| 3 | `container_name` | VARCHAR(200) | YES | NULL | - | 관련 컨테이너/서비스명 |
| 4 | `target_tables` | TEXT[] | YES | NULL | - | 관련 DB 테이블 목록 |
| 5 | `content` | TEXT | NO | - | - | 지식 본문 (검색 대상) |
| 6 | `query_template` | TEXT | YES | NULL | - | SQL 쿼리 템플릿 |
| 7 | `embedding` | VECTOR(768) | YES | NULL | - | content 임베딩 벡터 |
| 8 | `base_weight` | FLOAT | NO | `1.0` | - | 검색 점수 가중치 |
| 9 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |
| 10 | `updated_at` | TIMESTAMPTZ | NO | `NOW()` | - | 수정일시 (트리거 자동갱신) |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_knowledge_ns` | namespace | B-Tree | 네임스페이스 필터 |
| `idx_knowledge_emb` | embedding | HNSW (vector_cosine_ops) | 벡터 유사도 검색 |
| `idx_knowledge_fts` | to_tsvector('simple', content) | GIN | 전문 검색 (Full-Text Search) |

**트리거**: `trg_knowledge_updated_at` → UPDATE 시 `updated_at` 자동 갱신

**점수 산출 공식**:
```
final_score = (w_vector * v_score + w_keyword * k_score) * (1 + base_weight)
```

---

## 5. ops_query_log

**목적**: 사용자 질의를 기록하고, 해결 상태를 추적한다. 통계 대시보드와 미해결 케이스 관리에 활용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace` | VARCHAR(100) | YES | NULL | - | 소속 네임스페이스 |
| 3 | `question` | TEXT | YES | NULL | - | 사용자 질문 |
| 4 | `answer` | TEXT | YES | NULL | - | LLM 답변 (마이그레이션 추가) |
| 5 | `status` | VARCHAR(20) | NO | `'pending'` | - | 처리 상태 |
| 6 | `mapped_term` | VARCHAR(200) | YES | NULL | - | 매핑된 용어 |
| 7 | `message_id` | INT | YES | NULL | - | 연결된 메시지 ID |
| 8 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_query_log_ns` | namespace | B-Tree | 네임스페이스 필터 |
| `idx_query_log_created` | created_at | B-Tree | 시간순 정렬 |

**status 상태값**:

| 값 | 의미 | 전이 조건 |
|----|------|----------|
| `pending` | 보류 | 초기 상태 (검색 결과 있거나 LLM 답변 생성됨) |
| `resolved` | 해결 | 긍정 피드백 또는 관리자 해결 처리 |
| `unresolved` | 미해결 | 검색 결과 없음 AND LLM 실질 답변 없음, 또는 부정 피드백 |

---

## 6. ops_conversation

**목적**: 대화 스레드를 관리한다. 메시지와 요약의 상위 컨테이너 역할을 한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace` | VARCHAR(100) | NO | - | - | 소속 네임스페이스 |
| 3 | `title` | VARCHAR(200) | NO | `''` | - | 대화 제목 |
| 4 | `trimmed` | BOOLEAN | NO | `FALSE` | - | 메모리 요약 수행 여부 (마이그레이션 추가) |
| 5 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_conversation_ns` | namespace | B-Tree | 네임스페이스 필터 |

---

## 7. ops_message

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

## 8. ops_feedback

**목적**: 답변 품질에 대한 사용자 피드백(좋아요/싫어요)을 기록한다. 지식 가중치 자동 조정과 통계에 활용된다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `knowledge_id` | INT | YES | NULL | FK → ops_knowledge(id) SET NULL | 관련 지식 ID |
| 3 | `message_id` | INT | YES | NULL | FK → ops_message(id) SET NULL (마이그레이션 추가) | 관련 메시지 ID |
| 4 | `namespace` | VARCHAR(100) | YES | NULL | - | 네임스페이스 |
| 5 | `question` | TEXT | YES | NULL | - | 원본 질문 |
| 6 | `is_positive` | BOOLEAN | NO | - | - | 긍정 여부 |
| 7 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**FK 동작**: 지식/메시지 삭제 시 해당 필드 NULL 처리 (SET NULL)

---

## 9. ops_fewshot

**목적**: LLM 프롬프트에 포함할 질문-답변 예제 쌍을 저장한다. 질문 벡터로 유사한 예제를 검색하여 few-shot prompting에 활용한다.

| # | 컬럼명 | 데이터 타입 | NULL | 기본값 | 제약조건 | 설명 |
|---|--------|-----------|------|--------|---------|------|
| 1 | `id` | SERIAL | NO | auto | PK | 고유 식별자 |
| 2 | `namespace` | VARCHAR(100) | NO | - | - | 소속 네임스페이스 |
| 3 | `question` | TEXT | NO | - | - | 예제 질문 (임베딩 대상) |
| 4 | `answer` | TEXT | NO | - | - | 예제 답변 |
| 5 | `knowledge_id` | INT | YES | NULL | FK → ops_knowledge(id) SET NULL | 연결된 지식 ID |
| 6 | `embedding` | VECTOR(768) | YES | NULL | - | question 임베딩 벡터 |
| 7 | `created_at` | TIMESTAMPTZ | NO | `NOW()` | - | 생성일시 |

**인덱스**:

| 인덱스명 | 컬럼 | 타입 | 설명 |
|---------|------|------|------|
| `idx_fewshot_ns` | namespace | B-Tree | 네임스페이스 필터 |
| `idx_fewshot_emb` | embedding | HNSW (vector_cosine_ops) | 벡터 유사도 검색 |

**FK 동작**: 지식 삭제 시 `knowledge_id` NULL 처리 (SET NULL)

---

## 10. ops_conv_summary

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

## 11. 트리거 및 함수

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

## 12. 마이그레이션

애플리케이션 시작 시 `backend/main.py`의 `_run_migrations()`에서 자동 실행된다. 모든 마이그레이션은 멱등(idempotent)하다.

| # | 대상 테이블 | 변경 내용 | 설명 |
|---|-----------|----------|------|
| 1 | `ops_query_log` | `ADD COLUMN answer TEXT` | 답변 기록용 컬럼 추가 |
| 2 | `ops_conversation` | `ADD COLUMN trimmed BOOLEAN NOT NULL DEFAULT FALSE` | 메모리 요약 수행 여부 플래그 |
| 3 | `ops_feedback` | `ADD COLUMN message_id INT REFERENCES ops_message(id) ON DELETE SET NULL` | 메시지-피드백 연결 |

**데이터 마이그레이션**: `ops_query_log.answer`가 NULL인 레코드에 대해 `ops_message`에서 매칭되는 답변을 역보충(backfill)한다.

---

## 부록: PostgreSQL 확장

| 확장 | 버전 | 용도 |
|------|------|------|
| `vector` (pgvector) | - | VECTOR 타입, HNSW/IVFFlat 인덱스, cosine distance 연산 |
| `pg_trgm` | - | 트라이그램 기반 퍼지 문자열 매칭 |

## 부록: 벡터 인덱스 전략

모든 벡터 인덱스는 **HNSW** (Hierarchical Navigable Small World) 알고리즘을 사용한다.

| 인덱스 | 대상 | 거리 함수 | 용도 |
|--------|------|----------|------|
| `idx_glossary_emb` | ops_glossary.embedding | cosine | 용어 매핑 |
| `idx_knowledge_emb` | ops_knowledge.embedding | cosine | 지식 검색 |
| `idx_fewshot_emb` | ops_fewshot.embedding | cosine | few-shot 매칭 |
| `idx_conv_summary_vec` | ops_conv_summary.embedding | cosine | 대화 요약 리콜 |
