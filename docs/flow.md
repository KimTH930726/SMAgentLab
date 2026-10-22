# OpsLens 시스템 흐름도

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
│  │  Step 1: Glossary Term Match (v2.128, 글자 매칭)     │   │
│  │                                                     │   │
│  │  질문 텍스트 (또는 search_question)                   │   │
│  │      │                                              │   │
│  │      ▼                                              │   │
│  │  glossary_terms.resolve_query_terms()               │   │
│  │  질문에 글자 그대로 나온 용어·동의어만 (단어 경계)   │   │
│  │  "쿠폰 뺏어오기 실패한 건?" → 동의어 "뺏어오기"      │   │
│  │      │  → mapped_term: "회수"                       │   │
│  │      ▼                                              │   │
│  │  키워드 검색어에만 용어 추가 + 용어 설명은 LLM 문맥에 │   │
│  │  (없으면 아무것도 안 붙임 — 3절 참고)                │   │
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
│  │                                                     │   │
│  │  build_messages(context, question, history)          │   │
│  │  → [system + 과거요약] + [최근2회 교환] + [현재 질문] │   │
│  │  (inhouse는 _build_query로 같은 내용을 한 문자열로)   │   │
│  │  inhouse는 근거 문서·이전 답의 숫자 형태만 바꿔 보냄  │   │
│  │  (v2.126, 게이트웨이 오탐 임시책: 3.1.2.4→3．1．2．4,  │   │
│  │   8자리+ → 4자리마다 ·) — 답은 원복, 사용자 입력은    │   │
│  │   원문 그대로(진짜 민감정보는 게이트웨이가 계속 차단) │   │
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

텍스트를 1024개의 숫자 배열(벡터, nlpai-lab/KURE-v1)로 변환한 것이다.
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
│ id │ namespace │ content                 │ embedding (VECTOR 1024)      │
├────┼───────────┼─────────────────────────┼──────────────────────────────┤
│  1 │ coupon    │ "쿠폰 강제 회수 처리..."  │ [0.12, -0.34, 0.87, ... 1024개] │
│  2 │ coupon    │ "쿠폰 발급 오류 대응..."  │ [0.23,  0.11, 0.45, ... 1024개] │
│  3 │ gift      │ "선물 발송 실패 처리..."  │ [-0.05, 0.67, 0.31, ... 1024개] │
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

### 탐색형 질문 — 분류 목록 (v2.128, 2026-10-07)

```
"○○ 관련 정책 전부/목록" (looks_like_navigation_query) 또는 "재고 정책 좀"처럼 분류 이름 + 일반 표현뿐(is_topic_overview,
  글자 매칭만, 두 분류면 함께 속한 정책만) → 분류 찾기(이름 글자 매칭 → 없으면 의미 1위 0.58 이상)
    (글자로 나온 이름을 품은 더 구체적인 분류가 의미상 0.15 이상 가까우면 그쪽 — "주문서 … 쿠폰 정책" → "주문서 쿠폰정책", v2.133)
  → 그 분류 정책 전부(최대 40개, "경로 > 정책명 — 요약"(v2.152, 본문 앞줄 80자))를 [분류 목록] 블록으로 문맥 맨 앞에(용어 설명 다음) → 아래는 평소 검색 결과
  → 답 형식: 빠짐없이 "정책명 — 한 줄 요약", 세부는 다시 물어보라고 안내(v2.133, 사용자 결정). 같은 이름이 여러 업무면 업무별로 묶음
  분류 목록은 이번 질문만으로 고른다(멀티턴 보강 질문 X — 앞 턴 범위가 넘어오지 않게, v2.134)
  재질문(v2.134): 주제만 말한 질문("주문 정책 좀", 전부·모든·목록 명시 없음) + 정책 20개 초과 → LLM 없이 clarify 이벤트
    "어느 부분을 볼까요?" ①②③(업무별 또는 하위 분류별, 많은 순) ④전부 보기 ⑤기타(입력창) → 고르면 같은 대화에서 clarify_scope로 그 범위 답
    (다시 묻지 않음). 선택지는 메시지 metadata.clarify — 다시 열어도 보임. 재질문엔 답변 틀림·질의 기록·캐시 없음
  목록 답(전부·목록 신호 + 분류 목록 있음)이면 문맥에서 목록 밖 정책 검색 결과를 뺀다(v2.138 — 다른 분류 섞임 방지, 인용·판정은 원래 결과),
  목록 줄엔 번호(1. 2. …)를 붙인다(긴 목록 중간 누락 방지)
  못 찾으면 평소대로. 분류 목록이 있으면 근거 없음 판정 안 함
```

