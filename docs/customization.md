# Text-to-SQL 이식 작업 내역

## 1. 아키텍처 구조 변경

| 항목 | 벤치마크 (text2sql-main) | SMAgentLab (dev_0) |
|------|-------------------------|-------------------|
| 구조 | 독립형 FastAPI 앱 | DDD + Multi-Agent Framework (AgentBase) |
| 멀티테넌시 | 없음 (전역 단일) | **namespace_id** 기준 완전 격리 |
| 인증 | 없음 (오픈 API) | JWT + Admin RBAC (`require_admin`) |
| 채팅 통합 | 별도 chat 라우터 | **AgentBase.stream_chat()** 구현체로 통합 |
| 벡터 저장소 | Qdrant (외부 Docker) | **pgvector** (PostgreSQL 내장) |
| 임베딩 모델 | `multilingual-e5-small` (384차원) | `paraphrase-multilingual-mpnet-base-v2` (**768차원**) |

---

## 2. DB 스키마 변경

### 추가된 컬럼·테이블

| 테이블 | 추가 항목 | 이유 |
|--------|-----------|------|
| `sql_schema_table` | `namespace_id`, `pos_x`, `pos_y` | 멀티테넌시 분리 / ERD 위치 저장 |
| `sql_schema_column` | `namespace_id` | 멀티테넌시 분리 |
| `sql_synonym` | `namespace_id`, `embedding VECTOR(768)` | 멀티테넌시 + pgvector 검색 |
| `sql_fewshot` | `namespace_id`, `embedding VECTOR(768)` | 멀티테넌시 + pgvector 검색 |
| `sql_relation` | `namespace_id` | 멀티테넌시 분리 |
| `sql_target_db` | `namespace_id`, `encrypted_password` | 멀티테넌시 / Fernet 암호화 |
| `sql_schema_vector` | **신규** (namespace_id, column_id, embedding) | 컬럼 벡터를 별도 테이블로 분리 |

### 제거된 테이블

| 벤치마크 테이블 | 제거 이유 |
|----------------|-----------|
| `DailyStat` | 통계는 `ops_query_log` + 공통 stats 탭으로 대체 |
| `AppSetting` | 설정은 `ops_system_config` (key-value)로 대체 |
| `Conversation`, `ConversationTurn` | 대화 관리는 `ops_conversation`, `ops_message`로 대체 |

---

## 3. 파이프라인 변경 (10단계 → 7단계)

| 벤치마크 단계 | SMAgentLab | 변경 내용 |
|--------------|-----------|-----------|
| parse | parse.py | 동일 (intent/difficulty 추출) |
| **schema_link** | ❌ 제거 | LLM 테이블 선별 → generate 단계에서 RAG 컨텍스트로 대체 |
| rag | rag.py | 동일, 단 pgvector 쿼리로 변경 |
| **schema_explore** | ❌ 제거 | 샘플값 조회 단계 제거 (프롬프트 복잡도 감소) |
| generate | generate.py | 동일 + **멀티턴 대화 히스토리** 추가 |
| **candidates** | ❌ 제거 | 다중 SQL 후보 랭킹 제거 (단일 최선 SQL) |
| validate | validate.py | 동일 (안전성 + AST 검증) |
| fix | fix.py | 동일 (LLM 자동 수정) |
| execute | execute.py | 동일 (대상 DB 실행) |
| summarize | summarize.py | 동일 + 차트 추천 포함 |

---

## 4. API 엔드포인트 변경

### 네임스페이스 스코핑 추가
벤치마크의 전역 엔드포인트를 모두 `/namespaces/{namespace}/` 하위로 이동

```
벤치마크: GET /api/schema
SMAgentLab: GET /api/text2sql/namespaces/{namespace}/schema
```

### SMAgentLab에서 신규 추가된 엔드포인트

| 엔드포인트 | 설명 |
|-----------|------|
| `PUT /namespaces/{ns}/schema/positions` | ERD 테이블 위치 일괄 저장 |
| `POST /namespaces/{ns}/schema/reindex` | 스키마 벡터 재인덱싱 |
| `PUT /namespaces/{ns}/schema/tables/{id}/toggle` | RAG 포함 여부 토글 |
| `POST /namespaces/{ns}/synonyms/reindex` | 용어사전 벡터 재인덱싱 |
| `POST /namespaces/{ns}/synonyms/generate-ai` | AI 용어 자동생성 (LLM) |
| `POST /namespaces/{ns}/fewshots/reindex` | 예제 벡터 재인덱싱 |
| `POST /namespaces/{ns}/fewshots/generate-ai` | AI 예제 자동생성 (LLM) |
| `POST /namespaces/{ns}/relations/suggest-ai` | AI 관계 추천 (LLM) |

