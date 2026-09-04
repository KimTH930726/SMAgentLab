# 정책서 데이터화 파이프라인 + 저장소 전략 실험실 — 설계 브리프

> 작성일: 2026-09-03 (같은 날 SMAgentLab 구현 세션에서 §3 자산 실측 감사, §2-1 버전 관리,
> §2-2 용어집 재사용, §2-3 동적 처리+팀별 피드백 반영 — 실 샘플 2개 팀 데이터 기반)
> 상태(2026-09-04 갱신): **v1 파이프라인 + unresolved 집계 API/UI 구현 완료, 실 정책서 콘텐츠로
> 광범위 검증 완료**. `POST /api/policy/import`, `GET /api/policy/search`, `GET
> /api/policy/unresolved-summary`(+읽기전용 관리자 화면) 전부 실 HTTP E2E 검증(`service/policy/`
> — excel_parser/decompose/service/search/unresolved_report/router, 테스트 299개). 온라인스토어
> 1개 팀 파일(용어정의+상품/전시/주문/배송/클레임/리워드/재고 8개 시트, 398건)을 실 콘텐츠로
> 재구성해 실제 DB에 적재 검증(§9) — 이 과정에서 실버그 5건 발견·수정(병합셀, 검색 tsquery,
> 헤더/데이터유실, param value 배열, 계산식 파괴/할루시네이션). "시스템별 완전 별도 테이블"안도
> 검토했으나(팀마다 구조가 크게 다름 — §1) 통합 스키마로 확정: 새 시스템 추가 시 코드 변경 없이
> `category_path`(가변배열)로 흡수되고, 재설계 시에도 이미 한 곳에 모여있어 데이터 이관이 필요
> 없음. 남은 건 검토 UI(승인 화면), Track 2 저장전략 비교, §8 v2 후보 구조 4종(§6/§7/§8 참고).
> §7 미확정 항목(외부 SoR API 스펙, 정책서 원문 완료 시점, condition 표현 방식, effective_from/to
> 필요 여부, 상태전이 전용 구조 필요 여부)은 v1 범위 밖(Phase 2+) 또는 데이터가 더 쌓인 뒤 재검토.
> 목적: 엑셀로 된 비즈니스 정책서를 운영에서 활용 가능한 데이터로 전환하는 **파이프라인(Track 1)** 과, 그 결과를 어떤 저장소 전략으로 관리할지 **수치로 판정하는 실험실(Track 2)** 을 설계한다.
> 관련: `D:\MD자료\work-os\WBS\smagent.md`, `docs/tech/knowledge-lifecycle-design.md`(경로 정정),
> `docs/knowledge-refresh-automation-plan.md`, `docs/tech/policy-platform-feasibility.md`(더 큰
> 규모의 "정책 플랫폼" 통합 가능성 사전 검토 — `service/policy/` 구조를 오늘과 동일하게 권고했었음)
> (`docs/rag-improvement-strategy.md`는 2026-09-03 삭제 — 2026-06-30 시점 로드맵으로 실 코드
> 참조 없었고, 그 안의 "골든셋+Recall@K 평가 프레임워크" 제안은 이후 실측 검토 결과 지금 시스템
> 규모(전체 질의 104건)엔 안 맞는다는 게 확인됨, 메모리 `project_retrieval_routing` 참고)

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

**왜 3층인가**: 파라미터 팩트를 벡터화하면 어휘만 겹쳐도 오탐이 난다 — `docs/tech/knowledge-lifecycle-design.md`의 "rag_knowledge 88% 표형식 오염" 및 v2.49의 `_KEYWORD_ONLY_CATEGORIES`(코드표는 벡터 점수 0, 키워드 매칭만)와 정확히 같은 문제. 파라미터는 구조화해서 정확 조회, 서술은 벡터로 의미 검색, 카테고리는 RDB로 네비게이션. 이 원칙 자체는 `docs/tech/retrieval-routing-pattern.md`에 재사용 가능한 패턴으로 별도 정리했다(2026-09-04) — 다음 데이터 축을 편입할 때 이 문서보다 그쪽을 먼저 참고할 것.

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

### 2-4. v1 현재 상태 — 실제로 되는 것 vs 설계만 된 것 (2026-09-03)

**지금 UI 화면은 없다.** 업로드는 `POST /api/policy/import`를 직접(스크립트/curl) 호출해야
한다 — 그래서 "사용자 셀프 등록"보다 "초기 데이터를 관리자가 직접 밀어넣기(v0)"가 지금
구조와 자연스럽게 맞는다(엑셀이든 복붙 텍스트를 엑셀로 변환한 것이든 API 입장에선 동일).

