# Ops-Navigator API 명세서

> **Version**: 2.13
> **Base URL**: `http://localhost:8000`
> **Protocol**: REST + SSE (Server-Sent Events)
> **Content-Type**: `application/json` (기본), `text/event-stream` (SSE)
> **최종 갱신**: 2026-04-17

---

## 목차

1. [시스템 상태](#1-시스템-상태)
2. [인증 (Auth)](#2-인증-auth)
3. [인증 체계](#3-인증-체계)
4. [채팅 (Chat)](#4-채팅-chat)
5. [대화 관리 (Conversations)](#5-대화-관리-conversations)
6. [지식 베이스 (Knowledge)](#6-지식-베이스-knowledge)
   - [벌크 등록 / 인제스천 (CSV·텍스트·파일·URL)](#post-apiknowledgebulk)
7. [용어집 (Glossary)](#7-용어집-glossary)
8. [Few-Shot 예제](#8-few-shot-예제)
9. [피드백 (Feedback)](#9-피드백-feedback)
10. [통계 및 질의 로그 (Stats)](#10-통계-및-질의-로그-stats)
11. [네임스페이스 (Namespaces)](#11-네임스페이스-namespaces)
12. [LLM 설정 (LLM Settings)](#12-llm-설정-llm-settings)
13. [공통 에러 코드](#13-공통-에러-코드)

---

## 1. 시스템 상태

### GET /health

시스템 헬스체크. LLM 프로바이더 연결 상태를 포함한다.

> 인증 불필요 (공개 엔드포인트)

**Response** `200 OK`

```json
{
  "status": "ok",
  "llm_provider": "ollama",
  "llm": "connected"
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `status` | string | 항상 `"ok"` |
| `llm_provider` | string | 현재 LLM 프로바이더 (`ollama` \| `inhouse`) |
| `llm` | string | LLM 연결 상태 (`connected` \| `unavailable`) |

---

## 2. 인증 (Auth)

### POST /api/auth/register

회원가입. 새 사용자를 등록한다.

> 인증 불필요 (공개 엔드포인트)

**Request Body**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `username` | string | O | 사용자명 |
| `password` | string | O | 비밀번호 |
| `part` | string | O | 소속 파트 |
| `llm_api_key` | string | X | 개인 LLM API 키 |

**Response** `201 Created` — `UserOut`

```json
{
  "id": 1,
  "username": "hong",
  "role": "user",
  "part": "쿠폰파트",
  "is_active": true,
  "has_llm_credentials": false,
  "has_confluence_pat": false,
  "created_at": "2026-03-08T10:00:00+09:00"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `has_llm_credentials` | bool | 본인 LLM OAuth2 자격증명 등록 여부 (v2.17~, false면 .env 팀 공통 키 사용) |
| `has_confluence_pat` | bool | Confluence PAT 등록 여부 |

---

### POST /api/auth/login

로그인. Access Token과 Refresh Token을 발급한다.

> 인증 불필요 (공개 엔드포인트)

**Request Body**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `username` | string | O | 사용자명 |
| `password` | string | O | 비밀번호 |

**Response** `200 OK`

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "user": {
    "id": 1,
    "username": "hong",
    "role": "user",
    "part": "쿠폰파트",
    "is_active": true,
    "created_at": "2026-03-08T10:00:00+09:00"
  }
}
```

**실패 응답**
- `401` — 아이디 또는 비밀번호가 올바르지 않음(계정 열거 방지를 위해 두 경우 메시지 동일, v2.39), 또는 비활성화된 계정
- `429` — 동일 사용자명 또는 동일 클라이언트 IP로 5분 내 5회 로그인 실패 시 5분간 잠금(v2.39, Redis 기반 — Redis 미연결 시 제한 없이 통과)

---

### POST /api/auth/refresh

토큰 갱신. Refresh Token으로 새 Access Token을 발급한다.

> 인증 불필요 (공개 엔드포인트)

**Request Body**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `refresh_token` | string | O | 리프레시 토큰 |

**Response** `200 OK`

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs..."
}
```

---

### GET /api/auth/parts

파트(부서) 목록을 반환한다.

> 인증 불필요 (공개 엔드포인트)

**Response** `200 OK` — `Part[]`

```json
[
  { "id": 1, "name": "쿠폰파트" },
  { "id": 2, "name": "결제파트" }
]
```

---

### GET /api/auth/me

현재 로그인한 사용자의 정보를 반환한다.

> **인증 필요**: `Authorization: Bearer {access_token}`

**Response** `200 OK` — `UserOut`

```json
{
  "id": 1,
  "username": "hong",
  "role": "user",
  "part": "쿠폰파트",
  "is_active": true,
  "created_at": "2026-03-08T10:00:00+09:00"
}
```

---

### PUT /api/auth/me/password

현재 사용자의 비밀번호를 변경한다.

> **인증 필요**: `Authorization: Bearer {access_token}`

**Request Body**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `current_password` | string | O | 현재 비밀번호 |
| `new_password` | string | O | 새 비밀번호 |

**Response** `200 OK`

```json
{ "ok": true }
```

---

### PUT /api/auth/me/llm-credentials

현재 사용자의 LLM OAuth2 자격증명 트리플을 등록/변경한다 (v2.17~).

> **인증 필요**: `Authorization: Bearer {access_token}`

**Request Body**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `credentials.client_id` | string | O | DevX OAuth2 client_id |
| `credentials.client_secret` | string | O | DevX OAuth2 client_secret |
| `credentials.user_id` | string | O | DevX 시스템 사용자 식별자 |

저장 시 JSON 직렬화 → Fernet 암호화 → `ops_user.encrypted_llm_credentials`. 미등록 시 LLM 호출은 `.env` 팀 공통 자격증명 fallback.

**Response** `200 OK`

```json
{ "status": "ok" }
```

---

### DELETE /api/auth/me/llm-credentials

현재 사용자의 LLM 자격증명을 삭제한다. 이후 LLM 호출은 팀 공통 자격증명으로 동작.

> **인증 필요**: `Authorization: Bearer {access_token}`

**Response** `204 No Content`

---

### GET /api/auth/users

전체 사용자 목록을 반환한다.

> **인증 필요**: `Authorization: Bearer {access_token}`
> **권한**: admin 전용

**Response** `200 OK` — `UserOut[]`

```json
[
  {
    "id": 1,
    "username": "hong",
    "role": "admin",
    "part": "쿠폰파트",
    "is_active": true,
    "created_at": "2026-03-08T10:00:00+09:00"
  }
]
```

---

### PUT /api/auth/users/{id}

사용자 정보를 수정한다.

> **인증 필요**: `Authorization: Bearer {access_token}`
> **권한**: admin 전용

**Path Parameter**: `id` (int) — 사용자 ID

**Request Body**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `role` | string | X | 역할 (`user` \| `admin`) |
| `part` | string | X | 소속 파트 |
| `is_active` | bool | X | 활성 상태 |

**Response** `200 OK` — `UserOut`

---

### DELETE /api/auth/users/{id}

사용자를 삭제한다. 자기 자신은 삭제할 수 없다.

> **인증 필요**: `Authorization: Bearer {access_token}`
> **권한**: admin 전용

**Path Parameter**: `id` (int) — 사용자 ID

**Response** `204 No Content`

**Error** `400 Bad Request` (자기 자신 삭제 시도), `404 Not Found`

---

### POST /api/auth/parts

파트를 생성한다.

> **인증 필요**: `Authorization: Bearer {access_token}`
> **권한**: admin 전용

**Request Body**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `name` | string | O | 파트 이름 |

**Response** `201 Created` — `Part`

---

### DELETE /api/auth/parts/{id}

파트를 삭제한다. 해당 파트에 소속된 사용자가 존재하면 실패한다.

> **인증 필요**: `Authorization: Bearer {access_token}`
> **권한**: admin 전용

**Path Parameter**: `id` (int) — 파트 ID

**Response** `204 No Content`

**Error** `400 Bad Request` (소속 사용자 존재), `404 Not Found`

---

## 3. 인증 체계

### JWT Bearer Token 인증

Ops-Navigator는 JWT(JSON Web Token) 기반 Bearer 인증을 사용한다.

**인증 헤더 형식**:

```
Authorization: Bearer {access_token}
```

### 토큰 유효기간

| 토큰 | 유효기간 |
|------|----------|
| Access Token | 30분 |
| Refresh Token | 7일 |

### 공개 엔드포인트 (인증 불필요)

다음 엔드포인트는 인증 없이 접근 가능하다:

- `GET /health`
- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/refresh`
- `GET /api/auth/parts`

### 인증 필요 엔드포인트

위 공개 엔드포인트를 제외한 모든 API는 `Authorization: Bearer {access_token}` 헤더가 필요하다.

### Admin 전용 엔드포인트

다음 작업은 `role: admin` 사용자만 수행할 수 있다:

- **LLM 설정**: 설정 변경(PUT /api/llm/config), 연결 테스트(POST /api/llm/test, v2.38~), 임계값 변경(PUT /api/llm/thresholds)
- **사용자 관리**: 목록 조회, 수정, 삭제 (GET/PUT/DELETE /api/auth/users)

### 네임스페이스 소유 파트 기반 권한

네임스페이스 생성 시 생성자의 파트가 `owner_part`로 기록된다. 이후 해당 네임스페이스의 데이터(지식/용어/퓨샷) 생성·수정·삭제는 `owner_part`와 동일한 파트의 사용자만 가능하다. Admin은 모든 네임스페이스에 대해 무조건 통과한다.

- **owner_part가 NULL인 경우 (공통 namespace)**: **모든 인증 사용자**가 CRUD 가능 (admin이 생성한 namespace는 자동으로 owner_part=NULL)
- **네임스페이스**: 생성(POST) — 모든 인증 사용자. 삭제(DELETE) — 소유 파트 또는 admin만
- **지식/용어/퓨샷 CRUD**: 네임스페이스의 `owner_part`와 요청자의 파트 일치 필요
- **질의 로그 승인/삭제**: 해당 질의 로그의 네임스페이스 `owner_part`와 요청자의 파트 일치 필요
- **수정 시 작성자 갱신**: 수정한 사용자의 파트/ID가 `created_by_part`/`created_by_user_id`로 갱신됨

### 인증 에러

| HTTP 상태 코드 | 의미 | 설명 |
|----------------|------|------|
| `401` | Unauthorized | 토큰 없음, 만료, 또는 유효하지 않은 토큰 |
| `403` | Forbidden | 권한 부족 (admin 전용 또는 네임스페이스 소유 파트 불일치) |

---

## 4. 채팅 (Chat)

> 모든 채팅 엔드포인트는 `Authorization: Bearer {access_token}` 헤더가 필요하다.

### POST /api/chat

동기식 단건 채팅 응답. 검색 결과와 LLM 답변을 한 번에 반환한다.

**Request Body** — `ChatRequest`

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `namespace` | string | O | - | 업무 도메인 |
| `question` | string | O | - | 사용자 질문 |
| `w_vector` | float | X | 0.7 | 벡터 검색 가중치 (0.0~1.0) |
| `w_keyword` | float | X | 0.3 | 키워드 검색 가중치 (0.0~1.0) |
| `top_k` | int | X | 5 | 검색 결과 최대 개수 (1~20) |
| `conversation_id` | int | X | null | 기존 대화 연결 시 대화 ID |
| `categories` | string[] | X | null | 업무구분 다중 필터 — 지정 시 해당 카테고리들의 지식만 검색 (`k.category = ANY(...)`), 비우면 전체 검색 (v2.30~) |

**Response** `200 OK` — `ChatResponse`

```json
{
  "conversation_id": 1,
  "question": "쿠폰 발급 절차",
  "mapped_term": "쿠폰발급",
  "results": [
    {
      "id": 10,
      "container_name": "coupon-api",
      "target_tables": ["tb_coupon", "tb_coupon_hist"],
      "content": "쿠폰 발급 처리 절차...",
      "query_template": "SELECT * FROM tb_coupon WHERE ...",
      "final_score": 0.85
    }
  ],
  "answer": "쿠폰 발급 절차는 다음과 같습니다..."
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `conversation_id` | int | 대화 ID (신규 생성 또는 기존) |
| `question` | string | 원본 질문 |
| `mapped_term` | string \| null | 용어집 매핑 결과 |
| `results` | KnowledgeResult[] | 검색 결과 목록 |
| `answer` | string | LLM 생성 답변 |

---

### POST /api/chat/stream

SSE 스트리밍 채팅. 실시간 상태 업데이트와 토큰 단위 LLM 출력을 제공한다.

**Request Body** — `ChatRequest` (POST /api/chat과 동일)

**Response** `200 OK` — `text/event-stream`

**Headers**: `Cache-Control: no-cache`, `X-Accel-Buffering: no`

**SSE 이벤트 순서**:

| 순서 | type | 설명 | 데이터 예시 |
|------|------|------|------------|
| 1 | `meta` | 초기 메타 (대화ID, 검색결과) | `{"type":"meta","conversation_id":1,"message_id":5,"mapped_term":"쿠폰","results":[...]}` |
| 2~N | `status` | 파이프라인 진행 상태 | `{"type":"status","step":"glossary","message":"용어 매핑 중..."}` |
| N+1~ | `token` | LLM 토큰 스트림 | `{"type":"token","data":"쿠폰"}` |
| 마지막 | `done` | 완료 신호 | `{"type":"done","message_id":5}` |

---

### PATCH /api/chat/messages/{msg_id}/content

어시스턴트 메시지 내용을 업데이트한다. 스트림 중단 시 부분 저장에 사용된다.

**Path Parameter**: `msg_id` (int) — 메시지 ID

**Request Body**

```json
{ "content": "부분 저장된 답변 내용..." }
```

**Response** `200 OK`

```json
{ "status": "ok" }
```

---

### DELETE /api/chat/messages/{msg_id}

어시스턴트 메시지와 쌍을 이루는 사용자 메시지를 함께 삭제한다.

**Path Parameter**: `msg_id` (int) — 메시지 ID

**Response** `200 OK`

```json
{ "status": "ok" }
```

---

### POST /api/chat/debug

디버그용 검색 파이프라인 미리보기. 점수 산출 과정을 상세 반환한다.

**Request Body** — `ChatRequest` (POST /api/chat과 동일)

**Response** `200 OK` — `DebugSearchResponse`

```json
{
  "question": "쿠폰 발급",
  "namespace": "coupon",
  "enriched_query": "쿠폰발급 쿠폰 발급",
  "glossary_match": {
    "term": "쿠폰발급",
    "description": "쿠폰 발급 처리 프로세스",
    "similarity": 0.82
  },
  "w_vector": 0.7,
  "w_keyword": 0.3,
  "fewshots": [
    { "question": "쿠폰 발급 방법", "answer": "...", "similarity": 0.78 }
  ],
  "results": [
    {
      "id": 10,
      "container_name": "coupon-api",
      "target_tables": ["tb_coupon"],
      "content": "...",
      "query_template": "SELECT ...",
      "base_weight": 1.0,
      "v_score": 0.82,
      "k_score": 0.45,
      "final_score": 0.85
    }
  ],
  "context_preview": "[시스템 프롬프트 미리보기...]"
}
```

---

## 5. 대화 관리 (Conversations)

> 모든 대화 엔드포인트는 `Authorization: Bearer {access_token}` 헤더가 필요하다.
> 일반 사용자는 자신의 대화만 조회/관리할 수 있다. Admin은 전체 사용자의 대화를 조회할 수 있다.

### GET /api/conversations

네임스페이스의 대화 목록을 반환한다. 최근 50개까지.

**Query Parameters**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `namespace` | string | O | 네임스페이스 |

**Response** `200 OK` — `ConversationResponse[]`

```json
[
  {
    "id": 1,
    "namespace": "coupon",
    "title": "쿠폰 발급 절차 문의",
    "trimmed": false,
    "created_at": "2026-03-07T10:00:00+09:00"
  }
]
```

---

### POST /api/conversations

새 대화를 생성한다.

**Request Body** — `ConversationCreate`

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `namespace` | string | O | - | 네임스페이스 |
| `title` | string | X | `""` | 대화 제목 |

**Response** `201 Created` — `ConversationResponse`

---

### GET /api/conversations/{conv_id}/messages

대화의 전체 메시지 목록을 반환한다.

**Path Parameter**: `conv_id` (int) — 대화 ID

**Response** `200 OK` — `MessageResponse[]`

```json
[
  {
    "id": 5,
    "conversation_id": 1,
    "role": "user",
    "content": "쿠폰 발급 절차가 어떻게 되나요?",
    "mapped_term": null,
    "results": null,
    "status": "completed",
    "has_feedback": false,
    "created_at": "2026-03-07T10:00:00+09:00"
  },
  {
    "id": 6,
    "conversation_id": 1,
    "role": "assistant",
    "content": "쿠폰 발급 절차는...",
    "mapped_term": "쿠폰발급",
    "results": [...],
    "status": "completed",
    "has_feedback": true,
    "created_at": "2026-03-07T10:00:01+09:00"
  }
]
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `status` | string | `generating` (생성 중) \| `completed` (완료) |
| `has_feedback` | bool | 피드백 존재 여부 |

**Error** `404 Not Found` — 대화가 존재하지 않을 때

---

### DELETE /api/conversations/{conv_id}

대화와 관련 메시지를 모두 삭제한다 (CASCADE).

**Path Parameter**: `conv_id` (int) — 대화 ID

**Response** `204 No Content`

**Error** `404 Not Found`

---

## 6. 지식 베이스 (Knowledge)

> 모든 지식 베이스 엔드포인트는 `Authorization: Bearer {access_token}` 헤더가 필요하다.

> **벌크 등록은 백그라운드로 처리된다 (v2.29~)**: `/bulk`, `/import/csv`, `/import/text-split`,
> `/import/file`, `/import/url`, `/import/url/bulk-pages`, `/api/knowledge/import/teams` 는
> 요청 즉시 `{"created": 0, "job_id": N, "status": "processing"}`를 반환하고, 실제 임베딩·INSERT는
> 배치(50건) 단위로 백그라운드에서 진행된다. 실시간 진행률·최종 결과는
> `GET /api/knowledge/ingestion-jobs/{job_id}` 로 폴링하고, 중지가 필요하면
> `POST /api/knowledge/ingestion-jobs/{job_id}/cancel` 로 요청한다(진행 중 등록분 롤백). 아래 각
> 엔드포인트의 응답 예시에서 `created`는 항상 0이며, `auto_glossary`/`auto_fewshot`/`analyzer` 등
> 동기 처리되는 부가 필드만 실제 값을 담는다.

### GET /api/knowledge

지식 베이스 항목 목록을 반환한다.

**Query Parameters**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `namespace` | string | X | 필터링할 네임스페이스 |
| `status` | string | X | `active`(기본값) \| `pending_review` \| `rejected`. 미지정 시 `active`만 반환(v2.34) — fewshot과 달리 지정하지 않으면 승인대기/반려 항목은 섞여 보이지 않음 |

**Response** `200 OK` — `KnowledgeOut[]`

```json
[
  {
    "id": 10,
    "namespace": "coupon",
    "container_name": "coupon-api",
    "target_tables": ["tb_coupon", "tb_coupon_hist"],
    "content": "쿠폰 발급 처리 절차...",
    "query_template": "SELECT * FROM tb_coupon WHERE ...",
    "base_weight": 1.0,
    "category": "공통지식",
    "status": "active",
    "pending_review": false,
    "duplicate_matches": [],
    "created_by_part": "쿠폰파트",
    "created_by_user_id": 1,
    "created_by_username": "admin",
    "created_at": "2026-03-01T09:00:00+09:00",
    "updated_at": "2026-03-05T14:30:00+09:00"
  }
]
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `category` | string | 업무구분 (v2.27부터 등록 시 필수) |
| `status` | string | `active` \| `pending_review` \| `rejected` (v2.34) |
| `pending_review` | bool | `status === 'pending_review'`의 편의 필드 |
| `duplicate_matches` | object[] | `pending_review`인 경우 매칭된 기존 지식 목록(`id`, `content`, `similarity`) — 등록 응답에서만 채워짐, 목록 조회 시엔 별도로 `GET .../duplicate-matches` 호출 필요 |
| `created_by_part` | string \| null | 최종 수정자의 파트명 (수정 시 갱신됨) |
| `created_by_user_id` | int \| null | 최종 수정자 ID (수정 시 갱신됨) |
| `created_by_username` | string \| null | 최종 수정자 아이디 (JOIN으로 조회) |

---

### POST /api/knowledge

지식 항목을 등록한다. content 필드를 자동 임베딩하여 벡터 검색에 활용한다. 등록 시 같은 네임스페이스의 기존 활성 지식과 유사도를 비교해(v2.34), 임계값(`duplicate_min_similarity`, 기본 0.88) 이상이면 `pending_review` 상태로 저장하고(검색에서 숨김) 매칭 정보를 `rag_knowledge_duplicate_match`에 기록한다.

**Request Body** — `KnowledgeCreate`

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `namespace` | string | O | - | 네임스페이스 |
| `container_name` | string | X | null | 관련 컨테이너명 |
| `target_tables` | string[] | X | null | 관련 테이블 목록 |
| `content` | string | O | - | 지식 내용 (임베딩 대상) |
| `query_template` | string | X | null | SQL 쿼리 템플릿 |
| `base_weight` | float | X | 1.0 | 기본 가중치 (≥ 0.0) |
| `category` | string | O | - | 업무구분 (v2.27부터 필수 — 비어있으면 400) |

**Response** `201 Created` — `KnowledgeOut` (유사 지식이 있으면 `status: "pending_review"`, `pending_review: true`, `duplicate_matches`에 top-N 매칭 포함)

---

### GET /api/knowledge/{knowledge_id}/duplicate-matches

승인 대기 중인 지식이 어떤 기존 지식과 얼마나 유사했는지 조회한다. (v2.34)

**Path Parameter**: `knowledge_id` (int)

**Response** `200 OK` — `[{ "id": int, "content": string, "similarity": float }]` (유사도 내림차순)

---

### POST /api/knowledge/{knowledge_id}/resolve

승인 대기(pending_review) 지식에 대한 리뷰어 판단을 처리한다. (v2.34)

**Path Parameter**: `knowledge_id` (int)

**Request Body**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `action` | string | O | `approve`(그대로 활성화) \| `reject`(반려, `status='rejected'`로 보존) \| `merge`(매칭된 기존 지식의 content를 대체) |
| `target_id` | int | X | `merge` 시 병합 대상 지식 ID. 미지정 시 유사도 1위 매칭 사용 |
| `content` | string | X | `merge` 시 사용할 최종 내용. 미지정 시 승인 대기 지식의 원본 content 사용 |

**Response** `200 OK`

```json
{ "id": 42, "status": "rejected", "merged_into": 17 }
```

`merged_into`는 `merge` action에서만 포함된다. `reject`/`merge` 시 반려되는 지식이 이전에 어떤 질의를 "해결"시켰었다면(`ops_query_log.resolved_knowledge_id`), `reject`는 그 연결을 끊고 `merge`는 병합 대상으로 재연결한다(v2.38).

**Error** `400 Bad Request` (알 수 없는 action, 매칭 기록 없음, 병합 대상 없음)

---

### PUT /api/knowledge/{knowledge_id}

지식 항목을 수정한다. content가 변경되면 자동 재임베딩한다.

**Path Parameter**: `knowledge_id` (int)

**Request Body** — `KnowledgeUpdate` (모든 필드 선택)

| 필드 | 타입 | 설명 |
|------|------|------|
| `container_name` | string \| null | 컨테이너명 |
| `target_tables` | string[] \| null | 테이블 목록 |
| `content` | string \| null | 지식 내용 |
| `query_template` | string \| null | SQL 템플릿 |
| `base_weight` | float \| null | 가중치 (≥ 0.0) |

**Response** `200 OK` — `KnowledgeOut`

**Error** `404 Not Found`

---

### DELETE /api/knowledge/{knowledge_id}

지식 항목을 삭제한다.

**Path Parameter**: `knowledge_id` (int)

**Response** `204 No Content`

**Error** `404 Not Found`

---

### POST /api/knowledge/bulk-delete

선택한 지식 항목들을 일괄 삭제한다.

**Request Body**: `{ "ids": number[] }`

**Response** `200 OK` — `{ "deleted": number }`

---

### POST /api/knowledge/bulk-update

선택한 지식 항목들의 업무구분·소스유형을 일괄 변경한다 (v2.31~). 값을 지정한 필드만 바뀌고,
생략하거나 `null`인 필드는 유지된다. 네임스페이스 소유 파트 검증 필요.

**Request Body**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `ids` | int[] | O | 대상 지식 id 목록 |
| `category` | string | X | 변경할 업무구분 (생략 시 유지) |
| `source_type` | string | X | 변경할 소스유형 (생략 시 유지) |

**Response** `200 OK` — `{ "updated": number }`

**Error** `400 Bad Request` (`ids` 비어있음, 또는 `category`/`source_type` 둘 다 미지정)

---

### POST /api/knowledge/bulk

JSON 배열로 지식을 일괄 등록한다.

**Request Body**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `namespace` | string | Y | 네임스페이스 |
| `items` | KnowledgeItem[] | Y | 등록할 지식 목록 |
| `source_file` | string | N | 소스 파일명 |
| `source_type` | string | N | 소스 유형 (`manual`/`csv_import` 등) |

**Response** `201 Created`

```json
{ "created": 0, "job_id": 12, "status": "processing" }
```

---

### POST /api/knowledge/import/csv

CSV 파일을 업로드하여 컬럼 매핑 후 벌크 등록한다. `multipart/form-data`

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `file` | file | Y | CSV 파일 |
| `namespace` | string | Y | 네임스페이스 |
| `column_mapping` | string (JSON) | Y | `{"content":"컬럼명","category":"컬럼명",...}` |
| `category` | string | N | 기본 업무구분 |

**Response** `201 Created` — `{ "created": 0, "job_id": N, "status": "processing" }`

---

### POST /api/knowledge/import/text-split

텍스트를 붙여넣으면 자동 분할 후 벌크 등록한다.

**Request Body**

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `namespace` | string | — | 네임스페이스 |
| `raw_text` | string | — | 분할할 원문 텍스트 |
| `strategy` | string | `auto` | `auto`/`section`/`paragraph`/`fixed` |
| `category` | string | null | 업무구분 |

**Response** `201 Created` — `{ "created": 0, "job_id": N, "status": "processing", "chunks": N }`

### POST /api/knowledge/import/text-split/preview

텍스트 분할 결과를 미리보기한다 (등록 없음).

**Response** `200 OK` — `{ "chunks": string[], "count": N }`

---

### POST /api/knowledge/import/file

파일을 업로드하여 파싱 → 청킹 → 벌크 등록한다. `multipart/form-data`

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `file` | file | — | `.txt`/`.md`/`.pdf`/`.xlsx`/`.xlsm`/`.csv` (v2.17~ Excel·CSV 지원). 최대 50MB. 암호화 PDF 거부 |
| `namespace` | string | — | 네임스페이스 |
| `chunk_strategy` | string | `auto` | `auto`/`section`/`paragraph`/`fixed` |
| `category` | string | null | 업무구분 |
| `auto_analyze` | bool | false | LLM Analyzer로 청킹 전략/메타 자동 결정 |
| `auto_tag` | bool | false | LLM으로 카테고리·시스템명 자동 태깅 |
| `auto_glossary` | bool | false | LLM으로 용어 자동 추출 |
| `auto_fewshot` | bool | false | LLM으로 Q&A 자동 생성 → fewshot candidate |

**Response** `201 Created`

```json
{
  "created": 0, "job_id": 7, "status": "processing", "chunks": 12,
  "auto_glossary": 3, "auto_fewshot": 2,
  "analyzer": { "doc_type": "technical", "chunk_strategy": "section", ... },
  "source_name": "manual.pdf", "page_count": 8
}
```

### POST /api/knowledge/import/file/preview

파일 파싱·청킹 결과를 미리보기한다 (등록 없음). `multipart/form-data`

**Response** `200 OK` — `{ "source_name", "source_type", "chunk_count", "chunks": [{idx, text, title}...] }`

---

### POST /api/knowledge/import/url

웹 페이지 또는 Confluence 페이지를 수집하여 청킹 → 벌크 등록한다.

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `namespace` | string | — | 네임스페이스 |
| `url` | string | — | 수집할 URL |
| `confluence_token` | string | null | Confluence Personal Access Token (PAT). Confluence URL이면 필수 |
| `chunk_strategy` | string | `auto` | `auto`/`section`/`paragraph`/`fixed` |
| `category` | string | null | 업무구분 |
| `auto_tag` | bool | false | LLM 자동 태깅 |
| `auto_glossary` | bool | false | LLM 용어 추출 |

**지원 URL 형식:**
- 일반 웹: `https://example.com/page` (토큰 불필요)
- Confluence 단일 페이지: `https://confl.example.com/display/SPACE/제목`
- Confluence pageId: `https://confl.example.com/pages/viewpage.action?pageId=12345`

**지원하지 않는 형식:** Space 전체 URL (`/spaces/viewspace.action`) → 400 반환

**Response** `201 Created`

```json
{
  "created": 0, "job_id": 9, "status": "processing", "chunks": 8,
  "auto_glossary": 0, "source_name": "페이지 제목",
  "source_type": "confluence", "url": "https://..."
}
```

### POST /api/knowledge/import/url/preview

URL 수집 결과를 미리보기한다 (등록 없음). 요청/응답 형식은 `/import/url`과 동일.

---

### POST /api/knowledge/import/url/tree

입력 URL을 root로 자손 페이지 메타데이터를 BFS로 조회 (Confluence 전용, v2.17~).
본문 fetch 없이 트리만 반환하여 빠름.

**Request Body**

| 필드 | 타입 | 필수 | 기본 | 설명 |
|---|---|---|---|---|
| `url` | string | O | - | Confluence URL (트리의 root) |
| `confluence_token` | string | X | null | PAT (없으면 user.encrypted_confluence_pat 사용) |
| `max_depth` | int | X | 3 | 자손 탐색 깊이 (max 10) |
| `max_pages` | int | X | 100 | 최대 페이지 수 (hard max 500) |

**Response** `200 OK`

```json
{
  "root": {"page_id": "...", "title": "...", "url": "...", "depth": 0, "parent_id": null},
  "tree": [{"page_id": "...", "title": "...", "url": "...", "depth": 1, "parent_id": "root_id"}, ...],
  "truncated": false,
  "max_depth_reached": false,
  "max_depth": 3,
  "max_pages": 100
}
```

---

### POST /api/knowledge/import/url/bulk-pages/preview

선택된 페이지들을 fetch + 청킹 후 청크 메타만 반환 (등록 없음, v2.17~).

**Request Body**

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `namespace` | string | O | 네임스페이스 |
| `base_url` | string | O | Confluence base URL (예: `https://confl.sinc.co.kr`) |
| `pages` | array | O | `[{page_id, title?, url?}, ...]` (최대 200개) |
| `confluence_token` | string | X | PAT |
| `chunk_strategy` | string | X | 청킹 전략 (auto/section/paragraph/fixed) |

**Response** `200 OK`

```json
{
  "chunks": [{"idx": 0, "page_id": "...", "page_title": "...", "text": "...", "title": "..."}, ...],
  "chunk_count": 123,
  "pages": [{"page_id": "...", "title": "...", "chunk_start": 0, "chunk_count": 5}, ...],
  "failed_pages": [{"page_id": "...", "title": "...", "error": "..."}]
}
```

---

### POST /api/knowledge/import/url/bulk-pages

선택된 Confluence 페이지들을 일괄 인제스천 (v2.17~).
각 페이지를 병렬 fetch + 청킹 후 단일 `ingestion_job`으로 등록.

**Request Body**: `bulk-pages/preview`와 동일 + `category`, `auto_tag`, `auto_glossary` 옵션.

**Response** `201 Created`

```json
{
  "created": 0,
  "job_id": 42,
  "status": "processing",
  "pages_succeeded": 5,
  "pages_failed": 0,
  "failed_pages": [],
  "page_summaries": [{"page_id": "...", "title": "...", "chunks": 12, "chars": 4530}, ...],
  "chunks": 234,
  "auto_glossary": 0,
  "source_name": "Confluence bulk (5 pages)",
  "source_type": "confluence_bulk"
}
```

---

### GET /api/knowledge/ingestion-jobs

인제스천 작업 이력을 조회한다.

**Query Parameter**: `namespace` (필수)

**Response** `200 OK` — `IngestionJob[]`

```json
[{
  "id": 7, "namespace_id": 2,
  "source_file": "manual.pdf", "source_type": "file_upload",
  "status": "completed", "total_chunks": 12, "created_chunks": 12,
  "auto_glossary": 3, "auto_fewshot": 2,
  "chunk_strategy": "auto", "error_message": null,
  "created_at": "2026-04-17T10:30:00", "completed_at": "2026-04-17T10:30:45"
}]
```

---

### GET /api/knowledge/ingestion-jobs/{job_id}

단건 작업 진행률을 조회한다 (v2.29~, 프론트에서 1.5초 간격 폴링).

**Response** `200 OK`

```json
{
  "id": 7, "namespace_id": 2,
  "source_file": "manual.pdf", "source_type": "file_upload",
  "status": "processing", "total_chunks": 2530, "created_chunks": 900,
  "cancel_requested": false, "error_message": null,
  "created_at": "2026-04-17T10:30:00", "completed_at": null
}
```

`status`: `processing` → `completed` | `failed` | `cancelled`. `404` — 존재하지 않는 job_id.

---

### POST /api/knowledge/ingestion-jobs/{job_id}/cancel

진행 중인 인제스천 작업에 중지를 요청한다 (v2.29~). 네임스페이스 소유 파트 검증 필요.
백그라운드 작업이 처리 중이던 배치를 마치고 다음 배치 경계에서 확인해 중단하며, 그 작업이
이미 등록한 `rag_knowledge` 행을 전부 롤백(삭제)한 뒤 `status='cancelled'`로 전이한다.

**Response** `200 OK` — `{ "id": N, "status": "processing" }` (요청 접수 확인. 실제 취소 완료 여부는
`GET .../ingestion-jobs/{job_id}` 폴링으로 확인)

`404` — 존재하지 않거나 이미 종료된(processing이 아닌) 작업.

---

## 7. 용어집 (Glossary)

> 모든 용어집 엔드포인트는 `Authorization: Bearer {access_token}` 헤더가 필요하다.

### GET /api/knowledge/glossary

용어집 항목을 반환한다.

**Query Parameters**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `namespace` | string | X | 필터링할 네임스페이스 |

**Response** `200 OK` — `GlossaryOut[]`

```json
[
  {
    "id": 1,
    "namespace": "coupon",
    "term": "쿠폰발급",
    "description": "쿠폰 발급 및 배포 프로세스",
    "created_by_part": "쿠폰파트",
    "created_by_user_id": 1,
    "created_by_username": "admin"
  }
]
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `created_by_part` | string \| null | 최종 수정자의 파트명 (수정 시 갱신됨) |
| `created_by_user_id` | int \| null | 최종 수정자 ID (수정 시 갱신됨) |
| `created_by_username` | string \| null | 최종 수정자 아이디 (JOIN으로 조회) |

---

### POST /api/knowledge/glossary

용어를 등록한다. description 필드를 자동 임베딩한다.

**Request Body** — `GlossaryCreate`

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `namespace` | string | O | 네임스페이스 |
| `term` | string | O | 표준 용어 |
| `description` | string | O | 용어 설명 (임베딩 대상) |

**Response** `201 Created` — `GlossaryOut`

---

### PUT /api/knowledge/glossary/{glossary_id}

용어를 수정한다. description이 변경되면 자동 재임베딩한다.

**Path Parameter**: `glossary_id` (int)

**Request Body** — `GlossaryUpdate`

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `term` | string | O | 표준 용어 |
| `description` | string | O | 용어 설명 |

**Response** `200 OK` — `GlossaryOut`

**Error** `404 Not Found`

---

### DELETE /api/knowledge/glossary/{glossary_id}

용어를 삭제한다.

**Path Parameter**: `glossary_id` (int)

**Response** `204 No Content`

**Error** `404 Not Found`

---

## 8. Few-Shot 예제

> 모든 Few-Shot 엔드포인트는 `Authorization: Bearer {access_token}` 헤더가 필요하다.

### GET /api/fewshots

Few-shot 예제 목록을 반환한다.

**Query Parameters**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `namespace` | string | O | 네임스페이스 |

**Response** `200 OK` — `FewshotOut[]`

```json
[
  {
    "id": 1,
    "namespace": "coupon",
    "question": "쿠폰 발급 방법은?",
    "answer": "쿠폰 발급은 coupon-api 컨테이너에서...",
    "knowledge_id": 10,
    "created_by_part": "쿠폰파트",
    "created_by_user_id": 1,
    "created_by_username": "admin",
    "created_at": "2026-03-01T09:00:00+09:00"
  }
]
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `created_by_part` | string \| null | 최종 수정자의 파트명 (수정 시 갱신됨) |
| `created_by_user_id` | int \| null | 최종 수정자 ID (수정 시 갱신됨) |
| `created_by_username` | string \| null | 최종 수정자 아이디 (JOIN으로 조회) |

---

### POST /api/fewshots

Few-shot 예제를 등록한다. question 필드를 자동 임베딩한다.

**Request Body** — `FewshotCreate`

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `namespace` | string | O | 네임스페이스 |
| `question` | string | O | 예제 질문 (임베딩 대상) |
| `answer` | string | O | 예제 답변 |
| `knowledge_id` | int | X | 연결할 지식 ID |

**Response** `201 Created` — `FewshotOut`

---

### POST /api/fewshots/search

질문에 대해 매칭될 few-shot 예제를 미리보기한다.

**Request Body** — `FewshotSearchRequest`

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `namespace` | string | O | 네임스페이스 |
| `question` | string | O | 검색할 질문 |

**Response** `200 OK` — `FewshotSearchResponse`

```json
{
  "question": "쿠폰 발급",
  "namespace": "coupon",
  "fewshots": [
    { "question": "쿠폰 발급 방법은?", "answer": "...", "similarity": 0.78 }
  ],
  "prompt_section": "### 참고 예제\nQ: 쿠폰 발급 방법은?\nA: ..."
}
```

- 최소 유사도: 0.6
- 최대 결과: 2건

---

### PUT /api/fewshots/{fewshot_id}

Few-shot 예제를 수정한다. question이 변경되면 자동 재임베딩한다.

**Path Parameter**: `fewshot_id` (int)

**Request Body** — `FewshotUpdate`

| 필드 | 타입 | 설명 |
|------|------|------|
| `question` | string \| null | 예제 질문 |
| `answer` | string \| null | 예제 답변 |

**Response** `200 OK` — `FewshotOut`

**Error** `404 Not Found`

---

### DELETE /api/fewshots/{fewshot_id}

Few-shot 예제를 삭제한다.

**Path Parameter**: `fewshot_id` (int)

**Response** `204 No Content`

**Error** `404 Not Found`

---

## 9. 피드백 (Feedback)

> `Authorization: Bearer {access_token}` 헤더가 필요하다.

### POST /api/feedback

답변에 대한 피드백(좋아요/싫어요)을 기록한다.

**부수 효과**:
- 질의 로그 상태 갱신: 긍정 → `resolved`, 부정 → `unresolved`. `resolved_knowledge_id`가 함께 오면 `is_positive` 값과 무관하게 항상 `resolved`(나빠요 후 지식 등록으로 교정한 경우 — v2.36)
- 지식 가중치 조정: 긍정 +0.1 (최대 5.0), 부정 -0.1 (최소 0.0) — `is_positive`가 가중치 방향을 결정. 나빠요 후 교정 등록 시에는 `is_positive=false`로 보내 오답의 근거였던 지식을 정확히 페널티 처리한다(v2.36 이전엔 여기서 `true`를 잘못 보내 오히려 가중치가 올라가는 버그가 있었음)
- 매칭 우선순위: `message_id`가 있으면 그 값으로 정확히 매칭, 없으면 `(namespace, question)`으로 최신 1건 매칭(v2.36)
- 긍정 + answer 존재 시: Few-shot 예제로 자동 등록

**Request Body** — `FeedbackCreate`

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `knowledge_id` | int | X | null | 관련 지식 ID (가중치 조정 대상) |
| `namespace` | string | O | - | 네임스페이스 |
| `question` | string | O | - | 원본 질문 |
| `answer` | string | X | null | LLM 답변 |
| `is_positive` | bool | O | - | 긍정 여부 (가중치 조정 방향) |
| `message_id` | int | X | null | 메시지 ID (query_log 매칭 키) |
| `resolved_knowledge_id` | int | X | null | 지식 등록으로 해결한 경우, 등록된 지식 ID — `ops_query_log.resolved_knowledge_id`로 연결 (v2.36) |

**Response** `201 Created`

```json
{ "status": "ok" }
```

---

## 10. 통계 및 질의 로그 (Stats)

> 모든 통계 엔드포인트는 `Authorization: Bearer {access_token}` 헤더가 필요하다.
> 질의 로그 해결 처리(resolve) 및 삭제는 네임스페이스 소유 파트 기반 권한을 따른다. mark-resolved는 모든 인증 사용자가 가능하다.

### GET /api/stats

전체 네임스페이스의 통계를 반환한다.

**Response** `200 OK` — `StatsResponse`

```json
{
  "namespaces": [
    {
      "namespace": "coupon",
      "total_queries": 150,
      "resolved": 120,
      "pending": 25,
      "unresolved": 5,
      "positive_feedback": 100,
      "negative_feedback": 10,
      "knowledge_count": 30,
      "glossary_count": 15
    }
  ],
  "unresolved_cases": [
    {
      "namespace": "coupon",
      "question": "해결되지 않은 질문...",
      "created_at": "2026-03-07T10:00:00+09:00"
    }
  ]
}
```

- `unresolved_cases`: 최근 20건

---

### GET /api/stats/namespace/{name}

특정 네임스페이스의 상세 통계를 반환한다.

**Path Parameter**: `name` (string) — 네임스페이스명

**Response** `200 OK` — `NamespaceDetailStats`

```json
{
  "namespace": "coupon",
  "total_queries": 150,
  "resolved": 120,
  "pending": 25,
  "unresolved": 5,
  "term_distribution": [
    { "term": "쿠폰발급", "total": 50, "pending": 5, "unresolved": 1 }
  ],
  "unresolved_cases": [
    { "id": 42, "question": "...", "mapped_term": "쿠폰발급", "created_at": "..." }
  ]
}
```

- `term_distribution`: 최대 20건
- `unresolved_cases`: 최대 30건

---

### GET /api/stats/namespace/{name}/queries

네임스페이스의 질의 로그를 반환한다.

**Path Parameter**: `name` (string)

**Query Parameters**

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `status` | string | X | null | 필터 (`resolved` \| `pending` \| `unresolved`) |
| `limit` | int | X | 100 | 최대 건수 (≤ 500) |

**Response** `200 OK` — 질의 로그 배열

```json
[
  {
    "id": 42,
    "question": "쿠폰 발급 방법",
    "answer": "쿠폰 발급은...",
    "mapped_term": "쿠폰발급",
    "status": "resolved",
    "created_at": "2026-03-07T10:00:00+09:00"
  }
]
```

---

### PATCH /api/stats/query-log/{log_id}/resolve

"승인" 원클릭 처리 — 대기 중인 답변을 수정 없이 그대로 지식 베이스에 등록한다.

> **권한**: 네임스페이스 소유 파트 또는 admin

**부수 효과** (v2.38부터 지식등록 탭과 동일한 `create_knowledge()`를 재사용 — 이전엔 raw INSERT라 아래 검사들을 건너뛰었음):
- 답변 → 지식 등록 + 임베딩, 업무구분은 원클릭이라 별도 입력이 없어 `'공통지식'` 기본값 사용
- 기존 활성 지식과 유사도가 임계값 이상이면 `pending_review`로 등록(승인 대기), 응답에 `pending_review: true` 반환
- 질의 로그 상태 → `resolved`, `resolved_knowledge_id`를 새로 등록한 지식과 연결, 피드백 자동 추가

**Path Parameter**: `log_id` (int)

**Response** `200 OK`

```json
{ "status": "ok", "pending_review": false }
```

**Error** `404 Not Found`, `400 Bad Request` (답변 없음)

---

### PATCH /api/stats/query-log/{log_id}/mark-resolved

질의를 해결 상태로 변경한다. "수정 후 등록"/"지식 등록" 모달에서 `createKnowledge()` 호출 후 이 엔드포인트로 결과를 연결하는 용도(직접 등록은 이 엔드포인트가 하지 않음).

> **권한**: 모든 인증 사용자

**Path Parameter**: `log_id` (int)

**Request Body** (v2.36 추가, 선택)

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `knowledge_id` | int | X | null | 함께 등록한 지식 ID — 지정 시 `resolved_knowledge_id`로 연결되어 통계 화면에 등록 내용이 반영됨 |

**Response** `200 OK`

```json
{ "status": "ok" }
```

**Error** `404 Not Found`

---

### DELETE /api/stats/query-log/{log_id}

질의 로그를 삭제한다.

> **권한**: 네임스페이스 소유 파트 또는 admin

**Path Parameter**: `log_id` (int)

**Response** `204 No Content`

**Error** `404 Not Found`

---

### POST /api/stats/query-logs/bulk-delete

질의 로그를 일괄 삭제한다.

> **권한**: 네임스페이스 소유 파트 또는 admin (대상 로그의 네임스페이스 기준)

**Request Body**

```json
{ "ids": [1, 2, 3] }
```

**Response** `200 OK`

```json
{ "deleted": 3 }
```

**Error** `400 Bad Request` (ids 미제공)

---

## 11. 네임스페이스 (Namespaces)

> 조회(GET)는 인증된 모든 사용자. 생성(POST)은 모든 인증 사용자. 삭제(DELETE)는 소유 파트 또는 admin.
> 모든 엔드포인트는 `Authorization: Bearer {access_token}` 헤더가 필요하다.

### GET /api/namespaces

전체 네임스페이스 이름 목록을 반환한다.

**Response** `200 OK` — `string[]`

```json
["coupon", "gift", "payment"]
```

---

### GET /api/namespaces/detail

네임스페이스 상세 정보를 반환한다.

**Response** `200 OK` — `NamespaceInfo[]`

```json
[
  {
    "name": "coupon",
    "description": "쿠폰 도메인",
    "owner_part": "쿠폰파트",
    "knowledge_count": 30,
    "glossary_count": 15,
    "created_by_user_id": 1,
    "created_by_username": "admin",
    "created_at": "2026-03-01T09:00:00+09:00"
  }
]
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `owner_part` | string \| null | 소유 파트명 (NULL이면 admin만 수정/삭제 가능) |
| `created_by_user_id` | int \| null | 생성자 ID |
| `created_by_username` | string \| null | 생성자 아이디 (JOIN으로 조회) |

---

### POST /api/namespaces

네임스페이스를 생성한다. 생성자의 파트가 `owner_part`로 자동 기록된다.

> **권한**: 모든 인증 사용자

**Request Body** — `NamespaceCreate`

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `name` | string | O | - | 네임스페이스 이름 |
| `description` | string | X | `""` | 설명 |

**Response** `201 Created`

---

### DELETE /api/namespaces/{name}

네임스페이스와 관련 데이터를 모두 삭제한다.

> **권한**: 네임스페이스 소유 파트 또는 admin

**Path Parameter**: `name` (string)

**Response** `204 No Content`

**Error** `404 Not Found`

---

## 12. LLM 설정 (LLM Settings)

> 조회(GET)는 인증된 모든 사용자, 변경(PUT)은 admin 전용이다.
> 모든 엔드포인트는 `Authorization: Bearer {access_token}` 헤더가 필요하다.

### GET /api/llm/config

현재 LLM 프로바이더 설정을 반환한다.

**Response** `200 OK`

```json
{
  "provider": "inhouse",
  "is_runtime_override": true,
  "ollama": {
    "base_url": "http://host.docker.internal:11434",
    "model": "exaone3.5:7.8b",
    "timeout": 900
  },
  "inhouse": {
    "base_url": "https://devx-gw.shinsegae-inc.com",
    "agent_code": "playground",
    "agent_id": "b6958377-73f2-4234-a49c-2aa878350a2e",
    "conversation_id": "99cae258-25ee-43a4-8336-ff56379891c6",
    "model": "claude-sonnet-4.5",
    "has_credentials": true,
    "response_mode": "streaming",
    "timeout": 120
  },
  "is_connected": true
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `provider` | string | 현재 LLM 프로바이더 (`ollama` \| `inhouse`) |
| `is_runtime_override` | bool | 런타임 오버라이드 여부 (`true`면 관리자가 UI에서 변경한 값 사용 중, 컨테이너 재시작 시 `.env` 기본값으로 복귀) |
| `ollama` | object | Ollama 설정 (`base_url`, `model`, `timeout`) |
| `inhouse` | object | 사내 LLM 설정 (v2.17 OAuth2) |
| `inhouse.base_url` | string | DevX 게이트웨이 base URL. 코드에서 `/api/v1/auth/token`, `/api/v1/agent/chat` 경로 부착 |
| `inhouse.agent_code` | string | DevX agent_code (기본 `playground`) |
| `inhouse.agent_id` | string | DevX agent_id UUID (v2.17~) |
| `inhouse.conversation_id` | string | dify 사전 등록 시스템 공통 conversation_id (v2.17~) |
| `inhouse.model` | string | 선택된 모델 (`gpt-5.2`, `claude-sonnet-4.5`, `gemini-3.0-pro`, 빈 문자열=기본) |
| `inhouse.has_credentials` | bool | 시스템 OAuth2 자격증명(client_id+client_secret) 존재 여부 |
| `inhouse.response_mode` | string | 응답 방식 (`streaming` \| `blocking`) |
| `inhouse.timeout` | int | 타임아웃 (초) |
| `is_connected` | bool | LLM 서버 연결 상태 (시스템 자격증명으로 토큰 발급 성공 여부) |

---

### PUT /api/llm/config

LLM 프로바이더를 전환하거나 설정을 변경한다. 런타임 오버라이드로 적용되며, 컨테이너 재시작 시 `.env` 기본값으로 복귀한다.

> **권한**: admin 전용

**Request Body** — `LLMConfigUpdate`

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `provider` | string | O | `ollama` \| `inhouse` |
| `ollama_base_url` | string | X | Ollama 서버 URL |
| `ollama_model` | string | X | Ollama 모델명 |
| `ollama_timeout` | int | X | 타임아웃 (초) |
| `inhouse_llm_base_url` | string | X | DevX 게이트웨이 base URL (v2.17~). 코드에서 `/api/v1/auth/token`, `/api/v1/agent/chat` 경로 부착 |
| `inhouse_llm_client_id` | string | X | OAuth2 client_id (v2.17~) |
| `inhouse_llm_client_secret` | string | X | OAuth2 client_secret (v2.17~) |
| `inhouse_llm_agent_id` | string | X | DevX agent_id UUID (v2.17~, 이전 usecase_id) |
| `inhouse_llm_agent_code` | string | X | DevX agent_code (기본 `playground`) |
| `inhouse_llm_conversation_id` | string | X | dify 사전 등록 conversation_id (v2.17~). 미설정 시 첫 호출 0바이트 가능 |
| `inhouse_llm_model` | string | X | 모델명 (gpt-5.2, claude-sonnet-4.5, gemini-3.0-pro) |
| `inhouse_llm_response_mode` | string | X | 응답 방식 (`streaming` \| `blocking`) |
| `inhouse_llm_timeout` | int | X | 타임아웃 (초) |

**Response** `200 OK` — 갱신된 설정 (GET /api/llm/config 형식) + `is_connected`

**Error** `400 Bad Request` (유효하지 않은 provider), `422 Unprocessable Entity`

---

### POST /api/llm/test

LLM 프로바이더 연결을 테스트한다. 실제 전환하지 않는다.

> **권한**: admin 전용 (v2.38 — 이전엔 일반 사용자도 호출 가능해, 공격자가 임의 URL로 서버발 요청을 유발할 수 있는 SSRF 표면이었음)

**Request Body** — `LLMTestRequest`

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `provider` | string | O | 테스트할 프로바이더 |
| `ollama_base_url` | string | X | Ollama URL |
| `ollama_model` | string | X | 모델명 |
| `inhouse_llm_base_url` | string | X | DevX 게이트웨이 base URL |
| `inhouse_llm_client_id` | string | X | OAuth2 client_id |
| `inhouse_llm_client_secret` | string | X | OAuth2 client_secret |
| `inhouse_llm_agent_id` | string | X | agent_id UUID |
| `inhouse_llm_agent_code` | string | X | agent_code |
| `inhouse_llm_conversation_id` | string | X | dify 사전 등록 conversation_id |
| `inhouse_llm_model` | string | X | 모델명 |
| `inhouse_llm_response_mode` | string | X | 응답 방식 |

**Response** `200 OK`

```json
{
  "is_connected": true,
  "provider": "inhouse",
  "error": null
}
```

---

### GET /api/llm/thresholds

검색 유사도 임계값 설정을 반환한다.

**Response** `200 OK`

```json
{
  "glossary_min_similarity": 0.5,
  "fewshot_min_similarity": 0.6,
  "knowledge_min_score": 0.1,
  "knowledge_high_score": 0.7,
  "knowledge_mid_score": 0.4
}
```

---

### PUT /api/llm/thresholds

검색 유사도 임계값을 변경한다.

> **권한**: admin 전용

**Request Body** — `ThresholdUpdate` (모든 필드 선택)

| 필드 | 타입 | 범위 | 설명 |
|------|------|------|------|
| `glossary_min_similarity` | float | 0.0~1.0 | 용어집 매핑 최소 유사도 |
| `fewshot_min_similarity` | float | 0.0~1.0 | Few-shot 최소 유사도 |
| `knowledge_min_score` | float | 0.0~1.0 | 지식 검색 최소 점수 |
| `knowledge_high_score` | float | 0.0~1.0 | 고신뢰 검색 점수 |
| `knowledge_mid_score` | float | 0.0~1.0 | 중간 신뢰 검색 점수 |

**Response** `200 OK` — 갱신된 임계값

**Error** `400 Bad Request` (범위 초과)

---

## 13. Text-to-SQL 어드민 API (주요)

> Base: `/api/text2sql/namespaces/{namespace}/`
> 인증: JWT Bearer. 대부분 Admin 전용. `from-feedback`만 일반 사용자도 가능.

### GET /api/text2sql/namespaces/{ns}/fewshots

SQL Few-shot 목록 조회.

**Query Parameter**: `status` — `all`(기본) | `pending` | `approved` | `rejected`

**Response `200`**
```json
[
  {
    "id": 1,
    "question": "지난달 매출 상위 10개 제품은?",
    "sql": "SELECT product_name, SUM(amount) ...",
    "category": "",
    "hits": 3,
    "status": "approved",
    "created_at": "2026-03-19T10:00:00Z"
  }
]
```

### POST /api/text2sql/namespaces/{ns}/fewshots/from-feedback

채팅 좋아요 피드백으로 SQL Few-shot 후보를 등록합니다. **관리자 승인 필요**.
동일 질문이 이미 `pending`/`approved` 상태로 존재하면 중복 등록하지 않습니다.

**인증**: 일반 사용자 JWT (Admin 불필요)

**Request Body**
```json
{
  "question": "지난달 매출 상위 10개 제품은?",
  "sql": "SELECT product_name, SUM(amount) AS total FROM sales ..."
}
```

**Response `200`**
```json
{ "id": 5, "ok": true, "skipped": false }
```
`skipped: true` — 중복으로 인해 등록 건너뜀

### PATCH /api/text2sql/namespaces/{ns}/fewshots/{id}/status

Few-shot 상태 변경 (관리자 전용).

**Query Parameter**: `status` — `approved` | `pending` | `rejected`

**Response `200`**: `{ "ok": true }`

### GET /api/text2sql/namespaces/{ns}/schema/tables-available

대상 DB에서 사용 가능한 테이블 요약 조회 (빠른 조회 — 전체 inspect 없이).

**Response `200`**
```json
[
  { "table": "users", "column_count": 12 },
  { "table": "orders", "column_count": 8 }
]
```

### POST /api/text2sql/namespaces/{ns}/schema/tables/add

선택한 테이블만 증분 추가. 이미 등록된 테이블은 skip.

**Request Body**
```json
{ "tables": ["users", "orders"] }
```

**Response `200`**
```json
{ "ok": true, "added": 2, "skipped": 0 }
```

### DELETE /api/text2sql/namespaces/{ns}/schema/tables/{table_name}

앱 DB에서 테이블 삭제 (컬럼, 벡터, 관계 cascade).

**Response `200`**: `{ "ok": true }`

### POST /api/text2sql/namespaces/{ns}/synonyms/bulk-delete

용어 사전 일괄 삭제.

**Request Body**: `{ "ids": [1, 2, 3] }`

**Response `200`**: `{ "ok": true, "deleted": 3 }`

### POST /api/text2sql/namespaces/{ns}/fewshots/bulk-delete

SQL 예제 일괄 삭제.

**Request Body**: `{ "ids": [4, 5, 6] }`

**Response `200`**: `{ "ok": true, "deleted": 3 }`

### GET /api/text2sql/namespaces/{ns}/audit-logs

감사 로그 조회. v2.12부터 날짜 범위 필터 지원.

**Query Parameters**: `page`, `limit`, `status`, `date_from` (YYYY-MM-DD), `date_to` (YYYY-MM-DD)

---

## 14. 공통 에러 코드

| HTTP 상태 코드 | 의미 | 설명 |
|----------------|------|------|
| `200` | OK | 정상 처리 |
| `201` | Created | 리소스 생성 완료 |
| `204` | No Content | 삭제 완료 (응답 본문 없음) |
| `400` | Bad Request | 잘못된 요청 파라미터 |
| `401` | Unauthorized | 인증 실패 (토큰 없음, 만료, 유효하지 않음) |
| `403` | Forbidden | 권한 부족 (admin 전용 엔드포인트에 일반 사용자 접근) |
| `404` | Not Found | 리소스를 찾을 수 없음 |
| `422` | Unprocessable Entity | 요청 형식은 맞으나 처리 불가 |

---

## 부록: 내부 동작

### 자동 정리 (Cleanup)
- **메시지 정리**: 네임스페이스당 100건 초과 시 오래된 메시지 삭제
- **질의 로그 정리**: 해결(resolved) 상태 로그 90일 경과 시 삭제

### SSE 스트리밍 아키텍처
- LLM 생성은 `asyncio.Task`로 독립 실행
- `asyncio.Queue`를 통해 SSE 제너레이터와 통신
- 클라이언트 연결 해제 시에도 백그라운드 생성 계속 진행
- DB에 `status = 'generating'` → 완료 시 `'completed'`로 갱신
- 프론트엔드는 3초 간격 폴링으로 완료 감지