### 벤치마크에만 있던 엔드포인트 (미이식)

| 벤치마크 엔드포인트 | 미이식 이유 |
|--------------------|------------|
| `GET /api/stats/daily` | 공통 통계 탭으로 대체 |
| `POST /api/chat/feedback` | 공통 feedback 엔드포인트 사용 |
| `GET /api/settings/llm-providers` | 공통 LLM 설정 탭 사용 |

---

## 5. 벡터 검색 변경 (Qdrant → pgvector)

| 항목 | 벤치마크 | SMAgentLab |
|------|---------|------------|
| 저장소 | Qdrant (외부 컨테이너) | pgvector (PostgreSQL 확장) |
| 인덱스 | Qdrant 내부 HNSW | `CREATE INDEX USING hnsw (embedding vector_cosine_ops)` |
| 검색 쿼리 | Qdrant SDK API | `ORDER BY embedding <=> $1::vector LIMIT $2` |
| 네임스페이스 격리 | Qdrant 컬렉션 분리 | `WHERE namespace_id = $1` |
| 임베딩 차원 | 384 | **768** (더 높은 정확도) |

```sql
-- SMAgentLab 벡터 검색 패턴
SELECT *, (1 - (embedding <=> $1::vector)) AS score
FROM sql_synonym
WHERE namespace_id = $2
ORDER BY embedding <=> $1::vector
LIMIT $3
```

---

## 6. 암호화 변경

| 항목 | 벤치마크 | SMAgentLab |
|------|---------|------------|
| 암호화 방식 | Fernet (`ENCRYPTION_KEY` 환경변수) | Fernet (`fernet_secret_key` 설정, 없으면 `jwt_secret_key` fallback) |
| 대상 | DB 접속 비밀번호 | DB 비밀번호 + **사용자 LLM API Key** |
| 저장 위치 | `AppSetting` 테이블 | `sql_target_db.encrypted_password` + `ops_user.encrypted_api_key` |

---

## 7. LLM 연동 변경

| 항목 | 벤치마크 | SMAgentLab |
|------|---------|------------|
| Provider | OpenAI / Anthropic / Gemini 직접 호출 | **DevX MCP API** (InHouse) 또는 Ollama |
| API 형식 | 각 SDK 사용 | DevX: `usecase_code` + SSE / Ollama: `/api/chat` messages 배열 |
| Per-user 키 | 없음 | `ops_user.encrypted_api_key` → 복호화 후 Bearer 전달 |
| 멀티턴 | 없음 (단발성 쿼리) | `pipeline_ctx["history"]` 로 대화 히스토리 전달 |
| 모델 선택 | Provider별 하드코딩 | `INHOUSE_LLM_MODEL` 환경변수 (claude-sonnet-4.5 등) |

---

## 8. 프론트엔드 변경

| 항목 | 벤치마크 | SMAgentLab |
|------|---------|------------|
| 기술 스택 | Vite + React (기본) | Vite + React + **TailwindCSS + TanStack Query** |
| 상태 관리 | 없음 (useApi hook) | **Zustand** (useAppStore, useAuthStore, useThemeStore) |
| 타입 | 없음 (JS) | **TypeScript strict** |
| Admin UI | 없음 (또는 단순 설정폼) | **8탭 Admin 컴포넌트** (Text2SqlAdmin.tsx) |
| ERD | 기본 위치 표시 | **SVG 드래그 ERD** (위치 저장, 관계선, 줌, 되돌리기) |
| 채팅 결과 | 단순 텍스트 | **SQL 블록 + 결과 테이블 + SVG 차트** |
| 라이트/다크 | 없음 | **테마 전환 지원** (CSS 변수 기반) |

---

## 9. 핵심 설계 결정 (이식 시 변경 이유)

1. **Qdrant 제거** — 외부 서비스 의존성 감소, pgvector로 벡터+관계형 통합
2. **10→7 파이프라인** — schema_link/explore/candidates 단계가 사내 LLM 환경에서 오히려 노이즈 유발, 제거
3. **namespace 격리** — 단일 DB로 여러 팀/프로젝트 분리 운영 요건
4. **임베딩 768차원** — 한국어 성능 우선, e5-small(384)보다 mpnet(768)이 사내 데이터에 더 적합
5. **AgentBase 통합** — Text-to-SQL을 독립 앱이 아닌 멀티에이전트 플랫폼의 한 에이전트로 통합
6. **per-user API 키** — DevX API가 사용자별 키 사용을 권장하는 사내 정책 반영