```
[관리자/AIOps팀]                    [service/policy/]                    [DB]
     │                                    │                                │
     │  정책서 엑셀(또는 복붙→엑셀 변환)      │                                │
     ├──POST /api/policy/import─────────►│                                │
     │                                    │  ① 시트 판별(용어집/정책, 헤더 기반) │
     │                                    │  ② 헤더 퍼지매핑+동적 깊이 감지     │
     │                                    │     (대/중/소분류 vs 정책항목/     │
     │                                    │      세부항목 등 팀마다 달라도 흡수) │
     │                                    │  ③ 버전 체크(content_hash)        │
     │                                    │     — 안 바뀐 row는 여기서 스킵    │
     │                                    ├──④ LLM 분해(동시,세마포어5)──────►│ (LLM 게이트웨이)
     │                                    │     서술/파라미터/미해결 분류      │
     │                                    ├──⑤ policy_item/param/chunk INSERT►│ policy_item
     │                                    │     (전부 status=pending_review)  │ policy_param
     │                                    ├──⑥ 용어집은 create_glossary()───►│ policy_chunk
     │                                    │     (기존 함수 재사용, 즉시 반영)  │ rag_glossary
     │◄──ImportSummary(건수 요약)─────────┤                                │
     │                                    │                                │
     │  "일반 배달 장바구니 개수?" 같은 질문     │                                │
     ├──GET /api/policy/search──────────►│  ⑦ 파라미터: RDB tsquery 매칭      │
     │                                    │  ⑧ 서술: policy_chunk 벡터 검색   │
     │◄──params[] + narratives[]─────────┤     (둘 다 실행, 같이 반환)        │
```

**실제로 완성된 것**: 위 ①~⑧ 전 구간 — 실 HTTP E2E로 검증됨(§6, §4-2 아래). 용어집(⑥)만
예외적으로 "즉시 검색 반영"이다 — `rag_glossary`는 채팅의 기존 Glossary Term Mapping이 그대로
읽으므로, 업로드하는 순간부터 채팅에서 그 용어가 바로 매핑된다. `GET /api/policy/search`(⑦~⑧,
`service/policy/search.py`)는 **전용 엔드포인트**로 붙였다 — 기존 채팅이 공유하는
`retrieval.py`/`agent.py`는 아직 안 건드렸다(§4-2 이유 참고).

**아직 안 된 것**:
- **승인 화면이 없다** — 전부 `status='pending_review'`로 쌓여서(검색 대상엔 포함시켰다,
  안 그러면 아무것도 안 나옴), `active`로 바꿀 방법이 수동 SQL 말고는 없다.
- **메인 채팅에는 아직 안 얹었다** — 지금은 `/api/policy/search`를 따로 호출해야 나온다.
  일반 채팅 질문("장바구니 최대 몇 개?")이 자동으로 이 데이터까지 찾아 답하려면
  `agent.py`/`retrieval.py`에 policy 소스를 편입해야 하는데, 이건 Track 2 결과를 보고
  결정하는 게 안전하다고 판단해 일부러 미룸(§4-2).
- **카테고리 네비/조건 필터(§4의 나머지 2유형) 전용 조회는 없다** — 지금 `/search`는
  파라미터+서술 두 유형만 다룬다. "장바구니 관련 정책 다 보여줘" 같은 순수 브라우징은
  아직 API가 없음.

### 4-2. 검색을 전용 엔드포인트로 둔 이유 — 왜 채팅에 바로 안 얹었나 (2026-09-03)

Track 2(§4)가 아직 실행 전이라 "A: `rag_knowledge`에 얹기만 해도 되는가 vs B: 이 하이브리드
스키마"의 우열이 숫자로 확정되지 않았다. 검증 안 된 방식을 **모든 네임스페이스가 공유하는
핵심 채팅 검색 경로**에 먼저 섞으면, 나중에 Track 2가 "A가 더 낫다"고 나와도 이미 얽혀버린
코드를 다시 걷어내야 한다. 그래서 지금은 별도 엔드포인트로 완전히 분리해뒀고, Track 2가
B(하이브리드)의 우위를 확인해주면 그때 `agent.py`에 편입하는 게 안전하다.
용어집만 유일하게 지금 당장 실사용 효과가 있다(업로드 즉시 채팅 용어 매핑에 반영).

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

- **분가 가능**: Track 2는 Track 1의 출력에 의존하지만 별도 프로젝트로 진행 가능. 현재는 `work-os/WBS/smagent.md` A2. 다리가 붙으면 독립 WBS로.
- **되먹임**: 실험실 결과가 Track 1이 최종 커밋할 스키마 형태를 결정한다.

### 4-3. Track 2 실행 결과 (2026-09-04, 골든셋 89건)

