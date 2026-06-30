# 지속 개선형 RAG 파이프라인 — 고도화 전략 로드맵

> 작성일: 2026-06-30  
> 현재 버전: v2.19  
> 목적: 발표 "지속 개선형 RAG 파이프라인 구축" 근거 자료 + 엔지니어링 백로그

---

## 현재 파이프라인 (v2.19 기준)

```
질문 입력
  → 임베딩 (paraphrase-multilingual-mpnet-base-v2, 768d)
  → Semantic Cache 조회 (Redis, vec[:64] MD5)
  → Glossary Term Mapping (코사인 유사도 ≥ 0.5)
  → Enriched Query 구성 (원문 + 매핑 용어)
  → 1차 하이브리드 검색 (Vector × 0.7 + Keyword × 0.3, pgvector)
  → [선택] CrossEncoder 리랭킹 (20 후보 → top_k)
  → [선택] Freshness Decay (반감기 감쇠, 최소 50%)
  → Few-shot 섹션 병합
  → LLM 컨텍스트 생성 + SSE 스트리밍 응답
  → 쿼리 로그 기록 (status: pending / no_knowledge / unresolved)
  → 피드백 수집 (thumbs up/down) → base_weight 조정
  → 4교환마다 대화 요약 → pgvector 저장 (Semantic Recall)
```

### 이미 구현된 핵심 루프

| 레이어 | 구현 | 효과 |
|--------|------|------|
| 지식 등록 | LLM 자동 태깅 · 용어 추출 · Q&A 자동 생성 | 운영자 부담 절감 |
| 검색 품질 | Glossary Mapping + Hybrid Search | 도메인 용어 정확도 |
| 재정렬 | CrossEncoder Reranker (v2.19) | 최종 랭킹 품질 |
| 신선도 | Freshness Decay (v2.19) | 오래된 지식 패널티 |
| 갭 감지 | `no_knowledge` status DB 로깅 (v2.19) | 지식 공백 추적 가능 — 어드민 UI는 Phase 1-3 예정 |
| 피드백 | thumbs up/down → base_weight 반영 | 암묵적 사용자 신호 |
| 메모리 | ConversationSummaryBuffer + Semantic Recall | 멀티턴 컨텍스트 |

---

## Phase 1 — 평가 프레임워크 구축 (단기 1~2개월)

> **배경**: 개선이 실제로 효과 있는지 측정할 기준이 없으면 어떤 변경도 검증 불가.

### 1-1. 골든셋 Q&A 평가 데이터셋 구축

- 실제 사용자 질문 100~200개 + 정답 지식 ID 레이블링
- 지식베이스 관리자 + 도메인 전문가 검토
- `docs/eval/golden_set.jsonl` 형태로 버전 관리

### 1-2. 오프라인 평가 지표

| 지표 | 설명 | 목표 |
|------|------|------|
| **Recall@K** | 정답 청크가 top_k 안에 포함된 비율 | ≥ 0.85 |
| **MRR** (Mean Reciprocal Rank) | 정답이 몇 번째에 나오는지 역수 평균 | ≥ 0.75 |
| **nDCG@3** | 순위 가중 정확도 | ≥ 0.70 |
| **Answer Faithfulness** | LLM 답변이 컨텍스트에 근거하는 비율 | ≥ 0.90 |
| **No-context Rate** | `no_knowledge` 비율 | ≤ 0.10 |

### 1-3. 실시간 모니터링 지표 (기존 infra 활용)

```sql
-- 지식 갭 top 질문 (어드민 대시보드 후보)
SELECT question, COUNT(*) AS cnt
FROM ops_query_log
WHERE status = 'no_knowledge'
  AND created_at > NOW() - INTERVAL '7 days'
GROUP BY question
ORDER BY cnt DESC
LIMIT 20;
```

- `unresolved` / `no_knowledge` / `pending` 비율 주간 추적
- 피드백 thumbs down 비율 추적

---

## Phase 2 — 검색 품질 심화 (중기 2~4개월)

### 2-1. HyDE (Hypothetical Document Embedding)

**아이디어**: 사용자 질문 → LLM이 "가상의 답변 문서" 생성 → 그 임베딩으로 검색  
→ 짧은 질문 임베딩보다 실제 지식 문서의 임베딩 분포에 더 가깝게 매핑

```python
# 구현 위치: retrieval.py / agent.py
async def hypothetical_embed(question: str) -> list[float]:
    hypo_doc = await llm.generate_one_shot(f"다음 질문에 대한 짧은 답변 문서를 작성하라: {question}")
    return await embedding_service.embed(hypo_doc)
```

**주의**: LLM 호출 1회 추가 → 레이턴시 +1~3초. 캐시 적용 필수.

### 2-2. Parent-Child Chunking

**아이디어**: 현재는 청크 단위로 임베딩 + 저장. 검색은 세밀한 청크(child)로, 컨텍스트는 원본 섹션(parent)으로 LLM에 전달.

```
[지식 등록 시]
Parent 단위 저장 (섹션 전체 텍스트)
  └─ Child 청크들 (임베딩 대상, parent_id FK)

[검색 시]
Child 임베딩으로 유사 청크 찾기
  → parent_id로 원본 섹션 로드
  → LLM에 섹션 전체 전달
```

