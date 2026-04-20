# Ops-Navigator 시스템 흐름도

## 1. 질문 처리 전체 흐름

```
사용자
  │
  │  자연어 질문 입력
  │  "쿠폰 뺏어오기 실패한 건 어떻게 확인해?"
  ▼
┌─────────────────────┐
│   React Frontend    │
│   (Chat 페이지)      │
│                     │
│  - namespace 선택    │
│  - 벡터/키워드 비중  │
│  - Top-K 설정       │
└────────┬────────────┘
         │  POST /api/chat/stream  (SSE)
         │  { namespace, question, w_vector, w_keyword, top_k }
         ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI Backend                                            │
│                                                             │
│  chat/router.py → AgentRegistry.get(agent_type)             │
│       → agent.stream_chat(query, user, context)             │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Step 0-A: Semantic Cache 조회 (Redis)              │   │
│  │                                                     │   │
│  │  query_vec로 Redis 스캔 (semcache:{ns}:*)           │   │
│  │  코사인 유사도 ≥ 0.97 히트 → 저장된 답변 즉시 반환  │   │
│  │  미스 → Step 0-B 이후 정상 파이프라인 실행          │   │
│  │  Redis 미연결 시 이 단계 무시 (graceful degradation) │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Step 0-B: Multi-turn Search Enrichment             │   │
│  │  (멀티턴 검색 보강 — 2턴 이상 대화에서 자동 적용)      │   │
│  │                                                     │   │
│  │  현재 질문: "그 쿼리 알려줘"                         │   │
│  │      │                                              │   │
│  │      ▼  DB에서 직전 Q+A 조회 (ops_message)           │   │
│  │  prev_Q = "쿠폰 회수 실패 확인 방법?"[:80]           │   │
│  │  prev_A = "ops_coupon 테이블에서 status..."[:80]     │   │
│  │      │                                              │   │
│  │      ▼  검색용 질문 결합                             │   │
│  │  search_question = "{prev_Q} {prev_A} {현재 질문}"  │   │
│  │  → 이 결합된 텍스트로 임베딩 & 검색 수행              │   │
│  │  → 추가 LLM 호출 없음, DB 1회 조회 (<1ms)           │   │
│  │                                                     │   │
│  │  ※ 첫 질문이면 search_question = 현재 질문 그대로    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Step 1: Semantic Glossary Mapping                   │   │
│  │                                                     │   │
│  │  질문 텍스트 (또는 search_question)                   │   │
│  │      │                                              │   │
│  │      ▼                                              │   │
│  │  EmbeddingService.embed()                           │   │
│  │  "쿠폰 뺏어오기 실패한 건 어떻게 확인해?"            │   │
│  │      │  → vector [0.12, -0.34, ...] (768차원)       │   │
│  │      ▼                                              │   │
│  │  ops_glossary 벡터 유사도 검색                       │   │
│  │  SELECT term ORDER BY embedding <=> $query_vec      │   │
│  │      │  → mapped_term: "회수"                       │   │
│  │      ▼                                              │   │
│  │  enriched_query = "쿠폰 뺏어오기 실패한 건 어떻게 확인해? 회수"  │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Step 2: Weighted Hybrid Search                     │   │
│  │                                                     │   │
│  │  enriched_query 임베딩                              │   │
│  │      │                                              │   │
│  │      ├──────────────────────────────────────────┐  │   │
│  │      │ Vector Search                            │  │   │
│  │      │ 1 - (embedding <=> query_vec)            │  │   │
│  │      │ → v_score (0~1, 코사인 유사도)            │  │   │
│  │      │                                          │  │   │
│  │      │ Keyword Search                           │  │   │
│  │      │ ts_rank(to_tsvector, plainto_tsquery)    │  │   │
│  │      │ → k_score (BM25 기반 TF-IDF)             │  │   │
│  │      ▼                                          │  │   │
│  │  final_score = (w_vec × v_score + w_kw × k_score) │  │
│  │               × (1 + base_weight)              │  │   │
│  │                                                │  │   │
│  │  ORDER BY final_score DESC LIMIT top_k         │  │   │
│  └─────────────────────────────────────────────────┘   │   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Step 3: LLM 답변 생성                              │   │
│  │                                                     │   │
│  │  build_fewshot_section(fewshots)                    │   │
│  │  → 유사 Q&A 사례를 프롬프트 앞부분에 포맷            │   │
│  │                                                     │   │
│  │  build_context(results)                             │   │
│  │  → 검색된 문서들을 프롬프트 형식으로 포맷             │   │
│  │                                                     │   │
│  │  build_messages(context, question, history)          │   │
│  │  → [system + 과거요약] + [최근2회 교환] + [현재 질문] │   │
│  │                                                     │   │
│  │  LLMProvider.generate(context, question, history)   │   │
│  │  POST /api/chat (messages 배열, multi-turn)          │   │
│  │  → LLM 답변 텍스트                                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ops_query_log에 질의 기록 (namespace, question, status)    │
└─────────────────────────────────────────────────────────────┘
         │
         │  SSE 이벤트 스트림 (status → meta → token → done)
         ▼
┌─────────────────────┐
│   React Frontend    │
│                     │
│  - 단계별 진행 표시  │
│  - 용어 매핑 표시    │
│  - 결과 카드 렌더링  │
│    (컨테이너, 테이블, SQL) │
│  - AI 답변 Markdown │
│    렌더링 (테이블,  │
│    코드블록, 리스트) │
│  - 👍/👎 피드백 버튼 │
└─────────────────────┘
```

