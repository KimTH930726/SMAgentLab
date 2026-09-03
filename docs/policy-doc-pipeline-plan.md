# 정책서 데이터화 파이프라인 + 저장소 전략 실험실 — 설계 브리프

> 작성일: 2026-09-03 (같은 날 SMAgentLab 구현 세션에서 §3 자산 실측 감사, §2-1 버전 관리,
> §2-2 용어집 재사용, §2-3 동적 처리+팀별 피드백 반영 — 실 샘플 2개 팀 데이터 기반)
> 상태: **v1 구현 착수** — §2의 통합 스키마(`policy_item`/`policy_param`/`policy_chunk`, 실 DB
> 마이그레이션 적용 완료·버전체인 검증 완료)로 진행 확정. "시스템별 완전 별도 테이블"안도
> 검토했으나(팀마다 구조가 크게 다름 — §1), "빨리 열고 나중에 모아서 재설계"라는 목표엔
> 오히려 통합 스키마가 더 맞다고 판단해 기각: 새 시스템 추가 시 코드 변경 없이 `category_path`
> (가변배열)로 흡수되고, 나중에 재설계할 때도 이미 한 곳에 모여있어 데이터 이관이 필요 없음.
> LLM 분해(segment 단위 4유형 분류)도 실 샘플 5건으로 프로토타입 검증 완료 — 결과 양호.
> §7 미확정 항목(외부 SoR API 스펙, 정책서 원문 완료 시점, condition 표현 방식, effective_from/to
> 필요 여부, 상태전이 전용 구조 필요 여부)은 v1 범위 밖(Phase 2+) 또는 데이터가 더 쌓인 뒤 재검토.
> 목적: 엑셀로 된 비즈니스 정책서를 운영에서 활용 가능한 데이터로 전환하는 **파이프라인(Track 1)** 과, 그 결과를 어떤 저장소 전략으로 관리할지 **수치로 판정하는 실험실(Track 2)** 을 설계한다.
> 관련: `D:\MD자료\work-os\WBS\smagent.md`, `docs/knowledge-lifecycle-design.md`, `docs/rag-improvement-strategy.md`, `docs/knowledge-refresh-automation-plan.md`

---

## 0. 배경 / 위치

- **AIOps 팀 핵심**: 비즈니스 정책서를 데이터화해 운영에 활용할 수 있는 구조를 만드는 것. 공통 데이터 허브의 한 축.
- **현재 SMAgentLab**: VOC류 — 운영 담당자 노하우 / 기존 해결방안을 `rag_knowledge`에 등록 → LLM이 유사 사례에서 해결방안 유추. 정책서는 그 옆에 붙는 **새로운 지식 종류**.
- **최종 착지**: SMAgentLab에서 프로토타이핑 → 팀 공통 포털의 백엔드 모듈로 이관. SMAgentLab 전용 인프라(자체 인증·배포)에 과투자 금지, 처음부터 포털에 얹을 수 있게 API 경계를 깨끗하게.

## 1. 입력 분석 — 정책서 엑셀 (실 샘플 2개 팀 기준, 2026-09-03 갱신)

- **row 단위.** 시스템별 100~200 row.
- **⚠️ 팀별 표준화가 안 돼 있음** — 완전 표준 스키마를 전제로 파서를 짜면 안 된다. 실측한 2개 팀만
  봐도 이미 구조가 다르다:
  - 팀 A(딜리버스): 컬럼 `대분류/중분류/소분류/정책명/조건·상세/비고` — 3단 분류
  - 팀 B(카드/기프트카드): 컬럼 `정책항목/세부항목/정책명/조건/상세` — **2단 분류, 비고 컬럼 자체가 없음**
  - 같은 5~6단 구조여도 시트마다 헤더 라벨이 다름(예: "대분류" vs "정책") — 정확한 헤더 문자열
    매칭이 아니라 **퍼지 매핑 + 동적 깊이 감지**가 필수. 팀이 늘어날수록 새 변형이 계속 나올 걸
    전제해야 한다.
- **첫 시트 = 용어집.** 지금까지 브리프에 없던 발견 — 정책서 파일 맨 앞 시트에 `No./용어명/용어
  정의/비고` 구조의 용어집이 따로 있다(실측 25개 항목). §2-2 참고.
