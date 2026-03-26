# Knowledge RAG 파이프라인

> **Version**: v2.11 | **최종 수정**: 2026-03-24

---

## 전체 흐름도

```
사용자 질문
    │
    ▼
┌─────────────────────────────────────┐
│ 1. 멀티턴 검색 보강                   │
│    최근 Q&A 2개 + 현재 질문 결합       │
│    (각 80자 제한)                     │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 2. 임베딩 생성 (768차원)              │
│    query_vec (검색용)                 │
│    cache_vec (캐시용)     ← 병렬 생성  │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 3. 시맨틱 캐시 조회 (Redis)           │
│    코사인 유사도 ≥ 0.88 → HIT        │
│    TTL: 30분                         │
├──────────┬──────────────────────────┤
│  HIT ✓   │  MISS ✗                  │
│  즉시반환  │  ↓ 계속                  │
└──────────┴──────────┬───────────────┘
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
┌──────────────────┐  ┌──────────────────┐
│ 4a. 용어 매핑     │  │ 4b. 대화 이력 구성 │
│ 용어집 벡터 검색   │  │ 과거 요약 리콜    │
│ 유사도 ≥ 0.5     │  │ (≥0.45, 최대 2건) │
│                  │  │ + 최근 메시지 4개  │
└────────┬─────────┘  └────────┬─────────┘
         └───────┬─────────────┘
                 │  asyncio.gather (병렬)
          ┌──────┴──────┐
          ▼             ▼
┌──────────────────┐  ┌──────────────────┐
│ 5. 하이브리드 검색 │  │ 6. Few-shot 검색  │
│ 벡터 0.7 가중치   │  │ 유사 Q&A 사례     │
│ 키워드 0.3 가중치  │  │ 최대 2건          │
│ 상위 5건          │  │ 유사도 ≥ 0.5     │
└────────┬─────────┘  └────────┬─────────┘
         └───────┬─────────────┘
                 │  asyncio.gather (병렬)
                 ▼
┌─────────────────────────────────────┐
│ 7. LLM 답변 생성 (SSE 스트리밍)      │
│                                     │
│ System: 참고문서 기반 답변 원칙        │
│ Context:                            │
│   [과거 유사 사례] ← Few-shot        │
│   [참고 문서 1~5] ← 하이브리드 검색   │
│   [대화 이력]     ← 메모리           │
│                                     │
│ 20토큰마다 DB 업데이트                │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ 8. 후처리                            │
│    시맨틱 캐시 저장 (Redis)           │
│    쿼리 로그 생성                     │
│    4교환마다 대화 요약 → pgvector 저장 │
└──────────────┬──────────────────────┘
               │
               ▼
         최종 응답 반환
```

---

## 각 단계 상세

### 1. 멀티턴 검색 보강

| 항목 | 내용 |
|------|------|
| **파일** | `backend/agents/knowledge_rag/agent.py` |
| **입력** | 사용자 질문 + conversation_id |
| **출력** | search_question (문맥 보강된 질문) |
| **동작** | 직전 Q&A 2개(최대 80자씩)를 현재 질문에 결합 |
| **목적** | "그거 다시 알려줘" 같은 대명사 참조 해결 |

### 2. 임베딩 생성

| 항목 | 내용 |
|------|------|
| **파일** | `backend/shared/embedding.py` |
| **모델** | `paraphrase-multilingual-mpnet-base-v2` (768차원) |
| **출력** | query_vec (검색용), cache_vec (캐시용) — 병렬 생성 |
| **방식** | normalize_embeddings=True (코사인 유사도 사용) |

### 3. 시맨틱 캐시 조회

| 항목 | 내용 |
|------|------|
| **파일** | `backend/shared/cache.py` |
| **저장소** | Redis |
| **유사도 임계값** | 0.88 (한국어 단문 최적) |
| **TTL** | 30분 |
| **최대 비교 대상** | 200건 |
| **HIT 시** | 캐시된 답변 즉시 반환 (LLM 호출 없음) |
| **MISS 시** | 4단계부터 계속 |
| **키 형식** | `semcache:{namespace}:{MD5 hash}` |

### 4a. 용어 매핑 (Glossary Term Mapping)

| 항목 | 내용 |
|------|------|
| **파일** | `backend/agents/knowledge_rag/knowledge/retrieval.py` |
| **동작** | 질문 임베딩과 용어집 임베딩 비교 → 가장 가까운 용어 1개 반환 |
| **유사도 임계값** | 0.5 이상 |
| **출력** | `GlossaryMatch(term, description, similarity)` 또는 None |
| **활용** | 검색 쿼리 보강 + LLM 프롬프트에 용어 설명 전달 |

### 4b. 대화 이력 구성 (Memory)

| 항목 | 내용 |
|------|------|
| **파일** | `backend/service/chat/memory.py` |
| **과거 요약 리콜** | 질문과 유사한 과거 요약 최대 2건 (유사도 ≥ 0.45) |
| **최근 메시지** | 최근 2교환(4 메시지) 로드 |
| **요약 생성 트리거** | 4교환 이상 누적 시 자동 요약 → pgvector 저장 |
| **요약 범위** | 최근 2교환 제외한 이전 부분만 요약 |

### 5. 하이브리드 검색

