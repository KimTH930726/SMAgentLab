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
│  │  Step 0-A: Multi-turn Search Enrichment             │   │
│  │  (멀티턴 검색 보강 — 2턴 이상 대화에서 자동 적용,      │   │
│  │   memory.augment_query_for_search())                │   │
│  │                                                     │   │
│  │  현재 질문: "그 쿼리 알려줘"                         │   │
│  │      │                                              │   │
│  │      ▼  DB에서 직전 Q+A 조회 (ops_message)           │   │
│  │  prev_context = "{직전 Q}[:80] {직전 A}[:80]"        │   │
│  │      │                                              │   │
│  │      ▼  관련성 게이트(임베딩 유사도 ≥ 0.35만 결합)   │   │
│  │  관련 없으면(주제 전환) → search_question = 현재 질문 │   │
│  │  그대로, query_vec만 반환(재사용)                    │   │
│  │  관련 있으면 → search_question = "{prev_context} │   │
│  │  {현재 질문}" 로 결합 — 이 결합된 텍스트로 검색만 수행 │   │
│  │  (query_vec은 항상 현재 질문 단독 임베딩, 별도 반환) │   │
│  │  → 추가 LLM 호출 없음, DB 1회 조회 (<1ms)           │   │
│  │                                                     │   │
│  │  ※ 0.35 임계치가 낮아 "정책"처럼 흔한 단어 하나만    │   │
│  │  겹쳐도 무관한 주제를 "이어지는 질문"으로 오판할 수   │   │
│  │  있음(v2.91에서 실사고로 발견) — 검색 재현율엔 큰     │   │
│  │  문제가 안 됐지만(무관 문서가 조금 더 섞이는 정도),   │   │
│  │  아래 캐시 키에 이 결합 텍스트를 쓰면 안 되는 이유가  │   │
│  │  됨(바로 아래 참고)                                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Step 0-B: Semantic Cache 조회 (Redis)              │   │
│  │                                                     │   │
│  │  cache_vec = embed(normalize(현재 질문 그 자체))     │   │
│  │  (v2.91부터 — search_question이 아니라 항상 원본     │   │
│  │  query만 사용. 예전엔 위 Step 0-A의 결합 텍스트로     │   │
│  │  캐시 벡터를 만들어서, 직전 턴과 우연히 단어가 겹치는 │   │
│  │  전혀 다른 질문이 "직전 턴이 같다"는 이유로 캐시      │   │
│  │  벡터끼리 가까워져 무관한 답이 재사용되는 실사고가    │   │
│  │  있었음 — "쿠폰 회수 정책 알려줘" 다음에 무관한       │   │
│  │  "정책적으로 최대 재고 갯수가 몇개?"를 물었는데       │   │
│  │  이전 답이 그대로 나간 사례. 캐시는 "이 대화"가       │   │
│  │  아니라 "이 질문 하나"만 봐야 한다는 원칙)            │   │
│  │  Redis 스캔(semcache:{ns}:{agent_type}:*, v2.38부터  │   │
│  │  agent_type으로 분리)                                │   │
│  │  코사인 유사도 ≥ 임계치(기본 0.88, 관리자 설정으로    │   │
│  │  조정 가능) 히트 → 저장된 답변 즉시 반환             │   │
│  │  미스 → Step 1 이후 정상 파이프라인 실행            │   │
│  │  Redis 미연결 시 이 단계 무시 (graceful degradation) │   │
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
│  │      │  → vector [0.12, -0.34, ...] (1024차원)       │   │
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
│  │  Step 2-1: 정책 데이터 병행 검색 (2026-09-04 추가)   │   │
│  │                                                     │   │
│  │  policy_search.has_policy_data(namespace)            │   │
│  │  → 이 네임스페이스에 policy_item이 있는지 EXISTS     │   │
│  │    한 번(없으면 이하 전부 스킵 — 매 턴 낭비 방지)     │   │
│  │                                                     │   │
│  │  있으면: search_policy(ns, enriched_query, top_k=5,  │   │
│  │          query_vec=query_vec)  ← 이미 계산한 벡터    │   │
│  │          재사용, 재임베딩 안 함                       │   │
│  │  → policy_param(RDB tsquery) + policy_chunk(벡터)   │   │
│  │    동시 조회(raw_body도 함께 SELECT), 최대 5+5=10건  │   │
│  │  → 후보 전체를 Step 3의 _build_rrf_context()로 넘김   │   │
│  │    (agent.py) — 재현율 유지 위해 여기선 안 줄임       │   │
│  │  (Track 2 실측으로 하이브리드 스키마 우세 확정 후     │   │
│  │   편입 1단계(2026-09-04); "정책에서 온 답인지 구분이  │   │
│  │   안 되고 원문도 안 보인다"는 피드백으로 2단계        │   │
│  │   (2026-09-06) 진행 — 기존 results(rag_knowledge)    │   │
│  │   배열엔 안 섞고 policy_citations 별도 필드로 얹음    │   │
│  │   (FeedbackSection의 results[0].id 참조와 충돌 방지)) │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Step 2-2: 정책 근거 1건 역추적 (2026-09-07 추가)    │   │
│  │  ※ LLM 답변 생성(Step 3) "이후"에 실행됨              │   │
│  │                                                     │   │
│  │  select_cited_hit(policy_result, final_answer)       │   │
│  │  → 검색 후보 최대 10건 중 최종 답변 텍스트와 원문     │   │
│  │    (raw_body) 토큰이 가장 많이 겹치는 1건만 역추적    │   │
│  │  (구버전: 검색 직후 벡터 점수 1위를 미리 골라 LLM     │   │
│  │   컨텍스트까지 줄였다가, 그 1위가 실제로 무관해서     │   │
│  │   정답이 컨텍스트에서 빠져 "관련 지식을 찾지          │   │
│  │   못했습니다"로 답변이 실패하는 걸 실측으로 발견,     │   │
│  │   즉시 되돌림 — "근거가 너무 많이 보인다" 피드백은    │   │
│  │   화면 카드 개수만 줄이고 LLM 재현율은 안 건드려야    │   │
│  │   함을 확인)                                        │   │
│  │  → build_policy_citations(1건)으로 카드 데이터화 후   │   │
│  │    두 번째 meta SSE 이벤트로 늦게 전송(1차 meta는     │   │
│  │    policy_citations 빈 배열)                         │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Step 3: LLM 답변 생성                              │   │
│  │                                                     │   │
│  │  _build_rrf_context(results, policy_result,          │   │
│  │                      common_codes, db_columns)       │   │
│  │  (v2.86, 참조데이터 축은 v2.89)                       │   │
│  │  → 일반지식(cosine)·정책 파라미터(ts_rank)·정책      │   │
│  │    서술(cosine)·공통코드/DB스키마(ts_rank, 정확 매칭) │   │
│  │    를 항목 단위로 RRF(k=60)에 태워 하나의 순위로 합침 │   │
│  │    — 기존엔 "일반지식 먼저, 정책 나중" 순서로 그냥    │   │
│  │    이어붙여(build_context+build_policy_context) 점수 │   │
│  │    스케일이 다른데도 항상 같은 순서로 깔렸음.          │   │
│  │    build_context()/build_policy_context()는 디버그   │   │
│  │    검색·VOC 등 다른 화면에서 그대로 쓰여 안 건드림 —  │   │
│  │    agent.py 안에서만 항목 단위로 다시 포맷            │   │
│  │  참조데이터 축(v2.89): rag_knowledge의 "DB"/"공통코드"│   │
│  │    카테고리가 벡터축과 같은 테이블에서 final_score로  │   │
│  │    경쟁하다 top_k LIMIT에서부터 밀려나는 문제 실측     │   │
│  │    확인("DS14가 뭐야?"가 자기 매칭 대상을 27건 중     │   │
│  │    27등으로 후보 풀에도 못 넣음) — ref_common_code/   │   │
│  │    ref_db_column(v2.73에 스키마만 있던 구조화 테이블) │   │
│  │    으로 완전히 분리해 처음부터 독립 축으로 둠          │   │
│  │  (fewshot 섹션은 v2.84에서 완전 제거 — 후보→활성      │   │
│  │   승격을 담당할 사람이 없어 방치되던 걸 정적 안내문   │   │
│  │   ops_prompt_category_guide로 대체, §table-definition │   │
│  │   #48)                                              │   │
│  │                                                     │   │
│  │  build_messages(context, question, history)          │   │
│  │  → [system + 과거요약] + [최근2회 교환] + [현재 질문] │   │
│  │  (inhouse는 _build_query로 같은 내용을 한 문자열로)   │   │
│  │  참고 문서는 wrap_reference_context()로 조립(v2.109): │   │
│  │    [참고 문서] + 문서 + 고정 지시문(경계 역할),        │   │
│  │    문서 안의 [사용자]/[참고 문서] 등 라벨은 괄호로     │   │
│  │    중화 — 문서가 턴을 흉내 못 내게. 끝 표시는 "지식    │   │
│  │    없음" 응답 급증 원인으로 실측돼 두지 않음          │   │
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
│  - 정책 근거 카드    │
│    렌더링(파라미터/  │
│    서술 + 원문 표시, │
│    results와 별도)  │
│  - AI 답변 Markdown │
│    렌더링 (테이블,  │
│    코드블록, 리스트) │
│  - "답변 틀림" 버튼 │
│    (v2.121) +       │
│    근거 카드 "이    │
│    근거 틀림"(§6-1) │
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
  │  SentenceTransformer("nlpai-lab/KURE-v1")
  │  → 모델이 텍스트를 1024차원 공간에 매핑
  │  → normalize_embeddings=True 적용
  ▼
[0.12, -0.34, 0.87, 0.45, -0.22, ...] (1024개 float)
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

## 5. 지식 등록 흐름 (단건, 수동 입력)

```
관리자
  │
  │  지식 등록 폼 입력
  │  { namespace, content, category(선택), base_weight }
  ▼
┌─────────────────────┐
│   Admin (React)     │
│   ManualForm        │
└────────┬────────────┘
         │  POST /api/knowledge
         ▼
┌─────────────────────────────────────────┐
│  knowledge/service.py                   │
│                                         │
│  1. resolve_or_create_category()        │
│     사람이 지정 → 그대로                  │
│     안 지정 → LLM이 기존 목록 중 추천      │
│     그마저 실패 → "미분류" 자동생성        │
│  2. EmbeddingService.embed(content)     │
│     → 1024차원 벡터 생성(KURE-v1)         │
│  3. 유사도 중복 검사 → 너무 비슷하면       │
│     pending_review, 아니면 active        │
│  4. INSERT INTO rag_knowledge           │
│     (content, embedding, category, …)   │
└─────────────────────────────────────────┘
         │
         │  { id, namespace, content, pending_review, ... }
         ▼
┌─────────────────────┐
│   Admin (React)     │
│   목록에 즉시 반영   │
└─────────────────────┘
```

카테고리 필드는 원래 `RequiredCategoryField`로 항상 필수 선택이었는데, 2026-09-24부로
**등록 경로 전체(단건/파일 업로드/텍스트 분할/Teams)에서 필수가 아니게 됐다** — 비워
두면 백엔드(`resolve_or_create_category()`, `service/admin/service.py`)가 항상
유효한 값을 채운다. 프론트는 그 전에 먼저 시도해볼 뿐이다: 파일 업로드/텍스트 분할/
Teams 등록 폼은 내용이 확정되는 시점(파일 미리보기 성공·텍스트란 blur·Teams 메시지
선택)에 `autoSuggestCategoryIfUntouched()`가 `POST /categories/suggest`로 값을 먼저
채워준다(2026-09-22) — 사람이 직접 다른 값을 이미 골라뒀으면 건드리지 않는다. 결국
사람이 아무것도 안 골라도 등록은 항상 성공하고, 최소한 "미분류"로라도 남아 나중에
검토할 수 있다(예전엔 여기서 등록 자체가 거부됐음).

---

## 5-1. 컨플루언스 벌크 등록 + 카테고리 자동화 흐름 (2026-09-22~24)

여러 페이지를 한 번에 끌어올 때는 위 단건 흐름과 다르다 — 페이지마다 카테고리/문맥이
달라질 수 있어 배치 전체에 값 하나를 강제하면 안 되기 때문(실측: 카테고리가 5개
정의돼 있어도 실제로는 1개에만 몰리는 문제가 있었음).

```
관리자
  │  URL 입력 + "하위 페이지 포함" 체크
  ▼
┌──────────────────────┐
│  UrlForm (React)     │
└────────┬─────────────┘
         │  POST /import/url/tree
         ▼
┌─────────────────────────────────────────┐
│  preview_confluence_tree()              │
│  → fetch_confluence_tree()              │
│    본문 없이 메타데이터만(빠름):           │
│    page_id, title, parent_id, depth     │
└─────────────────────────────────────────┘
         │  트리 반환 → 관리자가 페이지 체크
         ▼
┌──────────────────────┐
│  트리 선택 모달       │
└────────┬─────────────┘
         │  POST /knowledge/import/url/bulk-pages/preview
         ▼
┌───────────────────────────────────────────────────────────┐
│  preview_confluence_bulk()  (knowledge/router.py)          │
│                                                             │
│  체크된 페이지마다 병렬로:                                    │
│  ┌───────────────────────────────────────────────────┐    │
│  │ 1. fetch_confluence_by_id()                        │    │
│  │    REST expand에 ancestors 포함                     │    │
│  │    → doc.metadata["parent_title"] = 직계 상위 페이지  │    │
│  │ 2. chunk_document() — 청킹                          │    │
│  │ 3. _resolve_confluence_page_category()             │    │
│  │    ① 사람이 폼에서 직접 지정했으면 그대로(오버라이드)   │    │
│  │    ② parent_title이 기존 카테고리에 있으면 재사용     │    │
│  │      없으면 → _ensure_category_exists()로 그 자리   │    │
│  │      에서 즉시 새 카테고리 생성(v2.102)              │    │
│  │    ③ parent_title 자체가 없을 때만(트리 루트) —      │    │
│  │      category_suggest LLM이 기존 목록 중에서만 추천   │    │
│  │    ④ 그래도 실패 시 "미분류" 자동생성                 │    │
│  │ 4. _enrich_heading_path()                          │    │
│  │    페이지 내 소제목 앞에 parent_title을 붙임          │    │
│  └───────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────┘
         │  청크마다 {content, category, heading_path, page_title}
         ▼
┌──────────────────────────────────────┐
│  ChunkReviewModal (perChunkCategory)  │
│  청크별 카테고리 배지 표시 — 사람은     │
│  포함/제외만 선택(카테고리는 이미 배정됨) │
└────────┬───────────────────────────────┘
         │  POST /knowledge/bulk  (선택 청크의 content+category+heading_path 그대로)
         ▼
┌─────────────────────────────────────────┐
│  bulk_create_knowledge()                │
│  → _run_bulk_ingestion()                │
│    임베딩 + INSERT INTO rag_knowledge    │
│    (category, heading_path 포함)         │
│    배치마다 status='staging'             │
│    (중복 유사도 높으면 'staging_review') │
│    → job 끝에 한 트랜잭션으로 일괄 전환   │
│      staging→active,                    │
│      staging_review→pending_review      │
│    → 시맨틱 캐시 네임스페이스 무효화      │
│    취소·실패 시 스테이징 행 삭제          │
└─────────────────────────────────────────┘
```

**핵심 지점은 `_resolve_confluence_page_category()`다** — 예전엔 이 자리에 로직이 없어
폼 상단에서 고른 값 하나가 배치 전체(최대 200페이지)에 그대로 복사됐다. `parent_title`
(직계 상위 페이지 제목)은 LLM 추측이 아니라 사람이 이미 컨플루언스에 만들어둔 실제
정보 구조라서, 기존 카테고리 목록에 없어도 그 자리에서 바로 새 카테고리로 만들어도
안전하다는 게 v2.102의 핵심 정정이다 — 그래야 "누가 먼저 카테고리를 만들어주나"라는
부트스트랩 문제 없이 첫 등록 순간부터 실제 조직 구조 그대로 분화된다. 반대로 LLM
추천(③)은 신뢰할 구조 정보가 없는 추측이라 여전히 새 카테고리를 못 만들게 막아둔다.

---

## 6. 통계 수집 전체 사이클

```
질문 처리 → ops_query_log INSERT (LLM 연결 실패 → status='system_error', 답변·공백·전체 통계에서 빠지고 건수만 표시)
  │
  ├─ 임계값을 넘는 근거(지식·정책·공통코드·DB 컬럼)가 없음, 또는 답변에 "관련 지식을 찾지 못했습니다"
  │    → status='no_knowledge' (지식 공백 — 근거 없이 LLM이 답했어도 환각 위험이라 답변으로 안 셈)
  └─ 그 밖 → status='pending' (답변 — 신고 없으면 맞은 것으로 봄)
  캐시 응답은 저장 시점의 근거 유무(had_context)로 판정 — 옛 캐시 항목은 지식·정책 근거 유무로 대신

  "답변 틀림" → 개선 원장 1건(정정 검토 탭) + 정정 모드(§6-1). 질의 상태·가중치는 안 바꿈(v2.121)
  (👍 "도움됐어요"는 v2.121에 제거 — 누를수록 가중치가 오르던 무검증 자동 반영)

status 정의 (v2.121 — 좋아요/싫어요 기반 해결·미해결 폐지, 틀린 답은 정정 요청이 맡음)
  pending      — 답변
  no_knowledge — 지식 공백. 지식 등록으로 메우면 상태 그대로 resolved_knowledge_id·resolved_at만 채움(공백 메움)

공백을 메운 질의는 통계 목록에서 원래 AI 답변 대신 그 지식의 최신 content를 보여준다(이후 반려/병합되면 원래
답변으로 폴백 — rag_knowledge LEFT JOIN status='active' + COALESCE).

어드민 통계 (GET /api/stats/namespace/{name})
  ├─ KPI 카드: 전체 / 답변 / 정정 요청(개선 원장 pending — 처리는 정정 검토 탭) / 지식 공백 / 공백 메움
  ├─ 도넛 "답변 현황": 답변 / 지식 공백 / 공백 메움 — 중앙 답변률 = 답변 / 전체
  └─ 지식 공백 카드 → 공백 목록 → "지식 등록" 폼 → PATCH /api/stats/query-log/{id}/fill (공백 메움 기록)

가중치(base_weight) — v2.121에 입력 제거, 모든 값 1.0(#63). 검색식 × (1 + w)는 남아 있지만 상수라 순위에 영향 없음
  (틀린 검색은 정정 흐름으로 내용·업무구분·용어집을 고친다. 승인 시 시맨틱 캐시도 비워 틀린 답이 다시 안 나옴)
```

### 답변률이 낮을 때(지식 공백이 많을 때)

```
1. 지식 등록 부족 → 지식 베이스 신규 등록
2. 용어 불일치   → 용어집 유의어 추가
3. 의미적 거리   → 질문 표현 그대로 포함한 지식 등록, 또는 키워드 비중 높임
```

---

## 6-1. 근거 정정 흐름 (v2.118, 2026-10-01)

원칙: **사용자는 고치지 않고 신호만 준다 — 사용자 입력은 담당자 승인 전엔 절대 검색에 반영되지 않는다.**
사용자 행동은 버튼 1번 + 한 줄. 수정안은 개선 원장(`ops_improvement_item`)에만 있어, 승인 전 미노출이 상태
필터가 아니라 구조적으로 보장된다(정책 검색은 pending_review도 보여서 정책 행으로 넣으면 안 됨).

```
채팅 답변
  ├─ "답변 틀림" 클릭 → 즉시 원장 1건(answer_signal, 근거 후보 저장) + 백그라운드 AI "추정"(의견 없을 때)
  │     └─ 이어서 한 줄(target_type=auto) → 같은 건을 채움     ← 근거를 고르지 않음, 신고 1번 = 1건
  ├─ 근거 카드 "이 근거 틀림" → 한 줄 (그 근거로 직접 지정)
  └─ 평가 게이트 "이상해요" → 원장 1건(search_noise, 대상 = 그 지식)
                                 ▼
  POST /api/corrections
     ├─ 후보 = 그 답변에 저장된 근거(ops_message.results + 정책 인용 id) — 클라이언트 id 안 믿음
     ├─ auto: LLM 1회로 사실 확인(user_claim / 같은 내용 근거 / 다른 내용 근거) + 이유·틀린 부분·고칠 내용 + 수정안
     │        → 판정은 코드가: 다른 내용 근거 있음=근거 정정 / 같은 내용만=답변 오류(지식 그대로) / 없음=빠진 내용
     │        LLM 실패 → 임베딩 최근접 근거로 "추정", 그것도 실패 → 빠진 내용(담당자 지정)
     ├─ 원장 INSERT(pending, 원문 스냅샷, 후보·AI 판정) — 같은 사용자·같은 근거 중복은 409
     └─ 답변 아래 "AI 분석" 카드(어느 근거·왜·틀린 부분·반영되면) / 근거 카드 "정정 검토 중" 배지
                                 ▼
  소유 파트 담당자·관리자: 지식 베이스 > 정정 검토 (대기 건수 배지: 사이드바 Admin + 탭)
     ├─ [AI 수정안으로 대체] / [직접 수정](AI안 또는 현재 내용으로 미리 채움) / 반려 — 대상 변경도 가능
     │    (의견 없는 건은 대상을 골라도 초안을 만들지 않음 — 맞는 내용 정보가 없으므로)
     │    카드엔 근거 대상 건도 질문·당시 답변을 표시, AI 초안 프롬프트에도 질문·답변을 함께 넣음
     │    추정이 없는 의견 없음 건(이관된 옛 신고 등)은 [AI로 원인 추정]으로 바로 분석, 탭 건수는 현재 파트만
     ├─ 답변 오류 = 승인 없음, 확인 후 종료(사유)
     ├─ 승인 = 버전 교체(한 트랜잭션, 원본이 현행인지 FOR UPDATE 확인)
     │    지식: 새 rag_knowledge(재임베딩, logical_document_id 승계, version+1, supersedes_id) + 원본 deprecated
     │    정책: 새 policy_item 버전(param/chunk 복사, 대상만 교체·재임베딩) + 원본 deprecated
     │          content_hash·pipeline_version은 원본 그대로 → 같은 엑셀 재업로드(강제 재처리 포함)엔
     │          정정 유지, 엑셀 원본이 실제로 바뀌면 원본 소유자 우선
     │    빠진 내용: 새 지식 active 등록(source_type='correction')
     │    → 같은 원본을 겨눈 다른 대기 신고 자동 종료(사유 기록, 신고자에게 알림) → 시맨틱 캐시 무효화
     │    (재임베딩은 트랜잭션 밖에서 미리, 잠금 후 원본이 바뀌었으면 중단)
     └─ 반려 = 원본 유지 + 사유 필수(신고자에게 표시 — 사용자 오정정 데이터)
                                 ▼
  신고자: 채팅 진입 시 "내 정정 신고 n건 처리됨(반영/반려 사유)" → 확인 시 reporter_seen_at
```

---

## 6-2. 정책 승인 대기 큐 — 위험도 분류 + 자동 통과 (v2.119, 2026-10-01)

"위험한 것만 사람이 본다." 위험도는 저장하지 않고 조회할 때마다 계산(parse_status·청크를 바꾸는 경로가 여럿).

```
검토대기 항목 → risk.classify (LLM 미사용)
   높음: parse_status unresolved/partial · 이전 반려(rejected/resubmitted 이력) · 자동 통과를 사람이 되돌림
   중간: 서술 청크 2개 이상(조건이 쪼개졌을 위험)
   낮음: 구조화 완료 + 서술 1개 → 규칙 low_risk_v1 후보
                 ▼
auto_review.run (관리자 미리보기→실행, 또는 임포트 직후 그 파트에 자동)
   낮음 중 md5(id) 기준 결정론적 10% → 표본(review_sample, 검토대기 유지)
   나머지 → active + review_source='auto_rule'(reviewed_by NULL — 사람 승인과 구분)
   전부 policy_review_log에 등급·근거·run_id 기록, FOR UPDATE SKIP LOCKED로 사람 결정과 경합 방지
                 ▼
사람: 검토 큐(위험도순) = 높음 + 중간 + 표본 (+ 규칙 정지 시 낮음)
   위험 높음(구조화 실패) 항목은 펼친 자리에서 미분류 조각을 서술/파라미터로 편입 → parse_status·위험도 즉시 재계산
   (미분류 집계 탭은 팀별 읽기 전용 — 정책서 표준화 요청 근거)
   목록 한 줄에 배지 + 짧은 이유(예: "미분류 조각 2개"), 펼치면 상세 이유 + "→ 할 일", 요약에 등급 기준 한 줄
   표본 반려 → 규칙 정지(ops_system_config policy_auto_review) → 관리자 확인 후 재개
   되돌리기: 단건 / 실행(run_id) / 규칙 — 파트 범위로, 되돌린 항목은 이후 위험 높음
검색: rejected·deprecated만 제외 → 검토대기↔자동 통과 이동은 채팅 결과에 영향 없음
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