---

## 2. 벡터 DB (pgvector) 동작 원리

### 임베딩이란

텍스트를 768개의 숫자 배열(벡터)로 변환한 것이다.
의미가 비슷한 텍스트일수록 벡터 공간에서 가까운 위치에 배치된다.

```
"쿠폰 회수"     → [0.12, -0.34, 0.87, ...]  ─┐ 벡터 공간에서 가깝다
"쿠폰 강제 반납" → [0.11, -0.31, 0.89, ...]  ─┘

"배송 조회"     → [-0.54, 0.22, -0.11, ...]  ─ 위와 멀다
```

### 저장 구조

```
ops_knowledge 테이블
┌────┬───────────┬─────────────────────────┬──────────────────────────────┐
│ id │ namespace │ content                 │ embedding (VECTOR 768)       │
├────┼───────────┼─────────────────────────┼──────────────────────────────┤
│  1 │ coupon    │ "쿠폰 강제 회수 처리..."  │ [0.12, -0.34, 0.87, ... 768개] │
│  2 │ coupon    │ "쿠폰 발급 오류 대응..."  │ [0.23,  0.11, 0.45, ... 768개] │
│  3 │ gift      │ "선물 발송 실패 처리..."  │ [-0.05, 0.67, 0.31, ... 768개] │
└────┴───────────┴─────────────────────────┴──────────────────────────────┘
                                                         ▲
                                                HNSW 인덱스로 빠른 검색
```

### HNSW 인덱스 (Hierarchical Navigable Small World)

```
전체 벡터 중 가장 유사한 것 찾기 = ANN(Approximate Nearest Neighbor)

인덱스 없이:  모든 문서와 거리 계산 → O(N) → 문서 수 늘수록 느려짐
HNSW 사용:    계층적 그래프 탐색    → O(log N) → 수십만 문서도 빠름

인덱스 생성:
CREATE INDEX ON ops_knowledge USING hnsw (embedding vector_cosine_ops);
→ vector_cosine_ops: 코사인 거리 기준으로 인덱스 구성
```

### 코사인 유사도 계산

```
거리 = embedding <=> query_vector      (pgvector 연산자)
점수 = 1 - 거리                         (유사도: 1에 가까울수록 비슷)

normalize_embeddings=True 적용 시:
  모든 벡터의 크기(norm) = 1
  → 코사인 유사도 = 내적 = 1 - L2거리 (수학적으로 동일)
  → 계산이 더 빠르고 안정적
```

### 지식 등록 시 임베딩 생성

```
관리자 지식 등록
  │  content = "쿠폰 강제 회수 처리 방법은..."
  ▼
EmbeddingService.embed(content)
  │  SentenceTransformer("paraphrase-multilingual-mpnet-base-v2")
  │  → 모델이 텍스트를 768차원 공간에 매핑
  │  → normalize_embeddings=True 적용
  ▼
[0.12, -0.34, 0.87, 0.45, -0.22, ...] (768개 float)
  │
  ▼
INSERT INTO ops_knowledge (content, embedding, ...)
  │
  ▼
HNSW 인덱스에 자동 반영 → 즉시 검색 가능
```