**DB 변경**: `rag_knowledge`에 `parent_id` 컬럼 추가, `is_summary` 플래그.

### 2-3. 멀티쿼리 확장

동일 질문을 다양한 관점으로 재작성해 검색 → 결과 union 후 dedup

```python
queries = await llm.rephrase_queries(question, n=3)
# e.g., "운영비용 조회" → ["비용 현황", "월별 운영비", "경비 내역"]
all_results = await asyncio.gather(*[search(q) for q in queries])
merged = deduplicate(flatten(all_results))
```

### 2-4. 리랭커 다국어 모델 교체

현재: `cross-encoder/ms-marco-MiniLM-L-6-v2` (영문 최적화)  
개선안: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (다국어 지원)  
→ `RERANKER_MODEL` env만 변경하면 즉시 적용

---

## Phase 3 — 지속 개선 루프 자동화 (중장기 4~8개월)

### 3-1. 지식 갭 자동 제안

`no_knowledge` 쿼리를 클러스터링 → 유사 질문 그룹화 → 어드민에 "미등록 주제 TOP 10" 위젯 노출 → 원클릭 지식 등록 초안 생성

```
ops_query_log (no_knowledge)
  → 주 1회 batch 클러스터링 (k-means on embeddings)
  → rag_knowledge_suggestion 테이블 적재
  → 어드민 "지식 갭 제안" 탭 노출
```

### 3-2. 자동 품질 저하 감지

```python
# 주간 배치: 골든셋 대상 Recall@3 측정
recall = evaluate_golden_set()
if recall < THRESHOLD:
    send_alert("RAG 품질 저하 감지: Recall@3 = {recall:.2f}")
```

### 3-3. 피드백 기반 지식 가중치 자동 재조정

현재: thumbs up/down 즉시 `base_weight` ±0.1 조정  
개선: Wilson score 기반 통계적 신뢰 구간 → 샘플 적을 때 과대반응 방지

```python
def wilson_score(ups: int, n: int, confidence: float = 0.95) -> float:
    # 이항분포 신뢰 하한 — 소수 샘플일 때 보수적 추정
    ...
base_weight = wilson_score(positive_count, total_feedback)
```

### 3-4. 지식 노후화 알림

- `updated_at`이 N일 이상 지난 지식 중 최근 조회 빈도 높은 항목 → 어드민 알림
- 쿼리 로그의 `message_id` FK → `rag_knowledge.id` JOIN으로 "자주 쓰이는 오래된 지식" 파악

---

## Phase 4 — 지식 수집 자동화 (장기 6개월+)

### 4-1. Confluence Webhook 연동

Confluence Space에 페이지 변경 webhook 등록 → 백엔드가 수신 → 자동 재수집 + 청킹 + 임베딩 업데이트

```
Confluence Page Update
  → webhook POST /knowledge/import/confluence/webhook
  → 기존 청크 삭제 (같은 source_url)
  → 재파싱 + 재임베딩 + 저장
```

### 4-2. 정기 크롤링 스케줄러

APScheduler 또는 celery beat로 등록된 URL 목록 주기적 재수집

```python
@scheduler.scheduled_job("cron", hour=3)  # 새벽 3시 자동 갱신
async def refresh_knowledge_sources():
    sources = await get_scheduled_sources()
    for s in sources:
        await reimport_url(s.url, s.namespace)
```

### 4-3. 능동 학습 (Active Learning) 루프

- 모델이 불확실한 질문(리랭커 top score 낮음 + `no_knowledge`) → 사용자에게 명시적 피드백 요청
- 수집된 피드백 → few-shot 자동 등록 → 다음 검색에 반영

---

## 구현 우선순위 요약

| 우선순위 | 항목 | 예상 효과 | 난이도 |
|---------|------|-----------|--------|
| ★★★ | 골든셋 구축 + Recall@K 측정 | 모든 개선의 기준선 | 중 |
| ★★★ | 지식 갭 제안 UI (no_knowledge 활용) | 운영자 즉시 활용 | 하 |
| ★★☆ | Parent-Child 청킹 | 긴 문서 정확도 ↑ | 상 |
| ★★☆ | HyDE | 짧은 질문 recall ↑ | 중 |
| ★★☆ | 다국어 리랭커 모델 교체 | 한국어 re-ranking ↑ | 하 |
| ★☆☆ | Wilson score 가중치 조정 | 피드백 안정성 ↑ | 중 |
| ★☆☆ | Confluence Webhook | 지식 freshness 자동화 | 상 |
| ★☆☆ | 능동 학습 루프 | 장기 품질 복리 효과 | 상 |

---

## 참고: 현재 코드 진입점

| 기능 | 파일 |
|------|------|
| 하이브리드 검색 | `backend/agents/knowledge_rag/knowledge/retrieval.py` |
| 리랭커 | `backend/shared/reranker.py` |
| RAG 파이프라인 오케스트레이션 | `backend/agents/knowledge_rag/agent.py` |
| 쿼리 로그 / 갭 감지 | `backend/service/chat/helpers.py` → `create_query_log` |
| 임베딩 싱글톤 | `backend/shared/embedding.py` |
| 청킹 | `backend/agents/knowledge_rag/ingestion/chunker.py` |
| 설정 | `backend/core/config.py` |