- **정책 본문** = 중첩 리스트(`1.` / `1)` / `-`). 그 안에 성격이 최소 4가지 섞여 있음(실측으로 2종 →
  4종으로 확장):
  - **(a) 서술 규칙** — "재고 없으면 SOLD OUT 표기". 자연어 Q&A 대상.
  - **(b) 파라미터 팩트** — "일반 배달: 20개", "도보 배달: 4개". 이산 key-value, 조건부.
  - **(c) 코드 열거형** (2026-09-03 신규 발견) — "HCB01 : 베이직 제휴카드", "N : 일반카드" 식의
    코드-값 표. `rag_knowledge`의 "88% 표형식 오염"·v2.49 `_KEYWORD_ONLY_CATEGORIES`와 같은
    성격 — 벡터화 대상이 아니라 정확 조회 대상.
  - **(d) 상태 전이 규칙** (2026-09-03 신규 발견) — "등록 : 미등록 → 등록" 식의 (이전상태, 액션,
    다음상태) 트리플. (a)/(b) 어디에도 안 맞는 세 번째 구조.
  - **한 row 안에 여러 성격이 섞여 있을 수 있음** — row 단위가 아니라 **문장/segment 단위로
    분류**해야 함(실측: "배달 가능 여부" row는 서술+파라미터 혼재).
- **비고** = 외부 SoR(System of Record) 포인터(팀 A에만 존재, 팀 B는 컬럼 자체가 없음). "영업정보
  시스템에 세팅된 정책을 활용하며 변경될 수 있음" → **이 값에 대해서는 정책서가 authoritative가
  아님**. 용어집 정의와 내용이 겹치는 경우도 실측 확인(예: "도보 배달" 정의가 용어집과 정책 비고
  양쪽에 있음) — 중복 등록 방지 고려 필요.

## 2. 데이터 모델 — 한 row를 3층으로 분해

| 테이블 | 단위 | 성격 | 저장소 |
|---|---|---|---|
| `policy_item` | 엑셀 row 1개 = 1 row | 카테고리 경로 + 원문 + 메타 | RDB |
| `policy_param` | `policy_item` 1:N | 추출된 파라미터 팩트 (조건부 key-value) | RDB |
| `policy_chunk` | `policy_item` 1:N | 서술 규칙 부분만 청킹·임베딩 | Vector (`rag_knowledge` 재사용 검토) |

스키마 스케치 (구현 세션에서 확정):

