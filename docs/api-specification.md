# Ops-Navigator API 명세서

> **Version**: 1.0
> **Base URL**: `http://localhost:8000`
> **Protocol**: REST + SSE (Server-Sent Events)
> **Content-Type**: `application/json` (기본), `text/event-stream` (SSE)
> **작성일**: 2026-03-07

---

## 목차

1. [시스템 상태](#1-시스템-상태)
2. [채팅 (Chat)](#2-채팅-chat)
3. [대화 관리 (Conversations)](#3-대화-관리-conversations)
4. [지식 베이스 (Knowledge)](#4-지식-베이스-knowledge)
5. [용어집 (Glossary)](#5-용어집-glossary)
6. [Few-Shot 예제](#6-few-shot-예제)
7. [피드백 (Feedback)](#7-피드백-feedback)
8. [통계 및 질의 로그 (Stats)](#8-통계-및-질의-로그-stats)
9. [네임스페이스 (Namespaces)](#9-네임스페이스-namespaces)
10. [LLM 설정 (LLM Settings)](#10-llm-설정-llm-settings)
11. [공통 에러 코드](#11-공통-에러-코드)

---

## 1. 시스템 상태

### GET /health

시스템 헬스체크. LLM 프로바이더 연결 상태를 포함한다.

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

## 2. 채팅 (Chat)

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
{ "ok": true }
```

---

### DELETE /api/chat/messages/{msg_id}

어시스턴트 메시지와 쌍을 이루는 사용자 메시지를 함께 삭제한다.

**Path Parameter**: `msg_id` (int) — 메시지 ID

**Response** `200 OK`

```json
{ "ok": true }
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

## 3. 대화 관리 (Conversations)

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

## 4. 지식 베이스 (Knowledge)

### GET /api/knowledge

지식 베이스 항목 목록을 반환한다.

**Query Parameters**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `namespace` | string | X | 필터링할 네임스페이스 |

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
    "created_at": "2026-03-01T09:00:00+09:00",
    "updated_at": "2026-03-05T14:30:00+09:00"
  }
]
```

---

### POST /api/knowledge

지식 항목을 등록한다. content 필드를 자동 임베딩하여 벡터 검색에 활용한다.

**Request Body** — `KnowledgeCreate`

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `namespace` | string | O | - | 네임스페이스 |
| `container_name` | string | X | null | 관련 컨테이너명 |
| `target_tables` | string[] | X | null | 관련 테이블 목록 |
| `content` | string | O | - | 지식 내용 (임베딩 대상) |
| `query_template` | string | X | null | SQL 쿼리 템플릿 |
| `base_weight` | float | X | 1.0 | 기본 가중치 (≥ 0.0) |

**Response** `201 Created` — `KnowledgeOut`

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

## 5. 용어집 (Glossary)

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
    "description": "쿠폰 발급 및 배포 프로세스"
  }
]
```

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

## 6. Few-Shot 예제

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
    "created_at": "2026-03-01T09:00:00+09:00"
  }
]
```

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

## 7. 피드백 (Feedback)

### POST /api/feedback

답변에 대한 피드백(좋아요/싫어요)을 기록한다.

**부수 효과**:
- 질의 로그 상태 갱신: 긍정 → `resolved`, 부정 → `unresolved`
- 지식 가중치 조정: 긍정 +0.1 (최대 5.0), 부정 -0.1 (최소 0.0)
- 긍정 + answer 존재 시: Few-shot 예제로 자동 등록

**Request Body** — `FeedbackCreate`

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `knowledge_id` | int | X | null | 관련 지식 ID |
| `namespace` | string | O | - | 네임스페이스 |
| `question` | string | O | - | 원본 질문 |
| `answer` | string | X | null | LLM 답변 |
| `is_positive` | bool | O | - | 긍정 여부 |
| `message_id` | int | X | null | 메시지 ID |

**Response** `201 Created`

```json
{ "status": "ok" }
```

---

## 8. 통계 및 질의 로그 (Stats)

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

미해결/보류 질의를 해결 처리한다. 답변을 지식 베이스에 자동 등록한다.

**부수 효과**: 답변 → 지식 등록 + 임베딩, 상태 → `resolved`, 피드백 자동 추가

**Path Parameter**: `log_id` (int)

**Response** `200 OK`

```json
{ "status": "ok" }
```

**Error** `404 Not Found`, `400 Bad Request` (답변 없음)

---

### PATCH /api/stats/query-log/{log_id}/mark-resolved

지식 등록 없이 해결 상태로만 변경한다.

**Path Parameter**: `log_id` (int)

**Response** `200 OK`

```json
{ "status": "ok" }
```

**Error** `404 Not Found`

---

### DELETE /api/stats/query-log/{log_id}

질의 로그를 삭제한다.

**Path Parameter**: `log_id` (int)

**Response** `204 No Content`

**Error** `404 Not Found`

---

### POST /api/stats/query-logs/bulk-delete

질의 로그를 일괄 삭제한다.

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

## 9. 네임스페이스 (Namespaces)

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
    "knowledge_count": 30,
    "glossary_count": 15,
    "created_at": "2026-03-01T09:00:00+09:00"
  }
]
```

---

### POST /api/namespaces

네임스페이스를 생성한다.

**Request Body** — `NamespaceCreate`

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `name` | string | O | - | 네임스페이스 이름 |
| `description` | string | X | `""` | 설명 |

**Response** `201 Created`

---

### DELETE /api/namespaces/{name}

네임스페이스와 관련 데이터를 모두 삭제한다.

**Path Parameter**: `name` (string)

**Response** `204 No Content`

**Error** `404 Not Found`

---

## 10. LLM 설정 (LLM Settings)

### GET /api/llm/config

현재 LLM 프로바이더 설정을 반환한다.

**Response** `200 OK`

```json
{
  "provider": "ollama",
  "ollama_base_url": "http://host.docker.internal:11434",
  "ollama_model": "exaone3.5:2.4b",
  "ollama_timeout": 120,
  "inhouse_llm_url": null,
  "inhouse_llm_model": null,
  "inhouse_llm_timeout": 120,
  "is_connected": true
}
```

---

### PUT /api/llm/config

LLM 프로바이더를 전환하거나 설정을 변경한다.

**Request Body** — `LLMConfigUpdate`

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `provider` | string | O | `ollama` \| `inhouse` |
| `ollama_base_url` | string | X | Ollama 서버 URL |
| `ollama_model` | string | X | Ollama 모델명 |
| `ollama_timeout` | int | X | 타임아웃 (초) |
| `inhouse_llm_url` | string | X | 사내 LLM URL |
| `inhouse_llm_api_key` | string | X | API 키 |
| `inhouse_llm_model` | string | X | 모델명 |
| `inhouse_llm_timeout` | int | X | 타임아웃 (초) |

**Response** `200 OK` — 갱신된 설정 + `is_connected`

**Error** `400 Bad Request` (유효하지 않은 provider), `422 Unprocessable Entity`

---

### POST /api/llm/test

LLM 프로바이더 연결을 테스트한다. 실제 전환하지 않는다.

**Request Body** — `LLMTestRequest`

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `provider` | string | O | 테스트할 프로바이더 |
| `ollama_base_url` | string | X | Ollama URL |
| `ollama_model` | string | X | 모델명 |
| `inhouse_llm_url` | string | X | 사내 LLM URL |
| `inhouse_llm_api_key` | string | X | API 키 |
| `inhouse_llm_model` | string | X | 모델명 |

**Response** `200 OK`

```json
{
  "is_connected": true,
  "provider": "ollama",
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

## 11. 공통 에러 코드

| HTTP 상태 코드 | 의미 | 설명 |
|----------------|------|------|
| `200` | OK | 정상 처리 |
| `201` | Created | 리소스 생성 완료 |
| `204` | No Content | 삭제 완료 (응답 본문 없음) |
| `400` | Bad Request | 잘못된 요청 파라미터 |
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
