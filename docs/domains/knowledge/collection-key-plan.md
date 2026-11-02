# 컬렉션 키 정리 — 이름 키 → id · 업무 코드 (11/02 설계, 전환은 승인 뒤)

사용자 결정(11/02): "키는 유일하게 — string 말고 int, 또는 기능별 코드(사람이 보기 좋게)". 마이그레이션 #105로 `ops_namespace.code` 추가.
코드 형식은 `POL-MBR`(정책) · `SYS-HP`(시스템)이고, 불변·유일하다.

## 1. 키 3종 (업계 방식)
| 키 | 예시 | 쓰는 곳 | 바뀌나 |
|---|---|---|---|
| 내부 id (정수) | 2568 | DB 연결 · API 경로 · 캐시 | 안 바뀜 |
| 업무 코드 | SYS-HP | 사람·외부 연계(온톨로지 포털) · 골든셋 · 로그 | 안 바뀜 |
| 화면 이름 | 홈페이지 | 보여 주기만 | 자유(겹쳐도 됨 — 전환 뒤) |

## 2. 전수검사 (11/02, DB 전 테이블 + 코드)
| # | 위험 | 곳 | 문제 | 고칠 방법 |
|---|---|---|---|---|
| 1 | 높음 | 컬렉션을 **이름**으로 가리킴: 백엔드 `namespace: str` 146곳, 프론트 272곳, 골든셋 29개 파일, 답변 캐시 키 | 이름을 바꾸거나 같은 이름을 쓰면 캐시·골든셋·저장된 선택이 어긋난다. "딜리버스" 정책과 시스템이 같은 이름을 쓸 수 없다 | §3 단계 전환 |
| 2 | 중간 | FK 없는 컬렉션 id 칸: `rag_import_page`(final·ai·proposed·sample_ns), `rag_import_batch.default_namespace_id`, `eval_question_candidate.namespace_id` | 컬렉션을 지우거나 합치면 지운 id를 가리킨다(실제로 리뷰 L4에서 발생) | FK + `ON DELETE SET NULL` 마이그레이션 |
| 3 | 중간 | `ops_link.from_key/to_key`(글자): 정책 logical_id · 용어 id · 절 "id\|경로" · 컬렉션 id가 한 칸에 섞임 (policy→term 2,062 · supports 4 …) | FK가 없어 지우면 고아 연결이 남는다. 컬렉션 연결(supports · same_work)까지 글자 id로 들고 있다 | 컬렉션끼리 연결은 정수 FK 표(`ops_collection_link`)로 옮긴다. 나머지 종류 섞인 연결은 그대로 두고, 고아 검사를 `check_ingest`에 더한다 |
| 4 | 중간 | 설정 키 안에 id: `ops_system_config`의 `glossary_mining_last_id:{id}` · `glossary_gap_done:{id}` | 컬렉션 합치기 때 키를 손으로 옮겨야 한다(restructure_domains.merge가 함). 지워도 남는다 | 컬렉션별 상태 칸 또는 FK 있는 표로 옮긴다 |
| 5 | 낮음 | 백업 표 6개(`*_backup_*`) | 옛 1회성 작업의 백업이 남아 있다 | 내용을 확인하고 지운다(정리 단계) |
| — | 정상 | `policy_item.source_key/system_key`, `rag_knowledge.confluence_page_id`, `rag_import_page.page_id`, `ops_prompt.func_key`, `eval_candidate.key` | 외부 시스템 id이거나 안정된 코드라 글자가 맞다 | — |

## 3. 이름 → id·코드 전환 단계 (승인 뒤)
1. **API가 둘 다 받기**: `namespace` 인자에 이름·코드·id를 모두 받는다(`resolve_collection()` 한 곳에서 풀기). 화면·외부 호출이 깨지지 않는다.
2. **화면을 id로**: 선택 상태(localStorage)와 API 호출을 id로 바꾼다. 옛 이름 값은 읽을 때 id로 바꿔 저장한다.
3. **캐시·기록을 id로**: 답변 캐시 키를 이름에서 id로 바꾼다. 질의 기록은 이미 정수 FK다.
4. **골든셋을 코드로**: 29개 파일의 `ns`를 코드로 바꾸고, 읽을 때 이름도 허용한다(옛 파일 호환).
5. **FK 보강**: §2-2·3·4.
6. **이름 유일 제약 풀기**: 화면 이름은 겹쳐도 되게 한다(같은 구분 안에서만 유일). 이 단계 전까지 시스템 키 이름은 "○○ 시스템"을 유지하고, 화면 이름만 뗀다.

단계마다 전체 테스트 + E2E + 마이그레이션 두 번 기동 검사를 거친다. 1·2단계는 하위 호환이라 따로 반입할 수 있다.

## 4. 안 하는 것
- 정책 logical_id · 컨플 page_id 같은 외부 키를 정수로 바꾸기 (원천이 글자)
- 연결 표 전체를 종류별 표로 쪼개기 (컬렉션끼리 연결만 옮기고, 나머지는 고아 검사로 충분)