### 조건 한정 질문 — 답 구조 (v2.138)

```
"X에만 / X만 따로 / ~할 때만 / ~경우에만 / X 전용" (looks_like_condition_query, 이번 질문만으로)
  → 문맥 맨 앞에 [답 구조] 지시: ① 조건에 해당하는 내용 먼저 ② 그 조건 전용 규정이 없으면 "없다"고 밝힘 ③ 함께 적용되는 일반 규정은 "공통 적용"으로 구분
  → 문서에 관련 내용이 있으면 "관련 지식을 찾지 못했습니다"로 끝내지 않음(근거 없음 즉시 판정은 그대로 앞에서 걸러짐)
```

### 정책 서술 검색 (v2.138)

```
질문 벡터 → 정책 조각(임베딩 = "정책명 (분류 > …): 조각" 머리 붙임, 저장·답 문맥은 조각 그대로) 상위 top_k×3
  → 정책 항목마다 가장 가까운 조각 하나로 묶음(한 항목이 여러 자리 차지 X)
  → 키워드 채널(메모리 BM25 — 조사 제거·금액 정규화 "3만 원" = "30,000원") 상위와 합침: 벡터 순서 유지, 벡터 상위 top_k에 없는
    키워드 상위 항목을 아래 3자리에(구제 방식 — 1위는 늘 벡터). 실패하면 벡터만(로그)
  → (리랭커 켜면) 원래 후보 수 + 3까지 재정렬 → top_k
임베딩 규칙이 바뀌면(CHUNK_EMBED_VERSION) 기동 때 백그라운드로 전 조각 재임베딩(ops_data_marker 기록, 그사이 편집된 조각은 건너뜀)
기법 본체(토큰화·BM25·구제·머리)는 libs/trustkit — 이 흐름은 정책 테이블 어댑터(v2.139)
```

### 정책서 범위 (v2.140, v2.142 업무 영역)
질문한 업무 영역(`namespace`)에서 정책서를 어디까지 찾는가 — `service/policy/scope.py`. 지식 검색은 고른 영역(+ 직계 하위, v2.153)만.
```
고른 영역 자신 + ops_link "같은 업무"로 연결된 영역 / 연결 없으면 정책이 있는 영역 전부 (정책이 있는 영역만)
  (테스트 영역 policy_test는 자기만 — 실제 채팅에 안 섞임)
  → 정책 검색(쿼리 한 번, namespace_id = ANY) · BM25 색인(영역들 합친 하나) · 분류 목록(영역 이름이 맨 위 분류)
  → 용어(고른 영역 + 정책 영역, 같은 이름이면 고른 영역 뜻) → 근거·문맥에 [업무 영역: …](여러 영역일 때만)
```
수정 권한(v2.142): 관리자 / 담당 파트 사람(조회 전용 제외) / 담당 없는 영역은 관리자만. 영역 자체(만들기·이름·삭제)는 관리자만.
업무 영역 트리(v2.153, 깊이 1단 `ops_namespace.parent_id`): 상위를 고르면 **검색**(지식·용어·정책 범위·참고 데이터·VOC 커버리지)이 자신 + 직계 하위
(`core/database.namespace_scope_ids`·`scope_ids_subquery`), 하위를 고르면 하위만. 등록·수정·통계는 고른 영역 자신. 하위 담당이 비면 상위 담당을 물려받는다
(권한 검사 4곳 COALESCE). 정정(쓰기) 범위는 담당이 같은 하위만(`scope_ids_same_owner_subquery` — 다른 파트가 직접 맡은 하위는 그 영역에서 신고).
정정 적용은 대상 행의 원래 영역에 새 버전. 답변 캐시는 자신 + 상위 이름을 같이 비운다(`invalidate_namespace_family`). 하위 있는 영역 삭제 409.
지식 조회는 누구나(v2.144, 다른 파트 담당 영역도), 사람 데이터(통계·VOC)는 담당 파트·관리자. 담당 지정: 사용자 관리 > 담당 업무 영역.
조직 컬렉션(v2.160, `kind='org_space'` — 팀 공통·파트): 고르면 지식·용어 검색 = 그 컬렉션 하나(하위 없음), 정책 범위는 위 규칙 그대로(조직엔 정책이 없어 "정책이 있는 영역 전부").
업무 컬렉션을 골랐을 때 질문자의 조직 컬렉션을 더하는 규칙은 설정 `chat.org_space_scope`(기본 꺼짐 — 정확도 게이트 뒤에 켬, 켤 땐 답변 캐시 키에 파트를 넣을 것).
정책 근거 문맥 = 본문 + "비고:"(비고는 검색 조각엔 없음 — v0 실측). 참고표·문서 개요는 적재만, 채팅 연결은 골든셋으로 기준을 잡은 뒤.

