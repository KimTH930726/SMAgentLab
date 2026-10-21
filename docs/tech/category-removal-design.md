# 업무구분(지식 category) 폐지 + 업무 영역 합치기·트리 — 설계 + 영향 분석 (설계안, 2026-10-21 개정, 검수 대기)

> 근거 결정: admin-ia-design.md §2-4·§4·§5-④·§5-3, 메모 project_org_domain_model(10/20~21).
> **10/21 사용자 확정(이 개정의 전제)**: ① 채팅 업무구분 필터·질문마다 도는 업무구분 자동 감지 LLM 호출은 ④에서 **삭제**(선택 아님).
> ② 하위 업무는 **업무 영역 트리**로(10/20 결정) — ④ 범위 안. ③ 외부서비스와 제휴서비스는 **같은 업무 → 한 영역으로 합친다**(10/20 딜리버스 DB → 딜리버스와 같은 방식).
> ④ 데이터 변경(합치기·하위 영역 생성·행 이동·삭제)은 항목별 승인.
> 대상: `rag_knowledge.category`·`rag_knowledge_category`·`ops_prompt(category_suggest)`와 이를 읽고 쓰는 코드, 그리고 `ops_namespace.parent_id`(트리).
> **대상 아님(이름만 같음)**: 정책 `policy_item.category_path`·`ref_table.category_path`·`service/policy/category_list.py`(탐색형 분류 목록·clarify),
> VOC `ops_email_analysis.category`(system_error 등 4종). 이들은 그대로 둔다.
> 수치는 2026-10-21 dev DB SELECT 실측. 원문(컨플루언스 본문·정책 본문)을 인용하지 않는다 — 개수·구조만.

## 1. 지금 실제로 막힌 일

1. **사람이 고르는 칸이 등록 경로마다 있다.** 수동·붙여넣기·파일·URL·컨플루언스 일괄 모두 `RequiredCategoryField`(KnowledgeTable.tsx:158)를
   거치고, 서버는 `_require_category`(knowledge/service.py:33)로 빈 값을 거부한다. §5-2 원칙("사람은 승인만")과 정면으로 어긋난다.
2. **기본 칸이 쓰레기통이 됐다.** 활성 73건 중 27건(37%)이 공통지식·공통업무·분류 확인 필요. 전체 617건 기준으론 517건(84%).
   "분류 확인 필요"는 AI 추천 실패 시 자동 생성되는 칸(admin/service.py:205·233)이라 정보가 0이다.
3. **유형 분기는 이미 죽은 가지다.** `_KEYWORD_ONLY_CATEGORIES=("DB","공통코드")`(retrieval.py:299)에 해당하는 **활성 지식 0건**
   (DB 10건 deleted, 공통코드 1건 deprecated). 구조화 데이터는 이미 `ref_common_code`(딜리버스 252행)로 옮겨져 refdata 채널(agent.py:242-246)로 검색된다.
4. **AI 호출 3종이 업무구분만을 위해 돈다** — `category_suggest`(등록 + **채팅 질문마다 자동 감지**), `auto_tag_chunks`(tagger.py), analyzer `suggested_categories`.
5. **하위 업무(배민·쿠팡)가 영역이 아니라 칸에 들어 있다** — 그래서 담당·권한·화면·측정이 하위 업무 단위로 안 된다(10/20 결정 미이행).
6. **같은 업무가 두 영역으로 갈라져 있다** — 외부서비스(컨플 지식 49, 정책 0)와 제휴서비스(v0 정책 68, 용어 23, 지식 0). 채팅에서
   외부서비스를 고르면 제휴서비스 용어(23)로 질문 확장이 안 되고, 제휴서비스를 고르면 컨플 지식이 안 나온다.

## 2. 영향 목록

### 2-1. DB (스키마·제약)