---

## 3. 용어집 등록 및 검색 반영 흐름

### 용어집이 필요한 이유

같은 의미라도 사람마다 다르게 표현한다:

```
실제 시스템 용어: "회수"
사용자 표현:
  - "뺏어오기"
  - "강제 반납"
  - "쿠폰 취소"
  - "발급 취소"

→ "뺏어오기"로 검색하면 "회수"가 포함된 문서를 못 찾을 수 있음
→ 용어집이 이를 브리지함
```

### 용어 등록 흐름

```
관리자
  │  { namespace: "coupon", term: "회수",
  │    description: "쿠폰 회수, 뺏어오기, 강제 반납, 강제 취소 등..." }
  ▼
POST /api/knowledge/glossary
  │
  ▼
knowledge.py
  │  1. EmbeddingService.embed(description)
  │     description의 의미 벡터 생성
  │     (용어 자체가 아닌 설명 전체를 임베딩 → 다양한 표현 포괄)
  │
  │  2. INSERT INTO ops_glossary
  │     (namespace, term, description, embedding)
  ▼
ops_glossary 테이블
┌────┬───────────┬───────┬───────────────────────────────┬───────────┐
│ id │ namespace │ term  │ description                   │ embedding │
├────┼───────────┼───────┼───────────────────────────────┼───────────┤
│  1 │ coupon    │ 회수  │ "쿠폰 회수, 뺏어오기, 강제..."  │ [...]     │
│  2 │ coupon    │ 발급  │ "쿠폰 지급, 발행, 부여..."       │ [...]     │
└────┴───────────┴───────┴───────────────────────────────┴───────────┘
```

### 검색 시 용어 매핑 흐름

```
사용자 질문: "쿠폰 뺏어오기 실패한 거 어떻게 확인해?"
  │
  ▼
질문 임베딩 생성
  "쿠폰 뺏어오기 실패한 거 어떻게 확인해?" → query_vec [...]
  │
  ▼
ops_glossary 벡터 검색 (namespace 필터)
  SELECT term FROM ops_glossary
  WHERE namespace = 'coupon'
  ORDER BY embedding <=> query_vec
  LIMIT 1
  │
  │  "뺏어오기"와 "회수" 설명의 벡터가 가장 가까움
  ▼
  mapped_term = "회수"
  │
  ▼
검색어 강화 (Query Enrichment)
  enriched_query = "쿠폰 뺏어오기 실패한 거 어떻게 확인해? 회수"
  │             ← 원본 질문 + 표준 용어 추가
  ▼
강화된 쿼리로 ops_knowledge 하이브리드 검색
  → "회수" 키워드가 포함된 문서 우선 반환
  → 결과 카드에 "🔤 용어 매핑: 회수" 표시
```

### 용어집이 없을 때 vs 있을 때

```
용어집 없음:
  질문: "뺏어오기"
  검색어: "쿠폰 뺏어오기 실패한 거 어떻게 확인해?" (원본만)
  → "뺏어오기" 단어가 지식에 없으면 키워드 매치 실패
  → 벡터 유사도만으로 부족할 수 있음

용어집 있음:
  질문: "뺏어오기"
  검색어: "쿠폰 뺏어오기 실패한 거 어떻게 확인해? 회수" (강화됨)
  → "회수" 키워드가 지식에 매치
  → 벡터 + 키워드 모두 높은 점수
  → 정확한 결과 반환
```

---

## 4. 대화 메모리 흐름 (ConversationSummaryBuffer + Semantic Recall)

대화가 길어져도 LLM 컨텍스트 윈도우를 넘기지 않으면서 관련 맥락을 유지하는 전략이다.

### 메모리 구성 원리