채점 방식: item-id 기반 hit@10(정답 item이 top-10 검색결과에 최소 1개 이상 포함되면 정답) —
narrative의 expected_answer가 원문 그대로가 아니라 LLM paraphrase라 텍스트 substring 비교는
불공정해서, "정답 출처(item)를 찾아내는가"로 통일해 채점. A그룹은 전체 378개 policy_item을
격리된 임시 네임스페이스의 `rag_knowledge`에 원문 그대로(정책명+본문) 청킹 없이 1항목=1엔트리로
얹어 실행, 평가 후 삭제(프로덕션 데이터 영향 없음).

| 유형 | 건수 | A(지식-only) | B(하이브리드) |
|---|---|---|---|
| param | 23 | **73.9%** | 34.8% |
| narrative | 23 | 60.9% | **91.3%** |
| navigation | 24 | 37.5% | **54.2%** |
| condition_filter | 19 | 47.4% | 47.4% |
| **전체** | 89 | 55.1% | 57.3% |

**가설과 반대로 나온 게 param 유형** — B가 A보다 크게 낮다(34.8% vs 73.9%). 원인을 실측으로
확인: **전체 378개 item 중 136개(36%)가 param만 있고 policy_chunk(벡터)가 하나도 없다** —
정책 본문 전체가 구조화 파라미터로만 분해되고 서술 segment가 안 남은 경우. 이런 item은 B에서
`policy_param`의 `to_tsquery` 정확 매칭에만 의존하는데, 골든셋 쿼리는 스펙대로 원문을 그대로
베끼지 않은 자연어 질문이라 짧은 param 필드(name/condition)와 어휘가 안 겹치면 그냥 못 찾는다
— 벡터 폴백이 아예 없다. 반면 A는 모든 item을 예외 없이 벡터로 색인해서 어휘가 안 겹쳐도
의미 유사도로 찾아낸다. narrative/navigation은 가설대로 B가 이겼다 — 서술은 벡터가, 카테고리
네비는(의외로 예상보다 격차가 작지만) 여전히 B가 우세.

**시사점(v2 후보, §8에 추가할 사항)**: 지금 스키마는 "구조화=키워드, 서술=벡터"를 **item 단위
배타적으로** 적용하고 있는데, 실측으로는 **param만 있는 item도 벡터 폴백이 있어야 자연어 질문에
강해진다**는 게 확인됐다. 후보 수정안: item마다 (파라미터로 완전히 분해됐더라도) 정책명+요약
텍스트 최소 1개는 항상 `policy_chunk`로 만들어 벡터 검색 사각지대를 없애는 것 — Phase 1처럼
"narrative segment가 있을 때만" 만드는 대신 "모든 item에 최소 1개"로 바꾸는 안. 아직 구현 안 함,
다음 세션 후보 작업.

condition_filter는 A/B 동률(47.4%) — 둘 다 명시적 조건 필터링 로직이 없어(B도 지금은 category만
필터링 가능, condition 필터 전용 조회는 §6에 아직 없음) 이 유형 자체가 두 그룹 다 취약. 카테고리
네비(navigation)는 B가 앞서지만 격차가 크지 않다(54.2% vs 37.5%) — A도 벡터 유사도만으로 어느
정도는 같은 카테고리 항목을 묶어 찾아낸다는 뜻.

**결론(1차)**: "하이브리드가 무조건 낫다"는 가설은 기각. narrative에서는 뚜렷한 우위가 있지만, param
유형에서 벡터 폴백 부재가 실제 약점으로 확인됐다.

### 4-4. 벡터 폴백 구현 + 재측정 (2026-09-04, 같은 날 바로 후속)

위 시사점을 바로 구현: `service.py`의 `_write_policy_result()`에서 narrative segment가 하나도
없는 item(param만 있거나 전부 unresolved)은 정책명+category_path+raw_body 전체를 폴백
`policy_chunk`로 벡터 색인하도록 수정(`SheetSummary.fallback_chunks_added`로 실제 LLM 추출
narrative와 구분 집계). 기존 임포트 데이터(480건 중 163건이 해당)는 재임포트 없이 직접
백필(`content_hash` 안 바뀌면 재임포트해도 다시 안 만들어지므로 별도 스크립트 필요했음). 테스트
3개 추가(총 312개).

**재측정 결과** — 같은 골든셋 89건, 같은 채점 방식으로 재실행:

| 유형 | 건수 | A(지식-only) | B(하이브리드, 폴백 적용 후) | 이전 B |
|---|---|---|---|---|
| param | 23 | 73.9% | **82.6%** | 34.8% |
| narrative | 23 | 60.9% | **91.3%** | 91.3% |
| navigation | 24 | 37.5% | **54.2%** | 54.2% |
| condition_filter | 19 | 47.4% | **73.7%** | 47.4% |
| **전체** | 89 | 55.1% | **75.3%** | 57.3% |