| 항목 | 내용 |
|------|------|
| **파일** | `backend/agents/knowledge_rag/knowledge/retrieval.py` |
| **벡터 검색** | pgvector `<=>` 연산자 (L2 거리 역함수) |
| **키워드 검색** | PostgreSQL `ts_rank` + `tsvector` (full-text search) |
| **가중치** | 벡터 0.7 + 키워드 0.3 |
| **최종 점수** | `(w_vector * v_score + w_keyword * k_score) * (1.0 + base_weight)` |
| **상위 결과** | 5건 |
| **신뢰도** | 높음 ≥ 0.7 / 보통 ≥ 0.5 / 낮음 < 0.5 |

### 6. Few-shot 검색

| 항목 | 내용 |
|------|------|
| **파일** | `backend/agents/knowledge_rag/knowledge/retrieval.py` |
| **동작** | 유사한 과거 Q&A 사례 검색 |
| **유사도 임계값** | 0.5 이상 |
| **최대 결과** | 2건 |
| **활용** | LLM 프롬프트의 "[과거 유사 질문 답변 사례]" 섹션 |

### 7. LLM 답변 생성

| 항목 | 내용 |
|------|------|
| **파일** | `backend/agents/knowledge_rag/agent.py` |
| **방식** | SSE 스트리밍 (토큰 단위) |
| **DB 업데이트** | 1번째 토큰 + 이후 20토큰마다 |
| **LLM Provider** | Ollama (로컬) / 사내 LLM (DevX MCP API) |

**프롬프트 구조:**
```
[System]
- 참고 문서 기반 답변 원칙
- Markdown 형식 (표, 목록, 코드블록)
- 근거 표시: 📎 문서 N 참고

[Context]
- [과거 유사 사례] ← Few-shot 2건
- [참고 문서 1~5] ← 하이브리드 검색 (점수/신뢰도 표시)
- [대화 이력]     ← 메모리 (요약 + 최근 메시지)

[User]
- 사용자 질문
```

### 8. 후처리

| 항목 | 내용 |
|------|------|
| **캐시 저장** | LLM 정상 응답 시만 Redis에 저장 |
| **쿼리 로그** | `ops_query_log`에 질문/답변/상태 기록 |
| **대화 요약** | 4교환마다 LLM으로 3~5문장 요약 → 임베딩 + pgvector 저장 |

---

## 주요 파라미터

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `w_vector` | 0.7 | 하이브리드 검색 벡터 가중치 |
| `w_keyword` | 0.3 | 하이브리드 검색 키워드 가중치 |
| `top_k` | 5 | 검색 결과 상위 개수 |
| `glossary_min_similarity` | 0.5 | 용어 매핑 최소 유사도 |
| `fewshot_min_similarity` | 0.5 | Few-shot 최소 유사도 |
| `knowledge_high_score` | 0.7 | 신뢰도 "높음" 기준 |
| `knowledge_mid_score` | 0.5 | 신뢰도 "보통" 기준 |
| `SUMMARY_TRIGGER` | 4 | 요약 생성 교환 수 |
| `RECENT_EXCHANGES` | 2 | 최근 메시지 교환 수 |
| `MAX_RECALL` | 2 | 과거 요약 리콜 개수 |
| `RECALL_THRESHOLD` | 0.45 | 과거 요약 유사도 임계값 |
| `cache_similarity_threshold` | 0.88 | 캐시 HIT 유사도 |
| `cache_ttl` | 1800 | 캐시 TTL (초) |

---

## LLM 호출 비용

| 상황 | LLM 호출 횟수 |
|------|-------------|
| 캐시 HIT | **0회** |
| 일반 질문 | **1회** (답변 생성) |
| 4교환 도달 | **2회** (답변 생성 + 대화 요약) |

---

## 병렬 처리 구간

```
4a 용어 매핑 ─┐
              ├─ asyncio.gather ─┐
4b 대화 이력 ──┘                  │
                                 │
5 하이브리드 검색 ─┐               ├─ 7. LLM 생성
                  ├─ asyncio.gather
6 Few-shot 검색 ──┘
```

---

## 관련 DB 테이블

```
ops_namespace
  ├── rag_knowledge       지식베이스 (content + embedding)
  ├── rag_glossary        용어집 (term + description + embedding)
  └── rag_fewshot         Few-shot 예제 (question + answer + embedding)

ops_conversation
  ├── ops_message         대화 메시지 (user/assistant)
  ├── rag_conv_summary    대화 요약 (summary + embedding)
  └── ops_query_log       질문 로그 (question + answer + status)
```

---

## 관련 소스 파일

| 파일 | 역할 |
|------|------|
| `backend/agents/knowledge_rag/agent.py` | 파이프라인 오케스트레이션 |
| `backend/agents/knowledge_rag/knowledge/retrieval.py` | 용어 매핑 + 하이브리드 검색 + Few-shot |
| `backend/service/chat/memory.py` | 대화 요약 + 시맨틱 리콜 |
| `backend/shared/cache.py` | 시맨틱 캐시 (Redis) |
| `backend/shared/embedding.py` | 임베딩 생성 (Sentence-Transformers) |
| `backend/service/llm/factory.py` | LLM 프로바이더 팩토리 |
| `backend/service/chat/helpers.py` | 메시지 업데이트 + 로깅 |