### AI가 답을 못 낼 때 — 근거만 보여 주기 (v2.141)
```
LLM 실패(연결·빈 답·게이트웨이 거부) → 정책 근거 = 검색 상위 3(답과 겹치는 것 고르기 대신) + 지식 근거(이미 표시)
  → 메시지 failed + metadata.degraded → 화면: 경고 상자 + 근거 카드, 피드백 없음 → 질의 기록 system_error, 캐시 안 함
LLM 밖 오류(검색·DB) → "처리 중 오류" failed + 장애 기록 + done (이미 저장된 답은 그대로, 도중 끊김은 degraded 표시)
```

### 지식 검색 (v2.139)

```
벡터(코사인) × w_vector + 키워드(ts_rank) × w_keyword 가중합 × (1 + base_weight) → ORDER BY 점수, id(동점 순서 고정) → top_k
  → 관련도(가중치 걷어낸 점수) 0.35 이상만 문맥에. 맥락 머리 임베딩 안 씀(지식에선 손해, trustkit-design §7-2)
  KNOWLEDGE_KEYWORD_RESCUE=1이면(기본 꺼짐) 채팅 문맥 호출만: 후보 30개 중 BM25 상위를 top_k의 30% 자리에 구제
```

### 검색 시 용어 활용 흐름 (v2.128 — 질문에 실제로 나온 용어만)