param이 34.8%→82.6%로 A를 역전했고, condition_filter도 47.4%→73.7%로 크게 개선됐다(같은
item이 param+condition 양쪽에 걸쳐있는 경우가 많아 같이 좋아짐). navigation은 폴백과 직접
관련 없어 그대로. **전체 정답률이 57.3%→75.3%로 20%p 개선** — 이제 "하이브리드가 A보다
낫다"는 가설이 4개 유형 전부에서 확인된다.

**결론(최종)**: 벡터 폴백 보완 후엔 하이브리드 스키마가 4개 유형 모두에서 지식-only보다
우세함이 실측으로 확인됐다. `agent.py`/`retrieval.py` 편입을 막던 이유가 해소됐으므로 §6의
편입 검토를 재개할 수 있다.

### 4-1. 골든셋 스펙 — 태훈에게 정확히 요청할 것 (2026-09-03)

**형식**: JSONL, 한 줄에 질문 하나. DB ID가 아니라 **원본 엑셀에서 바로 알 수 있는 값**으로
출처를 표시한다(정확한 `logical_id`는 임포트 후 정책명+카테고리로 매칭해 우리가 채운다).

```jsonl
{"qid": "q001", "query": "일반 배달 장바구니 최대 몇 개까지 담을 수 있어?", "type": "param",
 "source": {"file": "딜리버스_주문결제.xlsx", "sheet": "주문결제", "row": 2},
 "expected_answer": "20개"}
{"qid": "q002", "query": "재고 없으면 화면에 뭐라고 떠?", "type": "narrative",
 "source": {"file": "딜리버스_주문결제.xlsx", "sheet": "주문결제", "row": 3},
 "expected_answer": "SOLD OUT 표기"}
{"qid": "q003", "query": "장바구니 관련 정책 다 보여줘", "type": "navigation",
 "source": {"file": "딜리버스_주문결제.xlsx", "sheet": "주문결제", "category": "1-1. 장바구니"},
 "expected_answer": null}
{"qid": "q004", "query": "야구장 매장에서만 적용되는 규칙 알려줘", "type": "condition_filter",
 "source": {"file": "딜리버스_주문결제.xlsx", "sheet": "주문결제", "condition": "야구장 입점 매장"},
 "expected_answer": null}
```

**필드**
| 필드 | 설명 |
|---|---|
| `qid` | 고유 식별자 |
| `query` | 실제 질문. **정책서 원문 문구를 그대로 베끼지 말 것** — 그러면 검색이 너무 쉬워져 점수가 부풀려진다(오늘 검색 구현 중 "장바구니 개수"가 원문 그대로인 "장바구니 최대 메뉴 개수"와 안 맞아 실제로 검색 실패했던 사례가 있음 — 실사용자는 원문 그대로 안 물어본다는 방증) |
| `type` | `param`(파라미터 조회) / `narrative`(서술 Q&A) / `navigation`(카테고리 네비) / `condition_filter`(조건 필터) — §4 표의 4종과 1:1 |
| `source.file`/`sheet`/`row` | 정답이 있는 정확한 위치(엑셀 파일명/시트명/행번호) — `param`/`narrative` 타입은 필수 |
| `source.category` | `navigation` 타입일 때 — 몇 번째 대분류/중분류인지(예: "1-1. 장바구니") |
| `source.condition` | `condition_filter` 타입일 때 — 어떤 조건으로 걸러야 하는지(예: "야구장 입점 매장") |
| `expected_answer` | `param`/`narrative`만 — 정답 텍스트(자동 채점용). `navigation`/`condition_filter`는 "정답 row 집합"이 기준이라 null |

**규모**: 유형별 최소 10~15개, 전체 40~60개 권장 — VOC 때(전체 질의 104건이라 골든셋도 11개
로 작을 수밖에 없었음, `project_retrieval_routing` 메모리 참고)와 달리 정책서는 시스템당
100~200행 규모라 이 정도는 현실적으로 확보 가능.

**타이밍**: 여러 시스템 원문이 준비되는 대로 그때그때 보내도 된다 — Track 1(임포트+검색)은
이미 완성돼 있어 골든셋을 기다릴 필요가 없다. Track 2 비교 실행만 골든셋 도착 후 시작.

## 5. 포털 착지 관점 (API 경계)