```
대화방 (conversation_id = 42)
─────────────────────────────────────────────────

교환 1: Q "쿠폰 회수 방법?" → A "..."
교환 2: Q "그 쿼리 테이블 알려줘" → A "..."
교환 3: Q "에러 코드 COUPON_001은?" → A "..."
교환 4: Q "그럼 실패 원인은?" → A "..."   ← 4회 도달, 요약 트리거
  │
  ▼
maybe_summarize(conv_id, llm_provider)
  │  교환 1~2를 LLM으로 요약
  │  → "사용자가 쿠폰 회수 방법과 관련 테이블을 질문함..."
  │  → summary 임베딩 생성 → ops_conv_summary INSERT
  │     (conversation_id, summary, embedding, turn_start=1, turn_end=2)
  │
  ▼
교환 5: Q "선물 발송 오류는?" (새 질문)
  │
  ├─ build_context_history(conv_id, query_vec)
  │     │
  │     ├─ 최근 2회 raw 교환 (교환 3, 4) → working memory (항상 포함)
  │     │
  │     └─ ops_conv_summary 벡터 검색 (유사도 ≥ 0.45, 최대 2개)
  │           "선물 발송 오류" 벡터 vs 과거 요약 벡터
  │           → 쿠폰 관련 요약은 유사도 낮아 제외됨
  │
  ▼
LLM에 전달되는 messages:
  [system: 시스템프롬프트 + 검색 결과]
  ← 과거 요약은 유사도 미달로 미포함
  [user: "에러 코드 COUPON_001은?"]      ← 최근 2회 (working memory)
  [assistant: "..."]
  [user: "그럼 실패 원인은?"]
  [assistant: "..."]
  [user: "선물 발송 오류는?"]            ← 현재 질문
```

### 요약 트리거 조건

```
총 교환 횟수 (user+assistant 쌍) ÷ SUMMARY_INTERVAL(4) > 기존 요약 수
→ 아직 요약하지 않은 오래된 교환을 LLM으로 요약
→ 최근 KEEP_RECENT(2)회 교환은 항상 raw로 유지
→ 요약은 _post_save_tasks()에서 비동기 백그라운드 실행
```

### Semantic Recall이 효과적인 경우

```
대화방에서 10번째 교환 중:
  과거 교환 1~2에서 "쿠폰 회수 쿼리" 논의 → 요약 저장됨
  현재 교환 10: "아까 쿠폰 관련 쿼리 다시 알려줘"
  → query_vec와 과거 요약 벡터 유사도 0.72 → 리콜됨
  → LLM이 과거 맥락을 참고하여 정확한 답변 생성
```

---

## 5. 지식 등록 흐름

```
관리자
  │
  │  지식 등록 폼 입력
  │  { namespace, container_name, target_tables, content, query_template, base_weight }
  ▼
┌─────────────────────┐
│   Admin (React)     │
└────────┬────────────┘
         │  POST /api/knowledge
         ▼
┌─────────────────────────────────────────┐
│  knowledge.py (서비스 레이어)            │
│                                         │
│  1. EmbeddingService.embed(content)     │
│     → 768차원 벡터 생성                  │
│                                         │
│  2. INSERT INTO ops_knowledge           │
│     (content, embedding, base_weight, …)│
│                                         │
│  3. Return KnowledgeOut                 │
└─────────────────────────────────────────┘
         │
         │  { id, namespace, content, embedding, ... }
         ▼
┌─────────────────────┐
│   Admin (React)     │
│   목록에 즉시 반영   │
└─────────────────────┘
```

---

## 6. 통계 수집 전체 사이클

### 질의 → 로그 → 피드백 → 통계 흐름