```
policy_item
  id, namespace_id(FK, 기존 ops_namespace 재사용 — §7 해소), system_key(네임스페이스 내 하위 분류 문자열),
  category_path(TEXT[]),   -- 2026-09-03 변경: 대분류/중분류/소분류 고정 3컬럼 → 가변 배열.
                             -- 팀마다 분류 깊이가 다름(§1 실측: 팀 A 3단, 팀 B 2단) — 파서가
                             -- "No.와 정책명 사이 컬럼 수"를 동적으로 세서 채움
  기능명(정책명),
  raw_body(text), 비고(text, nullable),   -- 비고 자체가 없는 팀 존재(§1)
  source_file, source_sheet, source_row, content_hash,
  status('draft'|'pending_review'|'active'|'deprecated'),
  logical_id(기본=자기 id), version(기본 1), supersedes_id(nullable),
  parse_status('parsed'|'partial'|'unresolved'),   -- 2026-09-03 신규, §2-3 참고
  unresolved_segments(JSONB, nullable),             -- [{text, reason}] — 자동분류 실패분 원문+사유
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

**§1의 4가지 콘텐츠 유형이 저장소 3층에 매핑되는 방식**: (a)서술→`policy_chunk`, (b)파라미터→`policy_param`(그대로), (c)코드 열거형→`policy_param`(name=코드체계명, condition=코드, value=설명 — 구조적으로 (b)와 동일하게 수용 가능). **(d)상태 전이 규칙은 `policy_param`의 name/condition/value/unit에 억지로 안 맞는다** — Phase 1에서는 자동 분류하지 않고 `unresolved`로 캡처만 한다(§2-3). 실제로 상태 전이 조회 질문이 얼마나 나오는지 확인되면 Phase 2 이후 전용 구조(예: `policy_transition` 테이블)를 검토한다 — 한 번 본 사례로 스키마를 미리 늘리지 않는다.

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

### 2-2. 용어집 — 새 테이블 아니라 기존 `rag_glossary` 재사용 (2026-09-03 발견)

정책서 파일 맨 앞 시트가 용어집이라는 게 실측으로 확인됐다(§1). 이 구조가 기존 `rag_glossary`
(용어명→정의, 채팅 검색의 Glossary Term Mapping 1단계에서 이미 쓰는 테이블)와 사실상 동일하다
— **`policy_glossary`를 새로 만들지 않고 `rag_glossary`에 그대로 적재**한다. 이러면 정책서
관련 질문에서도 기존 용어 매핑이 별도 개발 없이 바로 작동한다.

예외: 일부 용어집 항목이 정의문이 아니라 파라미터 팩트에 가깝다(실측: "결제 완료 — 상태코드:
11"). 이런 항목은 파싱 시 `policy_param`으로 갈 수 있게 LLM 분해 단계에서 용어집 시트도 같은
(a)/(b)/(c)/(d) 분류를 거치게 한다(§1) — 용어집이라고 무조건 `rag_glossary` 직행은 아님.

### 2-3. 완전 자동화는 목표가 아니다 — 동적 처리 + 팀별 피드백 (2026-09-03 반영)

팀별 표준화가 안 돼 있다는 게 실측 2개 샘플만으로 확인됐다(§1) — 팀이 늘어날수록 새 구조 변형이
계속 나올 걸 전제해야 한다. **목표를 "완전 자동 파싱"이 아니라 "자동 처리 비율 최대화 + 안 되는
건 사유와 함께 캡처"로 잡는다.**

- LLM 분해 결과에 `parsed`(정상 분류)/`partial`(일부만 분류)/`unresolved`(분류 실패) 3상태를
  둔다(`policy_item.parse_status`). `unresolved_segments`에 실패한 원문 조각과 사유를 같이
  저장한다(예: `{"text": "...", "reason": "코드-값 열거형 패턴, 기존 파라미터/서술 분류에 안 맞음"}`).
- `unresolved` 건은 pending_review 큐로 가되, **집계해서 팀별 피드백 리포트를 만든다** —
  "카드 시스템 정책서 중 N건이 자동 분류 실패, 사유 상위 3개: …" 식으로, 원본 팀에 표준화를
  요청할 근거 자료가 된다.
- 이건 임시방편이 아니라 장기적으로도 유효하다 — 표준화 요청 자체가 한 번에 끝나지 않고
  반복될 것이므로, 실패 사유를 구조화해서 쌓아두는 게 매번 사람이 손으로 다시 파악하는 것보다
  싸다.

## 3. Track 1 — 정책서 데이터화 파이프라인

```
엑셀 업로드
  → 시트 판별: 첫 시트(용어집) vs 정책 시트 (§2-2)
  → [용어집 시트] → LLM 분류(정의문/파라미터) → 정의문은 rag_glossary 적재, 파라미터는 policy_param 후보
  → [정책 시트] row 파싱 (헤더 퍼지매핑 + 동적 깊이 감지로 category_path 추출 + raw_body + 비고)
  → LLM 분해: raw_body를 문장/segment 단위로 (a)서술 (b)파라미터 (c)코드열거형 (d)상태전이 분류
    ※ segment 단위 — 한 row 안에 여러 유형이 섞일 수 있음(§1)
    ※ 분류 안 되는 segment는 버리지 않고 parse_status='unresolved' + unresolved_segments에 사유 기록(§2-3)
  → policy_item + policy_param(후보) + policy_chunk(후보) 를 status='pending_review'로 적재
  → 검토 UI: 사람이 파라미터 값·조건 위주로 승인/수정/반려, unresolved 건은 별도 탭에서 확인
  → 승인 → RDB 확정 + policy_chunk 임베딩·인덱싱
  → 재업로드 시: source_row + content_hash 비교 → 바뀐 row만 재처리 (pending_review)
    ※ "재처리" = 기존 row UPDATE가 아니라 새 row INSERT(version+1, supersedes_id=이전 row) —
      이전 row는 deprecated로 전환·보존(§2-1). 과거 정책 조회는 deprecated row를 그대로 읽음
  → 비고에 external_source 있는 파라미터는 "신뢰 주의 — 외부 시스템 관리" 플래그 표시
  → (주기적) unresolved 건 팀·사유별 집계 → 팀별 피드백 리포트(§2-3)