파이프라인을 서비스로 노출 — SMAgentLab 내부 구조에 의존하지 않게(기존 컨벤션에 맞춰 `/api/` 프리픽스 사용, `/api/knowledge/*`·`/api/email-voc/*`와 동일):
- `POST /api/policy/import` (엑셀 업로드), `GET /api/policy/items?category=`, `GET /api/policy/params?item=&condition=`, `POST /api/policy/{id}/resolve` (승인)
- `GET /api/policy/items/{logical_id}/history` — 특정 정책의 과거 버전 전체 조회(§2-1 버전 관리의 직접적인 산출물)
- 검색: 기존 RAG 검색에 `policy` 소스 라우팅 추가 (chunk를 `rag_knowledge` FK로 연결하면 자연스러움)
- 인증은 주입받는 형태 (사내 SSO는 포털 레이어에서 처리 — 진행 중인 사내 SSO 통합 작업과 방향 조율 필요)

## 6. 다음 액션 (구현 세션)

- [x] **자체 검증 라운드(2026-09-03, 사용자 요청)** — API 구현 직후 "자체검증/시나리오/클린코드/
      성능까지 다 했냐"는 지적을 받고 재점검, 실제로 빠진 것 2건 발견·수정:
      - **버그**: 병합셀이 있으면 카테고리 컬럼이 빈 문자열로 들어감(openpyxl이 병합 영역의
        첫 셀에만 값을 주고 나머지는 None 반환 — 실측 확인). "마지막 non-empty 값으로
        forward-fill" 로직 추가 + 회귀 테스트 2건 추가(22개로 증가)
      - **성능**: row마다 순차로 LLM 분해 호출 — 100~200 row 규모(§1)면 파일 하나 임포트에
        수 분 소요 예상. 버전체크(저렴)→LLM 분해(동시, 세마포어 5)→DB 쓰기(순차, 커넥션
        안전성 때문에 병렬화 제외) 3단계로 재구성. 실측: 10 row 동시 처리 27.5초, row-segment
        매핑 무결성(동시 실행이 순서를 안 섞는지)도 실 DB로 확인
      - 클린코드: import 순서 정리(상수 사이에 끼어있던 import 문 수정), 에러 핸들링은 기존
        지식 임포트 라우터(`agents/knowledge_rag/knowledge/router.py`)와 동일 패턴 확인
- [x] §3 "현재 자산" 표 확정 — `rag_knowledge` 스키마, `chunker`, `retrieval` 라우팅, pending_review UI 재사용 범위 (2026-09-03 1차 실측 완료, 위 표 참고)
- [x] `policy_item`(`category_path` 가변배열, `parse_status`/`unresolved_segments`) / `policy_param` / `policy_chunk` 스키마 마이그레이션 — §2-1 버전 관리(logical_id/version/supersedes_id, INSERT-only) 반영. **실 DB 적용 완료**(`backend/main.py` `_migrate_policy_tables`), logical_id 트리거·버전체인 INSERT 실측 검증 통과. "시스템별 완전 별도 테이블"안 검토 후 이 통합 스키마로 확정(위 상태 참고)
- [x] LLM 분해 프로토타입 — segment 단위 (a)/(b)/(c)/(d) + unresolved 4+1 분류 (§2-3), 실 샘플(딜리버스 3건/카드 2건)로 **검증 완료** — 혼재 row 분리, 코드열거형→param 흡수, 상태전이→unresolved+사유 전부 정상 동작 확인
- [x] 엑셀 파서 구현 — 헤더 퍼지매핑 + 동적 깊이 감지 (`service/policy/excel_parser.py`). 실제
      xlsx가 없어 실 샘플 텍스트로 openpyxl 워크북을 재구성해 검증(용어집 시트 자동 판별,
      3단/2단 category_path 모두 정확 추출) — 완전한 검증은 진짜 xlsx 파일이 오면 재확인 필요
- [x] 용어집 시트 파서 + `rag_glossary` 적재 경로 (§2-2) — `_ingest_glossary_row()`, 기존
      `create_glossary()` 재사용, 중복 term은 스킵 카운트로 집계
- [x] 파싱+분해+적재를 실제 API로 감싸기 — `POST /api/policy/import`(`service/policy/router.py`),
      **실 HTTP E2E 검증 완료**: 3시트(용어집+정책 2개) 업로드 → 파싱→LLM분해→DB적재 전체
      경로 정상 동작. 재업로드 시나리오도 실측: 내용 불변 row는 LLM 재호출 없이 스킵, 내용
      변경 row는 새 버전 INSERT + 이전 버전 deprecated 전환까지 실 DB에서 확인
- [x] 재업로드 content_hash diff (§2-1: 새 버전 INSERT로 구현, UPDATE 금지) — 위 E2E 테스트에
      포함돼 실측 검증 완료
