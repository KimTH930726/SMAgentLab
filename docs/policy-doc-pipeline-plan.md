# 정책서 데이터화 파이프라인 + 저장소 전략 실험실 — 설계 브리프

> 작성일: 2026-09-03 (2026-09-03 SMAgentLab 구현 세션에서 §3 자산 실측 감사 + §2-1 버전 관리 반영)
> 상태: **1차 검토 완료** — §7 미확정 항목(외부 SoR API 스펙, 정책서 원문 완료 시점, condition 표현 방식,
> system_key/namespace 전략, effective_from/to 필요 여부) 해소 전까지는 스키마 최종 확정 아님.
> 목적: 엑셀로 된 비즈니스 정책서를 운영에서 활용 가능한 데이터로 전환하는 **파이프라인(Track 1)** 과, 그 결과를 어떤 저장소 전략으로 관리할지 **수치로 판정하는 실험실(Track 2)** 을 설계한다.
> 관련: `D:\MD자료\work-os\WBS\smagent.md`, `docs/knowledge-lifecycle-design.md`, `docs/rag-improvement-strategy.md`, `docs/knowledge-refresh-automation-plan.md`

---

## 0. 배경 / 위치

- **AIOps 팀 핵심**: 비즈니스 정책서를 데이터화해 운영에 활용할 수 있는 구조를 만드는 것. 공통 데이터 허브의 한 축.
- **현재 SMAgentLab**: VOC류 — 운영 담당자 노하우 / 기존 해결방안을 `rag_knowledge`에 등록 → LLM이 유사 사례에서 해결방안 유추. 정책서는 그 옆에 붙는 **새로운 지식 종류**.
- **최종 착지**: SMAgentLab에서 프로토타이핑 → 팀 공통 포털의 백엔드 모듈로 이관. SMAgentLab 전용 인프라(자체 인증·배포)에 과투자 금지, 처음부터 포털에 얹을 수 있게 API 경계를 깨끗하게.

## 1. 입력 분석 — 정책서 엑셀 (샘플 기준)

- **row 단위.** 시스템별 100~200 row. 시트별 컬럼 구조 거의 동일하게 맞춰져 있음.
- 컬럼 구성: `[순번] [대분류] [중분류] [소분류/처리단계(가끔 빈값)] [기능명] [정책 본문] [빈 컬럼 다수] [비고] ...`
- **정책 본문** = 중첩 리스트(`1.` / `1)` / `-`). 그 안에 성격이 섞여 있음:
  - **(a) 서술 규칙** — "재고 없으면 SOLD OUT 표기", "행사 있으면 할인 문구 노출". 자연어 Q&A 대상.
  - **(b) 파라미터 팩트** — "일반 배달 20개 / 도보 배달 4개", "최소 주문 일반 15,000 / 야구장 8,000", "배달 반경 2km(일부 2.5~3km)", "SSGPAY: 신용카드·계좌·SSG머니". 이산 key-value, 조건부.
- **비고** = 외부 SoR(System of Record) 포인터. "영업정보 시스템에 세팅된 정책을 활용하며 변경될 수 있음" → **이 값에 대해서는 정책서가 authoritative가 아님**.

## 2. 데이터 모델 — 한 row를 3층으로 분해

| 테이블 | 단위 | 성격 | 저장소 |
|---|---|---|---|
| `policy_item` | 엑셀 row 1개 = 1 row | 카테고리 경로 + 원문 + 메타 | RDB |
| `policy_param` | `policy_item` 1:N | 추출된 파라미터 팩트 (조건부 key-value) | RDB |
| `policy_chunk` | `policy_item` 1:N | 서술 규칙 부분만 청킹·임베딩 | Vector (`rag_knowledge` 재사용 검토) |

스키마 스케치 (구현 세션에서 확정):

