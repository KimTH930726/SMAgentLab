# 지식(rag_knowledge) 생명주기 관리 — 현황 분석 및 장기 설계 (2026-08-28 기준)

> 목적: "신규 지식 등록이 기존 지식의 개정본인지 어떻게 판단하는가", "장기적으로 RAG를 계속
> 쓴다면 뭐가 더 필요한가"에 대한 분석과 결정 근거를 한 곳에 남긴다 — 대화로만 남으면
> 인수인계 시 또는 시간이 지나 소실될 수 있어 문서화. 실행 여부와 무관하게 "왜 이 순서로
> 정했는지"가 핵심이니, 우선순위를 바꾸기 전에 이 문서의 근거부터 재확인할 것.

---

## 1. 결론 요약

- 지금 시스템엔 "신규 등록이 기존 지식의 개정본인지"를 **자동 판단하는 로직이 없다** — 사람이
  코사인 유사도로 걸러진 후보를 직접 비교해서 승인/반려/병합을 결정한다(v2.34, 아래 §2).
- 장기적으로 필요한 기능(버전 관리, 소스 동기화, 품질 평가, 임베딩 모델 버전 추적 등)은
  실제로 존재하지만, **지금 전부 만들 필요는 없다** — 이유는 §4, §5.
- 실측 확인(§5.1): 중복탐지가 실제로 발동된 건 프로젝트 전체에서 7건뿐이고 전부 2026-07-14~16
  사흘 안에 몰려있다(최초 대량 반입 시점). 그 이후 한 달 넘게 재발동 이력이 없다. 이 사실이
  우선순위 결정에 직접 영향을 준다.

---

## 2. 현재 상태 — 코드 기준 정확한 인벤토리

### 2.1 `rag_knowledge` 스키마
`init/01-init.sql`의 `ops_knowledge`를 `main.py:61`에서 `rag_knowledge`로 rename. 이후 마이그레이션으로
`source_file`/`source_chunk_idx`/`source_type`(`main.py:923-925`), `status VARCHAR(20) DEFAULT 'active'`
(`main.py:952`) 추가. 전체 18컬럼은 `docs/table-definition.md:172-191` 참조 — 문서와 코드 불일치 없음.

### 2.2 중복 등록 방지 (v2.34) — 사실상 "Knowledge Resolver"의 절반
- 트리거: `create_knowledge`(단건, `service.py:35-94`)와 `bulk_create_knowledge`(배치, `service.py:571-728`).
  **`update_knowledge`(`service.py:97-148`)는 중복 검사를 아예 안 함.**
- 비교 신호: `content` 임베딩의 순수 코사인 유사도만(`find_similar_active_knowledge`, `retrieval.py:106-125`).
  category/container_name 등 다른 필드는 후보 검색에 안 씀.
- 임계치: `duplicate_min_similarity` 기본 0.88(`core/config.py:60`).
- 임계치 이상이면 `status='pending_review'`로 저장 + 상위 3개 후보를 `rag_knowledge_duplicate_match`
  (영구 테이블: `id, new_knowledge_id, matched_knowledge_id, similarity, created_at`, `main.py:955-962`)에 기록.
- 처리 방식은 **승인/반려/병합** 3가지(`resolve_duplicate`, `service.py:302-364`, 라우터
  `POST /api/knowledge/{id}/resolve`):
  - **승인**: `pending_review` → `active`로 바뀔 뿐, 기존 유사 지식과 **병합되지 않고 별도 행으로 공존**한다
    (`service.py:324-327`). 이름과 달리 "이게 맞다"고 승인해도 중복이 그대로 남을 수 있다는 뜻 — UX상
    헷갈릴 수 있는 지점.
  - **반려**: `status='rejected'`로만 바뀜(하드 삭제 아님, 감사 기록으로 남음).
  - **병합**: 기존(타겟) 지식의 `content`/`embedding`/`updated_at`을 새 내용으로 **그 자리에서 덮어씀**
    — 이전 내용은 어디에도 보존되지 않는다(`service.py:340-364`). **실질적 데이터 소실 위험.**

### 2.3 버전/생명주기 — 없음
`logical_document_id`/`version` 분리, `supersedes_id`, `effective_from/to` 전부 없음. `status`
(`active`/`pending_review`/`rejected`, 3개뿐)와 `updated_at`만 변경 추적 수단.

### 2.4 삭제 — 하드 삭제
`delete_knowledge`/`bulk_delete_knowledge`(`service.py:151-166`)는 진짜 `DELETE FROM`. 복구 불가.

### 2.5 소스 동기화 / CDC — 없음, 그리고 구조적으로 지금은 불필요
`rag_ingestion_job`은 등록 이력만 남기지 content-hash나 변경감지 필드가 없다. 100% 관리자 수동
등록(`manual/csv_import/paste_split/file_upload/web/confluence/confluence_bulk/teams`)이라 재수집 시
"전에 가져온 적 있는지" 매칭할 안정적 외부 ID 자체가 없다 — Confluence 페이지를 다시 긁어도
페이지 ID로 매칭 안 되고 코사인 유사도로만 중복 판정됨.