- [x] **검색 API** — `GET /api/policy/search`(`service/policy/search.py`), 파라미터(RDB
      tsquery)+서술(벡터) 두 갈래 동시 반환. 전용 엔드포인트로 분리한 이유는 §4-2. 실
      HTTP E2E로 검증하다가 **실제 버그 발견**: ILIKE 부분문자열 매칭이라 "장바구니 개수"가
      "장바구니 최대 메뉴 개수"를 못 찾음 — `retrieval.py`와 동일한 to_tsquery 패턴으로 교체,
      재검증 통과. 테스트 5개 추가(총 289개)
- [x] **골든셋 스펙 명시** (§4-1) — 필드/타입/형식/규모/타이밍까지 확정, 태훈에게 바로 전달 가능
- [x] **LLM 분해 프롬프트 버그 수정(2026-09-04, 발표 데모 준비 중 실측으로 발견)** — 숫자 헤더
      줄("2. 상태 전이 기준") 자체가 내용 없이 단독 narrative segment로 뽑히면서 그 아래 실제
      내용("등록 : 미등록 → 등록")이 통째로 유실되는 실데이터 손실 버그. 원인: 프롬프트에
      구획헤더 vs 내용 구분 규칙 및 "누락 금지" 명시가 없었음. `SYSTEM_PROMPT`에 규칙 2개
      + 정확히 이 실패 사례를 쓴 정답/오답 예시 추가로 수정. 동일 실패 케이스로 재현 검증:
      수정 전 헤더만 추출/내용 유실 → 수정 후 헤더는 서술로 정리되고 전이 규칙은 unresolved로
      정상 보존. 이런 프롬프트 사각지대는 실 데이터를 넣을수록 계속 나올 것으로 예상 —
      아래 집계 리포트가 재발 발견 경로. 커밋 `10f82d0`