```
① 사용자 질문
   │
   ▼
POST /api/chat
   │
   ▼
검색 수행 (hybrid_search)
   │  results = [문서1, 문서2, ...]
   │
   ▼
ops_query_log INSERT
   ┌──────────────────────────────────────────────────────────┐
   │ namespace  │ question          │ status     │ created_at │
   ├──────────────────────────────────────────────────────────┤
   │ coupon     │ "쿠폰 뺏어오기..."  │ pending    │ 2024-01-15 │
   │            │  (검색 결과 있음)  │            │            │
   ├──────────────────────────────────────────────────────────┤
   │ gift       │ "알 수 없는 오류"  │ unresolved │ 2024-01-15 │
   │            │  (검색 결과 없음)  │            │            │
   └──────────────────────────────────────────────────────────┘
   pending    : LLM이 정상 답변을 생성했지만 아직 피드백 없음
   resolved   : 👍 긍정 피드백으로 확인됨
   unresolved : 검색 결과 0건 AND LLM 답변도 실패, 또는 👎 부정 피드백

② 사용자 피드백 (품질 자동 개선)

   👍 좋아요 클릭
   │
   ▼  POST /api/feedback { knowledge_id, is_positive: true, answer: "AI 답변 텍스트" }
   │
   ├─ ops_feedback INSERT  (로그)
   ├─ UPDATE ops_knowledge SET base_weight = LEAST(base_weight + 0.1, 5.0)
   │   WHERE id = knowledge_id
   │   → 이 문서가 이후 동일 주제 검색에서 더 높은 점수를 받음 (검색 랭킹 상승)
   ├─ UPDATE ops_query_log SET status = 'resolved'
   │   → pending → resolved 전환 (피드백으로 품질 확인됨)
   └─ INSERT INTO ops_fewshot (namespace, question, answer, knowledge_id, embedding, status='candidate')
       → 질문 임베딩 생성 후 저장 (status='candidate' — 어드민 검토 후 'active' 승인 필요)
       → status='active'인 few-shot만 LLM 프롬프트에 "[과거 유사 질문 답변 사례]"로 주입

   👎 싫어요 클릭
   │
   ▼  POST /api/feedback { knowledge_id, is_positive: false }
   │
   ├─ ops_feedback INSERT  (로그)
   ├─ UPDATE ops_knowledge SET base_weight = GREATEST(base_weight - 0.1, 0.0)
   │   → 이 문서가 이후 검색에서 낮은 점수를 받음 (검색 랭킹 하락)
   └─ UPDATE ops_query_log SET status = 'unresolved'
       → "결과는 나왔지만 내용이 틀렸음" → 미해결로 재분류
       → 통계 대시보드 미해결 케이스에 표시

③ Few-shot 활용 흐름

   다음 사용자가 유사한 질문 입력
   │
   ▼  질문 임베딩 생성 → query_vec
   │
   ▼  ops_fewshot 벡터 검색 (유사도 ≥ 0.6, 최대 2건)
   │   SELECT question, answer FROM ops_fewshot
   │   WHERE namespace = ? AND 1-(embedding <=> query_vec) >= 0.6
   │   ORDER BY embedding <=> query_vec LIMIT 2
   │
   ▼  LLM 프롬프트 구성
   │   [과거 유사 질문 답변 사례]
   │   Q: "쿠폰 뺏어오기 실패한 건 어떻게 확인해?"
   │   A: "ops_feedback 테이블에서 is_positive=false 건을 조회하세요..."
   │
   │   [참고 문서]
   │   --- 문서 1 (점수: 0.8432) ---
   │   ...
   │
   ▼  LLM이 과거 좋은 답변 패턴을 참고하여 더 나은 답변 생성

④ 통계 집계
   │  Admin > 통계 탭 클릭
   ▼
GET /api/stats
   │  WITH all_ns (ops_namespace UNION ops_knowledge UNION ops_glossary)
   │  LEFT JOIN q_agg  (질의 수, 해결/대기/미해결)
   │  LEFT JOIN fb_agg (좋아요/싫어요 수)
   │  LEFT JOIN k_agg  (지식 문서 수)
   │  LEFT JOIN g_agg  (용어집 항목 수)
   ▼

⑤ 대시보드 렌더링
   │
   ├─ KPI 카드  (총 질의 수 / 해결 / 대기 중 / 미해결 건수)
   ├─ 지식 베이스 현황 테이블  (namespace별 지식·용어집 개수)
   ├─ 질의 처리 현황  (해결/대기/미해결 도넛 차트)
   ├─ 피드백 현황    (👍/👎 그룹 바차트 + 만족도 텍스트 바)
   └─ 미해결 케이스 목록  (지식 보완 가이드 + 취약 namespace 자동 알림)

⑤ 미해결 케이스 → 지식 보완 사이클

   미해결 케이스 확인
      │
      ├─ 해당 질문 유형의 지식 없음 → 지식 베이스에 신규 등록
      ├─ 지식 있지만 내용 부족     → 기존 지식 수정
      └─ 용어 표현 문제            → 용어집에 유의어 추가
      │
      ▼
   다음 동일 질문에서 status = 'resolved' 로 기록
```

