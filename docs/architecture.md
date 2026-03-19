# Ops-Navigator 시스템 아키텍처 (v2.9)

## 개요

Ops-Navigator는 IT 운영팀의 반복적인 조회·확인 업무를 자동화하는 **지능형 운영 보조 에이전트 플랫폼**이다.
사용자는 에이전트를 선택해 목적에 맞는 AI를 사용한다: 지식 기반 Q&A(KnowledgeRAG) 또는 자연어 → SQL 쿼리 실행(Text-to-SQL).

**주요 이력 요약** (자세한 내용은 `dev-handoff.md` 참조)
- v2.9: `ops_prompt` 에이전트별 분리 — `agent_type` 컬럼 추가, text2sql 파이프라인 프롬프트 `ops_prompt`로 통합, 파이프라인 탭 프롬프트 편집 UI 제거
- v2.8: `domain/` → `service/` + `agents/{agent}/` 재구성 + DB 테이블 prefix 변경 (`rag_*`)
- v2.7: SQL Few-shot 피드백 워크플로우, ERD 캔버스 패닝, MCP 도구 에이전트 분리
- v2.6: Text-to-SQL 어드민 UI 개편, ERD 고도화, AI 자동생성
- v2.5: Agent-centric UI, Text-to-SQL 에이전트(7단계 파이프라인)
- v2.4: MCP 도구 + Semantic Cache
- v2.3: AgentRegistry 패턴 도입
- v2.0: DDD 구조, JWT 인증, 부서 기반 권한

---

## 전체 구성도