| 대상 | 현황 | 비고 |
|---|---|---|
| `rag_knowledge.category` VARCHAR(100) | **NULL 허용**, 인덱스·CHECK 없음 | 필수는 앱(`_require_category`)만 — 백필 불필요 |
| `rag_knowledge_category` | 9행(외부서비스 4·딜리버스 5), UNIQUE(namespace_id,name), FK CASCADE | init/01-init.sql:92, 02-migrate-fk.sql:96-217, main.py:74·285·301 |
| `ops_prompt` func_key=`category_suggest` | 1행(id 205) | **main.py:368 시드가 매 기동 재삽입** → 시드 먼저 지워야 삭제 유지. main.py:397도 참조 |
| `ops_improvement_item.original` jsonb | `category` 키 1행 | main.py:1198 이관 스냅샷 — 읽는 곳 없음, 그대로 |
| 마이그레이션 `_migrate_rename_unsorted_category` | main.py:1615·1893 | 컬럼 drop 단계에서 같이 제거 |
| `ops_namespace.parent_id` | 칸 있음(main.py:1513, FK SET NULL), **15개 전부 NULL**, 코드·API·화면 어디서도 안 읽음 | 트리는 스키마 변경 없이 이 칸만 쓴다. 깊이 1단(상위의 상위 금지)으로 제한 |

### 2-2. 데이터 — 값별 분류

| 영역 | 값 | 전체 | 활성 | 분류 | 근거 |
|---|---|---|---|---|---|
| 외부서비스 | 1. 배달의민족 | 44 (deprecated 20) | 24 | **하위 업무** | 컨플 상위 페이지 제목으로 자동 생성(router.py:978). 활성 24/24 `heading_path`에 같은 값 |
| 외부서비스 | 2. 쿠팡이츠 | 41 (deprecated 19) | 22 | **하위 업무** | 위와 같음, 22/22 |
| 외부서비스 | 공통업무 | 14 (deprecated 11) | 3 | 기본 칸 | 상위 영역에 남긴다 |
| 외부서비스 | 분류 확인 필요 | 5 (rejected) | 0 | 기본 칸 | 상위 영역에 남긴다 |
| 딜리버스 | 공통지식 418 · 분류 확인 필요 79 · DB 10 · 공통코드 1 · VOC 업무처리 0 · NULL 4 | 512 | 24 | 기본 칸·유형 | 딜리버스엔 하위 업무 없음 → 이동 없음 |
| 온라인스토어 | 분류 확인 필요 | 1 (deleted) | 0 | 기본 칸 | 고아 값 |

**하위 업무로 판정 = 배달의민족·쿠팡이츠 2개뿐**(외부서비스 85행, 활성 46). 다른 영역의 값은 전부 기본 칸·유형이라 트리로 안 옮긴다.

### 2-3. 합칠 두 영역 — namespace_id를 가진 표 전부(16개, FK 기준 `restructure_domains.ns_tables`와 같은 목록)

| 표 | 외부서비스(266) | 제휴서비스(531) | 비고 |
|---|---|---|---|
| rag_knowledge | 104 (활성 49) | 0 | |
| rag_knowledge_category | 4 | 0 | 같은 이름 충돌 0 |
| rag_ingestion_job | 10 | 0 | |
| ops_conversation | 13 | 0 | |
| ops_query_log | 27 | 0 | |
| policy_item | 0 | 68 (전부 active, v0_whole) | policy_chunk·param은 item FK로 따라감(namespace 칸 없음) |
| rag_glossary | 0 | 23 | **같은 이름 용어 0** |
| policy_doc_overview | 0 | 1 | |
| ops_email_analysis·ops_voc_cluster·**ops_voc_routing**·ops_improvement_item·policy_review_log·rag_source_document·ref_common_code·ref_db_column·ref_table | 0 | 0 | VOC 담당 연결 0건 — 옮길 것 없음 |

그 밖: `ops_link`에 두 영역 키(domain·section) 0건, `ops_system_config` 용어 수집 키(`glossary_mining_last_id:`/`glossary_gap_done:`) 0건.
담당 파트: 외부서비스 = 온라인서비스 파트(774), 제휴서비스 = 미지정. 설명: 외부서비스 비어 있음, 제휴서비스 있음.
이름 참조 코드: `scripts/eval_chat_retrieval.py:36`(NEGATIVE_NAMESPACES), `scripts/gen_confluence_questions.py:56`(기본값) — 이름 교체 필요. 의미 캐시는 이름 키라 두 이름 다 비운다.

### 2-4. 제휴서비스 정책 68건 — 제휴사별인지(제안, **사용자 확인 필요**)

`category_path`는 전부 2단, 1단 묶음 5개. 1단 이름에 제휴사 이름이 있는지만 셌다(본문 인용 없음).