### 해결률이 낮을 때 체크리스트

```
해결률 = resolved 건수 / total_queries × 100  (pending은 미확인 상태)

낮은 경우 원인:
  1. 해당 namespace에 지식 등록 부족
     → Admin > 지식 베이스에서 신규 등록

  2. 용어 표현 불일치
     → Admin > 용어집에 유의어 추가

  3. 질문 패턴이 기존 지식과 의미적으로 멀리 떨어짐
     → 해당 표현을 그대로 포함한 지식 등록
     → 또는 벡터 비중 낮추고 키워드 비중 높임 (슬라이더)
```

---

## 7. SSE 스트리밍 응답 흐름 (`/api/chat/stream`)

**asyncio.Task + Queue 디커플링** 방식으로 LLM 생성이 HTTP 연결 수명에서 완전히 분리된다.
클라이언트가 연결을 끊어도(새 대화, 탭 닫기 등) 백엔드 워커가 독립적으로 끝까지 실행하여 DB에 저장한다.

```
React Frontend                     Backend chat_stream()
─────────────────────────          ──────────────────────────────────────────

POST /api/chat/stream              ── HTTP 핸들러 (동기) ──
  { namespace, question,           │  _get_or_create_conversation(...)
    w_vector, w_keyword, top_k }   │  _cleanup_ghost_messages(conv_id)
                                   │  _save_user_message(conv_id, question)
                                   │  _pre_create_assistant_message(conv_id) → msg_id (status='generating')
                                   │  agent = AgentRegistry.get(agent_type)
                                   │  queue = asyncio.Queue()
                                   │  asyncio.create_task(agent.stream_chat(queue, ...))
                                   ▼
                                   ── event_generator (SSE 스트리밍) ──

                           ◀──── data: {"type":"meta",
[Early Meta: 0.02초 내 전송]              "conversation_id": 42,
                                          "message_id": 123,
                                          "mapped_term": null,
                                          "results": []}
                                   │
                                   │  queue.get() → worker가 넣은 이벤트 전달
                                   ▼
                           ◀──── data: {"type":"status","step":"embedding",...}
[파이프라인 진행 표시]       ◀──── data: {"type":"status","step":"context",...}
                           ◀──── data: {"type":"status","step":"search",...}
                                   ▼
                           ◀──── data: {"type":"meta",
[결과 카드 즉시 렌더링]              "mapped_term":"회수",
                                    "results":[...]}
                                   ▼
                           ◀──── data: {"type":"status","step":"llm",...}
                           ◀──── data: {"type":"token","data":"쿠폰"}
[답변 텍스트 스트리밍 출력]  ◀──── data: {"type":"token","data":" 회수"}
                                   ...  (20토큰마다 DB 부분 저장)
                                   ▼
                           ◀──── data: {"type":"done","message_id": 123}
[완료 — 피드백 버튼 활성화]        │  DB: status='completed'
```

**클라이언트 연결 끊김 처리 (asyncio.Task 디커플링):**
- `event_generator`에서 `GeneratorExit`/`CancelledError` 발생 → SSE 전송만 중단
- `_generate_worker` 태스크는 Queue에 독립적으로 이벤트를 넣으며 끝까지 실행
- 워커 완료 시 DB에 `status='completed'` 저장 + `queue.put(None)` (EOF)
- 프론트엔드가 이전 대화로 돌아오면 `status='generating'` 감지 → 3초 polling → 자동 갱신

**사이드바 대화 목록 타이밍:**
- 스트리밍 중 대화 목록 갱신 억제 (질문 즉시 목록에 나타나지 않음)
- 스트림 완료(active → false) 시에만 대화 목록 갱신 → 자연스러운 UX

**이벤트 타입 요약:**

| type | 발행 시점 | 프론트엔드 처리 |
|------|----------|---------------|
| `status` | 각 파이프라인 단계 시작 시 (embedding/context/search/llm) | 단계별 진행 표시 (토글 UI) |
| `meta` | ① Early Meta (DB insert 직후, 0.02초) ② 검색 완료 후 | ①에서 conversation_id/message_id 수신, ②에서 결과 카드 렌더링 |
| `token` | LLM 토큰마다 | 답변 영역에 토큰 누적 출력 |
| `done` | 모든 처리 완료 (DB status=completed) | message_id 수신, 피드백 버튼 활성화 |