```
policy_item
  id, system_key, 대분류, 중분류, 소분류, 기능명,
  raw_body(text), 비고(text),
  source_file, source_sheet, source_row, content_hash,
  status('draft'|'pending_review'|'active'|'deprecated'),
  logical_id(기본=자기 id), version(기본 1), supersedes_id(nullable),
  created_at, updated_at, reviewed_at, reviewed_by

policy_param
  id, policy_item_id(FK),   -- 버전마다 새 policy_item row가 생기므로 FK를 통해 자동으로 버전 격리됨(아래 §2-1)
  name(예: "장바구니 최대 개수"),
  condition(예: "일반 배달" / "도보 배달" / "야구장 입점 매장"),   -- 조건 표현 방식은 §7 미확정
  value, unit,
  external_source(nullable, 예: "영업정보 시스템"),   -- 비고에서 도출, 신뢰 주의 플래그
  approved(bool), created_at

policy_chunk  -- rag_knowledge 재사용 시: policy_item_id를 source_ref로 연결하고 별도 테이블 불필요할 수 있음
  id, policy_item_id(FK), chunk_text, embedding, chunk_idx
```

**왜 3층인가**: 파라미터 팩트를 벡터화하면 어휘만 겹쳐도 오탐이 난다 — `docs/knowledge-lifecycle-design.md`의 "rag_knowledge 88% 표형식 오염" 및 v2.49의 `_KEYWORD_ONLY_CATEGORIES`(코드표는 벡터 점수 0, 키워드 매칭만)와 정확히 같은 문제. 파라미터는 구조화해서 정확 조회, 서술은 벡터로 의미 검색, 카테고리는 RDB로 네비게이션.

### 2-1. 버전 관리 — 과거 정책도 조회 가능해야 한다 (2026-09-03 반영)

정책은 계속 바뀐다 — 그리고 "지금 정책이 뭔지"뿐 아니라 "예전엔 뭐였는지"도 조회 대상이어야
한다. `policy_item`에 이미 `logical_id`/`version`/`supersedes_id`가 있지만, §3 파이프라인의
"재업로드 시 바뀐 row만 재처리"라는 표현이 **그 자리에서 UPDATE하는 건지 새 row를 INSERT하는
건지 애매했다** — 전자면 `rag_knowledge` 병합(merge)이 `content`를 덮어써 이력이 소실되던 것과
똑같은 위험(`knowledge-lifecycle-design.md` 우선순위 1위 이슈)을 그대로 반복하게 된다.

**확정**: content_hash가 바뀌면 기존 row를 UPDATE하지 않는다 — **새 `policy_item` row를 INSERT**
한다(`logical_id`는 이전 row와 동일하게 유지, `version`+1, `supersedes_id`=이전 row의 id). 이전
row는 `status='deprecated'`로 전환하되 **삭제하지 않고 그대로 보존** — 과거 정책 조회는 이
`deprecated` row들을 그대로 읽으면 된다.

`policy_param`엔 별도 버전 컬럼을 추가하지 않는다 — `policy_item_id` FK가 특정 버전(row)을
가리키므로, 새 버전의 `policy_item`이 생기면 그 파라미터들도 새 `policy_item_id`에 딸린 새
row로 재생성되고, 이전 버전의 파라미터는 이전 `policy_item_id`에 그대로 남아 자동으로 버전이
격리된다 — FK 관계를 통한 자연스러운 이력 보존.

## 3. Track 1 — 정책서 데이터화 파이프라인

```
엑셀 업로드
  → row 파싱 (카테고리 경로 + raw_body + 비고 추출)
  → LLM 분해: raw_body에서 (a) policy_param 후보 추출  (b) 서술 규칙 부분 식별
  → policy_item + policy_param(후보) + policy_chunk(후보) 를 status='pending_review'로 적재
  → 검토 UI: 사람이 파라미터 값·조건 위주로 승인/수정/반려
  → 승인 → RDB 확정 + policy_chunk 임베딩·인덱싱
  → 재업로드 시: source_row + content_hash 비교 → 바뀐 row만 재처리 (pending_review)
    ※ "재처리" = 기존 row UPDATE가 아니라 새 row INSERT(version+1, supersedes_id=이전 row) —
      이전 row는 deprecated로 전환·보존(§2-1). 과거 정책 조회는 deprecated row를 그대로 읽음
  → 비고에 external_source 있는 파라미터는 "신뢰 주의 — 외부 시스템 관리" 플래그 표시
```