- [x] unresolved 팀별 집계 리포트 — 표준화 요청 근거 자료이자, 위 버그처럼 "우연히 발표
      준비하다 발견"이 아니라 시스템이 스스로 드러내는 경로 (§2-3). `GET
      /api/policy/unresolved-summary`(`service/policy/unresolved_report.py`) — namespace(+선택
      system_key)로 `parse_status IN ('unresolved','partial')` 항목을 system_key별로 묶어
      건수/segment 목록 반환. reason 자동 클러스터링은 하지 않음(LLM 자유 텍스트라 정확 매칭
      그룹핑이 무의미 — 건수가 쌓여 실제로 필요해지면 재검토, YAGNI). 실 HTTP E2E로 검증:
      상태전이 예시를 실제 임포트 → 카드팀 아래 1건/1segment로 정확히 집계, system_key 필터
      정상 동작 확인
- [ ] 검토 UI: 파라미터 값·조건 승인 화면 + unresolved 별도 탭 + 재검토 큐의 이전 버전 대비 diff 표시
      (v1은 API까지만 — pending_review로 쌓인 데이터를 승인하는 화면은 아직 없음)
- [x] **정책 항목 브라우저(2026-09-04)** — `GET /api/policy/items`(`service/policy/browse.py`),
      item→param/chunk 3층 구조를 쿼리 없이 전체 조회. 사용자 피드백(정책이 잘 저장됐는지 볼
      화면이 없다, RDB/벡터 연결 흐름을 가시적으로 보고 싶다)으로 착수. 관리자 화면 "정책 항목
      브라우저" 탭 신규. 실 HTTP E2E로 480건(온라인스토어+딜리버스) 데이터 조회 검증, 카테고리/
      정책명 필터 정상 동작 확인. §4 카테고리 네비/조건 필터 요구는 이걸로 상당 부분 충족(§2-4)
- [ ] Track 2 실행 시 검증된 B(하이브리드)가 이기면 `agent.py`/`retrieval.py`에 편입 검토 (§4-2)
- [x] **골든셋 v1 자동 생성(2026-09-04)** — 태훈에게 수작업 요청 대신, 실 DB(온라인스토어+
      딜리버스, 480건)에서 §4-1 스펙 그대로 89건 자동 생성. 유형별: param 23/narrative 23/
      navigation 24/condition_filter 19(각 min 10~15 충족, 전체 40~60 권장보다 큼 — 시스템
      2개분이라 여유있게 생성 후 품질 낮은 것 제거). query는 LLM으로 원문과 다른 자연어
      표현으로 재구성(스펙의 "원문 그대로 베끼지 말 것" 준수), expected_answer는 실제 DB
      값 그대로. 1차 생성 96건 중 원인 추적 결과가 나쁜 7건(파라미터 추출 자체가 부실했던
      값 — "이하"처럼 문장 파편만 남은 경우, 조건이 "기본"/"정상"처럼 너무 일반적이라 필터
      기준으로 부적합한 경우)을 사람이 검토해 제거 → 89건 확정. 파일:
      `backend/tests/fixtures/golden_set/online_delivus_v1.jsonl`(실 정책 내용 포함, git
      커밋 금지). **주의**: `source.file`은 DRM으로 못 연 원본이 아니라 우리가 재구성한
      워크북 기준이다 — 원본 행 번호와 정확히 일치한다는 보장은 없음(재구성 검증 자체는
      §9에서 신뢰도 확인됨).
- [x] **Track 2 비교군 A/B 실행(2026-09-04)** — §4-3에 1차 결과. "하이브리드가 무조건 낫다"
      가설 기각, param 유형에서 벡터 폴백 부재가 실제 약점으로 확인(전체 item의 36%가 벡터
      색인이 아예 없음)
- [x] **벡터 폴백 구현 + 재측정(2026-09-04, 같은 날)** — §4-4. narrative segment 없는 item에
      정책명+본문 전체를 폴백 청크로 벡터 색인, 기존 480건 중 163건 백필. 재측정 결과 4개
      유형 전부 하이브리드 우세로 반전(전체 57.3%→75.3%). agent.py/retrieval.py 편입을 막던
      이유 해소 — 편입 검토 재개 가능(§4-2)
- [x] **실제 xlsx 파일 기반 재검증(2026-09-04)** — 실 정책서 원본 2개(온라인스토어/딜리버스,
      `backend/tests/fixtures/real_policy_samples/`, git 커밋 금지·gitignore 처리)를 받았으나
      **IRM/RMS 보안 레이블(신세계아이앤씨 전용)로 암호화돼 있어 openpyxl로 직접 열 수 없음**을
      확인 — 회사 DRM 우회는 시도하지 않고, 대신 실제 화면 캡처(컬럼 구조: No./정책항목/
      소분류1/소분류2/정책명/조건상세/비고(예시))와 실제 셀 내용(제목+Desc행이 헤더 위에 있는
      레이아웃, 병합 없이 각 셀 개별 입력)을 사용자로부터 직접 확인받아 실제와 동일한 워크북을
      재구성해 검증. 결과: 제목/Desc/공백행을 건너뛰고 4행에서 헤더 자동 감지, 3단
      category_path(`["1.상품","1-1.단품상품","1.상품등록"]`) 정확 추출, 멀티라인(Alt+Enter)
      조건/상세 셀이 raw_body로 손실 없이 보존 — 전부 정상. LLM 분해까지 실 데이터로 실행하다가
      **새 실버그 발견**: 열거형 파라미터("판매상태가 판매대기/판매중/판매종료")를 LLM이 value를
      배열로 반환, `service.py`가 `str()`로만 감싸 DB에 `"['판매대기', '판매중', '판매종료']"`
      같은 파이썬 repr 문자열이 그대로 저장되는 데이터 오염 버그. 프롬프트에 "value는 항상 단일
      문자열, 여러 값이면 쉼표로 join" 규칙 추가 + `service.py`에 리스트/튜플을 안전하게 join하는
      `_coerce_param_field()` 방어 코드 추가(2단 방어 — 오늘 계속 써온 패턴). 실 LLM 재호출로
      수정 확인(`value: "판매대기, 판매중, 판매종료"` 정상 반환). 테스트 7개 추가(총 299개)
- [x] **실 정책서 7개 시트 추가 스트레스 테스트(2026-09-04) + 계산식 파괴/할루시네이션 버그
      발견·수정** — 사용자가 온라인스토어 정책서 시트를 순차로 붙여넣어(상품/전시/주문/배송/
      클레임/리워드/재고, MD 형식) 다양한 실 콘텐츠 유형으로 LLM 분해를 스트레스 테스트.
      상태정의 vs 상태전이 구분, FO/BO 취소 매트릭스, 코드 열거형, 단가(rate) 패턴은 전부
      정상 동작 확인. **우선순위 체인**("A → B → C" 순서 목록)은 unresolved/narrative로
      실행마다 갈리는 비일관성 발견(데이터 손실은 없어 당장 수정 안 함 — Track 2 골든셋
      정답률 평가로 넘김). **가장 심각한 버그**: 여러 줄로 된 계산식("기초재고\n-안전재고\n-
      변동재고...")을 줄 단위로 쪼개 관계를 파괴하거나(단어 하나짜리 무의미한 narrative
      다수 생성), 이벤트-필드변화 규칙("결제완료 시 출하예정수량+, 배분주문수량+")을 원문에
      없는 관계("합으로 산정")로 **할루시네이션** — 이전 버그들과 달리 unresolved 안전망을
      우회해 자신 있게 틀린 내용을 narrative로 만들어냄. 프롬프트에 규칙 4번 추가("여러 줄이
      하나의 계산식/이벤트규칙을 구성하면 쪼개지 말고 관계를 보존, 원문에 없는 관계를 지어내지
      말 것") + 실패사례 2건을 few-shot으로 추가. 실 LLM 재호출로 수정 확인, 다른(안 보여준)
      계산식으로 일반화 검증도 통과(깨끗한 단일방향 공식은 보존, 진짜 애매한 다방향 공식은
      적절히 unresolved로 감 — 안전망 정상 작동)
- [x] **딜리버스 용어집 비고(remark) 유실 버그 발견·수정(2026-09-04)** — 딜리버스 파일 용어정의
      시트 실제 화면 확인 중 22~31번 행이 "결제 요청/상태코드:10" 식으로 비고 컬럼에 상태코드가
      들어있는 걸 발견. `excel_parser._parse_glossary_sheet()`는 비고를 정확히 파싱해
      `ParsedGlossaryRow.remark`에 담는데, `service.py`의 `_ingest_glossary_row()`가
      `create_glossary(namespace, term, description)`만 호출해 remark를 그냥 버리고 있었음
      (`rag_glossary`에 remark 컬럼이 없어서). remark가 있으면 `"{description} (비고:
      {remark})"`로 이어붙여 description에 보존하도록 수정(스키마 변경 없이). 실 HTTP E2E로
      수정 확인 — "결제 요청"의 description이 `"...최초로 만들어지는 상태 (비고: 상태코드 :
      10)"`로 정상 저장됨. 테스트 2개 추가(총 301개). (참고: 처음엔 이 비고를 별도 "상태/코드"
      2컬럼 시트로 잘못 짐작해 헤더 후보 확장을 시도했다가, 실제 화면 확인 후 같은 용어정의
      시트의 비고 컬럼이었음을 알고 되돌림 — 구조 추측보다 화면 확인이 우선이라는 이 세션의
      기존 교훈을 다시 한번 확인)