```

**Phase 구분**
| Phase | 범위 |
|---|---|
| 1 | 용어집 시트 → `rag_glossary` 적재(§2-2) + 정책 시트 파서(동적 깊이 감지, §1) + LLM 분해(segment 단위, 4유형 + unresolved, §2-3) + pending_review 적재 + 검토 UI + 승인 → 확정. 재업로드 content_hash diff(§2-1: UPDATE 아닌 새 버전 INSERT). 재검토 큐에 오른 row는 이전 버전과의 **diff를 검토 화면에 표시**(전체 재검토 부담 축소). **여기까지가 "PoC 올리는" 최소 동작.** |
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

- [x] §3 "현재 자산" 표 확정 — `rag_knowledge` 스키마, `chunker`, `retrieval` 라우팅, pending_review UI 재사용 범위 (2026-09-03 1차 실측 완료, 위 표 참고)
- [x] `policy_item`(`category_path` 가변배열, `parse_status`/`unresolved_segments`) / `policy_param` / `policy_chunk` 스키마 마이그레이션 — §2-1 버전 관리(logical_id/version/supersedes_id, INSERT-only) 반영. **실 DB 적용 완료**(`backend/main.py` `_migrate_policy_tables`), logical_id 트리거·버전체인 INSERT 실측 검증 통과. "시스템별 완전 별도 테이블"안 검토 후 이 통합 스키마로 확정(위 상태 참고)
- [x] LLM 분해 프로토타입 — segment 단위 (a)/(b)/(c)/(d) + unresolved 4+1 분류 (§2-3), 실 샘플(딜리버스 3건/카드 2건)로 **검증 완료** — 혼재 row 분리, 코드열거형→param 흡수, 상태전이→unresolved+사유 전부 정상 동작 확인
- [ ] 엑셀 파서 프로토타입 — 헤더 퍼지매핑 + 동적 깊이 감지(§1: 팀 A 3단/팀 B 2단 실측 샘플로 검증) — 실제 xlsx 파일 필요(지금까진 텍스트로 붙여넣은 row만 받음)
- [ ] 용어집 시트 파서 + `rag_glossary` 적재 경로 (§2-2)
- [ ] 위 검증된 파싱+분해 로직을 실제 API(`POST /api/policy/import` 등)로 감싸기
- [ ] 검토 UI: 파라미터 값·조건 승인 화면 + unresolved 별도 탭 + 재검토 큐의 이전 버전 대비 diff 표시
- [ ] 재업로드 content_hash diff (§2-1: 새 버전 INSERT로 구현, UPDATE 금지)
- [ ] unresolved 팀별 집계 리포트 — 표준화 요청 근거 자료 (§2-3)
- [ ] (골든셋 도착 후) Track 2 비교군 A/B 실행 → 유형별 정답률 표

## 7. 미확정

- 영업정보 시스템 등 외부 SoR의 조회 API 실제 존재 여부·스펙 (가정: 존재, Phase 2에서 확인)
- 정책서 원문 완료 시점 (골든셋이 여기 의존) — 팀별 표준화가 안 된 게 확인돼(§1), 몇 개 팀 분량이
  더 필요할지도 아직 불확실
- `policy_param.condition` 표현 방식 — 조건 컬럼(문자열) vs JSON vs 별도 조건 테이블
- **`effective_from`/`effective_to`(시점 유효 기간) 필요 여부** (2026-09-03 추가) — 지금 §2-1 버전 관리는
  "현재 vs 과거 버전"은 답하지만 "특정 날짜엔 뭐가 유효했는지"는 못 답한다(예: "3월 기준 정책"). 감사/
  컴플라이언스 목적으로 실제 필요한지 확인 필요 — 필요하면 Phase 1 스키마에 nullable 컬럼으로 지금
  추가하는 게 싸다(같은 논리 적용).
- `policy_chunk`를 `rag_knowledge`에 통합할지 별도 테이블로 둘지
- **(d)상태 전이 규칙 전용 구조가 실제로 필요한지** (2026-09-03 추가) — 지금은 Phase 1에서 unresolved로
  캡처만 하기로 함(§2). 실제 조회 수요가 확인되면 재검토.
- ~~시트 = 시스템 1:1 인가~~ — **해소**: 기존 `ops_namespace`(FK) 재사용, `system_key`는 그 안의 하위
  분류 문자열로 처리 (2026-09-03 결정)