---

## 8. 하이브리드 검색 점수 계산 상세 (SQL 레벨)

```
입력:
  w_vector = 0.7   (벡터 비중, 사용자 슬라이더)
  w_keyword = 0.3  (키워드 비중, 자동 보완)
  base_weight = 1.0 (문서별 가중치)

벡터 점수 (v_score):
  코사인 유사도 = 1 - cosine_distance
  범위: 0.0 ~ 1.0
  → 의미가 유사할수록 높음

키워드 점수 (k_score):
  ts_rank(FTS 벡터, 검색어)
  범위: 0.0 ~ (이론상 무제한, 보통 0~0.1)
  → 정확한 단어 매치일수록 높음

최종 점수:
  final_score = (0.7 × v_score + 0.3 × k_score) × (1 + base_weight)
              = (0.7 × v_score + 0.3 × k_score) × 2.0  (base_weight=1.0 기본값)

필터 조건:
  - v_score IS NOT NULL OR k_score IS NOT NULL
  → 완전히 관련 없는 문서는 제외
  → 벡터 또는 키워드 중 하나라도 매치된 경우만 반환
```

---

## 9. LLM Provider 전환 흐름

### 런타임 전환 (Admin UI — 재시작 불필요)

```
관리자
  │  Admin → LLM 설정 탭
  │  프로바이더 선택 + 설정값 입력 + "저장 및 적용"
  ▼
PUT /api/llm/config
  │  { provider, ollama_base_url, ... }
  ▼
services/llm/__init__.py — switch_provider(config)
  │  _runtime_config = config  (전역 저장)
  │  _provider = None          (싱글톤 초기화)
  │  _create_provider()        (새 인스턴스 생성)
  ▼
provider.health_check()         (연결 확인)
  ▼
{ is_connected, is_runtime_override: true, ... } 반환

이후 모든 LLM 호출은 새 프로바이더 인스턴스 사용
컨테이너 재시작 시 .env 설정으로 복귀
```

---

## 10. MCP 도구 에이전트 플로우

`agent_type=mcp_tool` 로 요청 시 `McpToolAgent`가 처리. 3가지 진입 케이스.

```
Case 3 — 첫 진입 (approved_tool·selected_tool_id 없음)
  ① _fetch_active_tools (네임스페이스 활성 도구 목록)
  ② _select_tool → LLM이 도구 선택 + 파라미터 추출
  ③ SSE: tool_request 이벤트 emit → 스트림 종료
      action=confirm        → 파라미터 완성, 사용자 승인 대기
      action=missing_params → 필수 파라미터 누락, 사용자 입력 대기
      action=no_tool_needed → LLM이 도구 불필요 판단, 직접 선택 or 폴백
      action=no_tools       → 활성 도구 없음

Case 2 — 사용자가 도구 직접 선택 (selected_tool_id 포함)
  ① 해당 도구만 로드
  ② LLM 추출 없이 param_schema에서 required 파라미터 목록 파악
  ③ SSE: tool_request 이벤트 → 스트림 종료
      required 파라미터 있음 → missing_params (파라미터 입력 폼)
      required 파라미터 없음 → confirm (승인 카드)

Case 1 — 승인된 도구 실행 (approved_tool 포함)
  ① DB에서 도구 정보 로드
  ② asyncio.gather(_execute_http_call, _build_rag_context) 병렬 실행
  ③ HTTP 성공:
      SSE: tool_result (응답 미리보기)
      LLM 프롬프트 = API 응답 데이터 + ## 참고 문서(RAG) + 사용자 질문
      → 스트리밍 답변
  ④ HTTP 실패:
      SSE: tool_error (사용자에게 실패 알림)
      LLM 프롬프트 = RAG 컨텍스트만 (도구 정보 제외)
      → 스트리밍 답변 (도구 실패 사실은 LLM이 알지 못함)
```