**중요한 반례 — 같은 코드베이스 안에 이미 정답에 가까운 패턴이 있다**: Text2SQL의 스키마 스캔
(`agents/text2sql/admin/service.py:134` 이하)은 원격 DB에 실제로 접속해 테이블/컬럼을 diff 비교하고
변경분만 재임베딩하며 고아 관계까지 정리한다 — CDC+Reconciliation을 이미 구현하고 있다. 즉 "이
패턴을 몰라서 rag_knowledge에 안 한 게" 아니라 **rag_knowledge엔 diff 비교할 원본 시스템 자체가
없어서** 안 한 것. 나중에 실제 외부 시스템과 동기화하게 되면 이 패턴을 그대로 가져다 쓰면 된다
(새로 설계할 필요 없음).

### 2.6 품질/신선도 — 랭킹용일 뿐, 생명주기와 무관
`freshness_decay_halflife_days`(`core/config.py:69`, 기본 0=비활성)는 검색 시 `final_score`에 감쇠를
곱할 뿐(`retrieval.py:193-206`) — 오래된 지식을 플래그하거나 리뷰 큐로 보내는 동작은 전혀 없다.
LLM 기반 품질 심사(Uber Genie류)도 없음.

### 2.7 임베딩 모델 버전 — 추적 안 됨 (실제 위험)
`rag_knowledge`엔 자신의 `embedding`이 어떤 모델로 만들어졌는지 기록하는 컬럼이 없다.
`rag_ingestion_job.embedding_model`(`main.py:940`)만 job 단위로 기록되고 다시 읽히지 않는다(write-only).
**모델을 교체하면 신구 벡터가 같은 컬럼에 조용히 섞이고, 어느 행이 재임베딩됐는지 구분할 방법이
지금 없다** — 이전에 논의했던 "임베딩 모델 교체 시 전체 재인덱싱 필요"라는 말의 실제 근거가
DB에 없는 상태.

---

## 3. 이번 세션에서 참고한 사용자 제공 프레임워크 (요약)

사용자가 Databricks/AWS Bedrock/Azure AI Search/Google Vertex/Glean/Slack/Uber 사례를 조사해 제시한
프레임워크 핵심:
- Knowledge Registry(logical_id/version/status/effective_from-to/supersedes) 계층을 원본과 Vector DB
  사이에 둔다.
- 변경을 CREATE/UPDATE/DELETE/SUPERSEDE 4종으로 나눈다.
- Incremental Sync + 주기적 Full Reconciliation을 병행(원본-Registry-VectorDB 3자 diff로
  ORPHAN/STALE/MISSING/VERSION CONFLICT 탐지).
- 신규 지식 유입 시 "Knowledge Resolver": 후보 검색(hybrid) → LLM이 관계를
  NEW/DUPLICATE/UPDATE/SUPERSEDE/CONFLICT/RELATED로 분류 → source_id 일치처럼 확실한 경우만
  자동 처리, 나머지는 사람 리뷰.
- LLM 기반 지식 품질 평가 에이전트(Uber Genie), 임베딩/청킹 버전 별도 관리 + Blue/Green 인덱스 전환.
- 모니터링 지표 8개(Sync Lag, Stale Rate, Orphan Rate, Missing Rate, Version Conflict Rate,
  Unowned Rate, Expired Retrieval Rate, Latest-Version Hit Rate) — 특히 Expired Retrieval Rate=0%를
  불변식으로.

이 프레임워크는 **"원본 시스템에서 CDC/webhook으로 당겨오는" 구조를 전제**한다 — §2.5에서 확인했듯
우리는 그 원본 시스템 자체가 없어서(사람이 곧 입력 소스) 이 프레임워크의 상당 부분(CDC, Incremental
Sync, Reconciliation, source_id 자동매칭)이 지금 그대로 적용될 대상이 아니다. 반면 Knowledge Resolver
(신규 vs 업데이트 판정)는 소스 시스템 유무와 무관하게 바로 적용 가능하고, v2.34 인프라가 이미 절반
있다.

---

## 4. 장기 목표 아키텍처 (지금 안 만들어도, 스키마는 지금 정해야 하는 것)

핵심 원칙: **스키마(컬럼 추가)는 지금이 제일 싸고, 로직(자동화)은 나중에 붙여도 비용이 같다.**
지식 3천여 건인 지금 컬럼 추가는 사실상 공짜지만, 10만 건 쌓인 뒤 logical_id/version을 처음
도입하려면 전체 백필이 필요해 훨씬 비싸진다.