**Phase 구분**
| Phase | 범위 |
|---|---|
| 1 | 파서 + LLM 분해 + pending_review 적재 + 검토 UI + 승인 → 확정. 재업로드 content_hash diff(§2-1: UPDATE 아닌 새 버전 INSERT). 재검토 큐에 오른 row는 이전 버전과의 **diff를 검토 화면에 표시**(전체 재검토 부담 축소). **여기까지가 "PoC 올리는" 최소 동작.** |
| 2 | external_source 파라미터의 실제 대조(reconcile) — 영업정보 시스템 등 조회 API 연동. Text2SQL의 스키마 스캔(원격 접속 → diff → 변경분만 갱신) 패턴 재사용 검토 — ⚠️ 원본(`agents/text2sql/admin/service.py`)은 2026-09-03 `dev_0`/`main`에서 제거됨, `archive/with-text2sql` 브랜치에서 패턴만 참고해 새로 이식해야 함. |
| 3 | 정책서 원문 변경 알림 / 대량 변경 안전장치 — `docs/knowledge-refresh-automation-plan.md`와 스케줄러 인프라 공유(아래 자산 표 — 스케줄러 이미 존재). |

**현재 자산 — 재사용 / 신규 (2026-09-03 구현 세션에서 실측 확인 완료)**
| 구성 요소 | 실측 결과 | 확인 포인트 |
|---|---|---|
| 청킹 | `agents/knowledge_rag/ingestion/chunker.py`의 `chunk_document(doc, strategy=...)` 실존(strategy: `auto`/`section`/`paragraph`/`fixed`) | 정책서 서술부(중첩 리스트 `1./1)/-`)에 맞는 strategy가 기존 4종에 없음 — **신규 strategy 추가 필요할 가능성 높음**, "그대로 재사용" 아님 |
| pending_review 상태값 + 리뷰 UI | 상태 패턴(`resolve_duplicate`)은 실존하지만 프론트(`KnowledgeTable.tsx`)는 지식 행 전용 컴포넌트 | UI는 **재사용 아니라 패턴 복제** — `policy_param` 승인 화면은 신규 컴포넌트 |
| 임베딩 | `shared/embedding.py` 싱글톤(mpnet 768d) 재사용 | — |
| 검색 라우팅 | `agents/knowledge_rag/knowledge/retrieval.py` + `_KEYWORD_ONLY_CATEGORIES` 패턴 재사용 | policy 소스용 라우팅 추가 방식 |
| threshold 설정 | `agents/knowledge_rag/knowledge/retrieval.py`의 `get_thresholds`/`set_thresholds`, `PUT /api/llm/thresholds` 패턴 (※ `core/config.py` 아님 — 위치 정정) | 대량 변경 임계값 등 |
| 엑셀 파서 | 완전 신규는 아님 — text2sql 엑셀 임포터(헤더 퍼지매핑, preview→confirm 2단계, 병합셀 처리)가 같은 문제를 이미 풀었던 패턴. 코드는 `archive/with-text2sql` 브랜치에 있음(`excel_importer.py`), **패턴만 참고해 새로 작성** | openpyxl 등, 병합셀 처리 |
| policy_item/param 스키마 | **신규** 마이그레이션, §2-1 버전 관리 반영 | — |
| 스케줄러 | **실존** — `service/email_voc/scheduler.py`(30초 체크 간격, namespace별 격리, 중복실행 방지, 설정 즉시 반영 패턴) 그대로 재사용 가능. Phase 3에 신규 구축 불필요 | Phase 2~3에서 사용 |

## 4. Track 2 — 저장소 전략 실험실

**질문**: 정책서를 `rag_knowledge`에 "지식으로 얹기"만 해도 되는가, 아니면 별도 스키마(§2)가 얼마나 이득인가 — **수치로**.

**쿼리 유형 4종**
| 유형 | 예시 | 이론상 유리한 저장소 |
|---|---|---|
| 카테고리 네비 | "장바구니 관련 정책 다 보여줘" | RDB |
| 파라미터 조회 | "일반 배달 장바구니 최대 몇 개?" | RDB `policy_param` |
| 서술 Q&A | "재고 없으면 어떻게 표시돼?" | Vector |
| 조건 필터 | "야구장 매장 규칙만 알려줘" | RDB 조건 + Vector |