```
사용자 질문: "상품쿠폰에만 따로 적용되는 규정 있어?"
  │
  ▼
용어 찾기 (glossary_terms.find_terms — LLM·임베딩 호출 없음)
  용어집 용어 + 동의어를 질문에서 글자 그대로 찾음(띄어쓰기·대소문자·문장부호 무시, 긴 것 우선, 최대 3개)
  "상품쿠폰" == "상품 쿠폰"(정규화) → 용어 "상품 쿠폰"
  못 찾으면 아무것도 안 붙임 ("오늘 날씨" → 없음)
  │
  ▼
키워드 검색만 확장 (expansion_text)
  enriched = "상품쿠폰에만 … 있어? 상품 쿠폰 <동의어…>"   ← 질문에 표기 그대로 없는 것만
  벡터 검색은 원래 질문 임베딩 그대로(query_vec)
  │
  ▼
지식/정책/공통코드 검색 → RRF 문맥 → 맨 앞에 [용어 설명] 블록(근거가 있을 때만)
  │
  ▼
mapped_term = "상품 쿠폰" (여러 개면 쉼표) → 화면 "용어" 배지·질의 기록

동의어는 사람이 등록하지 않는다:
  · 용어 등록·수정 → LLM이 생성(잠깐 모아 15개씩) → 품질 게이트(표현↔용어 임베딩 유사도 ≥ 0.65) → rag_glossary_synonym(llm_term)
  · 하루 1회 반복 공백 확인(v2.128 개편): 같은 질문이 14일 안에 3번 이상 "관련 지식을 찾지 못했습니다"면(SQL, 없으면 LLM 안 부름)
    → 그 질문과 가까운 용어 10개만 LLM에 → "용어를 다르게 쓴 표현" 추출 → 질문에 실제로 있는지 검증 → 게이트(≥ 0.85)
    → 효과 확인(그 동의어를 넣고 다시 검색해 새 근거가 잡히나) → 잡히면 llm_gap으로 바로 사용, 아니면 버림
  · 사람은 용어집 화면에서 틀린 표현만 지움(blocked — 다시 안 붙음)
예전(v2.127까지, glossary_match_mode=embedding): 질문 임베딩 ↔ 용어 *설명* 최근접 1개가 0.5 이상이면 이름을 붙임 — 골든 79/88에 붙었지만
질문에 실제로 있던 건 6건, 무관 질문 21/60에도 붙어 검색어 오염(실측 2026-10-06).
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
  │  { namespace, content, category(선택) }
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

## 5-0. 문서 → 청크 공통 규칙 (v2.129, 2026-10-07)

모든 등록 경로(파일·붙여넣기·컨플루언스·웹)가 같은 두 단계를 거친다.

```
원문 ─▶ 제목 인식 ─────────────────────────────▶ 섹션 [{제목, 본문, 레벨}]
        HTML: h1~h6 / 표(행마다 | a | b |) / 코드(```) / 단락 — 문서 순서대로 한 번
        텍스트(txt·md·PDF·붙여넣기): "#" 제목, 두 단계 이상 번호(1.2.1.) — "1."·"3.5kg"·날짜·문장은 제목 아님
      ─▶ 섹션 청크(_chunk_by_sections) ─▶ 청크 [{본문, heading_path}]
        짧은 상위 도입부 → 하위 청크 앞에 한 번만 / 본문 있는 짧은 섹션은 유지 / 제목뿐 섹션은 하위가 없으면 버림
        큰 섹션 → 단락 재분할(조각마다 자기 제목) / "단, …"은 앞 청크에(크기 안에서)
      ─▶ 미리보기 = 저장(확정 시 heading_path까지) ─▶ job 끝 품질 검사(이웃 중복·매달린 제목·제목만) → 경고

원문 원장(v2.130): 미리보기 때 원문을 rag_source_document에 초안으로(해시 재사용) → 확정 때 청크에 source_document_id·"사용 중"
  → 같은 원본 위치 재등록: 등록 전 확인(replace-check) — 기존 조각에 사람 손 흔적(고침·삭제/반려·일부만)이면
    사유 모달 → 확인해야 교체(/bulk는 확인 없으면 409), 흔적 없으면 바로 → 이전 원문 "교체됨" + 그 청크 폐기
    (전환 트랜잭션에서 같은 원본 잠금 + 그 순간 기준으로 교체 대상 다시 계산, v2.132)
  → 결함 수정 뒤 "원문에서 다시 분할": 원장 원문 → 지금 코드로 파싱·분할 → 같으면 끝 / 다르면 원자적 교체(사람이 고친 청크 있으면 멈춤)
```

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
│  │ 3. (업무구분 배정 단계는 10/22 폐지, v2.158 —       │    │
│  │    parent_title은 아래 heading_path에만 쓴다)        │    │
│  │ 4. _enrich_heading_path()                          │    │
│  │    페이지 내 소제목 앞에 parent_title을 붙임          │    │
│  └───────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────┘
         │  청크마다 {content, heading_path, page_title}
         ▼
┌──────────────────────────────────────┐
│  ChunkReviewModal                     │
│  사람은 포함/제외만 선택                │
│  (업무구분 배지는 10/22 폐지)            │
└────────┬───────────────────────────────┘
         │  POST /knowledge/bulk  (선택 청크의 content+heading_path 그대로)
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
질문 처리 → ops_query_log INSERT (LLM 연결 실패·빈 응답·게이트웨이 민감정보 거부 → status='system_error', 시맨틱 캐시 저장 안 함, 답변·공백·전체 통계에서 빠지고 건수만 표시)
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
  (틀린 검색은 정정 흐름으로 내용·용어집을 고친다. 승인 시 시맨틱 캐시도 비워 틀린 답이 다시 안 나옴)
```

### 답변률이 낮을 때(지식 공백이 많을 때)

```
1. 지식 등록 부족 → 지식 베이스 신규 등록
2. 용어 불일치   → 용어집 유의어 추가
3. 의미적 거리   → 질문 표현 그대로 포함한 지식 등록, 또는 키워드 비중 높임
```

---

## 6-1. 근거 정정 흐름 (v2.118, 2026-10-01 · v2.150 정책 화면 제안·직접 수정·일괄 승인)

원칙: **사용자는 고치지 않고 신호만 준다 — 사용자 입력은 담당자 승인 전엔 절대 검색에 반영되지 않는다.**
정책 내용 변경도 전부 이 원장의 제안이다(v2.150): 정책서 화면의 [직접 수정]·[AI 수정 제안] → 정정 요청에서 승인할 때만 새 버전.
사용자 행동은 버튼 1번 + 한 줄. 수정안은 개선 원장(`ops_improvement_item`)에만 있어, 승인 전 미노출이 상태
필터가 아니라 구조적으로 보장된다(정책 테이블에 검토 대기 행을 만들지 않는다 — v0 검색은 승인된 active만 보지만(visibility.py), 옛 v1 항목은 pending_review도 검색되므로 상태로 막는 데 기대지 않는다).
정책 하나엔 대기 중 정정이 1건만(v2.150 리뷰 M1): 정책 화면 제안·채팅 신고(근거 카드 = 409 + 기존 번호, "답변 틀림" 자동 판정·AI 추정 = 신고는 받되 대상을 비워 둠)·대상 변경이 모두 정책 항목 잠금 + 같은 확인을 거친다(잠금 순서는 어느 길이든 자기 원장 행 → 정책 잠금). 다시 쓰기는 지금 저장된 수정안에서 출발한다(사람이 고친 안 보존, 덮어쓴 안은 `ai_verdict.redrafts[].previous`).

```
채팅 답변
  ├─ "답변 틀림" 클릭 → 즉시 원장 1건(answer_signal, 근거 후보 저장) + 백그라운드 AI "추정"(의견 없을 때)
  │     └─ 이어서 한 줄(target_type=auto) → 같은 건을 채움     ← 근거를 고르지 않음, 신고 1번 = 1건
  │  (근거 카드 "이 근거 틀림"은 v2.122 제거 — 실사용 0건, 대상은 AI가 찾고 담당자가 정정 검토에서 변경)
  ├─ 평가 게이트 "이상해요" → 원장 1건(search_noise, 대상 = 그 지식)
  └─ 정책서 화면(담당 파트·관리자, v2.150) — 같은 POST에 source를 붙여서
        직접 수정(source=policy_direct): 원문·비고 그대로 수정안(AI 호출 없음, v0는 서술 조각 = 원문)
        AI 수정 제안(source=policy_ai): 한 줄 → AI 초안(실패해도 제안은 남음 → 정정 요청에서 [AI 초안 만들기]/[직접 수정])
        JSON 가져오기(source=json_import, v2.151 — 관리자 전용 /api/policy/json/apply만): 바뀐 정책마다 직접 수정과 같은 제안
        같은 정책에 대기 중 정정이 있으면(출처 무관) 만들지 않고 409 + existing_id → 화면 "정정 #N 검토 중 → 정정 요청에서 보기"
        (확인·저장은 정책 항목 단위 advisory 잠금 안 — 동시에 두 번 눌러도 1건)
                                 ▼
  POST /api/corrections
     ├─ 후보 = 그 답변에 저장된 근거(ops_message.results + 정책 인용 id) — 클라이언트 id 안 믿음
     ├─ auto: LLM 1회로 사실 확인(user_claim / 같은 내용 근거 / 다른 내용 근거) + 이유·틀린 부분·고칠 내용 + 수정안
     │        → 판정은 코드가: 다른 내용 근거 있음=근거 정정 / 같은 내용만=답변 오류(지식 그대로) / 없음=빠진 내용
     │        LLM 실패 → 임베딩 최근접 근거로 "추정", 그것도 실패 → 빠진 내용(담당자 지정)
     ├─ 원장 INSERT(pending, 원문 스냅샷, 후보·AI 판정) — 같은 사용자·같은 근거 중복은 409
     └─ 답변 아래 "AI 분석" 카드(어느 근거·왜·틀린 부분·반영되면) / 근거 카드 "정정 검토 중" 배지
                                 ▼
  소유 파트 담당자·관리자: 품질 › 정정 요청 (대기 건수 배지: 메뉴) — 출처 거르기(채팅·정책 직접 수정·정책 AI 제안·JSON 가져오기·평가 게이트)
     (원문 확인 kind='source_check'는 여기 안 나옴 — 정책서 › 원문 확인, §6-1a)
     ├─ [승인](저장된 수정안) / [직접 수정](v2.150 되살림 — PATCH /{id}/proposed, 승인과 같은 검사, ai_verdict.edits에 누가·언제)
     │   / [AI 다시 쓰기](요청 한 줄 → AI가 다시 씀, POST /redraft) / 반려 — 대상 변경도 가능
     │   카드: 기존 vs 수정안 줄 비교(바뀐 줄 강조). 【확인 필요】 표시가 남으면 승인 불가 → 직접 수정하거나 다시 쓰기로 맞는 내용을 알려 줌
     │   일괄 승인: 체크(승인 가능한 건만) → [선택한 N건 승인] = 화면이 건마다 POST /{id}/approve를 차례로(보인 안을 보냄)
     │      → 그 사이 바뀐 건은 409로 빠지고 이유 표시(선택 유지), 나머지는 승인
     │    (의견 없는 건은 대상을 골라도 초안을 만들지 않음 — 맞는 내용 정보가 없으므로)
     │    카드엔 근거 대상 건도 질문·당시 답변을 표시, AI 초안 프롬프트에도 질문·답변을 함께 넣음
     │    추정이 없는 의견 없음 건(이관된 옛 신고 등)은 [AI로 원인 추정]으로 바로 분석, 탭 건수는 현재 파트만
     ├─ 답변 오류 = 승인 없음, 확인 후 종료(사유)
     ├─ 승인 = 버전 교체(한 트랜잭션, 원본이 현행인지 FOR UPDATE 확인)
     │    지식: 새 rag_knowledge(재임베딩, logical_document_id 승계, version+1, supersedes_id) + 원본 deprecated
     │    정책: 원본 deprecated 먼저 → 새 policy_item 버전 active(번호 = 같은 정책 최대+1, param/chunk 복사, 대상만 교체·재임베딩)
     │          비고: 수정안에 비고 칸이 있으면 그 값(빈 값 = 지움), 없으면 원본 그대로. source_key·content_hash 원본 그대로
     │          → 정책 색인 + 모든 업무 영역 답변 캐시 비움
     │    빠진 내용: 새 지식 active 등록(source_type='correction')
     │    → 같은 원본을 겨눈 다른 대기 신고 자동 종료(사유 기록, 신고자에게 알림) → 시맨틱 캐시 무효화
     │    (재임베딩은 트랜잭션 밖에서 미리, 잠금 후 원본이 바뀌었으면 중단)
     └─ 반려 = 원본 유지 + 사유 필수(신고자에게 표시 — 사용자 오정정 데이터)
                                 ▼
  신고자: 채팅 진입 시 "내 정정 신고 n건 처리됨(반영/반려 사유)" → 확인 시 reporter_seen_at
```

---

## 6-1a. 정책서 화면 — 지식 › 정책서 (v2.150, v0 재구축 2단계 · v2.151 원문 확인·JSON)

```
[정책] GET /api/policy/policies?namespace=&q=   ← 현행(active) 1건 = 1줄, 문서 순서(source_file·source_row), 첫 절로 접기
         한 줄: 정책명 · 하위 절 · 비고 표시 · v2+ · "정정 #N 검토 중"(이 항목을 겨눈 대기 정정, 출처 무관)
   └─ 누르면 상세 모달: GET /api/policy/policies/{logical_id}
         경로 · 이름 · 버전 · 원문 · 비고 · 용어(ops_link policy —uses→ term, from_key = logical_id)
         · 값(있을 때만 — 원문에 칠함, 원문에 없는 값은 빨강) · 버전 이력(적재 / 정정 #N) · [이전][다음](목록 순서)
         담당 파트·관리자: [AI 수정 제안] [직접 수정] → POST /api/corrections(source) → §6-1 정정 요청에서 승인
         검토 중 정정이 있으면 두 버튼 막힘 + "정정 요청에서 보기"(/admin/q.corrections?focus=N — 그 카드로)
[참고표] GET /api/policy/ref-tables → GET /ref-tables/{id}: 열(결정 열 표시) + 행 {열: 값}, 머리 고정·가로 스크롤
[문서 개요] GET /api/policy/overviews: 문서 서문(목적·적용 대상)
[원문 확인] GET /api/policy/source-checks?namespace=&status=open|done   (v2.151 — 정정이 아니라 원문에 대한 질문·문제)
   ├─ 항목 = 정정 원장 kind='source_check'(새 테이블 없음): 담당자 질문(적재 점검) · 정책끼리 어긋남 · 참고표 오류(골든셋 작성)
   │     대상 = 정책 logical_id들 / 참고표 logical_id들(original), 설명 = user_input(원문 인용 없이)
   │     [해결](메모) POST …/source-checks/{id}/resolve → approved · [닫기](사유) …/dismiss → rejected — 메모는 ai_verdict {resolution, note}
   │     고칠 게 정해지면 정책 상세의 수정 제안 → §6-1 정정 요청(여기선 정책이 안 바뀜)
   │     정정 요청 목록·배지·내 신고·정정 처리 API(/api/corrections/{id}/…)는 이 kind를 뺀다(처리 API는 404)
   └─ 제안 연결 = ops_link status='suggested'(source v0_rule, 적재기가 원문 절 번호 오류를 규칙으로 추정한 정책→절 참조)
         [확정] POST /api/policy/links/{id}/confirm → active · [거절] …/reject → rejected (처리자는 로그)
   씨앗: scripts/load_policy_v0.py --add-source-checks <jsonl> [--dry-run] — 원천 키 → 현행 logical_id, (출처, 해시) 중복은 건너뜀
[JSON](관리자 전용, v2.151) — 문서 개정본용
   ├─ GET /api/policy/json/export?namespace= → {schema: opslens/policy-v0/1, domain, exported_at, policies[source_key, category_path,
   │     policy_name, raw_body, remark, order, version]} (현행만, 원천 키 없는 정책은 빼고 로그)
   ├─ POST /api/policy/json/preview {namespace, content}: 얇은 형식 또는 팀원 policies.jsonl(v0_convert.build_policies로 같은 정리)
   │     → 원천 키로 비교(원문·비고만): 같음 · 바뀜 · 파일에만(보고만 — 새 정책 추가 미지원) · DB에만(보고만, 지우지 않음)
   │       · 검토 중이라 제외(대기 정정 있음) · 이름·경로 변경(가져오지 않음, 보고만) + doc_hash(파일) · plan_hash(바뀐 목록·지금 원문 해시)
   └─ POST /api/policy/json/apply {…, doc_hash, plan_hash}: 다시 계산해 다르면 409 → 바뀐 정책마다 정정 제안 1건
         (create_policy_proposal mode json_import — 직접 수정처럼 받은 원문·비고 그대로, AI 없음, 정책 테이블엔 안 씀)
         → §6-1 정정 요청(출처 "JSON 가져오기")에서 승인(일괄 승인 포함) = 새 버전
조회 권한 = 로그인 사용자 누구나(check_knowledge_read), 제안·원문 확인 처리 = 담당 파트·관리자(check_namespace_ownership), JSON = 관리자
```
옛 엑셀 경로(정책서 올리기·붙여넣기 → LLM 분해 → 검토 대기, 위험도 자동 통과 큐, 미분류 집계, 줄 안 파라미터/서술 편집)는 v2.150에서 화면·API·모듈을
뺐다 — v0는 `scripts/load_policy_v0.py`로 적재하고 변경은 정정 요청으로만(문서 개정본도 JSON 가져오기 → 정정 요청, v2.151).

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
  base_weight = 1.0 (문서별 가중치 — v2.121부터 입력 없음, 전부 1.0 상수)

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
              = (0.7 × v_score + 0.3 × k_score) × 2.0  (base_weight 전부 1.0 → 순위엔 영향 없음)
  채팅 채택 게이트는 이 값을 2로 나눈 원점수(relevance_score)로 knowledge_min_score와 비교

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
  │  Admin → 시스템 설정 → LLM 프로바이더(관리자만)
  │  사내 LLM(모델·자격증명 입력) / 로컬 LLM(주소·모델은 .env, 보기만) 선택 + "저장 및 적용"  (Ollama는 .env·API로만, v2.143)
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