```
┌─────────────────────────────────────────────────────────────┐
│                        Host Machine                         │
│                                                             │
│   ┌──────────────────────────────────────────────────────┐  │
│   │              Docker Compose Network                  │  │
│   │                                                      │  │
│   │  ┌─────────────┐    ┌─────────────┐                 │  │
│   │  │  Frontend   │───▶│   Backend   │                 │  │
│   │  │  React+nginx│    │   FastAPI   │                 │  │
│   │  │  :8501      │    │   :8000     │                 │  │
│   │  └─────────────┘    └──────┬──────┘                 │  │
│   │                            │                         │  │
│   │                    ┌───────┴────────┐                │  │
│   │                    │   PostgreSQL   │                │  │
│   │                    │  + pgvector    │                │  │
│   │                    │   :5432        │                │  │
│   │                    └───────────────┘                 │  │
│   └──────────────────────────────────────────────────────┘  │
│                                                             │
│   ┌──────────────────────┐                                  │
│   │  Ollama (호스트 직접)  │  ◀── Backend이 host.docker.      │
│   │  exaone3.5:2.4b      │       internal:11434 으로 호출   │
│   │  :11434              │                                  │
│   └──────────────────────┘                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 컴포넌트별 역할

### 1. Frontend — React + Nginx (`:8501`)

| 페이지 | 역할 |
|--------|------|
| **Login** (`/login`) | JWT 로그인 — Access Token + Refresh Token 발급 |
| **Register** (`/register`) | 회원가입 — 부서 선택 + 선택적 LLM API Key 등록 |
| **AgentSelect** (로그인 직후) | 에이전트 선택 화면 — 지식베이스 AI / Text-to-SQL 카드 선택. `selectedAgent=null`이면 이 화면 표시 (사이드바 없음) |
| **Chat** (`/`) | 에이전트별 채팅 — SSE 스트리밍, 결과 카드, 피드백(👍→few-shot/base_weight), 대화 메모리(요약+리콜), Markdown 답변, SQL블록+결과테이블+SVG차트 (text2sql), MCP 도구 토글 |
| **Admin** (`/admin`) | 에이전트별 관리 화면 — `agentScope` 필드로 탭 필터링. knowledge_rag: 네임스페이스·지식·용어집·Few-shot·MCP도구·캐시현황·통계·디버그. text2sql: 대상DB·스키마·ERD·용어사전·SQL Few-shot·파이프라인·감사로그. 공통: 시스템설정·사용자관리. (에이전트현황 탭 제거 — AgentSelect 화면에 헬스배지로 대체) |

- **Agent-centric 라우팅**: `useAppStore.selectedAgent: 'knowledge_rag' | 'text2sql' | null`. null이면 AgentSelect 표시, 설정 시 에이전트별 UI로 전환. 로그아웃 시 null로 리셋
- **MCP 도구 토글**: ChatContainer 내 `useHttpTool` boolean — ON 시 `agentType='mcp_tool'`, OFF 시 `selectedAgent` 값 사용. 에이전트가 아닌 도구
- **ProtectedRoute**: 로그인되지 않은 사용자는 `/login`으로 리다이렉트
- **useAuthStore** (Zustand): localStorage에 토큰 저장, 자동 Bearer 토큰 주입
- **401 Auto-refresh**: Access Token 만료 시 Refresh Token으로 자동 갱신, 실패 시 로그아웃
- **부서 기반 UI**: 지식/용어집/Few-shot 테이블에 부서 배지 표시, 같은 부서만 수정/삭제 버튼 노출
- Sidebar: 에이전트 배지 + 에이전트 변경 버튼, 사용자 정보 + 로그아웃, 네임스페이스 선택, 대화 목록, 검색 설정 슬라이더, 헬스 표시기
- Backend REST API만 호출 (직접 DB 접근 없음)
- 검색 비중(벡터/키워드 비율), Top-K를 사이드바 슬라이더로 실시간 조정 (개인 설정은 localStorage에 저장, DB 저장 없음)
- nginx 정적 빌드 서빙 + `/api/*` 요청을 Backend(`:8000`)로 프록시

### 2. Backend — FastAPI (`:8000`)

```
backend/
├── main.py              # 앱 진입점, 라이프사이클 (DB풀·임베딩·LLM·에이전트 초기화)
├── agents/              # 에이전트 레이어 (AgentBase + AgentRegistry 패턴)
│   ├── base.py          #   AgentBase 추상 클래스 + AgentRegistry 싱글톤
│   ├── knowledge_rag/
│   │   ├── agent.py     #   KnowledgeRagAgent — 하이브리드 검색 + LLM 스트리밍
│   │   ├── knowledge/   #   지식/용어집 CRUD + 하이브리드 검색 (retrieval.py)
│   │   └── fewshot/     #   Few-shot CRUD (status: active/candidate)
│   ├── mcp_tool/
│   │   └── agent.py     #   McpToolAgent — 3-case 플로우 + RAG + 감사 로그
│   ├── text2sql/
│   │   ├── agent.py     #   Text2SqlAgent (startup 병렬화, _cache_hit)
│   │   ├── admin/       #   Text2SQL 어드민 API (대상DB·스키마·ERD·용어사전·Few-shot·파이프라인·감사로그)
│   │   └── pipeline/    #   7단계: parse→rag→generate→validate→fix→execute→summarize
│   └── http_tool/       #   HttpToolAgent (레거시)
├── service/             # 플랫폼 공통 레이어 (was domain/, platform/ 명칭 stdlib 충돌로 service/ 확정)
│   ├── auth/            #   인증/계정 (JWT, bcrypt, Fernet API Key 암호화)
│   ├── chat/            #   채팅 라우터·헬퍼·메모리 (AgentRegistry 위임)
│   ├── feedback/        #   피드백 기록 + base_weight 조정
│   ├── admin/           #   네임스페이스·통계·LLM 설정
│   ├── mcp_tool/        #   MCP 도구 CRUD + 감사 로그
│   ├── prompt/          #   프롬프트 관리 (get_prompt: DB 우선, fallback)
│   └── llm/             #   LLM Provider 추상화 (ollama / inhouse)
├── core/
│   ├── config.py        # pydantic-settings, JWT·Fernet 키
│   ├── database.py      # asyncpg 풀 + resolve_namespace_id() 헬퍼
│   ├── security.py      # JWT, bcrypt, Fernet
│   └── dependencies.py  # get_current_user, get_current_admin, check_namespace_ownership
└── shared/
    ├── embedding.py     # Sentence-Transformers 싱글톤
    └── cache.py         # Semantic Cache (Redis, 유사도 0.88, TTL 30분, graceful degradation)
```

**주요 설계 원칙:**
- **AgentRegistry 패턴**: `chat_stream` → `AgentRegistry.get(agent_type).stream_chat()` 위임. 새 에이전트 추가 시 `agents/` 하위 모듈 + Registry 등록만으로 완결. 플랫폼(인증/세션/피드백)과 에이전트(파이프라인)를 분리.
- **DDD 구조**: 도메인별 디렉토리로 schemas/service/router를 응집 — 플랫 구조 대비 코드 탐색·확장 용이
- **비동기 전용**: asyncpg + httpx async — 블로킹 없는 I/O
- **임베딩 싱글톤**: 앱 시작 시 모델 1회 로드, 이후 thread executor로 재사용
- **LLM Provider 패턴**: `ollama` / `inhouse` 환경변수 하나로 교체 가능
- **LLM별 프롬프트 형식**: Ollama는 `build_messages()` messages 배열, InHouse(DevX MCP API)는 `_build_query()`로 단일 query 문자열 생성
- **대화 맥락**: ConversationSummaryBuffer + Semantic Recall — 오래된 교환을 LLM으로 요약·벡터 저장, 현재 질문과 유사한 과거 요약 + 최근 2회 raw 교환을 history로 LLM에 전달
- **멀티턴 검색 보강**: 직전 Q+A(각 80자)를 현재 질문에 결합하여 임베딩/검색 — 짧은 후속 질문에서도 이전 대화 맥락이 반영되어 유사도 향상 (추가 LLM 호출 없음)
- **마크다운 답변**: 시스템 프롬프트에 Markdown 형식 지시 포함, 프론트엔드에서 `react-markdown` + `remark-gfm` + `rehype-raw`로 테이블/코드/리스트/HTML 태그 렌더링
- **JWT 인증/인가**: Access Token(30분) + Refresh Token(7일), FastAPI Depends로 라우터 수준 보호
- **네임스페이스 소유 파트 기반 권한**: 네임스페이스의 `owner_part`와 동일한 부서 구성원만 해당 네임스페이스의 데이터 CRUD 가능, 타 부서는 읽기 전용. `owner_part` NULL이면 **모든 사용자(파트 무관)**가 CRUD 가능 (공통 namespace). Admin이 생성한 namespace는 자동으로 `owner_part = NULL`. Admin은 모든 권한 보유
- **수정 시 작성자 갱신**: 지식/용어/퓨샷 수정 시 `created_by_part`/`created_by_user_id`가 최종 수정자로 갱신됨
- **Graceful Degradation**: LLM 연결 실패 시 검색 결과는 정상 반환, 안내 메시지 출력

### 3. 인증/인가 시스템 (v2.0.0 신규)

```
┌──────────┐     POST /api/auth/register     ┌──────────────┐
│  사용자   │  ──────────────────────────────▶ │  auth/service │
│          │     (username, password,         │              │
│          │      part_id, api_key?)          │  bcrypt hash │
│          │                                  │  Fernet enc  │
│          │  ◀────────────────────────────── │              │
│          │     201 Created                  └──────┬───────┘
│          │                                         │
│          │     POST /api/auth/login                │
│          │  ──────────────────────────────▶        │
│          │  ◀──────────────────────────────        │
│          │     {access_token, refresh_token}       │
│          │                                         │
│          │     GET /api/chat/stream                │
│          │     Authorization: Bearer <access>      │
│          │  ──────────────────────────────▶ ┌──────┴───────┐
│          │                                  │ dependencies │
│          │                                  │ get_current_ │
│          │                                  │ user()       │
└──────────┘                                  └──────────────┘
```

**핵심 구성 요소:**

| 모듈 | 역할 |
|------|------|
| `core/security.py` | JWT 토큰 발급/검증 (HS256), bcrypt 비밀번호 해싱, Fernet 대칭 암호화 (API Key) |
| `core/dependencies.py` | `get_current_user` — Bearer 토큰 검증 후 사용자 반환 |
| | `get_current_admin` — admin 역할 검증 |
| | `check_namespace_ownership` — 네임스페이스의 `owner_part`와 요청자 부서 일치 확인 |
| `service/auth/service.py` | 회원가입 (중복 체크, bcrypt 해싱, Fernet API Key 암호화), 로그인, 토큰 갱신 |
| `service/auth/router.py` | `/api/auth/*` 엔드포인트 |

**권한 모델 (네임스페이스 기반):** 상세 규칙은 `api-specification.md § 3. 인증 및 권한` 참조.
- Admin은 모든 리소스 CRUD 가능. 일반 사용자는 `owner_part` 일치 시에만 CRUD (불일치 시 읽기 전용). `owner_part = NULL` (공통 namespace)는 모든 사용자 CRUD 가능.
- 대화 소유권: `ops_conversation.user_id` FK로 사용자별 대화 격리.

**사용자별 LLM API Key:**
- 회원가입 또는 마이페이지에서 사내 LLM API Key 등록 (선택사항)
- Fernet 대칭 암호화로 DB에 저장 → 요청 시 복호화하여 InHouse LLM Provider에 전달
- 개인 키가 없으면 시스템 기본 키(`INHOUSE_LLM_API_KEY`) 사용

### 4. PostgreSQL + pgvector (`:5432`)

```sql
-- 플랫폼 공통 (ops_* prefix)
ops_part              -- 부서 레지스트리
ops_user              -- 사용자 (role, part_id FK, encrypted_api_key)
ops_namespace         -- 네임스페이스 (owner_part_id FK, created_by_user_id)
ops_conversation      -- 대화방 (namespace_id FK, user_id FK, agent_type)
ops_message           -- 대화 메시지 (role, content, results JSONB, metadata JSONB)
ops_feedback          -- 👍/👎 피드백 로그 (agent_type, meta JSONB)
ops_query_log         -- 질의 로그 (status: pending/resolved/unresolved, agent_type)
ops_mcp_tool          -- MCP 도구 정의 (hub_base_url, tool_path, param_schema JSONB, agent_type)
ops_mcp_tool_log      -- MCP 도구 감사 로그
ops_prompt            -- 프롬프트 관리 (agent_type별 에이전트 스코핑, Admin 시스템설정 탭에서 편집)
ops_system_config     -- 시스템 설정 key-value (캐시 임계값/TTL 등 영속화)

-- KnowledgeRAG 전용 (rag_* prefix, v2.8에서 ops_*→rag_* 변경)
rag_knowledge         -- 지식 베이스 (HNSW + GIN FTS, base_weight, namespace_id FK)
rag_knowledge_category -- 카테고리 목록
rag_glossary          -- 용어집 (HNSW, 유사도 0.5+ 매핑)
rag_fewshot           -- Few-shot Q&A (HNSW, status: active/candidate)
rag_conv_summary      -- 대화 요약 (embedding VECTOR(768), Semantic Recall용)

-- Text-to-SQL 전용 (sql_* prefix)
sql_target_db         -- 대상 DB 연결 설정 (암호화 저장)
sql_schema_table      -- 테이블 메타데이터 (pos_x/pos_y ERD 위치)
sql_schema_column     -- 컬럼 메타데이터 (is_pk, fk_reference)
sql_relation          -- FK 관계 정의
sql_synonym           -- 자연어 → SQL 표현 매핑 (embedding VECTOR(768))
sql_fewshot           -- Q&A Few-shot (embedding VECTOR(768), status: pending/approved/rejected)
sql_pipeline_stage    -- 파이프라인 단계 설정 (is_enabled/order_num 등 메타. 프롬프트는 ops_prompt sql2_* 키 사용)
sql_audit_log         -- 쿼리 실행 감사 로그
sql_cache             -- 쿼리 결과 캐시
sql_schema_vector     -- 스키마 벡터 인덱스
```

- **HNSW 인덱스** (`vector_cosine_ops`): 벡터 근사 최근접 이웃 검색
- **GIN 인덱스** (`to_tsvector('simple', content)`): 전문 검색(FTS)
- **pg_trgm**: 트리그램 유사도 지원 (활성화됨)
- **namespace_id integer FK**: 모든 테이블에서 도메인 격리. namespace 이름 변경 시 cascade 업데이트 불필요
- **CASCADE 삭제**: `ops_conversation.user_id` → 사용자 삭제 시 대화 자동 삭제

### 5. Ollama — LLM 추론 (`:11434`)

- 호스트 머신에서 직접 실행 (컨테이너 외부)
- 모델: `exaone3.5:2.4b`
- Backend에서 `host.docker.internal:11434`로 접근
- **`/api/chat` 엔드포인트** 사용 (GPT 방식 messages 배열, multi-turn 지원)

---

## 임베딩 모델

| 항목 | 값 |
|------|-----|
| 모델명 | `paraphrase-multilingual-mpnet-base-v2` |
| 벡터 차원 | 768 |
| 한국어 지원 | O |
| 실행 위치 | Backend 컨테이너 내 (CPU) |
| 캐시 볼륨 | `model-cache:/root/.cache/huggingface` |

- Docker 빌드 시 이미지에 모델 사전 다운로드 (컨테이너 시작 지연 없음)
- `normalize_embeddings=True` 적용 → 코사인 유사도 = 내적

---

## API 엔드포인트 목록

### 인증 (`/api/auth`) — v2.0.0 신규

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| `POST` | `/api/auth/register` | 없음 | 회원가입 (부서 선택 + 선택적 LLM API Key) |
| `POST` | `/api/auth/login` | 없음 | 로그인 → Access Token(30min) + Refresh Token(7days) 발급 |
| `POST` | `/api/auth/refresh` | Refresh Token | Access Token 갱신 |
| `GET` | `/api/auth/me` | Bearer | 내 정보 조회 |
| `PUT` | `/api/auth/me/password` | Bearer | 비밀번호 변경 |
| `PUT` | `/api/auth/me/api-key` | Bearer | 개인 LLM API Key 등록/변경 (Fernet 암호화 저장) |
| `GET` | `/api/auth/users` | Admin | 전체 사용자 목록 |
| `PUT` | `/api/auth/users/{id}` | Admin | 사용자 정보 수정 (역할 변경 등) |
| `DELETE` | `/api/auth/users/{id}` | Admin | 사용자 삭제 |
| `GET` | `/api/auth/parts` | 없음 | 부서 목록 조회 (회원가입용 — 슈퍼어드민 파트 자동 제외) |
| `GET` | `/api/auth/parts/all` | Admin | 부서 목록 전체 조회 (관리자용 — 슈퍼어드민 파트 포함) |
| `POST` | `/api/auth/parts` | Admin | 부서 생성 |
| `PATCH` | `/api/auth/parts/{id}` | Admin | 부서 이름 변경 (name 컬럼만 업데이트 — integer FK로 cascade 불필요) |
| `DELETE` | `/api/auth/parts/{id}` | Admin | 부서 삭제 (소속 사용자 없는 경우만) |

### 채팅/대화

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/health` | 서버·LLM 상태 확인 |
| `POST` | `/api/chat` | 하이브리드 검색 + LLM 답변 (JSON) |
| `POST` | `/api/chat/stream` | 하이브리드 검색 + LLM 답변 (SSE 스트리밍, 단계별 status 이벤트) |
| `POST` | `/api/chat/debug` | LLM 없이 검색 파이프라인 전 과정 반환 (v_score, k_score, 용어집 유사도, few-shot 목록, LLM 컨텍스트 미리보기 포함) |
| `GET` | `/api/conversations` | 네임스페이스별 대화방 목록 (최근 50개, 본인 소유만) |
| `POST` | `/api/conversations` | 대화방 신규 생성 (user_id 자동 연결) |
| `GET` | `/api/conversations/{id}/messages` | 대화방 전체 메시지 조회 (status 필드 포함) |
| `DELETE` | `/api/conversations/{id}` | 대화방 삭제 (메시지 cascade) |
| `PATCH` | `/api/chat/messages/{id}/content` | 메시지 부분 저장 (프론트엔드 스트림 중단 시) |
| `DELETE` | `/api/chat/messages/{id}` | Ghost 메시지 삭제 (빈 assistant + 짝 user + 빈 대화방) |

### 지식/용어집

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/knowledge` | 지식 목록 조회 (namespace 필터) |
| `POST` | `/api/knowledge` | 지식 신규 등록 (네임스페이스 소유 파트 검증, 임베딩 자동 생성) |
| `PUT` | `/api/knowledge/{id}` | 지식 수정 (네임스페이스 소유 파트 또는 admin만) |
| `DELETE` | `/api/knowledge/{id}` | 지식 삭제 (네임스페이스 소유 파트 또는 admin만) |
| `GET` | `/api/knowledge/glossary` | 용어집 목록 |
| `POST` | `/api/knowledge/glossary` | 용어 신규 등록 (임베딩 자동 생성) |
| `PUT` | `/api/knowledge/glossary/{id}` | 용어 수정 (재임베딩 자동, 같은 부서 또는 admin만) |
| `DELETE` | `/api/knowledge/glossary/{id}` | 용어 삭제 (같은 부서 또는 admin만) |

### 피드백/Few-shot

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/feedback` | 피드백 기록 + base_weight 조정 + few-shot 저장(👍시) |
| `GET` | `/api/fewshots` | Few-shot 목록 조회 (namespace 필터) |
| `POST` | `/api/fewshots` | Few-shot 신규 등록 (임베딩 자동 생성) |
| `PUT` | `/api/fewshots/{id}` | Few-shot 수정 (질문 변경 시 재임베딩, 같은 부서 또는 admin만) |
| `DELETE` | `/api/fewshots/{id}` | Few-shot 삭제 (같은 부서 또는 admin만) |
| `POST` | `/api/fewshots/search` | 질문으로 few-shot 검색 테스트 (실제 검색 결과 + 프롬프트 섹션 미리보기) |
| `PATCH` | `/api/fewshots/{id}/status` | Few-shot 상태 전환 (`active` ↔ `candidate`) |

### 관리/설정

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/namespaces` | 등록된 네임스페이스 목록 (문자열 배열) |
| `GET` | `/api/namespaces/detail` | 네임스페이스 상세 목록 (지식 수, 용어집 수 포함) |
| `POST` | `/api/namespaces` | 네임스페이스 신규 생성 (admin이 생성하면 owner_part=NULL) |
| `PATCH` | `/api/namespaces/{name}` | 네임스페이스 이름 변경 (name 컬럼만 업데이트 — integer FK로 cascade 불필요) |
| `DELETE` | `/api/namespaces/{name}` | 네임스페이스 및 하위 데이터 전체 삭제 |
| `GET` | `/api/llm/config` | 현재 LLM 프로바이더 설정 + 연결 상태 조회 |
| `PUT` | `/api/llm/config` | LLM 프로바이더 런타임 전환 — Admin은 전체 시스템 저장, 일반 사용자는 브라우저 localStorage에만 저장 |
| `POST` | `/api/llm/test` | 설정값으로 연결 테스트 (실제 전환 없음) |
| `GET` | `/api/stats` | 네임스페이스별 통계 (전체 namespace, 지식/용어집 개수 포함) |
| `GET` | `/api/stats/namespace/{name}` | 네임스페이스 상세 통계 (업무 유형별 분포, 미해결 목록) |
| `DELETE` | `/api/stats/query-log/{id}` | 미해결 질의 로그 삭제 (지식 등록 후 처리 완료 표시) |
| `GET` | `/api/admin/cache/stats` | 네임스페이스 Semantic Cache 통계 (total_entries, total_hits, connected) |
| `GET` | `/api/admin/cache/entries` | 캐시 엔트리 목록 (히트 수 내림차순, 질문·TTL·hits 포함) |
| `DELETE` | `/api/admin/cache` | 네임스페이스 캐시 전체 무효화 |
| `DELETE` | `/api/admin/cache/entry` | 단일 캐시 엔트리 삭제 |
| `POST` | `/api/admin/glossary/suggest` | 미매핑 질문 LLM 분석 → 용어 후보 반환 (`limit` 파라미터로 조회 건수 설정, 기본 50, 최대 200) |
| `POST` | `/api/admin/glossary/suggest/apply` | 추천 용어 1-click 등록 (임베딩 자동 생성) |

### Text-to-SQL (`/api/text2sql`) — v2.5 신규, v2.6 확장

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET/PUT` | `/api/text2sql/namespaces/{ns}/target-db` | 대상 DB 연결 설정 조회/저장 |
| `POST` | `/api/text2sql/namespaces/{ns}/target-db/test` | 연결 테스트 |
| `POST` | `/api/text2sql/namespaces/{ns}/target-db/scan` | 스키마 자동 스캔 (테이블·컬럼 수집) |
| `GET` | `/api/text2sql/namespaces/{ns}/schema` | 전체 스키마 (테이블+컬럼) 조회 |
| `PUT` | `/api/text2sql/namespaces/{ns}/schema/tables/{id}` | 테이블 설명 수정 |
| `PUT` | `/api/text2sql/namespaces/{ns}/schema/tables/{id}/toggle` | 테이블 RAG 포함 여부 토글 |
| `PUT` | `/api/text2sql/namespaces/{ns}/schema/columns/{id}` | 컬럼 설명 수정 |
| `POST` | `/api/text2sql/namespaces/{ns}/schema/reindex` | 스키마 벡터 재인덱싱 |
| `PUT` | `/api/text2sql/namespaces/{ns}/schema/positions` | ERD 테이블 위치 일괄 저장 (pos_x/pos_y) — v2.6 신규 |
| `GET/POST/DELETE` | `/api/text2sql/namespaces/{ns}/relations/{id?}` | FK 관계 CRUD |
| `POST` | `/api/text2sql/namespaces/{ns}/relations/suggest-ai` | AI 관계 추천 (LLM이 컬럼명 패턴 분석) — v2.6 신규 |
| `GET/POST/DELETE` | `/api/text2sql/namespaces/{ns}/synonyms/{id?}` | 용어사전 CRUD |
| `POST` | `/api/text2sql/namespaces/{ns}/synonyms/reindex` | 용어사전 벡터 재인덱싱 |
| `POST` | `/api/text2sql/namespaces/{ns}/synonyms/generate-ai` | AI 용어 자동생성 (30+ 항목, SQL 키워드 필터) — v2.6 신규 |
| `GET/POST/DELETE` | `/api/text2sql/namespaces/{ns}/fewshots/{id?}` | 예제 Q&A CRUD |
| `POST` | `/api/text2sql/namespaces/{ns}/fewshots/reindex` | 예제 벡터 재인덱싱 |
| `POST` | `/api/text2sql/namespaces/{ns}/fewshots/generate-ai` | AI 예제 자동생성 (20+ QA 쌍) — v2.6 신규 |
| `GET` | `/api/text2sql/pipeline` | 파이프라인 단계 목록 |
| `PUT` | `/api/text2sql/pipeline/{id}/toggle` | 단계 활성/비활성 |
| `GET` | `/api/text2sql/namespaces/{ns}/audit-logs` | 쿼리 감사 로그 (페이지네이션) |
| `GET/DELETE` | `/api/text2sql/namespaces/{ns}/cache/{id?}` | 쿼리 결과 캐시 조회/삭제 |

### MCP 도구

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/mcp-tools` | 네임스페이스 MCP 도구 목록 |
| `POST` | `/api/mcp-tools` | 도구 등록 |
| `PATCH` | `/api/mcp-tools/{id}` | 도구 수정 |
| `PATCH` | `/api/mcp-tools/{id}/toggle` | 도구 활성/비활성 토글 |
| `DELETE` | `/api/mcp-tools/{id}` | 도구 삭제 |
| `POST` | `/api/mcp-tools/{id}/test` | 도구 테스트 실행 |
| `POST` | `/api/mcp-tools/autocomplete` | 자연어 입력 → 도구 JSON 자동완성 |
| `GET` | `/api/mcp-tools/logs` | 도구 호출 감사 로그 조회 |

---

## LLM Provider 확장 구조

```python
# domain/llm/base.py
def build_messages(context, question, history=None) -> list[dict]:
    # [system: 시스템프롬프트+참고문서] + [history...] + [user: 질문]

class LLMProvider(ABC):
    async def generate(context, question, history=None, api_key=None) -> str: ...
    async def generate_stream(context, question, history=None, api_key=None) -> AsyncIterator[str]: ...
    async def health_check() -> bool: ...

# 현재 구현체
OllamaProvider     # LLM_PROVIDER=ollama — /api/chat (messages 배열, multi-turn)
InHouseLLMProvider # LLM_PROVIDER=inhouse — DevX MCP API (usecase_code, query, response_mode)
                   #   inputs.model로 모델 선택 (GPT 5.2 / Claude Sonnet 4.5 / Gemini 3.0 Pro)
                   #   SSE: data JSON 안에 event 필드 포함하는 비표준 형식 대응
                   #   api_key: per-user 키 우선, 없으면 시스템 기본 키 사용
```

**`api_key` 파라미터 (v2.0.0 신규):**
- `generate()`, `generate_stream()`에 선택적 `api_key` 파라미터 추가
- 사용자가 개인 LLM API Key를 등록한 경우, Fernet 복호화 후 이 파라미터로 전달
- 개인 키가 없으면 `None` → Provider가 시스템 기본 키(`INHOUSE_LLM_API_KEY`) 사용
- OllamaProvider는 `api_key` 무시 (로컬 모델이므로 불필요)

**대화 맥락 전달 방식 (ConversationSummaryBuffer + Semantic Recall):**
```
messages = [
  {"role": "system",    "content": "시스템 프롬프트\n\n[참고 문서]\n{검색 결과}"},
  {"role": "system",    "content": "이 대화의 관련 과거 맥락:\n[과거 맥락 1]\n{요약}"},  ← Semantic Recall (유사도 0.45 이상)
  {"role": "user",      "content": "이전 질문"},   ← 최근 2회 raw 교환
  {"role": "assistant", "content": "이전 답변"},
  {"role": "user",      "content": "현재 질문"},
]
```

**메모리 동작 원리:**
- 4회 교환마다 오래된 대화를 LLM으로 요약 → `ops_conv_summary`에 임베딩 저장 (백그라운드)
- 새 질문 시 현재 질문 벡터로 과거 요약 검색 → 유사도 0.45 이상 최대 2개 추출
- 최근 2회 raw 교환은 항상 포함 (working memory)

**런타임 전환 (재시작 없음):**
- Admin → LLM 설정 탭에서 프로바이더 선택/설정 후 "저장 및 적용"
- `switch_provider(config)` 호출 → 싱글톤 교체, `_runtime_config` 전역 저장
- 컨테이너 재시작 시 `.env` 설정으로 복귀
- `get_runtime_config()`: 런타임 override 여부(`is_runtime_override`), 연결 상태(`is_connected`) 포함 반환

새 LLM 추가 시: `LLMProvider` 상속 → 3개 메서드 구현 → `service/llm/factory.py`에 등록

---

## 환경변수 설정

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DATABASE_URL` | `postgresql://ops:ops1234@postgres:5432/opsdb` | DB 연결 문자열 |
| `LLM_PROVIDER` | `inhouse` | `ollama` 또는 `inhouse` |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama 서버 주소 |
| `OLLAMA_MODEL` | `exaone3.5:7.8b` | 사용할 Ollama 모델명 |
| `OLLAMA_TIMEOUT` | `900` | CPU 추론 최대 대기 시간(초), httpx read timeout에 적용 |
| `INHOUSE_LLM_URL` | (없음) | DevX MCP API 엔드포인트 URL |
| `INHOUSE_LLM_API_KEY` | (없음) | 사내 LLM 시스템 기본 API 키 (Bearer 토큰) |
| `INHOUSE_LLM_MODEL` | (없음) | inputs.model 파라미터 (gpt-5.2, claude-sonnet-4.5, gemini-3.0-pro) |
| `INHOUSE_LLM_AGENT_CODE` | `playground` | DevX usecase_code |
| `INHOUSE_LLM_RESPONSE_MODE` | `streaming` | 응답 방식 (`streaming` \| `blocking`) |
| `EMBEDDING_MODEL` | `paraphrase-multilingual-mpnet-base-v2` | 임베딩 모델명 |
| `VECTOR_DIM` | `768` | 벡터 차원 수 |
| `DEFAULT_TOP_K` | `5` | 기본 검색 결과 수 |
| `DEFAULT_W_VECTOR` | `0.7` | 기본 벡터 검색 비중 |
| `DEFAULT_W_KEYWORD` | `0.3` | 기본 키워드 검색 비중 |
| `BACKEND_URL` | `http://backend:8000` | Frontend → Backend 주소 |
| `JWT_SECRET_KEY` | (필수) | JWT 서명 비밀 키 (HS256) |
| `FERNET_SECRET_KEY` | (필수) | Fernet 대칭 암호화 키 (사용자 API Key 암호화용) |
| `ADMIN_DEFAULT_PASSWORD` | (필수) | 초기 admin 계정 비밀번호 |

---

## pgvector 데이터 구성

### 벡터가 쓰이는 테이블

| 테이블 | 벡터 컬럼 | 용도 |
|--------|----------|------|
| `rag_knowledge` | `embedding VECTOR(768)` | 문서 내용 임베딩 → 질문과 코사인 유사도로 관련 문서 검색 |
| `rag_glossary` | `embedding VECTOR(768)` | 용어 설명 임베딩 → 질문과 비교해 표준 용어 자동 매핑 (유사도 0.5 이상만 사용) |
| `rag_fewshot` | `embedding VECTOR(768)` | 과거 질문 임베딩 → 유사 Q&A를 LLM 프롬프트에 few-shot 삽입 (유사도 0.6 이상) |
| `rag_conv_summary` | `embedding VECTOR(768)` | 과거 대화 요약 임베딩 → 현재 질문과 유사한 과거 맥락 Semantic Recall (유사도 0.45 이상) |

### 검색 점수 공식

```
final_score = (w_vec × v_score + w_kw × k_score) × (1 + base_weight)
               └벡터 유사도    └BM25 키워드 점수    └문서 자체 가중치
```

- **v_score**: 코사인 유사도 (0~1) — HNSW 인덱스로 근사 탐색
- **k_score**: `ts_rank` BM25 점수 — GIN 인덱스로 전문 검색
- **base_weight**: `ops_knowledge` 행(문서)에 직접 붙는 가중치. 👍 피드백 시 +0.1, 👎 시 -0.1 자동 조정

### 비벡터 주요 테이블

`ops_part`, `ops_user`, `ops_namespace`, `rag_knowledge_category`, `ops_feedback`, `ops_query_log`, `ops_conversation`, `ops_message`, `ops_mcp_tool`, `ops_mcp_tool_log`, `ops_prompt`, `ops_system_config`

전체 스키마 정의는 `docs/table-definition.md` 참조.