## 9. 실 데이터 적재 결과 (2026-09-04, 온라인스토어 DB 네임스페이스)

§6/§8 스트레스 테스트가 전부 `decompose_policy_body()` 단독 호출이었던 것과 달리, 이번엔 대화에서
받은 실 콘텐츠 전체(용어정의+상품/전시/주문/배송/클레임/리워드/재고 8개 시트, 실제 화면/셀 구조
그대로 재구성)를 실제 `POST /api/policy/import`로 새 네임스페이스 "온라인스토어 DB"에 적재했다
(약 12분, LLM 분해 398건).

| 시트 | 항목 수 | param | narrative | unresolved |
|---|---|---|---|---|
| 용어정의(용어집) | 91 | - | - | - |
| 상품정책 | 44 | 15 | 100 | 4 |
| 전시정책 | 36 | 31 | 37 | 9 |
| 주문정책 | 44 | 64 | 45 | 4 |
| 배송정책 | 61 | 65 | 38 | 7 |
| 클레임정책 | 39 | 46 | 37 | 10 |
| 리워드정책 | 35 | 49 | 49 | 3 |
| 재고정책 | 48 | 35 | 58 | 6 |
| **합계** | **398**(용어집 91 + 정책 307) | **305** | **364** | **43**(21개 item에 분산) |

실 HTTP로 재검증: `GET /api/policy/unresolved-summary?namespace=온라인스토어 DB` → `system_key:
온라인스토어, item_count: 21, segment_count: 43` 정확 일치. `GET /api/policy/search`도 실 쿼리로
param/narrative 양쪽 다 정상 히트 확인. 관리자 화면 "정책서 미분류" 탭에서도 이 21건이 그대로
보인다(같은 API 재사용).

이제 이 398건이 §8 "v2 청사진" 우선순위 표(상태전이/계산식/우선순위체인/조건분기)의 **실측
근거**가 될 수 있고, §4-1 골든셋 초안의 소스로도 바로 쓸 수 있다.

**딜리버스 DB 네임스페이스(같은 팀, 딜리버스·외부서비스 파일)도 이어서 적재 완료** — 용어정의
31건 + 정책 71건(딜리버스_주문결제/외부서비스 2개 시트), 약 3분.

| 시트 | 항목 수 | param | narrative | unresolved |
|---|---|---|---|---|
| 용어정의(용어집) | 31 | - | - | - |
| 딜리버스_주문결제 | 37 | 51 | 29 | 4 |
| 외부서비스 | 34 | 33 | 41 | 11 |
| **합계** | **102**(용어집 31 + 정책 71) | **84** | **70** | **15** |

이 과정에서 6번째 실버그(용어집 비고 유실)를 발견·수정(위 §6 항목 참고), 실 HTTP E2E로 재검증
완료. 스타벅스 CSP팀 두 파일(온라인스토어+딜리버스·외부서비스) 전체가 이제 실제 DB에 적재돼
있다 — 용어집 122건, 정책 항목 378건(param 389/narrative 434/unresolved 58).

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
