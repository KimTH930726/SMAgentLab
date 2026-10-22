# OpsLens 시스템 아키텍처 (v2.160)

## 개요

OpsLens(구 Ops-Navigator, 2026-10-06 이름 변경)는 정책서·매뉴얼·운영 지식·공통코드처럼 흩어진 IT 운영 지식을 한곳에
모아 근거와 함께 답하는 **IT 운영 지식 통합 AI 플랫폼**이다. 틀린 답은 사용자 신고 → 담당자 검토로 바로잡는다.
사용자는 에이전트를 선택해 목적에 맞는 AI를 사용한다: 지식 기반 Q&A(KnowledgeRAG).

> Text-to-SQL 에이전트는 v2.51에서 `dev_0`/`main`에서 분리·제거됐다(현재 과업 범위 아님) — 코드는
> `archive/with-text2sql` 브랜치(2026-09-03 시점 스냅샷)에 형상관리용으로 보존돼 있다.
> MCP 도구 에이전트는 v2.67에서 완전 제거됐다(Text2SQL과 달리 브랜치 보존 없음 — 배경은
> `docs/archive/architecture-history-v2.0-v2.99.md` v2.67 항목).

**주요 이력 요약** (스키마 변경 상세는 `table-definition.md` §20 마이그레이션 이력 참조)
- v2.160 (2026-10-22, dev_0): **조직 컬렉션 — 팀 공통 · 파트(지식 문서·용어만) + 고르는 창 [업무 | 조직] 탭.** 설계 `docs/tech/collection-design.md` §2·§15-13, `org-space-design.md` §3.
  스키마(#83): `ops_namespace.owner_team_id`(팀 공통의 팀) + `kind='org_space'`, 이름 = id 키(`org:team:<id>` · `org:part:<id>`, 팀·파트 이름은 데이터에 안 넣고 화면 이름은 조회 때),
  CHECK `ops_namespace_org_shape`(org: 접두사 ⇔ 조직 · 팀 칸은 조직만 · 상위 없음) + 담당 표 트리거(팀 공통엔 담당 줄 금지, 파트 컬렉션엔 그 파트 한 줄만). 팀·파트마다 하나씩 채움(멱등).
  담당: 파트 컬렉션 = 그 파트(담당 표 한 줄), 팀 공통 = 그 팀의 **모든** 파트(줄 없이 `OWNER_ROWS_SQL`이 계산 — 파트가 팀을 옮기면 저절로 따라감). 권한 SQL 원천은 `core/database.py` 한 곳,
  규칙(`edit_denied_reason`)은 그대로 하나. 생명주기: 팀·파트 만들기 트랜잭션이 조직 컬렉션도 만들고, 지울 때 지식(사용·검토 대기)·용어가 있으면 거부(팀 409·파트 400), 없으면 같이 지움.
  종류 거부(400): 정책 가져오기(미리보기·적용)·정책 수정 제안·VOC 메일함 연결·참조데이터 적재. 업무 컬렉션 관리 API(이름·담당·상위·삭제)·`org:` 이름 만들기도 400, 조직 컬렉션은 상위가 될 수 없음.
  API: `/namespaces/detail`에 `kind`·`display_name`("팀 공통 · ○○팀" / "○○ 파트")·`owner_team_id`·`team_id`·`team`. `GET /namespaces`는 전체 이름(조직 키 포함 — 기억한 값 확인용).
  채팅: 조직 컬렉션을 고르면 지식 검색 = 그 컬렉션 하나(하위 없음) + 정책은 예전 그대로(전체). 업무 컬렉션 + 내 조직 컬렉션 자동 포함은 설정 `chat.org_space_scope`(기본 꺼짐, 정확도 게이트 뒤) — 꺼져 있으면 검색 호출이 예전과 똑같다.
  화면: 고르는 창 [업무 | 조직] 탭 — 조직 = 팀 카드(내 팀 보라 먼저) → 그 팀의 팀 공통 + 파트 카드(내 파트 보라 먼저), "조직 › 팀" 경로. 두 탭·관리 화면이 같은 카드·묶음 함수(`layout/collectionCards.tsx`).
  칩·경로 표시줄·정정 카드·캐시 화면은 화면 이름. 조직 컬렉션을 고르면 정책서·시스템 정보는 "조직 컬렉션엔 정책·시스템 정보가 없어요". 메뉴 컬렉션 = 업무 컬렉션 · 조직 컬렉션(팀 → 파트, 개수, [지식 등록 →]).
  업무 컬렉션 목록·담당 업무 매핑·파트 창은 업무 컬렉션만. 테스트: sqlite 실제 SQL(`test_org_collections.py`), 실서버 `scripts/scenarios/org_collections.py`, E2E `org-collection.spec.ts`.
- v2.159 (2026-10-22, dev_0): **업무 컬렉션 담당 파트 N개.** 설계 `docs/tech/collection-design.md` §15-8(사용자 결정 10/22 — 주 담당 없음).
  규칙: 담당 파트 중 어느 파트 사람이든 쓰기·승인, 담당 없으면 관리자만, 하위는 자기 담당이 없으면 상위 담당 전부를 물려받음(깊이 1단). 정정(쓰기) 범위의 하위 포함은
  "상위 담당 전부가 하위 담당에도 있을 때"(상위에서 쓸 수 있는 사람이 하위도 쓸 수 있을 때만 — 예전 "같은 담당"의 일반화).
  스키마: `ops_namespace_owner`(마이그레이션 #82, 옛 `owner_part_id` 1회 복사) = 담당의 단일 기준. 읽기 전부 이 표로(권한 검사 3곳·질의 기록 일괄 삭제·정정 범위·목록·파트 담당·파트 삭제 확인),
  SQL 조각은 `core/database.py` 한 곳(`effective_owner_ns_sql`·`EFFECTIVE_OWNERS_SQL`). 옛 칸은 승인 뒤 삭제 — 그때까지 쓰기 때 "가장 작은 part_id" **임시 거울**.
  API: `/namespaces/detail`에 `owner_parts`·`effective_owner_parts`([{id, name}], 옛 단일 칸은 호환용으로 유지), `PATCH /namespaces/{name}/settings`에 `owner_part_ids`(통째 교체, 없는 파트 400,
  옛 `owner_part`·`clear_owner`도 받음). `PUT /auth/parts/{id}/domains`: 체크 = 이 파트를 담당에 **더함**(다른 파트 담당 유지), 해제 = 이 파트만 뺌.
  화면: 업무 컬렉션 목록 담당 칸 전부 표시, 수정 창 담당 = 팀별 체크 목록(확인 창에 더함/뺌), 담당 업무 창 "다른 담당: …"·확인 창 "함께 담당", 경로 표시줄 "담당: A · B",
  고르는 창·사이드바 첫 선택 = 내 파트가 담당 중 하나면 '내 담당', 하나라도 우리 팀 파트면 '우리 팀'. 테스트: sqlite 실제 SQL로 N개 담당 규칙·교체·파트별 담당·정정 범위(`TestMultiOwner`).
- v2.158 (2026-10-22, dev_0): **업무구분(지식 category) 끊기 — 코드·화면 전부(C3~C5).** 설계 `docs/tech/category-removal-design.md` §3·§4, 사용자 "업무구분 관련 내용 싹 지워줘".
  읽기: 검색의 DB·공통코드 키워드 전용 분기(점수 CASE·채택·"정확 매칭" 라벨 — 해당 활성 지식 0건), 채팅 업무구분 필터, **질문마다 돌던 업무구분 자동 감지 LLM 호출**,
  VOC 해결책 후보의 DB·공통코드 제외, `/knowledge/keyword-only-categories`. 쓰기: 등록 필수·AI 추천·자동 태깅(tagger 업무구분 태깅만 — 용어 추출은 유지)·analyzer 추천·
  컨플 "상위 페이지 제목 → 업무구분 자동 생성"·일괄 변경·원장 `mixed_category`·재분할 최빈값 계승·정정 승인의 category. 관리: 업무구분 CRUD·추천 API, 업무 컬렉션 화면 업무구분 칸,
  `category_suggest` 시드(기존 행은 DELETE 안 함). 화면: 지식 등록·수정·필터·배지, 통계 공백 창("업무구분은 내용으로 자동 지정"), VOC 등록 필수 칸, 통합 검색 상세.
  **남긴 것**: `rag_knowledge.category` 칸·`rag_knowledge_category` 표·프롬프트 행(1~2주 운영 뒤 항목별 확인으로 C6 삭제), 옛 마이그레이션. 새 지식은 category NULL.
  측정(같은 날 전·후, LLM 없음): 지식 106문항 · 정책 골든셋 441문항 **문항별 완전 동일**, `eval_gate` 통과. 실서버 권한 시나리오(업무구분 API 대신 replace-check로 권한 확인) 30/30·20/20.
- v2.157 (2026-10-22, dev_0): **조직·권한 묶음 — 팀 관리 · 서브탭 · 트리.** 사용자 요청(팀 7개·파트 4~6개로 늘어 "팀 추가가 없다", "사용자·팀파트·담당 업무를 하나로").
  백엔드: 팀 API `GET/POST /api/auth/teams`, `PATCH/DELETE /api/auth/teams/{id}`(파트가 남은 팀 삭제는 409 — FK가 SET NULL이라 그냥 지우면 파트가 조용히 팀 없음이 된다),
  파트 만들기에 `team_id`(없는 팀 400), `PUT /api/auth/parts/{id}/team`(옮기기). 삭제는 팀 행 FOR UPDATE, 파트 만들기·옮기기는 KEY SHARE — 동시에 들어와도 팀 없는 파트 0
  (실서버 시나리오 `scripts/scenarios/team_crud.py` 31/31 — 동시 이름 겹침도 500 없이 409). 화면: 시스템·권한 › **조직·권한** 하나 + 서브탭[사용자 | 팀·파트 | 담당 업무](설정 화면과 같은 모양) —
  팀·파트 = 팀 › 파트 트리([+ 팀], 팀마다 이름·삭제·[+ 파트], 파트 카드에 팀 옮기기), 담당 업무 = 팀 줄 아래 파트. 관리자 메뉴: 지금 화면이 든 묶음도 접힌다(화살표가 안 먹어 고장처럼 보임).
  E2E `org-access.spec`. 옛 메뉴 id(org.users·org.parts·org.domains) → `org` 하나.
- v2.156 (2026-10-22, dev_0): **컬렉션 개편 A — 보는 컬렉션 고르기 · 메뉴 · 문구.** 설계 `docs/tech/collection-design.md` §14~16(10/22 확정).
  사이드바 업무 select → "보는 컬렉션" 칩 + 고르는 창(`DomainPicker`): 타이핑 없이 카드, 묶음 = 내 파트 담당 › 우리 팀 담당(팀은 `/auth/parts`의 team) › 다른 팀·담당 없음,
  묶음 안은 내용 있는 것 먼저·가나다, 빈 컬렉션 흐리게. 테스트 컬렉션(kind=policy_test) 숨김(값으로는 유효 — E2E·직접 주소). **선택 유지**: 예전엔 저장이 없어
  새로고침마다 내 담당으로 덮였다 → 사용자별 `localStorage opslens.namespace:<user>`(실패해도 기본값), 처음 선택 = 기억한 값 → 내 파트 담당 첫 → 첫 컬렉션,
  담당 정보를 못 받으면 첫 컬렉션 + 화면 표시. 평가 스크립트 2곳(track2 TrackA, eval_confluence_structure)이 임시 컬렉션을 일반 업무로 만들던 것 → policy_test
  (10/22 TrackA 최대 오염원의 뿌리). 메뉴: 지식 · 컬렉션(업무 컬렉션) · 품질 · VOC · 시스템·권한(사용자·팀파트·담당 업무를 옛 "조직"에서
  이동 — 컬렉션 › 조직과 이름이 겹침), 주소 id는 그대로. 문구 "업무 영역" → "업무 컬렉션". E2E: `selectDomain`(저장값 + 새로고침) · `collection-picker.spec`(숨김·고르기·새로고침 유지·등록 메뉴).
  안 한 것: 조직 탭([업무|조직], D 때) · 공용 Modal 접근성(dialog 역할·포커스 — 기존 공용 부품, 백로그).
- v2.155 (2026-10-22, dev_0): **VOC 반복 묶기 정확도 — 요약 지침 "대상 기능 + 증상" + 옛 묶음과 경계.** 사용자 검증 요청("21건째가 맞나"): 건수는 정확(묶음 25건 =
  VOC번호 25개, 중복 0)했지만 **같은 주제의 다른 문제가 한 묶음**이었다(프리퀀시 27건 = 선물하기 링크 오류 6 + 적립·증정 문의 21). 원인 = 묶기용 요약이 주제 수준
  ("프리퀀시 문의"), 기준을 0.8로 올려도 안 갈림. v2.154의 0.7 판단은 "다른 주제와 안 섞임"만 봐서 주제 안 섞임을 놓쳤다. 요약 지침을 대상 기능(서비스 이름 말고 기능까지)
  + 증상, 단순 문의는 "<기능> <주제> 문의"로(예시는 쿠폰·주문 등 일반 사례 — 오늘 데이터에 맞춘 규칙 아님). 측정(운영 게이트웨이, 27건): 기준 0.8에서 지금 = 12건 묶음에
  선물하기 4 섞임 / 새 = 선물하기 4건만 묶음 + 다른 묶음 섞임 0. 마이그레이션 #81(옛 줄 그대로일 때만 교체 + 경계 id 기록), 묶기는 경계 이후 묶음·단독끼리만
  (`pattern_detection` — 묶음은 번호, 단독 메일은 벡터를 쓴 시각 updated_at으로 가름: 재처리가 옛 번호 행에 새 벡터를 쓰기 때문. `voc_reprocess.py`가 벡터를 쓸 때 updated_at 갱신·캐시 버전 3. 경계는 `routing_service.get_settings`가 읽기 전용으로 넘김). 기본값 0.8 / 3일(사용자) — 새 설치 시드·코드·화면 기본값만, 기존 환경은 설정 화면.
  안 한 것: 관리자가 화면에서 프롬프트를 고칠 때 경계 갱신(사례 생기면), 카드에 "주제 N건 중 같은 증상 M건"(묶음이 증상 단위가 돼서 불필요), VOC 별도 에이전트 분리(후순위 —
  먼저 같은 프로젝트 안 레이어 경계).
- v2.154 (2026-10-22, dev_0): **VOC 순서 바로잡기 — VOC끼리 반복 묶기가 먼저, 업무 지식은 해결책 찾기에만.** 예전엔 "메일함 업무 지식과 비슷한가"(관련도)를
  입구 필터로 써서 업무 지식에 없는 주제는 반복 묶기까지 못 가고 버려졌다(9/22~10/22 팀즈 실제 발송 0, 10/22 프리퀀시 문의 20여 건도 전부 걸러짐).
  이제 모든 메일을 분석·묶고 반복(같은 유형 3·6·9건째)이면 팀즈. 관련도는 표시만 — 미달이면 LLM에 빈 근거, 카드에 "📭 관련 지식 없음 — 지식 공백 후보",
  무관한 지식 초안·참고 지식 비움. "IT 무관" 판정은 발송을 막지 않음(분류 표시만). 단독 메일은 여전히 안 보냄. 묶기 기준 0.8 → 0.7(설정, 오늘 메일 재처리로
  고름: 프리퀀시 묶음이 다른 주제와 안 섞임). 공용 함수 `pipeline.analyze_message`·`cluster_and_notify`·`notify_cluster`. `scripts/voc_reprocess.py`:
  옛 규칙으로 건너뛴 메일을 새 순서로 다시 처리(미리보기 기본, --send는 다 묶은 뒤 묶음당 최종 건수로 1장 — 밀린 메일 연발 방지), 10/22 프리퀀시 32건 →
  묶음 16·3건 팀즈 2장. 묶음 last_seen_at은 과거로 안 되돌림(GREATEST). 남은 것: 해결책 찾기에 정책서 포함(VOC 후순위 백로그).
- v2.153 (2026-10-22, dev_0): **④ 업무구분 폐지 1~3단계 — 외부서비스·제휴서비스 합치기 + 업무 영역 트리(백엔드).** 설계 `docs/tech/category-removal-design.md`
  (10/22 검수 확정). ① 데이터: `scripts/merge_partner_domains.py`(미리보기/--apply/--rollback) — 백업 표 5개(업무구분·지식별 (id,영역,업무구분)·업무구분 추천
  프롬프트·옮긴 행 id·두 영역 행) 뒤 `restructure_domains.merge`로 외부서비스(지식 104·질의 27·대화 13·적재 10·업무구분 4) → 제휴서비스, 담당 파트 이어받음.
  표마다 행 수 = 두 영역 합 점검, 되돌리기 시험 → 원래 숫자 복구 → 재적용. 측정(같은 날, 로컬 LLM): 정책 검색 375/381 → 375/381, 지식 검색 101/106 → 101/106,
  제휴서비스 정책 답 93.8 → 93.8%, 지식 답 67.9 → 67.9% — 게이트 통과. ② 트리(깊이 1단 `parent_id`): 검색 경로만 자신 + 직계 하위(`namespace_scope_ids`,
  지식 검색 `= ANY`, BM25 캐시 키 = id 튜플, 용어·정책 범위·참고 데이터·VOC 커버리지), 담당 상속(권한 4곳 COALESCE, 목록 API `effective_owner_part`·
  `owner_inherited` — `owner_part`는 직접 지정 그대로, 프론트 권한 판단만 실효 담당), 생성·수정 `parent_id`(깊이 1·자기 자신·하위 있는 영역 거부 400, id 순 잠금),
  하위 있는 영역 삭제 409. 정정 원장: 신고·제안·승인 범위 = 자신 + **담당이 같은** 하위, 새 버전은 원래 행의 영역에. 답변 캐시 = 자신 + 상위 이름.
  하위 0개라 동작 변화 0 — 반영 전후 검색 상세 441·106행 순위까지 동일. 실서버 `scripts/scenarios/domain_tree.py` 20/20. ③ 측정 도구: `eval_answers.py collect --ns`
  (영향권 문항만), `eval_knowledge_answers.py --llm local`(로컬 LLM 전환 함수 공유). 하위 영역 생성·이동 스크립트 `split_partner_subdomains.py`는 화면(C2) 뒤 실행.
  스키마 변경 없음(백업 표만 추가).
- v2.152 (2026-10-21, dev_0): **목록형 답 개선 — 목록 질문 알아보기 + 목록 줄 = 정책명 — 요약.** 골든셋 v0 진단(목록형 18문항×2): 실패 25회 중
  10회는 한 정책의 세부 조건을 줄줄이 펼쳐 "목록 밖 항목"이 됐고, 18문항 중 9문항은 "정리해줘·기준엔 뭐가 있어"처럼 "전체·모든" 없이 물어 목록 질문으로
  안 잡혔다. ① trustkit `query/intent.py` 목록 질문 말투 추가(기준·정책·규정·항목이 붙을 때만 — 골든 목록형 42/42, 새 오탐 0) ② trustkit
  `answer/shaping.list_line_summary()` — 목록 줄 = "경로 > 정책명 — 요약"(본문 앞줄을 글머리만 떼고 80자, LLM 없음, 정책명과 같은 제목 줄은 건너뜀;
  981개 전부 빈 요약 0·본문 밖 글자 0) ③ `category_list.py` 목록 블록 지시: 정책 개수 명시, 목록 밖 정책 금지, 세부 조건 펼치지 말기, 잘렸으면 남은 개수;
  분류 이름 비교에서 띄어 쓴 "및" 무시, 질문 속 더 긴 분류 이름 우선. 측정(로컬 Qwen, 같은 날): 목록형 11/36 → **19/36**(좋아짐 5·나빠짐 0),
  전체 89.6 → 89.9%(오차 안), 검색 문항별 동일 — 게이트 통과.
- v2.151 (2026-10-21, dev_0): **정책 v0 재구축 3단계 — 원문 확인 + JSON 내려받기·가져오기.** 고치는 곳은 여전히 하나(품질 › 정정 요청).
  ① 정책서 › [원문 확인](`SourceCheckTab`, `service/policy/source_check.py`): 원문 자체에 대한 질문·문제(내용 수정이 아님) — 적재 점검의 담당자 질문 4,
  골든셋 작성 중 찾은 정책끼리 어긋남 3쌍·참고표 오류 4. 저장은 새 테이블 없이 정정 원장 `kind='source_check'`(대상 = 정책/참고표 logical_id,
  original {type, policies, ref_tables, hash}, 설명은 원문 인용 없이). [해결](메모)/[닫기](사유)만, 담당 파트·관리자. 정정 요청 목록·대기 배지·내 신고·
  업무 영역 통계의 정정 건수·정정 처리 API(승인·반려·다시 쓰기 등은 404)에서 이 kind를 뺀다. 같은 화면에 제안 연결(ops_link `suggested`, 6건) [확정]/[거절].
  씨앗: `scripts/load_policy_v0.py --add-source-checks <jsonl> [--dry-run]`(원천 키 → 현행 logical_id, (출처, 해시) 멱등 — 씨앗 파일은 git 제외 eval_out).
  API: `GET /api/policy/source-checks`, `POST …/source-checks/{id}/resolve|dismiss`, `POST /api/policy/links/{id}/confirm|reject`.
  ② 정책서 › [JSON](관리자, `PolicyJsonTab`, `service/policy/json_io.py`): 내려받기 = 얇은 형식 `opslens/policy-v0/1`(현행 버전). 가져오기 = 그 형식 또는 팀원
  v0 policies.jsonl(적재기와 같은 `v0_convert.build_policies` 정리) → 미리보기(원천 키로 원문·비고 비교: 같음·바뀜·파일에만·DB에만·검토 중이라 제외, 이름·경로
  변경은 보고만) → 반영 = 바뀐 정책마다 정정 제안 1건(source `json_import`, AI 없음, 정책 테이블엔 안 씀) → 정정 요청(출처 "JSON 가져오기")에서 승인.
  미리보기 뒤 파일(doc_hash)·정책/정정 상태(plan_hash)가 바뀌면 409. 새 정책 추가·삭제는 안 함(보고만). API: `GET /api/policy/json/export`,
  `POST /api/policy/json/preview|apply`. 스키마 변경 없음.
- v2.150 (2026-10-21, dev_0): **정책 v0 재구축 2단계 — 정책서 화면 새로 + 모든 정책 변경은 정정 요청에서 승인.** ① 지식 › 정책서 =
  [정책 | 참고표 | 문서 개요](`components/admin/policy/`). 정책: 문서 순서 목록(첫 절로 접기, 정책명·원문 검색, "정정 #N 검토 중" 배지) → 상세 모달
  (경로·이름·버전, 원문, 비고, 용어, 값은 있을 때만 — 원문에 칠하고 원문에 없는 값은 빨강, 버전 이력 "적재/정정 #N", [이전][다음]).
  담당 파트·관리자는 [AI 수정 제안](한 줄)·[직접 수정](원문·비고 모달)을 같은 모양으로 — 둘 다 정정 원장에 제안 1건(source `policy_ai`/`policy_direct`)만
  만들고 정책 테이블엔 아무것도 안 쓴다. 같은 정책에 대기 중 정정이 있으면(출처 무관) 409 + 그 번호(`detail.existing_id`), 화면은 "정정 요청에서 보기"로 그 카드에.
  참고표: 표 고르기 + 원표(머리 고정·가로 스크롤, 결정 열 표시). 새 API(읽기, 누구나): `GET /api/policy/policies`·`/policies/{logical_id}`·`/ref-tables`·
  `/ref-tables/{id}`·`/overviews`(`service/policy/catalog.py`). 정책→용어 연결은 `ops_link(from_type='policy', relation='uses')`, from_key = 정책 logical_id.
  ② 정정 요청: [직접 수정] 되살림(`PATCH /api/corrections/{id}/proposed` — 승인과 같은 검사, 대기 중만, `ai_verdict.edits`에 누가·언제) + [AI 다시 쓰기] +
  [반려] + [승인]. 기존 vs 수정안 줄 비교(LCS, `utils/lineDiff.ts`), 출처 거르기(채팅·정책 직접 수정·정책 AI 제안·평가 게이트), 골라서 일괄 승인
  (화면이 건마다 승인 요청을 차례로 — 그 사이 바뀐 건은 409로 빠지고 이유 표시, 【확인 필요】는 고를 수 없음). 비고 정정: 수정안에 비고 칸이 있으면
  새 버전 비고가 그 값(빈 값 = 지움), 없으면 그대로 — 비고만 바꾼 안도 "바뀐 안". 원문 통째(v0) 정책의 수정안은 서술 조각 = 원문으로 맞춰 저장.
  ③ 삭제(사용자 승인): 옛 엑셀 경로 화면(항목 브라우저·미분류 집계·정책서 올리기/붙여넣기·위험도 자동 통과 큐·줄 안 파라미터/서술 편집)과 API
  (`/policy/sources`·`import`·`import-paste`·`unresolved-summary`·`unresolved/*`·`params/*`·`narratives/*`·`review-summary`·`auto-review/*`·
  `GET items`·`items/*/approve|reject|revert-auto`), 그것만 쓰던 모듈(`browse`·`unresolved_report`·`review`·`edit`·`auto_review`·`risk`·`decompose`·
  `excel_parser`·`json_format`, `policy/service.py`의 엑셀 가져오기 파이프라인 — 임베딩 규칙만 남김, `scripts/excel_to_policy_json.py`)와 그 테스트·E2E.
  스키마 변경 없음(`policy_review_log`·`policy_item.review_*` 칸은 남음 — 이력 보존).
- v2.149 (2026-10-21, dev_0): **정책 v0 재구축 1단계 — 승인된 버전만 검색, 승인 = 옛 버전 은퇴.** ① 검색 대상 조건을 한 곳으로
  (`service/policy/visibility.searchable_sql`): v0(원문 통째)는 active만, 옛 엑셀 경로는 예전처럼 검토 대기도 — 정책 검색·키워드 색인·분류 목록·
  채팅 정책 범위·업무 영역 정책 개수·정정 대상 찾기가 모두 같은 조건(사람이 고친 새 버전이 승인 전에 답변에 섞이던 것). ② v0 새 버전 승인은
  한 트랜잭션에서 옛 버전(supersedes_id)을 잠그고 아직 현행인지 본 뒤 deprecated → 새 버전 active(그 사이 바뀌었으면 "반려 후 다시 고쳐 주세요"),
  반려는 새 버전만. ③ 정정 승인의 새 버전 번호 = 같은 정책의 가장 큰 번호 + 1(반려된 v2와 겹치던 것). ④ 정책이 바뀌면(가져오기·승인·정정 승인)
  정책 색인 + 모든 업무 영역 답변 캐시를 비움(`caches.invalidate_policy_caches` — 채팅은 정책을 여러 영역에 걸쳐 찾는데 고친 영역만 비웠다).
  ⑤ 자동 통과는 v0 항목을 건너뜀(사람 큐로 셈). ⑥ #80: 리뷰 이력 action에 submitted, 같은 정책에 v0 대기 하나·active 하나 고유 인덱스.
- v2.148a (2026-10-21, dev_0): **정책서 v0 골든셋 평가 + 업무 영역 수정 모달 + 등록 점수 요청 제거 + 프롬프트 정리 + v0 용어 동의어.**
  ① 골든셋 v0 485문항(옛 정책 평가 자산 폐기 후 재출발, 파일은 git 제외 `eval_out/golden_v0/`) — 검색 측정 `scripts/eval_golden_v0.py`(정답 = 원천 키 →
  현행 정책, 못 찾으면 실패로 셈), 답 측정 `eval_answers.py --golden v0 --llm local --concurrency N`(이 측정에서만 로컬 LLM, 답 없는 질문 채점 규칙).
  기준선(로컬 Qwen): 검색 Recall@10 98.4%·Top-1 81.9%, 답 90%(목록형 6/18이 가장 약함, 답 없음 거절 53/60). ② 업무 영역 수정 모달에서 이름 + 담당 파트(관리자,
  확인 창). ③ 지식 등록의 문서 분석·청크 태깅이 안 쓰는 중요도 점수(priority_score)를 더는 LLM에 요청하지 않음(토큰 절약). ④ 채팅 프롬프트의
  "컨테이너명·테이블명" 지시(v2.82에 칸은 이미 뺌) → "SQL·코드는 코드 블록으로" — 답 게이트 통과(-1.0%p, 오차 안). ⑤ v0 용어 712개에 동의어 811건
  (Claude 생성 → 임베딩 유사도 게이트) — 검색 나빠짐 0, 답 +0.9%p(오차 안).
- v2.148 (2026-10-21, dev_0): **VOC 프롬프트 분류 정리.** 프롬프트 관리에서 VOC 이메일 분석 프롬프트 2개가 "RAG"로 표시되던 것을
  "VOC"로(#79 `ops_prompt.agent_type` = voc). 공통·VOC는 어떤 에이전트를 골라도 보인다. 점검 결과 안 쓰는 프롬프트는 0개 — 업무구분 추천은 ④ 업무구분 폐지 때 제거.
- v2.147 (2026-10-21, dev_0): **수정은 모달 + 정정은 승인/다시 쓰기/반려 + 경로 표시줄 + 조직 트리.** ① 수정 기능을 공통 `ui/FormModal`로(업무 영역
  이름·용어·파트 이름·사용자 수정·VOC 메일함 담당) — 그 자리에서 고치던 칸은 포커스가 빠지거나 취소를 눌러도 저장됐고, 표의 선택칸은 바꾸는 순간
  저장됐다. ② 정정 요청에서 사람의 직접 편집 제거(사용자 결정 — 사람은 최종 승인만): [승인] / [다시 쓰기](요청 한 줄 → AI가 수정안을 다시 씀,
  `POST /api/corrections/{id}/redraft`, 이력은 ai_verdict.redrafts) / [반려]. 【확인 필요】 표시가 남은 검토 초안도 다시 쓰기로 승인 가능해짐.
  ③ 관리 본문 맨 위 경로 표시줄(업무 영역 › 묶음 › 화면 + 담당 파트) — 화면마다 붙이던 업무 영역 배지 제거. ④ 조직: 팀·파트가 맨 위, 파트를 누르면
  현황 트리(팀 › 파트 › 사람 · 담당 업무 영역). ⑤ 서버에 닿지 못하면(연결 실패·재시작 중 nginx 502/504) "Failed to fetch"·"HTTP 502" 대신 한국어
  안내, 토큰 갱신은 401/403일 때만 로그아웃. 리뷰 반영: 다시 쓰기는 앞선 요청을 모두 쌓아 넘기고(대상을 바꾸면 비움 — 지난 요청은
  redrafts_before_retarget), 승인은 화면에 보인 안과 잠근 뒤 다시 읽은 안·대상이 같을 때만(아니면 409), 늦게 끝난 AI 원인 추정은 판정이 있는 신고를 덮어쓰지 않음.
- v2.146 (2026-10-21, dev_0): **업무 영역 화면(설계안 ③).** 카드 목록 → 표: 업무 영역 · 담당 파트 · 정책 · 지식 문서 · 용어 · 업무구분(곧 없어짐).
  줄 = 그 영역 고르기(사이드바와 같은 값), 숫자 = 그 영역을 고른 채 해당 화면으로. 삭제 확인에 정책·지식·용어 개수. 목록 API에 policy_count·
  category_count. 화면마다 있던 업무 영역 선택기를 없애고 "업무 영역: ○○" 표시만(고르는 곳은 사이드바 하나) — 영역이 바뀌면 그 화면 필터·선택을
  비우는 정리는 값이 바뀔 때마다로 옮김(예전엔 화면 안 선택기에서 바꿀 때만이라 사이드바로 바꾸면 남았다).
- v2.145 (2026-10-21, dev_0): **관리자 메뉴 재배치 — 왼쪽 아코디언(설계안 ②, 사용자 결정).** 위쪽 탭 줄을 없애고 사이드바에 6묶음
  (업무 영역 / 지식: 정책서·지식 문서·용어집·시스템 정보(준비 중) / 조직: 사용자·팀·파트·담당 업무 영역 / 품질: 통계·정정 요청·평가 게이트 / VOC 이메일 /
  시스템: 설정·캐시). 메뉴 정의 한 곳 `pages/adminMenu.ts`, 주소 `/admin/<메뉴>`. 업무 영역 선택은 사이드바 선택기 하나(앱 공용 상태 —
  화면마다 따로 들던 값을 없앰). 정정 요청은 지식 탭에서 품질로(대기 건수 배지도 메뉴로). 데이터 화면 문구 "파트" → "업무 영역".
  관리 화면에서 업무 영역을 바꾸면 채팅의 열린 대화도 비운다(대화는 한 업무 영역에 속함 — 다른 영역 값으로 이어 쓰면 엉뚱한 영역에 질문이
  들어감; 대화는 서버에 남아 원래 영역 목록에서 다시 열 수 있음). 캐시·VOC 화면도 같은 값. 없는·권한 없는 메뉴 주소는 보여 주는 메뉴 주소로 바로잡음.
- v2.144 (2026-10-21, dev_0): **담당 업무 영역 화면 + 지식 조회 열기.** 관리자 화면 설계안(docs/tech/admin-ia-design.md) 검수 뒤 ①단계:
  사용자 관리 > 담당 업무 영역 — 파트별 담당 영역(1:N) 체크 + 저장 전 바뀌는 내용 확인, `PUT /api/auth/parts/{id}/domains`(한 트랜잭션, 없는 영역이
  섞이면 아무것도 안 바뀜, 다른 파트 것은 옮겨 옴). 지식 조회(정책·참고 데이터)는 다른 파트 담당 영역도 누구나(`check_knowledge_read`, 사용자 결정) —
  통계·VOC 같은 사람 데이터는 그대로 담당 파트·관리자. 실서버 점검표 31항목.
- v2.143 (2026-10-20, dev_0): **LLM 설정 화면 — Ollama 자리를 로컬 LLM으로.** 선택지 = 사내 LLM / 로컬 LLM(OpenAI 호환 vLLM, 주소·모델은
  .env `OPENAI_COMPAT_*`, 화면은 보기만 — 관리자가 임의 주소로 요청을 보내지 않게). 모델은 비우면 연결 확인 때 서버의 첫 모델을 알아 두고 화면·사이드바에
  표시. 로컬 사용 중에도 화면이 "사내"로 보이던 것 수정. Ollama는 .env·API로만(비상용, 사용자 결정 — 기본 LLM은 사내 유지).
- v2.142 (2026-10-20, dev_0): **조직(사람)과 업무 영역(지식) 분리.** 파트 이름은 바뀔 수 있어 지식은 업무 중심으로 관리한다(사용자 결정).
  ① 사람 축: 팀 > 파트 > 사용자(`ops_team`, `ops_part.team_id`) — 파트는 소속·권한만, 데이터에 파트 이름을 남기지 않음(등록자는 사람으로 표시).
  슈퍼어드민은 역할(role=admin)이고 파트에 속하지 않음('슈퍼어드민' 파트 폐지). ② 지식 축: 업무 영역(`ops_namespace`) — 정책/업무 영역
  구분 폐지(kind=domain), 트리 자리 `parent_id`(지금 비움 — 하위 업무를 트리 단계로 두는 P2에서). ③ 담당: 영역 ↔ 파트(`owner_part_id`) —
  담당 없는 영역은 관리자만 수정(예전엔 누구나), 영역 만들기·이름·삭제는 관리자만. 백엔드·화면이 같은 규칙(`utils/domainAccess.ts`).
  ④ 데이터 정리(`scripts/restructure_domains.py`, 한 트랜잭션 + 행 수 점검): 같은 업무의 지식 공간과 정책 영역 합침(온라인스토어 DB→온라인스토어,
  딜리버스 DB→딜리버스 — 17개 표·연결 키·용어 수집 상태), 이름 정리(팀 공통·외부서비스), 스타벅스CSP팀 > 온라인서비스·온라인스토어
  파트. ⑤ VOC 담당은 파트 연결(`part_id`, 필수 — 쓰이는 파트는 삭제 불가), LLM 문맥 표시 [파트:] → [업무 영역:], 정책서 엑셀 차단은 영역 종류 대신 내용(통째 정책 유무)으로.
  ⑥ 컬럼 정리: 지식·용어 `created_by_part`·VOC `part` 글자 삭제(사용자 확인), 용어 출처는 범용 `source` 칸(정책서 712건 = policy_doc, 화면 "정책서"),
  연결 표 종류 'part'→'domain'. 조회와 수정 권한 분리(조회는 예전 그대로 — 담당 없는 영역도 열람).
  ⑦ 가입은 조회 전용으로 시작 → 관리자 승인, 화면이 역할·소속을 서버에서 다시 읽음. 담당 지정은 API(`PATCH /api/namespaces/{name}/settings`)·
  스크립트로 — 화면은 조직 관리 메뉴 설계 검수 뒤(사용자 10/20: 매핑은 기준정보관리가 아니라 조직 쪽 메뉴).
  ⑧ 막 쓰기 테스트(API ~250요청 + UI)·독립 리뷰 4회 반영: 없는 업무구분 변경이 지식을 먼저 바꾸던 것, 파트 삭제·사용자 이동 경쟁, 아무 역할 글자
  저장, 조회 검사 빠진 통계, 이름 검증(공통 `service/validation.py`), DB 오류 500 → 공통 4xx, 마이그레이션 재기동 실패(점검 `scripts/check_migrations_twice.py`).
  재발 점검표 `scripts/scenarios/domain_permissions.py`(실서버 23항목).
  ⑨ 처음 만든 상위 묶음 8개(혜택·주문 등, 데이터 없음)와 기준정보관리의 상위 영역·담당 파트 선택 칸은 뺐다 — 실사용·관리 주체 없는 과설계,
  검수 없이 넣은 화면(사용자 10/20). 관리자 화면은 도메인 경계(DDD)대로 다시 설계해 검수받는다(`docs/tech/admin-ia-design.md`).
- v2.141 (2026-10-20, dev_0): **장애 대응 — 근거만 보여 주기 + 대체 처리 전수 점검.** 로컬 LLM이 실제로 죽은 일을 계기로 실패 경로를
  전수 조사(백엔드·화면). ① AI가 답을 못 내면(연결 실패·빈 답·게이트웨이 거부) 일반 답 모양의 실패 문구 대신 경고 + 찾은 근거(정책 상위
  3·지식) — 예전엔 정책 근거를 실패 문구와 겹치는 것으로 골라 늘 사라졌다. 메시지 metadata `degraded`, 상태 failed, 피드백 숨김
  ② LLM 밖 오류(검색·DB)를 "LLM 연결 불가"·completed로 저장하던 것 → `SYSTEM_ERROR_MSG`·failed·장애 기록·끝 신호, 이미 저장된 답은 덮지
  않음 ③ 로그인 실패 제한: Redis 장애 시 메모리로 계속(예전엔 꺼짐), 사용자 IP를 nginx `X-Real-IP`로(예전엔 전원이 nginx 주소라 아무나
  5번 틀리면 전원 잠김) ④ 로그 없던 대체 처리 12곳 로그·AI 용어 추출 실패를 "완료"로 표시하던 것 ⑤ 대화 요약 템플릿 자리 표시 판정 버그.
  E2E는 파일 간에도 한 번에 하나(LLM 전환이 백엔드 전체에 걸림). 독립 리뷰 반영.
- v2.140 (2026-10-20, dev_0): **정책서 v0 — 최종본(DOCX 14개, 팀원 구조화 데이터)으로 새출발 + 채팅 정책서 범위.** 옛 엑셀 정책 보관(10/19) 뒤
  v0 적재: 결정적 변환기(`service/policy/v0_convert.py`, LLM 없음) + 적재기(`scripts/load_policy_v0.py` — 미리보기·불변식·트랜잭션 안 재계산).
  정책 981(정책 1건 = 조각 1개, 맥락 머리, 비고는 검색 제외·답변 문맥 포함) · 정책 파트 14(`ops_namespace.kind`) · 용어 712(정책 파트) ·
  참고표 7(원천 표 11, 조건표+결과표 No. 잇기, 결정 열 압축 무손실 · 채팅 연결은 골든셋 뒤) · 문서 개요 14(서문) · 범용 연결 `ops_link` 2,099
  (+ 원문 번호 오류 추정 연결 6건 suggested). 근거 실험 7회(맥락 머리 1위 69→85%, 통째 ≈ 규칙 구간·팀원 청크, 비고를 검색에 넣으면 손해,
  참고표는 검색만으론 상한 → 되묻기). 채팅: 지식은 고른 파트, 정책서(정책·분류 목록·용어)는 정책 파트까지(`service/policy/scope.py`,
  "같은 업무" 연결 없으면 전부) — 여러 파트를 쿼리 한 번, 근거에 파트 이름. 옛 구조 가정 갱신: 위험도(통째 항목엔 분해 사유 없음)·수정/정정
  (조각 = 원문 유지, 원천 키 유지)·정책서 파트 엑셀 업로드 차단·답 거절 기준 끔(옛 조각 점수 — v0 골든셋으로 재보정). 독립 리뷰 2회 반영.
  설계·근거: `docs/tech/policy-v0-design.md`.
  dev 전용 로컬 LLM(OpenAI 호환 `service/llm/openai_compat.py`, `OPENAI_COMPAT_BASE_URL` — 운영 .env엔 없음, 비면 선택 불가) + E2E가 실행
  동안만 전환·`DELETE /api/llm/config`로 .env 복귀(`e2e/llm-local.setup.ts`) — E2E가 게이트웨이를 쓰던 것(1회 42호출) 차단. E2E는 v0 기준
  (임시 테스트 파트 kind=`policy_test` — 다른 파트 채팅 범위에 안 섞임) 14/14.
- v2.139 (2026-10-19, dev_0): **신뢰도 레이어 `trustkit` 분리 — 이식 가능한 팀 자산.** OpsLens의 본질은 답을 믿게 만드는 레이어(적재 품질·
  검색 기법·문맥/답 다듬기·평가)라 그것을 `backend/libs/trustkit/`(표준 라이브러리만, 앱 코드 import 금지를 테스트로 강제)으로 뽑음 —
  조사·금액 정규화, BM25, 묶기·구제·RRF, 맥락 머리, 질문 유형, 목록 범위·조건형 지시문, 적재 품질·청크 불변식, 평가 통계, 게이트(신규).
  기존 모듈은 재노출·DB 어댑터로(경로 그대로), Dockerfile `PYTHONPATH=/app/libs/trustkit`. 동작 변화 0 확인: 정책 검색 148줄 동일·답 문맥
  176/176 동일. `scripts/eval_gate.py`(전/후 판정, 종료 코드), 검색 상세에 qid. 두 번째 사례 = 컨플루언스 지식: 로컬 LLM 합성 문항 106개
  (`scripts/confluence_synth_questions.py`·`eval_knowledge_retrieval.py`) — 맥락 머리는 손해(95.3→94.3%, 중복 판정 쌍 13→30)라 안 씀, BM25 구제
  1자리는 게이트 실패(좋아짐 3·나빠짐 2)라 기본 꺼짐(`KNOWLEDGE_KEYWORD_RESCUE`), 지식 검색 동점 순서 고정만 적용. 구제 자리 = 문맥의 30%
  (`default_rescue_slots`). 컨플루언스 답 정확도 92.0%(90–94%, `scripts/eval_knowledge_answers.py` — 실패 대부분이 "근거는 있는데 답 부족").
  평가 스크립트가 게이트웨이 거부·빈 응답을 운영과 같은 장애 판정(`is_llm_failure`)으로 "오류" 처리(정책 `eval_answers`도). 상세: `docs/tech/trustkit-design.md`.
- v2.138 (2026-10-13, dev_0): **정책 검색 구조 개선(머리 붙인 임베딩·항목 묶기·BM25 구제) + 답 생성(탐색형 목록 범위·조건형 답 구조).**
  전수 점검(적재·청크·검색·평가) 결과 정책 서술 검색이 벡터만이고 조각에 무엇에 대한 값인지가 없었다. ① 조각 임베딩 입력 = "정책명 (분류 > …): 조각"
  (`chunk_embed_text` — 가져오기·편집·미분류 편입·정정 승인 공통, 저장·문맥은 조각 그대로), `CHUNK_EMBED_VERSION` + `ops_data_marker`(#76)로
  기동 때 기존 조각 1회 재임베딩(비교 후 갱신 — 그사이 편집된 조각은 안 덮음). ② 조각 후보 3배 → 정책 항목당 최고 조각 1개. ③ 메모리 BM25 키워드
  채널(`service/policy/keyword_index.py` — 조사 2회 제거·금액 정규화) + "구제" 융합(벡터 순서 유지, 벡터 상위 top_k에 없는 키워드 상위 항목을
  아래 3자리에 — 같은 무게 RRF는 서술 Top-1 91→65%라 기각). 골든 88 검색: Recall@10 88.6→95.5%(좋아짐 6·나빠짐 0, McNemar p=0.031), Top-1
  60.2→62.5%. 근거 없음 판정 재측정 → `POLICY_ABSTAIN_MIN_PARAM_RANK=0.0`(거짓 거절 0/88, 차단 36/60). ④ 목록 답(전부·목록 또는 재질문 범위
  선택)의 문맥에서 목록 밖 정책 검색 결과 중 첫 목록 안 정책보다 아래 것은 뺌(`list_scoped` — "전체 취소"처럼 단어만 든 구체 질문의 1위
  정답은 남김) + 목록 번호. ⑤ 조건 한정 질문("X에만/때만/전용", `looks_like_condition_query`)에 답 구조 지시(해당 내용 먼저·공통 적용 구분·
  "없다" 단정 금지 — 첫 판의 "없으면 없다고"는 오답을 늘려 바꿈). 답(같은 날 채점, 2회 반복, 짝 비교): 조건형 58→78%(거절 8→3, 오답 3→0),
  탐색형 62→75%(+12.5%p, 구간 +2.5~+22.5 — 의미 있음), 전체 77→84%(+6.8%p, 오차 안). 측정 도구: `--reuse`가 1회차를 매번 새로 생성하던 결함
  수정. 독립 리뷰 3회 반영(재임베딩 경합, top_k 고정, 색인 캐시, "전체" 단어 오탐, 재질문 범위, 동점 순서 등). 상세·출처:
  `docs/tech/accuracy-structure-2026-10.md`. 운영 반입: `.env` 변경·첫 기동 재임베딩·캐시 비우기(`deployment-closed-network.md` §3-6).
- v2.137 (2026-10-13, dev_0): **평가 체계를 업계 기준으로 — 오차 범위·채점기 보정·반복 측정·시나리오 저장소화.** 자평(업계 자료 대비):
  개발 과정 검증은 상, 품질 측정은 하~중하(점수에 오차 없음·채점기 미검증·한 번만 측정·정책서만·시나리오 재현 불가). 사용자 방향: 사람 검수가
  필요한 것(채점기 보정 판정·실제 질문 기반 문항·holdout)은 운영하면서 점진적으로, 구조는 지금 업계 기준으로, 목표는 실제 정확도 향상. 한 것:
  ① 리포트에 정답률 95% 구간(같은 원문 시트끼리 묶은 군집 표준오차 — Anthropic/Miller "Adding Error Bars to Evals")과 같은 문항끼리의 차이
  판정("의미 있는 차이/오차 안") — `trustkit.eval.stats(libs/trustkit)`(표준 라이브러리만). 실측: 88문항 1회 측정은 82%(75–89%), 프롬프트 C 대 옛 판
  +2.3%p(−8.9~+13.4) → 오차 안. 즉 지금 체계로는 ±10%p보다 작은 차이를 개선이라 못 한다. ② 채점기 보정: `review-html --blind`(채점기 라벨을
  숨기고 사람이 직접 라벨 — 보고 따라가지 않게), `review-apply`가 원 일치율·카파(정답 여부·4라벨)·혼동행렬·유형별(카파 ≥0.6 운영 가능, ≥0.8 우수 —
  실무 기준). ③ `collect --repeat N`: 문항마다 N번 답, 문항별 평균으로 구간(생성 흔들림을 줄임 — 비용 N배). ④ 실서버 시나리오 6개를
  `scripts/scenarios/`로(원장 재등록·동시 확정·컨플루언스 실경로·재질문·후속 질문 캐시·멀티턴) + 공통 실행 잠금(겹치면 캐시 비우기가 서로를 망친
  10/13 사고). 안 한 것(다시 볼 조건): 근거 충실도 지표·운영 답변 표본 채점(5~10%)·CI 자동 실행 — 위 기반이 쓰이기 시작한 뒤.
- v2.136 (2026-10-13, dev_0): **시스템 프롬프트 점검.** 실행 중 쓰이는 프롬프트 23개 전수 점검(위치·선택 방식·길이·붙는 문맥). 고친 것:
  ① 대화 요약·업무구분 추천이 시스템 프롬프트 없이 불려 채팅 답변 규칙("참고 문서만", "없으면 찾지 못했습니다", "📎 근거 표시")이
  붙었다 → 각자 전용 시스템 프롬프트. ② 채팅 프롬프트에서 "📎 문서 N, 문서 M 참고"(화면·코드 어디도 안 읽고 근거 카드는 답 뒤에 따로
  고른다 — 실제 답의 표기가 제각각, 문맥에 없는 번호까지) · "[과거 유사 사례]"(그런 블록을 붙이는 코드 없음) 지시 제거. ③ 코드 기본값과
  DB 시드를 한 문구로(`service/llm/base.CHAT_SYSTEM_PROMPT` — 예전엔 기본값이 옛 규칙). ④ 마이그레이션 #75: 기존 DB의 chat_system에서 그 두
  줄만 정확히 그 문구일 때 지움(관리자가 고친 다른 부분은 그대로 — 시드는 DO NOTHING이라 운영 DB는 이걸로 맞춤), 안 쓰는 프롬프트 삭제
  (제거된 Text-to-SQL의 sql2_* 8개·MCP 도구의 tool_answer·tool_select·autocomplete — 코드 참조 0건, 사용자 승인, 백업 남김). 손대지 않은 것: 정정 초안·정책 분해·VOC·문서 분석·용어집 프롬프트
  (용도별 정의 — 문제 없음). 비용 대부분은 프롬프트가 아니라 검색 문맥(질문당 ~1.2만 토큰). 측정(88문항, 같은 날 채점): 근거 표시 줄만 빼면 거절 7→11·정답 80%→74%(같은 문항 5회 반복 A/B로 프롬프트 탓 확인 — 근거 표시 지시가 "답한다"는 쪽으로 기울이고 있었다) → 과잉 거절 방지 한 줄("관련 내용이 일부라도 있으면 있는 만큼 답하고 없는 부분은 없다고")을 넣은 판으로 확정: 정답 82%·오답 4→2·거절 7. 마이그레이션은 옛 문구를 정확히 이 판으로 바꾸고 다시 돌려도 같다(검증).
- v2.125 ~ v2.135: `docs/archive/architecture-history-v2.125-v2.135.md`로 옮김(2026-10-19 경량화 — 판단 근거 기록은 그대로 보존).
- v2.100 ~ v2.124: `docs/archive/architecture-history-v2.100-v2.124.md`로 옮김(2026-10-07 경량화 — 판단 근거 기록은 그대로 보존).
- v2.0 ~ v2.99: `docs/archive/architecture-history-v2.0-v2.99.md`로 옮김(2026-10-06 경량화 — 판단 근거 기록은 그대로 보존).

---

## 전체 구성도

```
┌─────────────────────────────────────────────────────────────┐
│                        Host Machine                         │
│                                                             │
│   ┌──────────────────────────────────────────────────────┐  │
│   │              Docker Compose Network                  │  │
│   │                                                      │  │
│   │  ┌─────────────┐    ┌─────────────┐                 │  │
│   │  │  Frontend   │───▶│   Backend   │                 │  │
│   │  │  React+nginx│    │   FastAPI   │                 │  │
│   │  │  :8501      │    │   :8000     │                 │  │
│   │  └─────────────┘    └──────┬──────┘                 │  │
│   │                            │                         │  │
│   │                    ┌───────┴────────┐                │  │
│   │                    │   PostgreSQL   │                │  │
│   │                    │  + pgvector    │                │  │
│   │                    │   :5432        │                │  │
│   │                    └───────────────┘                 │  │
│   └──────────────────────────────────────────────────────┘  │
│                                                             │
│   ┌──────────────────────┐                                  │
│   │  Ollama (호스트 직접)  │  ◀── Backend이 host.docker.      │
│   │  exaone3.5:2.4b      │       internal:11434 으로 호출   │
│   │  :11434              │                                  │
│   └──────────────────────┘                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 컴포넌트별 역할

### 1. Frontend — React + Nginx (`:8501`)

| 페이지 | 역할 |
|--------|------|
| **Login** (`/login`) | JWT 로그인 — Access Token + Refresh Token 발급 |
| **Register** (`/register`) | 회원가입 — 부서 선택 + 선택적 LLM API Key 등록 |
| **AgentSelect** (로그인 직후) | 에이전트 선택 화면 — 지식베이스 AI 카드 선택. `selectedAgent=null`이면 이 화면 표시 (사이드바 없음) |
| **Chat** (`/`) | 에이전트별 채팅 — SSE 스트리밍, 근거 카드, "답변 틀림" 신고(정정 검토로), 대화 메모리(요약+리콜), Markdown 답변 |
| **Admin** (`/admin`) | 에이전트별 관리 화면 — `agentScope` 필드로 탭 필터링. knowledge_rag: 기준정보·지식 베이스(정정 검토 포함)·VOC·용어집·평가 게이트·정책·캐시현황·통계. 공통: 시스템설정·사용자관리. (에이전트현황 탭 제거 — AgentSelect 화면에 헬스배지로 대체) |

- **Agent-centric 라우팅**: `useAppStore.selectedAgent: 'knowledge_rag' | null`. null이면 AgentSelect 표시, 설정 시 에이전트별 UI로 전환. 로그아웃 시 null로 리셋
- **ProtectedRoute**: 로그인되지 않은 사용자는 `/login`으로 리다이렉트
- **useAuthStore** (Zustand): localStorage에 토큰 저장, 자동 Bearer 토큰 주입
- **401 Auto-refresh**: Access Token 만료 시 Refresh Token으로 자동 갱신, 실패 시 로그아웃
- **부서 기반 UI**: 지식/용어집/Q&A 테이블에 부서 배지 표시, 같은 부서만 수정/삭제 버튼 노출
- **어드민 테이블 검색**: 지식베이스·용어집·Q&A 세 테이블 모두 텍스트 검색(즉시 필터) + 벡터 유사도 검색([문자열|벡터] 토글) 지원. 벡터 검색은 Enter 또는 검색 버튼으로 실행하고 유사도 % 배지 표시
- **어드민 테이블 다건 삭제**: 전체 선택 체크박스 + 행별 체크박스, N개 선택 시 액션 바 표시 → 일괄 삭제
- Sidebar: 에이전트 배지 + 에이전트 변경 버튼, 사용자 정보 + 로그아웃, 네임스페이스 선택, 대화 목록, 검색 설정 슬라이더, 헬스 표시기
- Backend REST API만 호출 (직접 DB 접근 없음)
- 검색 비중(벡터/키워드 비율), Top-K를 사이드바 슬라이더로 실시간 조정 (개인 설정은 localStorage에 저장, DB 저장 없음)
- nginx 정적 빌드 서빙 + `/api/*` 요청을 Backend(`:8000`)로 프록시

### 2. Backend — FastAPI (`:8000`)

```
backend/
├── main.py              # 앱 진입점, 라이프사이클 (DB풀·임베딩·LLM·에이전트 초기화)
├── agents/              # 에이전트 레이어 (AgentBase + AgentRegistry 패턴)
│   ├── base.py          #   AgentBase 추상 클래스 + AgentRegistry 싱글톤
│   ├── knowledge_rag/
│   │   ├── agent.py     #   KnowledgeRagAgent — 하이브리드 검색 + LLM 스트리밍
│   │   ├── knowledge/   #   지식/용어집 CRUD + 하이브리드 검색 (retrieval.py, DB/공통코드 카테고리는 벡터 점수 0 처리 v2.49)
│   │   ├── ingestion/   #   지식 인제스천 파이프라인 (Tier 1~3)
│   │   │   ├── adapters.py      #   파일 파싱 (.txt/.md/.pdf → ParsedDocument)
│   │   │   ├── chunker.py       #   청킹 엔진 (section/paragraph/fixed/auto)
│   │   │   ├── analyzer.py      #   LLM Analyzer Agent (doc_type, chunk_strategy 자동 결정)
│   │   │   ├── tagger.py        #   LLM 자동 태깅 + 용어 추출
│   │   │   └── web_crawler.py   #   URL/Confluence 수집 (httpx + BeautifulSoup + Confluence REST API)
├── service/             # 플랫폼 공통 레이어 (was domain/, platform/ 명칭 stdlib 충돌로 service/ 확정)
│   ├── auth/            #   인증/계정 (JWT, bcrypt, Fernet API Key 암호화)
│   ├── chat/            #   채팅 라우터·헬퍼·메모리 (AgentRegistry 위임)
│   ├── feedback/        #   "답변 틀림" 신고 접수 → 개선 원장 (v2.121부터 가중치·질의 상태 안 바꿈)
│   ├── improvement/     #   개선 원장(정정 검토) — 신고·AI 판정/수정안(draft.py)·승인 시 버전 교체 (v2.118/120)
│   ├── refdata/         #   공통코드·DB 컬럼 참조 데이터 검색
│   ├── admin/           #   네임스페이스·통계·LLM 설정
│   ├── prompt/          #   프롬프트 관리 (get_prompt: DB 우선, fallback)
│   ├── llm/             #   LLM Provider 추상화 (ollama / inhouse)
│   ├── email_voc/       #   VOC 이메일 분석 채널 (v2.40 신규)
│   │   ├── graph_client.py    #   Microsoft Graph API 클라이언트 (msal 토큰 발급, 메일 조회, 페이지네이션/재시도)
│   │   ├── service.py         #   check_relevance()(관련지식 사전 필터, v2.41) + analyze_email() — 기존 RAG 파이프라인 재사용 분류/심각도/오배치 판정 + issue_signature(정규화 이슈요약) 출력 (v2.49)
│   │   ├── pattern_detection.py #   반복 VOC 클러스터링(centroid 기반, issue_signature 임베딩 비교, LLM 호출 없음) + 커버리지 LLM 검증 게이트(DB/공통코드 카테고리 제외, v2.49) (v2.47 신규)
│   │   ├── pipeline.py        #   수집→관련지식필터→분석→반복패턴탐지→중복제거→알림 오케스트레이션, 이력 조회
│   │   ├── routing_service.py #   파트별 메일함 라우팅 CRUD, 폴링 설정(관련지식 임계치 포함), Graph 자격증명(Fernet 암호화) CRUD
│   │   ├── delegated_auth.py  #   Delegated Permission 로그인 상태 관리 (Authorization Code Flow/PKCE, v2.41 신규·v2.42 인증방식 교체)
│   │   ├── teams_notify.py    #   Teams Workflows 웹훅 발송
│   │   ├── scheduler.py       #   백그라운드 폴링 루프 (asyncio.create_task, lifespan 등록)
│   │   ├── retention.py       #   30일 고정 보관정책 자동 정리
│   │   ├── schemas.py         #   Pydantic 스키마
│   │   └── router.py          #   /api/email-voc/* 엔드포인트
│   └── policy/           #   정책서(v0 원문 통째, docs/tech/policy-v0-design.md) — 옛 엑셀 분해 파이프라인은 v2.150에서 삭제(docs/archive/policy-doc-pipeline-plan.md)
│       ├── v0_convert.py      #   팀원 구조화 데이터 → 저장 단위 변환(LLM 없음·결정적, 적재는 scripts/load_policy_v0.py)
│       ├── service.py         #   서술 조각 머리 붙인 임베딩(chunk_embed_text) + 기동 때 재임베딩
│       ├── visibility.py      #   검색 대상 조건(searchable_sql) + 정책을 겨눈 대기 중 정정 조회(open_correction_sql) — 잎 모듈
│       ├── catalog.py         #   정책서 화면 조회(목록·상세·버전·참고표·개요, 읽기 전용 — 내용 변경은 정정 원장 제안으로만)
│       ├── caches.py          #   정책이 바뀐 뒤 비울 캐시 한 곳(답변 캐시 전 영역 + 키워드·분류 색인)
│       ├── search.py          #   파라미터(RDB tsquery)+서술(벡터·BM25 RRF) 검색 — 채팅(agent.py)과 /api/policy/search가 같이 사용
│       ├── keyword_index.py · category_list.py · scope.py  #   키워드 색인(BM25) · 탐색형 분류 목록 · 채팅의 정책 범위(영역 넘어 찾기)
│       ├── query_type.py · korean_text.py  #   navigation형 질문 판별 · 한국어 조사/어미 제거
│       ├── track2.py · answer_eval.py · pipeline_stats.py  #   검색 비교 실행 · 답변 정확도 이력 · 축적 현황 집계
│       ├── schemas.py         #   Pydantic 스키마
│       └── router.py          #   GET /api/policy/policies(/{logical_id}), /ref-tables(/{id}), /overviews, /search, /track2/*, /answer-eval/history, /pipeline-stats
├── core/
│   ├── config.py        # pydantic-settings, JWT·Fernet 키
│   ├── database.py      # asyncpg 풀 + resolve_namespace_id() 헬퍼
│   ├── security.py      # JWT, bcrypt, Fernet
│   └── dependencies.py  # get_current_user, get_current_admin, check_namespace_ownership
└── shared/
    ├── embedding.py     # Sentence-Transformers 싱글톤
    ├── cache.py         # Semantic Cache (Redis, 유사도 0.88, TTL 30분, graceful degradation)
    └── http_client.py   # 아웃바운드 HTTP 호출 공용 레이어 (call_http, v2.67 — VOC Teams 발송이 사용)
```

**주요 설계 원칙:**
- **AgentRegistry 패턴**: `chat_stream` → `AgentRegistry.get(agent_type).stream_chat()` 위임. 새 에이전트 추가 시 `agents/` 하위 모듈 + Registry 등록만으로 완결. 플랫폼(인증/세션/피드백)과 에이전트(파이프라인)를 분리.
- **DDD 구조**: 도메인별 디렉토리로 schemas/service/router를 응집 — 플랫 구조 대비 코드 탐색·확장 용이
- **비동기 전용**: asyncpg + httpx async — 블로킹 없는 I/O
- **임베딩 싱글톤**: 앱 시작 시 모델 1회 로드, 이후 thread executor로 재사용
- **LLM Provider 패턴**: `ollama` / `inhouse` 환경변수 하나로 교체 가능
- **LLM별 프롬프트 형식**: Ollama는 `build_messages()` messages 배열, InHouse(DevX MCP API)는 `_build_query()`로 단일 query 문자열 생성
- **대화 맥락**: ConversationSummaryBuffer + Semantic Recall — 오래된 교환을 LLM으로 요약·벡터 저장, 현재 질문과 유사한 과거 요약 + 최근 2회 raw 교환을 history로 LLM에 전달
- **멀티턴 검색 보강**: 직전 Q+A(각 80자)를 현재 질문에 결합하여 임베딩/검색 — 짧은 후속 질문에서도 이전 대화 맥락이 반영되어 유사도 향상 (추가 LLM 호출 없음)
- **마크다운 답변**: 시스템 프롬프트에 Markdown 형식 지시 포함, 프론트엔드에서 `react-markdown` + `remark-gfm` + `rehype-raw`로 테이블/코드/리스트/HTML 태그 렌더링
- **JWT 인증/인가**: Access Token(30분) + Refresh Token(7일), FastAPI Depends로 라우터 수준 보호
- **네임스페이스 소유 파트 기반 권한**: 네임스페이스의 `owner_part`와 동일한 부서 구성원만 해당 네임스페이스의 데이터 CRUD 가능, 타 부서는 읽기 전용. `owner_part` NULL(담당 미지정)이면 **관리자만** 수정(v2.142 — 예전엔 누구나). 업무 영역 만들기·이름·삭제는 관리자만, 새 영역은 담당 미지정으로 생김. Admin은 모든 권한 보유
- **수정 시 작성자 갱신**: 지식/용어 수정 시 `created_by_part`/`created_by_user_id`가 최종 수정자로 갱신됨
- **Graceful Degradation**: LLM 연결 실패 시 검색 결과는 정상 반환, 안내 메시지 출력

### 3. 인증/인가 시스템 (v2.0.0 신규)

```
┌──────────┐     POST /api/auth/register     ┌──────────────┐
│  사용자   │  ──────────────────────────────▶ │  auth/service │
│          │     (username, password,         │              │
│          │      part_id, api_key?)          │  bcrypt hash │
│          │                                  │  Fernet enc  │
│          │  ◀────────────────────────────── │              │
│          │     201 Created                  └──────┬───────┘
│          │                                         │
│          │     POST /api/auth/login                │
│          │  ──────────────────────────────▶        │
│          │  ◀──────────────────────────────        │
│          │     {access_token, refresh_token}       │
│          │                                         │
│          │     GET /api/chat/stream                │
│          │     Authorization: Bearer <access>      │
│          │  ──────────────────────────────▶ ┌──────┴───────┐
│          │                                  │ dependencies │
│          │                                  │ get_current_ │
│          │                                  │ user()       │
└──────────┘                                  └──────────────┘
```

**핵심 구성 요소:**

| 모듈 | 역할 |
|------|------|
| `core/security.py` | JWT 토큰 발급/검증 (HS256), bcrypt 비밀번호 해싱, Fernet 대칭 암호화 (LLM API Key + Confluence PAT) |
| `core/dependencies.py` | `get_current_user` — Bearer 토큰 검증 후 사용자 반환 |
| | `get_current_admin` — admin 역할 검증 |
| | `check_namespace_ownership` — 업무 영역 수정 권한(규칙 본체 `edit_denied_reason`), `check_namespace_read` — 관리 화면 조회 권한 |
| `service/auth/service.py` | 회원가입(조회 전용으로 시작, bcrypt, Fernet API Key 암호화), 로그인, 토큰 갱신, 팀·파트·사용자 관리 |
| `service/auth/router.py` | `/api/auth/*` 엔드포인트 |

**조직·업무 영역·권한 (v2.142):**
```
사람 축  ops_team(팀) ─ ops_part(파트: 소속·권한만, 데이터에 이름을 남기지 않음) ─ ops_user(role admin/user/viewer)
지식 축  ops_namespace(업무 영역) — kind domain / policy_test(E2E), parent_id는 트리 자리(지금 비움, P2 하위 업무)
         정책·지식 문서·용어·시스템 정보가 모두 namespace_id로 한 영역에 소속
연결     ops_namespace.owner_part_id(담당 파트) — 관리자 API(PATCH /api/namespaces/{name}/settings)·스크립트, 화면은 조직 관리 메뉴(설계 검수 중)
```
| 동작 | 규칙 |
|---|---|
| 데이터 수정(지식·용어·업무구분·정책 검토·VOC) | 관리자 / 담당 파트 사람(viewer 제외) / 담당 없는 영역은 관리자만. 프론트 `utils/domainAccess.ts`가 같은 규칙 |
| 지식 조회(지식·정책·용어·참고 데이터) | 로그인 사용자 누구나 — 다른 파트 담당 영역도(v2.144, `check_knowledge_read`). 화면은 수정 버튼만 숨김 |
| 사람 데이터 조회(통계·질의 기록·VOC 메일) | viewer 불가, 담당 없는 영역은 누구나, 담당이 있으면 그 파트·관리자(`check_namespace_read`) |
| 업무 영역 만들기·이름·삭제·담당 지정 | 관리자만. 삭제 시 글자 키 연결(ops_link)도 정리 |
| 가입 | 조회 전용(viewer)으로 시작 → 관리자가 사용자 관리에서 승인. 화면은 앱 열 때·창 포커스 때 `/auth/me`로 역할·소속을 다시 읽음 |
| 채팅 | 로그인 사용자 누구나(다른 파트 영역 조회는 막지 않고 기록만 — `log_cross_part_namespace_read`) |
- 대화 소유권: `ops_conversation.user_id` FK로 사용자별 대화 격리.
- 입력 검증: 이름 칸은 `service/validation.py` 공통 타입(앞뒤 공백 제거·길이·'/' 금지). DB 값 오류·중복·연결 경쟁은 공통 처리기가 400/409 + 한국어 사유(로그 남김),
  요청 검증 오류(422)도 한국어 문구.

**사용자별 LLM API Key:**
- 회원가입 또는 계정 설정에서 사내 LLM API Key 등록 (선택사항)
- Fernet 대칭 암호화로 DB에 저장 → 요청 시 복호화하여 InHouse LLM Provider에 전달
- 개인 키가 없으면 시스템 기본 키(`INHOUSE_LLM_API_KEY`) 사용

**사용자별 Confluence PAT (v2.14 신규):**
- 계정 설정 또는 URL 수집 폼에서 Confluence Personal Access Token 등록 (선택사항)
- LLM API Key와 동일한 Fernet 암호화 방식으로 `ops_user.encrypted_confluence_pat`에 저장
- URL 수집 시 PAT를 명시하지 않으면 DB에 저장된 개인 PAT 자동 로드 → Confluence 인증 자동 처리

### 4. PostgreSQL + pgvector (`:5432`)

```sql
-- 플랫폼 공통 (ops_* prefix)
ops_part              -- 부서 레지스트리
ops_user              -- 사용자 (role, part_id FK, encrypted_llm_api_key, encrypted_confluence_pat)
ops_namespace         -- 네임스페이스 (owner_part_id FK, created_by_user_id)
ops_conversation      -- 대화방 (namespace_id FK, user_id FK, agent_type)
ops_message           -- 대화 메시지 (role, content, results JSONB, metadata JSONB)
ops_improvement_item  -- 개선 원장(정정 검토): 답변 틀림·근거 정정·빠진 내용·검색 노이즈 + AI 수정안, 승인 전 검색 미노출 (v2.118/120)
ops_query_log         -- 질의 로그 (status: pending=답변 / no_knowledge=지식 공백 / system_error=LLM 연결 실패(통계 밖),
                      --   공백을 메우면 resolved_knowledge_id — v2.121)
ops_prompt            -- 프롬프트 관리 (agent_type별 에이전트 스코핑, Admin 시스템설정 탭에서 편집)
ops_system_config     -- 시스템 설정 key-value (캐시 임계값/TTL 등 영속화, VOC 폴링 정책/Graph 자격증명도 여기 저장)

-- VOC 이메일 분석 채널 전용 (v2.40 신규)
ops_voc_routing       -- 파트별 담당 메일함 ↔ Teams 웹훅 ↔ 온콜 연락처 매핑
ops_email_analysis    -- 이메일 건별 분석 결과 (source_message_id UNIQUE로 중복 수집 방지, 30일 보관, v2.47: embedding/voc_cluster_id FK 추가 — 벡터는 1024차원)
ops_email_poll_cycle  -- 폴링 사이클(스케줄러 실행 회차)별 성공/실패 이력 (30일 보관)
ops_voc_cluster       -- 반복 VOC 클러스터 (representative_embedding=centroid, member_count, coverage_knowledge_id/coverage_verified — 해결방안 LLM 검증 캐시) (v2.47 신규)

-- KnowledgeRAG 전용 (rag_* prefix, v2.8에서 ops_*→rag_* 변경)
rag_knowledge         -- 지식 베이스 (HNSW + GIN FTS, base_weight(v2.121부터 입력 없음·전부 1.0), source_file/chunk_idx 추적,
                      --   status: active/pending_review/rejected/deleted(v2.68 소프트삭제),
                      --   logical_document_id/version/supersedes_id: 근거 정정 승인 시 버전 교체에 사용(v2.118))
rag_knowledge_duplicate_match -- 중복탐지 매칭 후보 (v2.34)
rag_knowledge_history -- 병합(merge) 시 덮어써지기 전 content/embedding 보존 (v2.68)
rag_knowledge_category -- 카테고리 목록
rag_glossary          -- 용어집 (질문에 글자로 나온 용어만 매칭 — v2.128, 동의어는 rag_glossary_synonym)
rag_conv_summary      -- 대화 요약 (embedding VECTOR(1024), Semantic Recall용)
rag_ingestion_job     -- 인제스천 작업 이력 (source_type, status, auto_glossary/fewshot 수, analyzer_result JSONB)

-- Text-to-SQL 전용 (sql_* prefix) — v2.51에서 에이전트 제거, 마이그레이션 호출도 제거됨
-- (기존 설치엔 테이블이 남아있지만 더 이상 갱신되지 않음. 스키마 상세는 archive/with-text2sql 브랜치 참고)
```

- **HNSW 인덱스** (`vector_cosine_ops`): 벡터 근사 최근접 이웃 검색
- **GIN 인덱스** (`to_tsvector('simple', content)`): 전문 검색(FTS)
- **pg_trgm**: 트리그램 유사도 지원 (활성화됨)
- **namespace_id integer FK**: 모든 테이블에서 도메인 격리. namespace 이름 변경 시 cascade 업데이트 불필요
- **CASCADE 삭제**: `ops_conversation.user_id` → 사용자 삭제 시 대화 자동 삭제

### 5. Ollama — LLM 추론 (`:11434`)

- **비상용**(v2.143): `.env LLM_PROVIDER=ollama` 또는 API로만 — 관리자 설정 화면 선택지는 사내 LLM / 로컬 LLM(OpenAI 호환)
- 호스트 머신에서 직접 실행 (컨테이너 외부)
- 모델: `exaone3.5:2.4b`
- Backend에서 `host.docker.internal:11434`로 접근
- **`/api/chat` 엔드포인트** 사용 (GPT 방식 messages 배열, multi-turn 지원)

---

## 임베딩 모델

| 항목 | 값 |
|------|-----|
| 모델명 | `nlpai-lab/KURE-v1` (v2.72부터, 이전 `paraphrase-multilingual-mpnet-base-v2`) |
| 벡터 차원 | 1024 |
| 한국어 지원 | O |
| 실행 위치 | Backend 컨테이너 내 (CPU) |
| 캐시 볼륨 | `model-cache:/root/.cache/huggingface` |

- Docker 빌드 시 이미지에 모델 사전 다운로드 (컨테이너 시작 지연 없음)
- `normalize_embeddings=True` 적용 → 코사인 유사도 = 내적
- `EmbeddingService`는 `asyncio.Lock` 1개로 `embed()`/`embed_batch()`/`embed_long()`의 모델·토크나이저 접근을 직렬화(v2.49) — HuggingFace fast tokenizer(Rust, GIL 해제)가 동시 요청 시 내부 상태가 깨지는 `RuntimeError: Already borrowed`를 동시성 부하 실측 중 확인해 추가
- 같은 텍스트라도 **용도에 따라 다른 임베딩을 쓰는 경우가 있다**(v2.49) — VOC는 지식 검색(RAG)엔 원문 임베딩을, 반복 유형 클러스터링엔 LLM이 뽑은 정규화 요약(`issue_signature`)의 임베딩을 쓴다: 정밀 매칭은 문맥 보존이 유리하고, 표현 차이를 넘어선 그룹핑은 정규화가 유리하다는 기준

---

## API 엔드포인트

손으로 관리하던 엔드포인트 목록은 제거했다(2026-10-02) — 실제와 계속 어긋났다(없어진 `/api/feedback` 가중치 조정,
`/api/stats` 해결 처리 등이 남아 있었음). 현재 스펙은 FastAPI 자동 문서 `http://<backend>:8000/docs`가 정확하다.

## LLM Provider 확장 구조

```python
# domain/llm/base.py
def build_messages(context, question, history=None) -> list[dict]:
    # [system: 시스템프롬프트+참고문서] + [history...] + [user: 질문]

class LLMProvider(ABC):
    async def generate(context, question, history=None, api_key=None) -> str: ...
    async def generate_stream(context, question, history=None, api_key=None) -> AsyncIterator[str]: ...
    async def health_check() -> bool: ...

# 현재 구현체
OllamaProvider     # LLM_PROVIDER=ollama — /api/chat (messages 배열, multi-turn) — 비상용
OpenAICompatProvider # LLM_PROVIDER=openai_compat — 로컬 LLM(vLLM 등) /v1/chat/completions, 생각 과정 걸러냄, 모델 자동 감지
InHouseLLMProvider # LLM_PROVIDER=inhouse — DevX MCP API (usecase_code, query, response_mode)
                   #   inputs.model로 모델 선택 (GPT 5.2 / Claude Sonnet 4.5 / Gemini 3.0 Pro)
                   #   SSE: data JSON 안에 event 필드 포함하는 비표준 형식 대응
                   #   api_key: per-user 키 우선, 없으면 시스템 기본 키 사용
```

**`api_key` 파라미터 (v2.0.0 신규):**
- `generate()`, `generate_stream()`에 선택적 `api_key` 파라미터 추가
- 사용자가 개인 LLM API Key를 등록한 경우, Fernet 복호화 후 이 파라미터로 전달
- 개인 키가 없으면 `None` → Provider가 시스템 기본 키(`INHOUSE_LLM_API_KEY`) 사용
- OllamaProvider는 `api_key` 무시 (로컬 모델이므로 불필요)

**대화 맥락 전달 방식 (ConversationSummaryBuffer + Semantic Recall):**
```
messages = [
  {"role": "system",    "content": "시스템 프롬프트\n\n[참고 문서]\n{검색 결과}"},
  {"role": "system",    "content": "이 대화의 관련 과거 맥락:\n[과거 맥락 1]\n{요약}"},  ← Semantic Recall (유사도 0.45 이상)
  {"role": "user",      "content": "이전 질문"},   ← 최근 2회 raw 교환
  {"role": "assistant", "content": "이전 답변"},
  {"role": "user",      "content": "현재 질문"},
]
```

**메모리 동작 원리:**
- 4회 교환마다 오래된 대화를 LLM으로 요약 → `ops_conv_summary`에 임베딩 저장 (백그라운드)
- 새 질문 시 현재 질문 벡터로 과거 요약 검색 → 유사도 0.45 이상 최대 2개 추출
- 최근 2회 raw 교환은 항상 포함 (working memory)

**런타임 전환 (재시작 없음):**
- Admin → LLM 설정 탭에서 프로바이더 선택/설정 후 "저장 및 적용"
- `switch_provider(config)` 호출 → 싱글톤 교체, `_runtime_config` 전역 저장
- 컨테이너 재시작 시 `.env` 설정으로 복귀
- `get_runtime_config()`: 런타임 override 여부(`is_runtime_override`), 연결 상태(`is_connected`) 포함 반환

새 LLM 추가 시: `LLMProvider` 상속 → 3개 메서드 구현 → `service/llm/factory.py`에 등록

---

## 환경변수 설정

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DATABASE_URL` | `postgresql://ops:ops1234@postgres:5432/opsdb` | DB 연결 문자열 |
| `LLM_PROVIDER` | `inhouse` | `ollama` 또는 `inhouse` |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama 서버 주소 |
| `OLLAMA_MODEL` | `exaone3.5:7.8b` | 사용할 Ollama 모델명 |
| `OLLAMA_TIMEOUT` | `900` | CPU 추론 최대 대기 시간(초), httpx read timeout에 적용 |
| `OPENAI_COMPAT_BASE_URL` | (없음) | 로컬 LLM(OpenAI 호환) 주소 — 비면 화면에서 고를 수 없음(운영 .env엔 없음) |
| `OPENAI_COMPAT_MODEL` | (없음) | 비우면 서버의 첫 모델 자동 사용 |
| `OPENAI_COMPAT_TIMEOUT` | `300` | 응답 대기 시간(초) |
| `INHOUSE_LLM_URL` | (없음) | DevX MCP API 엔드포인트 URL |
| `INHOUSE_LLM_API_KEY` | (없음) | 사내 LLM 시스템 기본 API 키 (Bearer 토큰) |
| `INHOUSE_LLM_MODEL` | (없음) | inputs.model 파라미터 (gpt-5.2, claude-sonnet-4.5, gemini-3.0-pro) |
| `INHOUSE_LLM_AGENT_CODE` | `playground` | DevX usecase_code |
| `INHOUSE_LLM_RESPONSE_MODE` | `streaming` | 응답 방식 (`streaming` \| `blocking`) |
| `EMBEDDING_MODEL` | `nlpai-lab/KURE-v1` (v2.72부터) | 임베딩 모델명 |
| `VECTOR_DIM` | `1024` | 벡터 차원 수 |
| `DEFAULT_TOP_K` | `5` | 기본 검색 결과 수 |
| `DEFAULT_W_VECTOR` | `0.7` | 기본 벡터 검색 비중 |
| `DEFAULT_W_KEYWORD` | `0.3` | 기본 키워드 검색 비중 |
| `BACKEND_URL` | `http://backend:8000` | Frontend → Backend 주소 |
| `JWT_SECRET_KEY` | (필수) | JWT 서명 비밀 키 (HS256) |
| `FERNET_SECRET_KEY` | (필수) | Fernet 대칭 암호화 키 (사용자 API Key 암호화용) |
| `ADMIN_DEFAULT_PASSWORD` | (필수) | 초기 admin 계정 비밀번호 |

---

## pgvector 데이터 구성

### 벡터가 쓰이는 테이블

| 테이블 | 벡터 컬럼 | 용도 |
|--------|----------|------|
| `rag_knowledge` | `embedding VECTOR(1024)` | 문서 내용 임베딩 → 질문과 코사인 유사도로 관련 문서 검색 |
| `rag_glossary` | `embedding VECTOR(1024)` | 용어집 화면의 벡터 검색·동의어 품질 게이트용(v2.128부터 채팅 용어 매칭은 글자 매칭, `GLOSSARY_MATCH_MODE=embedding`이면 예전 방식) |
| `rag_conv_summary` | `embedding VECTOR(1024)` | 과거 대화 요약 임베딩 → 현재 질문과 유사한 과거 맥락 Semantic Recall (유사도 0.45 이상) |
| `policy_chunk` · `ops_email_analysis` · `ops_voc_cluster` | `VECTOR(1024)` | 정책 서술 검색 · VOC 메일 유사도/클러스터 |

### 검색 점수 공식

```
final_score = (w_vec × v_score + w_kw × k_score) × (1 + base_weight)
               └벡터 유사도    └BM25 키워드 점수    └문서 자체 가중치
```

- **v_score**: 코사인 유사도 (0~1) — HNSW 인덱스로 근사 탐색
- **k_score**: `ts_rank` BM25 점수 — GIN 인덱스로 전문 검색
- **base_weight**: 문서별 가중치 — v2.121부터 입력 경로가 없고 전부 1.0(상수). 채팅 채택 게이트는 이 값을 걷어낸 원점수로 판단.
  식에서 빼려면 메일 VOC 인용 임계값 재측정이 먼저(컬럼 삭제 때 같이)

### 비벡터 주요 테이블

`ops_part`, `ops_user`, `ops_namespace`, `rag_knowledge_category`, `ops_improvement_item`, `ops_query_log`, `ops_conversation`, `ops_message`, `ops_prompt`, `ops_system_config`

전체 스키마 정의는 `docs/table-definition.md` 참조.
