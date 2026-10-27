# 처리 흐름 — 배경 설명 (flow.md에서 분리, 2026-10-27)

> 동작 원리·SQL 상세처럼 자주 안 바뀌는 배경. 지금 흐름은 `docs/flow.md`.

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