**프론트엔드 ToolRequestCard 동작:**
- `confirm` / `missing_params`: 파라미터 입력 후 승인 → `approved_tool` 포함 재요청
- "다른 도구로 변경" 버튼: `selected_tool_id` 포함 재요청 → Case 2 진입
- "도구 없이 진행" (`no_tool_needed`): `knowledge_rag` 에이전트로 폴백 재요청
- "취소": 스트림 상태 초기화

---

## 11. Text-to-SQL 파이프라인 흐름

```
사용자 질문
  │
  ▼
┌─ 캐시 확인 ─────────────────────────────────────────────────────┐
│  sql_cache에서 question_hash 매칭                               │
│  히트 → SQL 즉시 반환 (execute로 점프)                          │
│  미스 → 파이프라인 시작                                          │
└─────────────────────────────────────────────────────────────────┘
  │
  ▼  Stage 1: Parse
  │  LLM이 의도/난이도/엔티티 분석 → JSON
  ▼  Stage 2: RAG
  │  pgvector 벡터 검색 (스키마 20건 + 용어 5건 + 예제 3건)
  ▼  Stage 3: Generate
  │  LLM이 SQL 생성 (CoT + 관계 정보 + Dialect 규칙)
  │  → SSE: sql 이벤트 (UI에 SQL 표시)
  ▼  Stage 4: Validate
  │  sqlparse + safety 검증
  │  ├─ 통과 → Stage 6
  │  └─ 실패 → Stage 5
  ▼  Stage 5: Fix (v2.12 안정성 개선)
  │  original_sql 보존 → LLM 재생성 (최대 2회)
  │  ├─ 빈 SQL 반환 → 원본으로 재시도 (연쇄 오염 방지)
  │  ├─ 주석만/산문 → 코드블록 검증으로 거부
  │  ├─ safety 위반 → 원본으로 복귀
  │  └─ MAX_RETRIES 초과 → 원본 SQL 반환
  ▼  Stage 6: Execute
  │  원격 대상 DB에 SELECT 실행 (timeout 30s, max 1000행)
  │  ├─ 성공 → SSE: table 이벤트 + 캐시 저장
  │  └─ 실패 → SSE: sql 이벤트(최종 SQL) + error 메시지
  ▼  Stage 7: Summarize (선택적)
     LLM이 결과 요약 + 차트 추천
```

### 스키마 개별 관리 흐름 (v2.12)

```
기존: DB 연결 → "스키마 스캔" → 전체 테이블 일괄 등록 (diff 방식)
추가: DB 연결 → "테이블 불러오기" → 원하는 테이블만 선택 등록

┌─ GET tables-available ─────────────────────────────────────────┐
│  get_table_summary() — DB별 최적화 쿼리 (테이블명+컬럼수만)     │
│  전체 inspect 대비 수십배 빠름                                   │
└────────────────────────────────────────────────────────────────┘
  │ 사용자가 테이블 선택
  ▼
┌─ POST tables/add ──────────────────────────────────────────────┐
│  get_tables(only=[선택 테이블]) — 선택한 것만 상세 inspect       │
│  기존 테이블 skip (case-insensitive)                            │
│  자동 ERD 배치 (pos_x, pos_y)                                   │
│  컬럼 임베딩 생성 (sql_schema_vector)                            │
└────────────────────────────────────────────────────────────────┘

두 방식(전체 스캔 / 개별 추가) 공존 — 사용자 선택
```

---

### 환경변수 기반 전환 (영구 적용)

```
.env 또는 환경변수
  LLM_PROVIDER=ollama   →  OllamaProvider
                             POST :11434/api/chat (messages 배열, multi-turn)

  LLM_PROVIDER=inhouse  →  InHouseLLMProvider
                             POST {INHOUSE_LLM_URL}/v1/chat/completions
                             Authorization: Bearer {INHOUSE_LLM_API_KEY}

신규 LLM 추가 시:
  1. services/llm/new_llm.py 생성
     class NewLLMProvider(LLMProvider):
         async def generate(...): ...
         async def generate_stream(...): ...
         async def health_check(...): ...

  2. services/llm/__init__.py 팩토리에 등록
     elif settings.llm_provider == "new_llm":
         _provider = NewLLMProvider()

  3. 환경변수 LLM_PROVIDER=new_llm 설정
```