```
Phase 0 (지금, 스키마만) ─────────────────────────────
rag_knowledge에 nullable 컬럼만 추가, 로직 없음:
  logical_document_id (기본값=자기 id), version(기본값 1), supersedes_id(nullable),
  embedding_model(신규분부터 채움, 과거분은 NULL="모델 불명"), quality_score/reviewed_at/owner(nullable)

Phase 1 (지금 착수 권장 — 실제 위험 완화) ───────────────
- 병합(merge) 시 이전 content를 간단한 이력 테이블에 보존 (§2.2 데이터 소실 위험 직접 대응)
- 삭제를 soft-delete로 전환(status='deleted') (§2.4 하드 삭제 위험 대응)

Phase 2 (트리거: 외부 시스템과 실제로 동기화를 시작할 때) ──
- Text2SQL 스키마 diff 패턴(§2.5)을 rag_knowledge에 이식
- source_id 매칭 → 자동 UPDATE 경로, Reconciliation 배치

Phase 3 (트리거: 중복/오래된 지식이 실제 운영 부담이 될 때) ─
- LLM Comparator(§5.2에서 지금은 보류 결정 — 재검토 트리거 참고)
- LLM 품질 평가 에이전트 — VOC의 _verify_coverage_with_llm 패턴 재사용 가능
- review_due_at 기반 리뷰 큐

Phase 4 (트리거: 임베딩 모델을 실제로 교체하는 시점) ──────
- Phase 0의 embedding_model 컬럼으로 "재임베딩 안 된 행" 필터링
- Blue/Green 인덱스 전환 + 회귀 검증 게이트
```

---

## 5. 우선순위 결정과 그 근거 (실측 기반, 2026-08-28)

| 순위 | 항목 | 위험도/가치 | 작업량 | 상태 |
|---|---|---|---|---|
| 1 | 병합 시 기존 content 이력 보존 | 위험 높음(지금도 열려있는 사고) | 작음 | 미착수 |
| 2 | 삭제 하드→soft-delete | 위험 높음 | 작음 | 미착수 |
| 3 | Phase 0 스키마 컬럼 선추가 | 낮음(지금), 나중엔 비쌈 | 작음 | 미착수 |
| 4 | LLM Comparator(중복탐지 AI 보조) | **재평가 후 보류** — §5.2 | 중간 | 보류 |
| 5 | rag_knowledge 88% 표형식 오염 정리 | 이미 확인된 품질 문제 | 스코프 결정 필요 | 검색 정확도 영향은 v2.49로 완화(아래 참고), 데이터 자체 정리는 별도 논의 대기 |

### 5.1 실측: 중복탐지 발동 빈도

```sql
-- rag_knowledge_duplicate_match 전체
count=7, 기간: 2026-07-14 ~ 2026-07-16 (사흘)
-- 이후 재발동: 0건 (2026-08-28 기준 한 달 넘게)

-- rag_knowledge 월별 등록량
2026-03: 17건, 04: 2건, 05: 3건, 06: 5건, 07: 3050건(99%), 08: 0건
```

**해석**: 지식 3077건 중 3050건(99%)이 2026-07 한 달, 사실상 최초 대량 반입 1회에 쏠려 있다.
중복탐지 7건도 전부 그 반입 사흘 안에서 나온 것이고, 그 이후 일상적 개별 등록에서는 단 한 번도
재발동하지 않았다.

### 5.2 LLM Comparator(#4) 우선순위를 낮춘 이유

처음엔 "VOC 커버리지 검증에 쓴 패턴을 재사용하면 싸다"는 이유로 4번을 상위권에 뒀으나,
위 실측(§5.1)을 반영해 재조정한다. **이 기능이 실제로 판단을 도와줄 이벤트 자체가 지금
거의 발생하지 않는다** — 등록 볼륨이 월 2~17건 수준이면 애초에 사람이 직접 봐도 부담이 크지
않다. 이 상태로 만들면 few-shot 승인 큐(활성 1건/대기 12건, `feedback_*` 메모리 참고)와 같은
"만들었지만 방치되는 기능" 패턴이 될 위험이 크다.

**재검토 트리거**: 지식 등록이 정기적 프로세스(예: 매주 N건 이상)로 바뀌거나, Confluence
재수집을 주기적으로 돌리기 시작해 실제로 재등록 볼륨이 늘어나는 시점. 그때 이 문서의 §5.1
숫자를 다시 재보고 판단할 것.

---

## 6. 다음에 이어서 할 일

- [ ] Phase 1(병합 이력 보존, soft-delete) 실행 여부 결정 및 착수
- [ ] Phase 0(스키마 선추가) 실행 여부 결정 및 착수
- [x] #5(rag_knowledge 88% 표형식 오염)의 **검색 정확도 영향**은 v2.49로 완화 —
      "벡터화되면 안 되는데 벡터화된 것"에 해당(코드표 덤프를 코사인 유사도로 비교하면
      어휘만 겹쳐도 오탐)을 확인해, `category IN ('DB','공통코드')` 행은 `search_knowledge()`
      랭킹에서 벡터 점수를 0으로 만들고 키워드 매칭만 쓰도록 라우팅(`retrieval.py`
      `_KEYWORD_ONLY_CATEGORIES`, `pattern_detection.py` 커버리지 후보에서도 동일 제외).
      상세: `docs/architecture.md` v2.49. **단, 데이터 자체(88% 표형식 오염) 정리·재구조화는
      스코프 밖** — 다른 기능(Text2SQL 등)이 이 원본 데이터를 실제로 쓰는지 확인 후 별도 논의 필요
- [ ] §5.2 재검토 트리거 발생 시 이 문서를 갱신하고 LLM Comparator 우선순위 재논의