| 1단 묶음 | 정책 수 | 1단 이름에 든 제휴사 | 제안 |
|---|---|---|---|
| ① | 16 | 배달의민족 | → 하위 영역 **배달의민족** |
| ② | 16 | 쿠팡(이츠) | → 하위 영역 **쿠팡이츠** |
| ③ | 18 | 땡겨요 | 상위에 둠(땡겨요 지식 0 — 하위 영역을 만들 실익 없음) |
| ④ | 9 | 네이버 | 상위에 둠(같은 이유) |
| ⑤ | 9 | 없음(공통) | 상위에 둠 |

검색 영향은 작다: 정책 범위(scope.py)는 연결(ops_link) 0이면 "정책 있는 영역 전부"라 정책이 하위로 가도 상위·하위 어느 쪽에서 물어도 찾는다.
이득은 화면·담당(하위 영역에서 그 제휴사 정책·지식이 같이 보임). 용어 23건은 영역 공통이라 상위에 둔다. → **안 해도 되는 선택 항목(D3)**.

### 2-5. 백엔드 코드 — 업무구분 (지식 category만)

| 묶음 | 위치 | 하는 일 |
|---|---|---|
| 검색 분기 | retrieval.py:157·299-305·342-349·393·476, agent.py:93·175·196·332·401 | 키워드 전용 점수·채택·라벨, 업무구분 필터 |
| 채팅 필터 | chat/router.py:55·70·195·247·297·309, chat/schemas.py:23·83 | `req.categories` |
| VOC 판정 | pattern_detection.py:64·360·366·411·438 | DB·공통코드 지식 제외(활성 0건) |
| 등록 필수·자동 | knowledge/service.py:33-51·70-77·141·231·834-855·96-103·1014 | 필수 검사·자동 결정 |
| 등록 자동 | knowledge/router.py:57-72·100·125·356-410·433-446·531-563·628-682·777-807·930-987·1018-1100 | 컨플 상위 제목 → 새 업무구분 등 |
| AI 호출 | admin/service.py:152-234, tagger.py:14-84, analyzer.py:27·39·68·106·138 | 프롬프트 `category_suggest` |
| 관리 API | admin/router.py:115-230, admin/schemas.py:25-29·46, admin/service.py:33-47 | |
| 일괄 수정·원장·정정 | knowledge/router.py:194-222, service.py:223-241·351·374-393, source_ledger.py:112-146, improvement/service.py:55-59·603-629·779-801 | |
| 공개 API·스크립트 | `GET /knowledge/keyword-only-categories`(router.py:38), eval_confluence_structure·bench_*·export_table_spec·scenarios/*·**domain_permissions.py:55-64(권한 확인을 업무구분 생성 API로 함)** | |

### 2-6. 백엔드 코드 — 트리(하위 포함 검색·담당 상속)

| 대상 | 지금 | 최소 변경 |
|---|---|---|
| 영역 범위 | `resolve_namespace_id`(core/database.py:35) = 이름 → id 하나, 호출 118곳 | 새 함수 `namespace_scope_ids(conn, name) -> list[int]`(자신 + 직계 하위). **검색 경로만** 바꾸고 나머지 118곳(등록·수정·통계)은 그대로 = 고른 영역 자신 |
| 지식 검색 | retrieval.py:339 `search_knowledge` — vector·keyword CTE·본문 WHERE 3곳 `namespace_id = $2`, BM25 캐시 `_kw_cache[ns_id]`(:319-329) | `= ANY($2::int[])`, 캐시 키를 id 튜플로 |
| 용어 | glossary_terms.py:158-168 — 자신 + `extra_ns_ids`(정책 범위) | 자신 대신 범위 id 목록. 하위 영역을 골랐을 때 상위 용어는 정책 범위("전부")로 이미 들어옴 |
| 정책 범위 | scope.py `_SQL` cand: 자신 ord 0 / 연결 / 전부 ord 2 | cand에 직계 하위를 ord 0으로 추가(지금도 "전부"에 들어오지만 순서만 앞으로) |
| 참고 데이터 | refdata/service.py:71·119 `has_refdata`·`search_common_codes`·`search_db_columns` | 같은 범위 id 목록(외부서비스 계열은 0행 — 일관성용) |
| VOC 커버리지 | pattern_detection이 `search_knowledge` 사용 | 자동으로 따라감 |
| 담당 상속 | `check_namespace_ownership`(dependencies.py:84-95)·`check_namespace_read`(:127)·`log_cross_part_namespace_read`(:142)·질의 기록 일괄 삭제(admin/router.py:384) — 4곳이 `owner_part_id`를 직접 SELECT | 4곳 SQL을 `COALESCE(n.owner_part_id, p.owner_part_id) … LEFT JOIN ops_namespace p ON p.id = n.parent_id`로. `edit_denied_reason`은 그대로(규칙 한 곳 유지) |
| 영역 목록 API | admin/service.py:21-45 `list_namespaces_detail` — parent_id 없음 | `parent_id`·상위 이름·**실효 담당 파트**(상속 반영) 반환 → 프론트 `canEditDomain`(utils/domainAccess.ts:8, 이름 비교)이 고칠 필요 없이 맞음 |
| 영역 생성·수정 | admin/service.py:61-73·146 | `parent_id` 받기. 검사: 상위는 최상위여야(깊이 1)·자기 자신 금지 |
| 영역 삭제 | admin/service.py:116 — 하위는 FK SET NULL로 조용히 최상위가 되고 담당 상속이 끊김 | 하위가 있으면 409 "하위 영역 n개를 먼저 옮기거나 지우세요" |

### 2-7. 프론트

| 위치 | 하는 일 |
|---|---|
| KnowledgeTable.tsx(105회): `RequiredCategoryField`(158-188, 5곳), 목록 필터·일괄 수정·배지·키워드 전용 목록 | 업무구분 삭제 |
| ChatContainer.tsx:166-250 `CategoryFilter`, 570-584(자동 감지 → `suggestCategory` = 채팅마다 LLM 1회) | **삭제(확정)** |
| useAppStore.ts:49-84, useStreamStore.ts:57·133·161, api/chat.ts:13·29 | 필터 상태·전달 삭제 |
| NamespaceManager.tsx:23-130 `CategorySection`, 356-382(업무구분 열), 488 | 업무구분 삭제 + **트리 표시**: 기존 표 그대로, 하위 행은 상위 바로 아래 들여쓰기(정렬: 상위 → 하위). 생성·수정 모달에 "상위 영역" 선택(최상위만 후보) |
| Sidebar.tsx:54-124 `sortNamespacesByUserPart` | 하위를 상위 아래 들여써서 나열. 상위를 고르면 하위 포함 검색(툴팁으로 안내) |
| UnifiedAdhocSearch.tsx·KnowledgeStructurePanel.tsx·api/knowledge.ts·api/namespaces.ts·types/index.ts(`Namespace`에 parent 추가)·e2e/knowledge-registration.spec.ts | 타입·API·e2e |

새 화면 요소(들여쓰기 표·상위 영역 선택·사이드바 하위 표시)는 **만들기 전에 모양을 보여주고 검수**(feedback_ui_needs_review_first).

### 2-8. 테스트(백엔드)

지울 것: test_retrieval_category_routing(29), test_confluence_category_resolution(17), test_admin_category_suggest(22), test_require_category_guard(11).
고칠 것: test_ingestion(35)·tier2·tier3, test_knowledge_weight_removed, test_agent_rrf_context, test_email_voc_pattern_detection, test_improvement, test_source_ledger, conftest.
추가: 범위(하위 포함·하위만·깊이 1 거부·자기 자신 거부), 담당 상속 4경로(하위 owner NULL → 상위 담당 파트는 수정 가능·다른 파트 403·상위도 미지정이면 관리자만), 하위 있는 영역 삭제 409,
합치기 불변식(표마다 두 영역 합 = 합친 뒤, 옛 id 가리키는 행 0). `test_category_list.py`는 정책 분류 목록이라 **그대로**.

## 3. 목표 설계 (MVP)

| 지금 쓰임 | 바뀌는 것 |
|---|---|
| 같은 업무 두 영역 | **외부서비스 → 제휴서비스로 합침**(남는 이름 "제휴서비스" = 정책서 공식 이름, **확인 필요**). 남는 id = 531(정책·용어·개요가 붙은 쪽). 담당 = 온라인서비스 파트(외부서비스 것을 옮김), 설명 = 제휴서비스 것 |
| 하위 업무(배민·쿠팡) | **하위 영역**: 제휴서비스 > 배달의민족, 쿠팡이츠(이름에서 "1. "·"2. " 번호 뗌, 확인 필요). owner_part_id NULL = 상위 담당 상속. 상위를 고르면 하위 포함 검색 |
| 유형(DB·공통코드) | 시스템 정보 = 기존 `ref_common_code`/`ref_db_column`. "지식 유형" 칸 안 만듦. 키워드 전용 분기·상수·API 삭제 |
| 기본 칸 | 없앰. 지식은 영역에 직접(category NULL). 공통업무 3건 등은 상위 영역에 남음 |
| 채팅 업무구분 필터·자동 감지 LLM | **삭제**. "배민만"은 사이드바에서 하위 영역 배달의민족을 고르는 것으로 대체 |
| 등록 시 선택·AI 추천·자동 태깅 | 삭제. 컨플 일괄의 "상위 페이지 제목 → 업무구분 자동 생성"도 삭제 — 등록자가 하위 영역을 골라 넣는다 |
| 원장 `mixed_category`·재분할 최빈값 계승 | 삭제 |
| 업무 영역 화면 | 업무구분 열·관리 삭제, 트리(들여쓰기) 표시 |

**검색이 어떻게 바뀌나(측정 전 예측)**
- 업무구분 제거: category는 임베딩·LLM 문맥에 안 들어가고(retrieval.py:479-482) 키워드 전용 분기에 걸리는 활성 행이 0 → **변화 0**.
- 트리 이동: 상위 검색 = 상위 + 하위 행 합 = 옮기기 전과 **같은 행 집합** → 벡터·키워드(행별 ts_rank)·BM25(같은 문서 집합) 점수 동일 → **변화 0**. 하위만 검색은 후보가 줄어 달라질 수 있음(보고만).
- 합치기: **바뀐다(의도)** — 제휴서비스 채팅에 컨플 지식 49건이 정책과 RRF로 경쟁, 외부서비스 쪽 질문엔 용어 23건 확장·정책 68건이 붙는다. 그래서 측정 게이트를 건다(§4).

**마이그레이션 순서(되돌릴 수 있게, 각 단계 멱등)**

1. **백업(추가)** — `rag_knowledge_category_backup_20261021`(9행) + `rag_knowledge_category_value_backup`(id·namespace_id·category, 617행 — 트리 이동 되돌림 매핑 겸용)
   + `ops_prompt_category_suggest_backup`(1행) + `ns_merge_backup_20261021`(표 이름·행 id, 외부서비스 쪽 158행 + 영역 행 자체: 이름·설명·담당).
2. **합치기(D1)** — `restructure_domains.py`의 `ns_tables`·`counts`·`merge`를 그대로 import하는 1회성 스크립트(MERGE={"외부서비스":"제휴서비스"}, 미리보기 → `--apply` 트랜잭션 하나,
   점검: 표마다 합 = 합친 뒤, 266 가리키는 행 0, 실패면 전부 되돌림). 담당 774를 531로 옮김, 의미 캐시 두 이름 비움, 스크립트 2곳 이름 교체.
3. **트리 코드(C1·C2)** — 하위가 0인 상태로 배포 → 동작 변화 0(테스트는 임시 영역으로).
4. **하위 영역 생성 + 이동(D2)** — 배달의민족·쿠팡이츠 생성(parent 531) → `UPDATE rag_knowledge SET namespace_id=<하위> WHERE namespace_id=531 AND category=…`(모든 상태 85행: 컨플 페이지 교체·중복 판정이
   영역 단위라 옛 버전도 같이 가야 함). 점검: 하위별 행 수 = 표(44·41), 상위 잔여 = 19(공통업무 14·분류 확인 필요 5). 캐시 비움.
5. **(선택, D3) 정책 32건 하위로** — §2-4 ①②. 확인 받은 경우만.
6. **읽기 끊기(C3) → 쓰기 끊기(C4) → 관리·프롬프트(C5)** — 프롬프트는 main.py:368 시드·397 참조 제거 후 `DELETE FROM ops_prompt WHERE func_key='category_suggest'`.
7. **버리기(C6, 1~2주 운영 뒤)** — `DROP COLUMN IF EXISTS category`, `DROP TABLE IF EXISTS rag_knowledge_category`, main.py:74·285·301·1615 정리. `scripts/check_migrations_twice.py` 필수.

**명시 승인이 필요한 데이터 변경(항목별)**

| # | 변경 | 정확한 대상 | 되돌리기 |
|---|---|---|---|
| 1 | 백업 테이블 4개 생성 | 9 + 617 + 1 + 158(+영역 1) 행, 추가만 | DROP |
| 2 | **외부서비스 → 제휴서비스 합치기** | rag_knowledge 104 · rag_knowledge_category 4 · rag_ingestion_job 10 · ops_conversation 13 · ops_query_log 27 → 531. 외부서비스 영역 행 삭제. 담당 531 ← 774. ops_link·용어 수집 키·VOC 연결·같은 이름 용어 = 0 | 아래 R1 |
| 3 | 하위 영역 2개 생성 | 배달의민족·쿠팡이츠(parent 531, 담당 NULL=상속) | 하위 행을 상위로 되돌린 뒤 삭제 |
| 4 | 지식 85행 이동 | 배달의민족 44(활성 24·deprecated 20), 쿠팡이츠 41(활성 22·deprecated 19). 상위 잔류 19 | 아래 R2 |
| 5 | (선택) 정책 32건 이동 | §2-4 ① 16 → 배달의민족, ② 16 → 쿠팡이츠. 상위 잔류 36 + 용어 23 + 개요 1 | `UPDATE policy_item SET namespace_id=531 WHERE id = ANY(저장한 id)` |
| 6 | 프롬프트 삭제 | `ops_prompt` id 205 | 시드 되살리고 백업에서 복원 |
| 7 | `rag_knowledge.category` 칸 삭제 | 617행 값(활성 73) | 백업에서 `ADD COLUMN` + `UPDATE … FROM` |
| 8 | `rag_knowledge_category` 테이블 삭제 | 9행 | 백업에서 재생성 |

**롤백**
- R1(합치기): 외부서비스 영역을 백업 값(이름·설명·담당 774)으로 다시 만들고(새 id), `ns_merge_backup`의 표·행 id로 `UPDATE <표> SET namespace_id=<새 id> WHERE id = ANY(…)`. 531 담당은 NULL로.
  합친 뒤 외부서비스 지식이 새로 들어왔다면(백업에 없는 행) 사람이 판단 — 롤백 스크립트가 개수를 먼저 출력한다.
- R2(트리 이동): `UPDATE rag_knowledge k SET namespace_id=531 FROM rag_knowledge_category_value_backup b WHERE k.id=b.id AND k.namespace_id IN (<하위>)`,
  백업에 없는 하위 행(이동 뒤 등록분)도 531로, 하위에 붙은 다른 행(대화·질의 기록 등 — FK CASCADE라 지우면 사라짐)을 표마다 세어 531로 옮긴 뒤 `DELETE FROM ops_namespace WHERE parent_id=531`.
- 코드(C1~C5)는 revert만으로 원상(DB 값이 남아 있음). C6 이후는 백업 테이블 복원 SQL(이 문서와 함께 커밋), 백업은 C6 뒤 한 달 보관.

## 4. 하는 것 / 안 하는 것, 측정, 커밋 나누기

**하는 것(MVP)**: §3 표 전부, 순서 1~6. 7은 운영 확인 뒤. 정책 이동(D3)은 확인 받으면.

**안 하는 것 + 다시 볼 조건**
- 깊이 2단 이상 트리·재귀 범위 — 실제로 하위의 하위가 필요한 업무가 나올 때.
- 땡겨요·네이버 하위 영역 — 그 제휴사 지식이 들어오거나 담당이 갈릴 때(정책 ③ 18 · ④ 9가 후보).
- 하위 영역을 골랐을 때 상위 지식 포함 — "하위에서 물었는데 공통업무 답이 안 나온다"는 실사례가 생길 때.
- 관리 화면 지식 목록의 하위 포함 보기 — 지금은 고른 영역 자신만(하위는 따로 고름).
- "지식 유형" 칸 — rag_knowledge에 정책·지식 외 유형이 실제로 들어올 때. "미분류" 상태 — 단일 등록 창구(§5-2) 설계 때. 업무 영역 꼬리표 0..N — 보류.

**측정 계획(같은 날 전·후, `scripts/eval_gate.py`)**

| 시점 | 무엇 | 문항 | 게이트 |
|---|---|---|---|
| D1 전·후 | 정책 골든셋 v0 검색 `eval_golden_v0.py --details …` | 승인 485, **제휴서비스에서 묻는 25문항이 영향권**(지식 49건이 새로 경쟁) | 검색 나빠짐 0 |
| D1 전·후 | 정책 답 `eval_answers.py` → `eval_gate.py --answers-*` | 제휴서비스 문항 | 의미 있는 하락 없음 |
| D1 전·후 | 지식 검색 `eval_knowledge_retrieval.py` + `eval_knowledge_answers.py`(로컬 LLM 채점) | confluence_synth 106. **문항 `ns`가 옛 이름(외부서비스DB 92·딜리버스 DB 14)이라 지금은 조용히 0건** → 이름만 바꾼 사본 2개를 eval_out에: 전 = 외부서비스·딜리버스, 후 = 제휴서비스·딜리버스(원문 안 엶) | hit 하락 없음, 채택 id 차이는 원인 확인 |
| D2 전·후 | 같은 지식 세트(ns = 제휴서비스) | 92 | **채택 id 목록 문항별 완전 동일**(예측 차이 0) — 다르면 진행 금지 |
| D2 후 | 하위만 사본(정답 조각의 옛 category로 ns 지정: 배달의민족 44·쿠팡이츠 42, 공통업무 6은 상위 유지) | 86 | 보고만 |
| C3 전·후 | 위 지식 세트 + 골든셋 | | 변화 0 |
| C4 후 | 등록 시나리오: 수동·붙여넣기·컨플 일괄 각 1건 | | category NULL·heading_path 유지·LLM 호출 수 감소(전/후 값) |
| C1 후 | 담당 상속 시나리오(`scenarios/domain_permissions.py`를 업무구분 API 대신 영역 수정 API로) | | 하위 수정: 상위 담당 파트 200·다른 파트 403·조회 전용 403 |

**커밋 나누기(dev_0, 각자 테스트+리뷰 후, 작은 것부터)**

| # | 내용 | 규모(대략) |
|---|---|---|
| C0 | (선행) 지금 미커밋 묶음(tagger·analyzer·admin/service·NamespaceManager 등) 먼저 커밋 | — |
| D1 | 합치기 스크립트(restructure_domains 함수 재사용) + 스크립트 이름 2곳 + 측정 → 승인 후 `--apply` | ~60줄 |
| C1 | 트리 백엔드: `namespace_scope_ids`·검색 3경로(지식·용어·정책 범위·참고 데이터)·담당 상속 4곳·목록 API parent/실효 담당·생성/수정 parent 검사·하위 있는 삭제 409 + 테스트 | 백 ~120줄 |
| C2 | 트리 화면: 업무 영역 표 들여쓰기·상위 영역 선택·사이드바 하위 표시(모양 검수 먼저) | 프론트 ~120줄 |
| D2 | 하위 영역 2개 + 85행 이동 스크립트(미리보기/--apply, 점검 포함) → 승인 후 실행 + 측정 | ~50줄 |
| D3 | (선택) 정책 32건 이동 | ~20줄 |
| C3 | 읽기 끊기: retrieval·agent·chat 필터·**자동 감지 LLM**·VOC 제외·`/keyword-only-categories`·ChatContainer·store + 테스트 2파일 삭제 | 백 ~120줄 삭제, 프론트 ~150줄 |
| C4 | 쓰기 끊기: 등록·정정·원장·일괄 수정·AI 추천·tagger·analyzer + KnowledgeTable·UnifiedAdhocSearch + e2e | 백 ~300줄, 프론트 ~250줄 |
| C5 | 관리 API·NamespaceManager 업무구분 열·`category_count` + 프롬프트 시드 제거·삭제 + 백업 테이블 | 백 ~150줄, 프론트 ~130줄 |
| C6(나중) | 칸·테이블 drop + 옛 마이그레이션 정리 + docs 3종 동기화 | ~60줄 |

## 5. 위험·열린 질문

1. **이름 확인** — 남는 영역 "제휴서비스", 하위 "배달의민족"·"쿠팡이츠"(번호 뗌). 사이드바에 "외부서비스"를 기억한 사용자는 목록에 없으면 자동으로 첫 영역으로 바뀐다(Sidebar.tsx:104) — 한 번 안내.
2. **정책 32건 하위 이동(D3)을 할지** — §2-4. 검색 영향은 작고 화면·담당 이득만.
3. **미커밋 작업과 겹친다** — C0가 먼저.
4. **컨플 재적재 경로** — 상위 페이지 제목 → 업무구분 자동 생성이 사라지면 일괄 등록 때 하위 영역을 등록자가 고른다. 한 번에 상위 페이지 전체를 넣으면 상위 영역에 몰린다 → 재적재(원문 원장) 설계 때 "상위 페이지 → 하위 영역" 자동 매핑을 볼지.
5. **구조화 데이터가 다시 rag_knowledge로 들어오면** 키워드 전용 보호가 없다 — refdata 업로드가 정식 경로라는 안내로 충분한지.