**비교군**
- A: `rag_knowledge`에 정책서 통째로 청킹해 얹기 (지식-only)
- B: 별도 스키마 하이브리드 (`policy_item`/`policy_param` 정확 조회 + `policy_chunk` 벡터)
- (선택) C: 파라미터만 RDB-only

**측정**: 골든셋 30문항(유형별 분포) → 각 비교군에 동일 질문 → 유형별 정답률 + 전체.
**산출물**: "파라미터 조회에서 A 63% → B 90%" 식 표. 팀 보고 근거 = "별도 스키마가 필요한 이유"를 숫자로.

- **골든셋**: 정책서 원문 완료 후 태훈이 제공(질문 + 정답 + 출처 row). Track 1 Phase 1 구현이 선행, 실험실 비교 실행은 골든셋 도착 후.
- **분가 가능**: Track 2는 Track 1의 출력에 의존하지만 별도 프로젝트로 진행 가능. 현재는 `work-os/WBS/smagent.md` A2. 다리가 붙으면 독립 WBS로.
- **되먹임**: 실험실 결과가 Track 1이 최종 커밋할 스키마 형태를 결정한다.

## 5. 포털 착지 관점 (API 경계)

파이프라인을 서비스로 노출 — SMAgentLab 내부 구조에 의존하지 않게(기존 컨벤션에 맞춰 `/api/` 프리픽스 사용, `/api/knowledge/*`·`/api/email-voc/*`와 동일):
- `POST /api/policy/import` (엑셀 업로드), `GET /api/policy/items?category=`, `GET /api/policy/params?item=&condition=`, `POST /api/policy/{id}/resolve` (승인)
- `GET /api/policy/items/{logical_id}/history` — 특정 정책의 과거 버전 전체 조회(§2-1 버전 관리의 직접적인 산출물)
- 검색: 기존 RAG 검색에 `policy` 소스 라우팅 추가 (chunk를 `rag_knowledge` FK로 연결하면 자연스러움)
- 인증은 주입받는 형태 (사내 SSO는 포털 레이어에서 처리 — 진행 중인 사내 SSO 통합 작업과 방향 조율 필요)

## 6. 다음 액션 (구현 세션)

- [ ] §3 "현재 자산" 표 확정 — `rag_knowledge` 스키마, `chunker`, `retrieval` 라우팅, pending_review UI 재사용 범위 (2026-09-03 1차 실측 완료, 위 표 참고)
- [ ] `policy_item` / `policy_param` / (`policy_chunk`) 스키마 마이그레이션 초안 — §2-1 버전 관리(logical_id/version/supersedes_id, INSERT-only) 반영
- [ ] 엑셀 파서 + LLM 분해(파라미터/서술 분리) 프로토타입 — 이 브리프의 샘플 9 row로 먼저
- [ ] 검토 UI: 파라미터 값·조건 승인 화면 + 재검토 큐의 이전 버전 대비 diff 표시
- [ ] 재업로드 content_hash diff (§2-1: 새 버전 INSERT로 구현, UPDATE 금지)
- [ ] (골든셋 도착 후) Track 2 비교군 A/B 실행 → 유형별 정답률 표

## 7. 미확정

- 영업정보 시스템 등 외부 SoR의 조회 API 실제 존재 여부·스펙 (가정: 존재, Phase 2에서 확인)
- 정책서 원문 완료 시점 (골든셋이 여기 의존)
- `policy_param.condition` 표현 방식 — 조건 컬럼(문자열) vs JSON vs 별도 조건 테이블
- 시트 = 시스템 1:1 인가, `system_key` / namespace 전략
- **`effective_from`/`effective_to`(시점 유효 기간) 필요 여부** (2026-09-03 추가) — 지금 §2-1 버전 관리는
  "현재 vs 과거 버전"은 답하지만 "특정 날짜엔 뭐가 유효했는지"는 못 답한다(예: "3월 기준 정책"). 감사/
  컴플라이언스 목적으로 실제 필요한지 확인 필요 — 필요하면 Phase 1 스키마에 nullable 컬럼으로 지금
  추가하는 게 싸다(같은 논리, §2-1/56번째 줄 참고).
- `policy_chunk`를 `rag_knowledge`에 통합할지 별도 테이블로 둘지
